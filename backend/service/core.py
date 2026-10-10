"""SQLite WAL persistence; one dedicated thread executes every write transaction."""
from concurrent.futures import Future
import csv
import hashlib
import io
import json
import math
from pathlib import Path
import queue
import sqlite3
import threading
import time
from urllib.parse import quote
import uuid

from .schema import DECISIONS, validate_observation


def uid(prefix):
    return prefix + "_" + uuid.uuid4().hex


def packed(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def digest(value):
    return hashlib.sha256(value).hexdigest()


class Conflict(ValueError):
    pass


class Missing(KeyError):
    pass


SCHEMA = """
CREATE TABLE IF NOT EXISTS cases (
 id TEXT PRIMARY KEY, name TEXT NOT NULL, current_version INTEGER NOT NULL, created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS imports (
 id TEXT PRIMARY KEY, case_id TEXT NOT NULL REFERENCES cases(id), case_version INTEGER NOT NULL,
 filename TEXT NOT NULL, source_sha256 TEXT NOT NULL, raw_bytes BLOB NOT NULL, created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS messages (
 id TEXT PRIMARY KEY, case_id TEXT NOT NULL, case_version INTEGER NOT NULL, source_id TEXT NOT NULL,
 unit_id TEXT NOT NULL, position INTEGER NOT NULL, source_message_id TEXT NOT NULL, author_key TEXT,
 reply_to TEXT, timestamp TEXT, relative_seconds REAL, text TEXT NOT NULL, input_task TEXT, focus INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS source_units (
 id TEXT PRIMARY KEY, source_id TEXT NOT NULL, source_record_number INTEGER NOT NULL,
 source_unit_ref TEXT, input_task TEXT);
CREATE TABLE IF NOT EXISTS windows (
 id TEXT PRIMARY KEY, case_id TEXT NOT NULL, case_version INTEGER NOT NULL, source_id TEXT NOT NULL,
 unit_id TEXT NOT NULL, ordinal INTEGER NOT NULL, input_hash TEXT NOT NULL, message_ids TEXT NOT NULL,
 input_task TEXT, focus_message_id TEXT, estimated_tokens INTEGER NOT NULL, token_budget_exceeded INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS jobs (
 id TEXT PRIMARY KEY, window_id TEXT NOT NULL REFERENCES windows(id), case_id TEXT NOT NULL,
 case_version INTEGER NOT NULL, input_hash TEXT NOT NULL, pipeline_version TEXT NOT NULL,
 idempotency_key TEXT UNIQUE NOT NULL, status TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
 lease_owner TEXT, lease_until REAL, not_before REAL NOT NULL DEFAULT 0, error_code TEXT,
 result_json TEXT, raw_output TEXT, created_at REAL NOT NULL, updated_at REAL NOT NULL,
 UNIQUE(window_id,input_hash,pipeline_version));
CREATE TABLE IF NOT EXISTS review_tasks (
 id TEXT PRIMARY KEY, job_id TEXT UNIQUE NOT NULL REFERENCES jobs(id), case_id TEXT NOT NULL,
 case_version INTEGER NOT NULL, input_hash TEXT NOT NULL, state TEXT NOT NULL,
 human_gold TEXT, created_at REAL NOT NULL, updated_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS decisions (
 id TEXT PRIMARY KEY, review_task_id TEXT NOT NULL REFERENCES review_tasks(id),
 reviewer_id TEXT NOT NULL, decision TEXT NOT NULL, evidence TEXT NOT NULL, rationale TEXT NOT NULL,
 created_at REAL NOT NULL, UNIQUE(review_task_id,reviewer_id));
CREATE TABLE IF NOT EXISTS audit (
 seq INTEGER PRIMARY KEY AUTOINCREMENT, event TEXT NOT NULL, ref_id TEXT NOT NULL,
 metadata TEXT NOT NULL, created_at REAL NOT NULL);
CREATE INDEX IF NOT EXISTS ix_jobs_ready ON jobs(status,not_before,lease_until);
CREATE INDEX IF NOT EXISTS ix_messages_case ON messages(case_id,case_version,unit_id,position);
CREATE INDEX IF NOT EXISTS ix_windows_case ON windows(case_id,case_version);
"""


def flat_event_key(row, index):
    """A case ID is a container, not evidence that messages share a conversation."""
    for field in ("thread_id", "conversation_id", "event_id"):
        value = row.get(field)
        if value is not None and value != "":
            if not isinstance(value, str):
                raise ValueError("Explicit conversation/event IDs must be strings.")
            return (field, row.get("case_id"), value), value
    return ("independent_row", index), row.get("case_id")


def parse_units(raw, filename):
    if len(raw) > 32 * 1024 * 1024:
        raise ValueError("import_too_large")
    text = raw.decode("utf-8-sig")
    extension = Path(filename).suffix.lower()
    if extension == ".csv":
        rows = list(csv.DictReader(io.StringIO(text)))
        if not rows:
            raise ValueError("empty_import")
        grouped = {}
        for index, row in enumerate(rows):
            body = row.get("text", row.get("content"))
            if not isinstance(body, str) or not body.strip():
                raise ValueError(f"CSV row {index + 2}: text/content is required.")
            raw_relative = row.get("relative_time_seconds")
            try:
                relative = float(raw_relative) if raw_relative is not None and raw_relative != "" else None
            except (TypeError, ValueError) as exc:
                raise ValueError(f"CSV row {index + 2}: invalid relative time.") from exc
            group, reference = flat_event_key(row, index)
            grouped.setdefault(group, {"case_id": reference, "task": "language_observation_decision", "messages": []})["messages"].append({
                "message_id": row.get("message_id") or f"row-{index + 1}",
                "author_key": row.get("author_key") or row.get("actor_id") or None,
                "reply_to": row.get("reply_to") or None,
                "timestamp": row.get("timestamp") or None, "text": body,
                "relative_time_seconds": relative,
            })
        return list(grouped.values())
    if extension not in {".jsonl", ".json"}:
        raise ValueError("Only UTF-8 CSV, JSONL, or JSON is supported.")
    if extension == ".json":
        value = json.loads(text)
        rows = value if isinstance(value, list) else [value]
    else:
        rows = [json.loads(line) for line in text.splitlines() if line.strip()]
    if not rows or len(rows) > 20000:
        raise ValueError("empty_or_too_many_units")
    units = []
    flat_rows = []
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise ValueError(f"record {index + 1}: expected an object.")
        if isinstance(row.get("messages"), list):
            units.append(row)
        elif isinstance(row.get("text"), str):
            flat_rows.append(row)
        else:
            raise ValueError(f"record {index + 1}: messages or text is required.")
    if flat_rows and units:
        raise ValueError("Do not mix flat messages and event records in one import.")
    if flat_rows:
        grouped = {}
        for index, row in enumerate(flat_rows):
            group, reference = flat_event_key(row, index)
            grouped.setdefault(group, {"case_id": reference, "task": "language_observation_decision", "messages": []})["messages"].append({**row, "message_id": row.get("message_id", row.get("id", f"row-{index + 1}"))})
        units = list(grouped.values())
    return units


class Store:
    def __init__(self, path, token_budget=2048):
        self.path = Path(path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.token_budget = token_budget
        self._requests = queue.Queue()
        self._ready = threading.Event()
        self._error = None
        self._thread = threading.Thread(target=self._writer_loop, name="sqlite-single-writer", daemon=True)
        self._thread.start()
        self._ready.wait(15)
        if self._error:
            raise self._error
        if not self._ready.is_set():
            raise RuntimeError("SQLite writer startup timed out.")

    def _writer_loop(self):
        try:
            connection = sqlite3.connect(self.path, timeout=15)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA synchronous=FULL")
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA busy_timeout=5000")
            connection.executescript(SCHEMA)
            connection.commit()
        except Exception as exc:
            self._error = exc
            self._ready.set()
            return
        self._ready.set()
        while True:
            request = self._requests.get()
            if request is None:
                break
            function, future = request
            try:
                connection.execute("BEGIN IMMEDIATE")
                result = function(connection)
                connection.commit()
                future.set_result(result)
            except Exception as exc:
                connection.rollback()
                future.set_exception(exc)
        connection.close()

    def write(self, function):
        if not self._thread.is_alive():
            raise RuntimeError("SQLite writer is closed.")
        future = Future()
        self._requests.put((function, future))
        return future.result(timeout=30)

    def read(self, sql, parameters=()):
        url = "file:" + quote(self.path.as_posix(), safe="/:") + "?mode=ro"
        with sqlite3.connect(url, uri=True, timeout=15) as connection:
            connection.row_factory = sqlite3.Row
            return [dict(row) for row in connection.execute(sql, parameters).fetchall()]

    def close(self):
        self._requests.put(None)
        self._thread.join(15)

    @staticmethod
    def _audit(connection, event, ref_id, metadata):
        connection.execute("INSERT INTO audit(event,ref_id,metadata,created_at) VALUES(?,?,?,?)",
                           (event, ref_id, packed(metadata), time.time()))

    def import_bytes(self, raw, filename, case_name, case_id=None, expected_version=None):
        units = parse_units(raw, filename)
        prepared = []
        for unit_number, unit in enumerate(units, 1):
            incoming = unit["messages"]
            if not incoming or len(incoming) > 10000:
                raise ValueError("empty_or_too_many_messages")
            mapping = {}
            for index, message in enumerate(incoming):
                original_id = message.get("message_id", message.get("local_message_id", message.get("id", f"message-{index + 1}")))
                if not isinstance(original_id, str) or not original_id or original_id in mapping:
                    raise ValueError("Missing or duplicate message ID within an event.")
                mapping[original_id] = uid("msg")
            messages = []
            for index, message in enumerate(incoming):
                original_id = message.get("message_id", message.get("local_message_id", message.get("id", f"message-{index + 1}")))
                body = message.get("text")
                if not isinstance(body, str) or not body.strip():
                    raise ValueError("Every message requires nonempty text.")
                reply = message.get("reply_to", message.get("reply_to_local_id"))
                if reply is not None and reply not in mapping:
                    raise ValueError("Dangling reply reference; preserve unknown reply as null.")
                author = message.get("author_key", message.get("actor_id"))
                if author is not None and (not isinstance(author, str) or not author):
                    raise ValueError("Invalid author key.")
                relative = message.get("relative_time_seconds")
                if relative is not None and (type(relative) not in {int, float} or not math.isfinite(relative) or relative < 0):
                    raise ValueError("Invalid relative time.")
                messages.append({
                    "message_id": mapping[original_id], "source_message_id": original_id,
                    "author_key": author, "reply_to": mapping.get(reply),
                    "timestamp": message.get("timestamp"), "relative_time_seconds": relative,
                    "text": body, "position": index,
                })
            focus = unit.get("focus_message_id")
            if focus is not None and focus not in mapping:
                raise ValueError("Unknown focus message ID.")
            source_reference = unit.get("case_id", unit.get("request_id"))
            prepared.append({"unit_id": uid("unit"), "source_record_number": unit_number,
                             "source_unit_ref": str(source_reference) if source_reference is not None and source_reference != "" else None,
                             "input_task": unit.get("task", "language_observation_decision"),
                             "focus": mapping.get(focus), "messages": messages})
        now = time.time()
        source_hash = digest(raw)
        source_id = uid("source")
        new_case_id = case_id or uid("case")

        def transaction(connection):
            old = connection.execute("SELECT * FROM cases WHERE id=?", (new_case_id,)).fetchone()
            if case_id and old is None:
                raise Missing("case")
            if old:
                if expected_version is None or expected_version != old["current_version"]:
                    raise Conflict("case_version_conflict")
                existing = connection.execute("SELECT id,case_version FROM imports WHERE case_id=? AND source_sha256=? AND case_version=?",
                                              (new_case_id, source_hash, old["current_version"])).fetchone()
                if existing:
                    return {"case_id": new_case_id, "case_version": old["current_version"],
                            "source_id": existing["id"], "source_sha256": source_hash, "deduplicated": True}
                version = old["current_version"] + 1
                connection.execute("UPDATE cases SET current_version=? WHERE id=?", (version, new_case_id))
                connection.execute("UPDATE jobs SET status='stale',lease_owner=NULL,lease_until=NULL,error_code='case_version_changed',updated_at=? WHERE case_id=? AND case_version<?", (now, new_case_id, version))
                connection.execute("UPDATE review_tasks SET state='stale',updated_at=? WHERE case_id=? AND case_version<?", (now, new_case_id, version))
            else:
                version = 1
                connection.execute("INSERT INTO cases VALUES(?,?,?,?)", (new_case_id, case_name, version, now))
            connection.execute("INSERT INTO imports VALUES(?,?,?,?,?,?,?)",
                               (source_id, new_case_id, version, Path(filename).name, source_hash, sqlite3.Binary(raw), now))
            window_count = message_count = 0
            for unit in prepared:
                connection.execute("INSERT INTO source_units VALUES(?,?,?,?,?)",
                                   (unit["unit_id"], source_id, unit["source_record_number"], unit["source_unit_ref"], unit["input_task"]))
                for message in unit["messages"]:
                    connection.execute("INSERT INTO messages VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
                        message["message_id"], new_case_id, version, source_id, unit["unit_id"], message["position"],
                        message["source_message_id"], message["author_key"], message["reply_to"], message["timestamp"],
                        message["relative_time_seconds"], message["text"], unit["input_task"], int(message["message_id"] == unit["focus"])))
                    message_count += 1
                start = 0
                ordinal = 0
                while start < len(unit["messages"]):
                    chunk = unit["messages"][start:start + 8]
                    ordinal += 1
                    estimated = 200 + sum(len(message["text"]) + 30 for message in chunk)
                    input_hash = digest(packed([{
                        "message_id": message["message_id"], "text": message["text"],
                        "author_key": message["author_key"], "reply_to": message["reply_to"],
                        "timestamp": message["timestamp"], "relative_time_seconds": message["relative_time_seconds"],
                    } for message in chunk]).encode("utf-8"))
                    focus_in_chunk = unit["focus"] if any(message["message_id"] == unit["focus"] for message in chunk) else None
                    connection.execute("INSERT INTO windows VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", (
                        uid("window"), new_case_id, version, source_id, unit["unit_id"], ordinal, input_hash,
                        packed([message["message_id"] for message in chunk]), unit["input_task"],
                        focus_in_chunk, estimated, int(estimated > self.token_budget)))
                    window_count += 1
                    if start + 8 >= len(unit["messages"]):
                        break
                    start += 6
            self._audit(connection, "source_imported", source_id, {"case_id": new_case_id, "version": version, "sha256": source_hash})
            return {"case_id": new_case_id, "case_version": version, "source_id": source_id,
                    "source_sha256": source_hash, "units": len(prepared), "messages": message_count,
                    "windows": window_count, "deduplicated": False}
        return self.write(transaction)

    def cases(self):
        return self.read("SELECT * FROM cases ORDER BY created_at DESC")

    def case(self, case_id):
        rows = self.read("SELECT * FROM cases WHERE id=?", (case_id,))
        if not rows:
            raise Missing("case")
        value = rows[0]
        value["sources"] = self.read("SELECT id,case_version,filename,source_sha256,created_at FROM imports WHERE case_id=? ORDER BY case_version", (case_id,))
        return value

    def manifests(self, case_id):
        case = self.case(case_id)
        rows = self.read("SELECT * FROM windows WHERE case_id=? AND case_version=? ORDER BY unit_id,ordinal", (case_id, case["current_version"]))
        for row in rows:
            row["message_ids"] = json.loads(row["message_ids"])
            row["token_budget_exceeded"] = bool(row["token_budget_exceeded"])
        return rows

    def window(self, window_id):
        rows = self.read("SELECT * FROM windows WHERE id=?", (window_id,))
        if not rows:
            raise Missing("window")
        window = rows[0]
        ids = json.loads(window.pop("message_ids"))
        messages = []
        for message_id in ids:
            message = self.read("SELECT id AS message_id,source_message_id,author_key,reply_to,timestamp,relative_seconds AS relative_time_seconds,text FROM messages WHERE id=?", (message_id,))[0]
            messages.append(message)
        window["messages"] = messages
        window["token_budget_exceeded"] = bool(window["token_budget_exceeded"])
        return window

    def enqueue(self, window_id, pipeline_version, idempotency_key=None, expected_hash=None, expected_version=None):
        window = self.window(window_id)
        if expected_hash is not None and expected_hash != window["input_hash"]:
            raise Conflict("input_hash_conflict")
        if expected_version is not None and expected_version != window["case_version"]:
            raise Conflict("case_version_conflict")
        key = idempotency_key or digest((window_id + "|" + window["input_hash"] + "|" + pipeline_version).encode())
        now = time.time()

        def transaction(connection):
            case = connection.execute("SELECT current_version FROM cases WHERE id=?", (window["case_id"],)).fetchone()
            if case["current_version"] != window["case_version"]:
                raise Conflict("stale_window")
            existing = connection.execute("SELECT * FROM jobs WHERE idempotency_key=? OR (window_id=? AND input_hash=? AND pipeline_version=?)",
                                          (key, window_id, window["input_hash"], pipeline_version)).fetchall()
            if existing:
                for row in existing:
                    if row["window_id"] != window_id or row["input_hash"] != window["input_hash"] or row["pipeline_version"] != pipeline_version:
                        raise Conflict("idempotency_key_conflict")
                return dict(existing[0])
            job_id = uid("job")
            connection.execute("INSERT INTO jobs(id,window_id,case_id,case_version,input_hash,pipeline_version,idempotency_key,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                               (job_id, window_id, window["case_id"], window["case_version"], window["input_hash"], pipeline_version, key, "queued", now, now))
            self._audit(connection, "job_queued", job_id, {"window_id": window_id, "pipeline_version": pipeline_version})
            return dict(connection.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone())
        return self.write(transaction)

    def jobs(self, case_id=None):
        sql = "SELECT * FROM jobs"
        rows = self.read(sql + (" WHERE case_id=?" if case_id else "") + " ORDER BY created_at DESC", (case_id,) if case_id else ())
        return [self._job_value(row) for row in rows]

    @staticmethod
    def _job_value(row):
        value = dict(row)
        raw_result = value.pop("result_json", None)
        value["result"] = json.loads(raw_result) if raw_result else None
        value["human_gold"] = None
        return value

    def job(self, job_id):
        rows = self.read("SELECT * FROM jobs WHERE id=?", (job_id,))
        if not rows:
            raise Missing("job")
        return self._job_value(rows[0])

    def claim(self, owner, lease_seconds=300, now=None):
        now = time.time() if now is None else now
        def transaction(connection):
            row = connection.execute(
                "SELECT j.* FROM jobs j JOIN cases c ON c.id=j.case_id "
                "WHERE j.case_version=c.current_version AND "
                "((j.status IN ('queued','waiting_model') AND j.not_before<=?) OR (j.status='running' AND j.lease_until<?)) "
                "ORDER BY j.created_at ASC LIMIT 1", (now, now)).fetchone()
            if row is None:
                return None
            connection.execute("UPDATE jobs SET status='running',attempts=attempts+1,lease_owner=?,lease_until=?,updated_at=? WHERE id=?",
                               (owner, now + lease_seconds, now, row["id"]))
            self._audit(connection, "job_claimed", row["id"], {"owner": owner, "previous_status": row["status"]})
            return dict(connection.execute("SELECT * FROM jobs WHERE id=?", (row["id"],)).fetchone())
        return self.write(transaction)

    def recover_after_exclusive_start(self):
        now = time.time()
        def transaction(connection):
            count = connection.execute("UPDATE jobs SET status='queued',lease_owner=NULL,lease_until=NULL,not_before=0,updated_at=? WHERE status='running'", (now,)).rowcount
            if count:
                self._audit(connection, "abandoned_jobs_recovered", "service", {"count": count})
            return count
        return self.write(transaction)

    def extend_lease(self, job_id, owner, seconds):
        now = time.time()
        return self.write(lambda connection: connection.execute(
            "UPDATE jobs SET lease_until=?,updated_at=? WHERE id=? AND lease_owner=? AND status='running'",
            (now + seconds, now, job_id, owner)).rowcount)

    @staticmethod
    def _review(connection, job):
        existing = connection.execute("SELECT id FROM review_tasks WHERE job_id=?", (job["id"],)).fetchone()
        if existing:
            return existing["id"]
        review_id = uid("review")
        now = time.time()
        connection.execute("INSERT INTO review_tasks VALUES(?,?,?,?,?,?,?,?,?)",
                           (review_id, job["id"], job["case_id"], job["case_version"], job["input_hash"], "pending_single", None, now, now))
        return review_id

    def finish(self, job_id, owner, status, result=None, raw_output=None, error_code=None, retry_seconds=0):
        if status not in {"succeeded", "waiting_model", "invalid_output", "inconsistent_output", "truncated_output", "failed", "context_required"}:
            raise ValueError("Invalid terminal/wait state.")
        job = self.job(job_id)
        if result is not None:
            validate_observation(result, self.window(job["window_id"])["messages"])
        now = time.time()
        def transaction(connection):
            current = connection.execute("SELECT j.*,c.current_version FROM jobs j JOIN cases c ON c.id=j.case_id WHERE j.id=?", (job_id,)).fetchone()
            if current is None:
                raise Missing("job")
            if current["status"] != "running" or current["lease_owner"] != owner or current["lease_until"] < now:
                raise Conflict("lease_lost")
            if current["case_version"] != current["current_version"]:
                raise Conflict("stale_job")
            if status == "succeeded" and result is None:
                raise ValueError("Successful jobs require validated observations.")
            if status != "succeeded" and result is not None:
                raise ValueError("Error states cannot contain a substitute decision.")
            connection.execute("UPDATE jobs SET status=?,lease_owner=NULL,lease_until=NULL,not_before=?,error_code=?,result_json=?,raw_output=?,updated_at=? WHERE id=?",
                               (status, now + retry_seconds, error_code, packed(result) if result is not None else None, raw_output, now, job_id))
            if status in {"succeeded", "invalid_output", "inconsistent_output", "truncated_output", "failed", "context_required"}:
                self._review(connection, current)
            self._audit(connection, "job_finished", job_id, {"status": status, "error_code": error_code})
            return self._job_value(dict(connection.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()))
        return self.write(transaction)

    def create_review(self, job_id):
        def transaction(connection):
            job = connection.execute("SELECT j.*,c.current_version FROM jobs j JOIN cases c ON c.id=j.case_id WHERE j.id=?", (job_id,)).fetchone()
            if job is None:
                raise Missing("job")
            if job["case_version"] != job["current_version"]:
                raise Conflict("stale_job")
            return {"review_task_id": self._review(connection, job)}
        return self.write(transaction)

    def reviews(self, case_id=None):
        sql = "SELECT * FROM review_tasks"
        tasks = self.read(sql + (" WHERE case_id=?" if case_id else "") + " ORDER BY created_at DESC", (case_id,) if case_id else ())
        for task in tasks:
            task["decisions"] = self.read("SELECT * FROM decisions WHERE review_task_id=? ORDER BY created_at", (task["id"],))
            for decision in task["decisions"]:
                decision["evidence"] = json.loads(decision["evidence"])
            task["human_gold"] = None
        return tasks

    def decide(self, review_id, reviewer_id, decision, evidence_ids, rationale, case_version, input_hash):
        if decision not in DECISIONS or not isinstance(rationale, str) or not rationale.strip():
            raise ValueError("Decision and nonempty rationale are required.")
        if decision == "supported" and not evidence_ids:
            raise ValueError("Supported manual observations require evidence.")
        tasks = self.read("SELECT * FROM review_tasks WHERE id=?", (review_id,))
        if not tasks:
            raise Missing("review")
        task = tasks[0]
        job = self.job(task["job_id"])
        messages = {m["message_id"]: m for m in self.window(job["window_id"])["messages"]}
        if len(evidence_ids) != len(set(evidence_ids)) or any(key not in messages for key in evidence_ids):
            raise ValueError("Invalid evidence message IDs.")
        evidence = [{"message_id": key, "quote": messages[key]["text"], "start": 0, "end": len(messages[key]["text"])}
                    for key in evidence_ids]
        now = time.time()
        def transaction(connection):
            current = connection.execute("SELECT r.*,c.current_version FROM review_tasks r JOIN cases c ON c.id=r.case_id WHERE r.id=?", (review_id,)).fetchone()
            if current["case_version"] != current["current_version"] or case_version != current["case_version"] or input_hash != current["input_hash"]:
                raise Conflict("stale_review")
            old = connection.execute("SELECT * FROM decisions WHERE review_task_id=? AND reviewer_id=?", (review_id, reviewer_id)).fetchone()
            if old:
                if old["decision"] == decision and old["evidence"] == packed(evidence) and old["rationale"] == rationale:
                    return {"decision_id": old["id"], "state": "single_reviewed", "human_gold": None, "deduplicated": True}
                raise Conflict("decision_is_immutable")
            decision_id = uid("decision")
            connection.execute("INSERT INTO decisions VALUES(?,?,?,?,?,?,?)",
                               (decision_id, review_id, reviewer_id, decision, packed(evidence), rationale, now))
            connection.execute("UPDATE review_tasks SET state='single_reviewed',human_gold=NULL,updated_at=? WHERE id=?", (now, review_id))
            self._audit(connection, "single_review_recorded", review_id, {"reviewer_id": reviewer_id, "decision_id": decision_id, "human_gold": False})
            return {"decision_id": decision_id, "state": "single_reviewed", "human_gold": None, "deduplicated": False}
        return self.write(transaction)

    def report(self, case_id):
        case = self.case(case_id)
        windows = self.manifests(case_id)
        jobs = self.jobs(case_id)
        current_jobs = [job for job in jobs if job["case_version"] == case["current_version"]]
        completed_windows = {job["window_id"] for job in current_jobs if job["status"] == "succeeded"}
        failed_windows = {job["window_id"] for job in current_jobs if job["status"] in {"failed", "invalid_output", "inconsistent_output", "truncated_output", "context_required"}}
        return {
            "schema_version": "1", "scope": "language_observation_only", "case": case,
            "window_count": len(windows), "model_completed_windows": len(completed_windows),
            "manual_exception_windows": len(failed_windows), "model_coverage": len(completed_windows) / len(windows) if windows else 0,
            "all_windows_model_completed": len(completed_windows) == len(windows),
            "jobs": jobs, "review_tasks": self.reviews(case_id), "human_gold": None,
            "legal_determination": None,
            "audit": self.read("SELECT * FROM audit WHERE ref_id IN (SELECT id FROM imports WHERE case_id=?) OR ref_id IN (SELECT id FROM jobs WHERE case_id=?) OR ref_id IN (SELECT id FROM review_tasks WHERE case_id=?) ORDER BY seq",
                               (case_id, case_id, case_id)),
        }
