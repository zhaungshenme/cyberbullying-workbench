"""Durable, reference-only Dify submission outbox for the Kali workbench."""
import asyncio
import contextlib
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import threading
import time
import uuid

import httpx

from service.core import Conflict, Missing, packed, parse_units


TERMINAL = {"succeeded", "invalid_output", "inconsistent_output", "truncated_output",
            "failed", "context_required", "stale"}
ACK_KEYS = {"job_id", "window_id", "case_id", "case_version", "input_hash", "pipeline_version",
            "status", "error_code", "review_ref"}


def public_job(job):
    """Only validated results and bounded provenance cross the workbench API."""
    result = dict(job)
    raw = result.pop("raw_output", None)
    result.pop("raw_response", None)
    provenance = {}
    if isinstance(raw, str):
        try:
            envelope = json.loads(raw)
            if isinstance(envelope, dict):
                for name in ("model_sha256", "tokenizer_sha256", "dynamic_schema_sha256", "output_schema_sha256"):
                    value = envelope.get(name)
                    if isinstance(value, str) and re.fullmatch(r"[a-fA-F0-9]{64}", value):
                        provenance[name] = value.lower()
                for name in ("prompt_eval_count", "preflight_prompt_tokens", "eval_count", "token_budget"):
                    value = envelope.get(name)
                    if type(value) is int and value >= 0:
                        provenance[name] = value
                delta = envelope.get("prompt_token_count_delta")
                if type(delta) is int:
                    provenance["prompt_token_count_delta"] = delta
        except ValueError:
            pass
    result["model_provenance"] = provenance
    result["job_id"] = result["id"]
    return result


def prepare_input(content, filename, settings):
    raw = content.encode("utf-8")
    if not raw or len(raw) > settings.max_input_bytes:
        raise ValueError("input_empty_or_exceeds_1_mib")
    extension = Path(filename).suffix.lower()
    if extension == ".txt":
        imported_name = Path(filename).stem + ".json"
        import_bytes = packed({"case_id": "uploaded-text", "task": "language_observation_decision",
            "focus_message_id": "message-001", "messages": [{"message_id": "message-001", "text": content,
                "author_key": None, "reply_to": None, "timestamp": None, "relative_time_seconds": None}]}).encode("utf-8")
    elif extension in {".json", ".jsonl", ".csv"}:
        imported_name, import_bytes = Path(filename).name, raw
    else:
        raise ValueError("unsupported_file_type_use_txt_json_jsonl_csv")
    units = parse_units(import_bytes, imported_name)
    windows = 0
    for unit in units:
        messages = unit.get("messages")
        if not isinstance(messages, list) or not messages or len(messages) > 10000:
            raise ValueError("empty_or_too_many_messages")
        ids = set()
        for position, message in enumerate(messages):
            if not isinstance(message, dict) or not isinstance(message.get("text"), str) or not message["text"].strip():
                raise ValueError("every_message_requires_nonempty_text")
            mid = message.get("message_id", message.get("local_message_id", message.get("id", f"message-{position + 1}")))
            if not isinstance(mid, str) or not mid or mid in ids:
                raise ValueError("missing_or_duplicate_message_id")
            ids.add(mid)
        windows += 1 + max(0, (len(messages) - 8 + 5) // 6)
    if windows > settings.max_windows:
        raise ValueError("input_exceeds_100_windows_no_truncation")
    return raw, imported_name, import_bytes


class SubmissionDB:
    def __init__(self, path):
        self.lock = threading.RLock()
        self.db = sqlite3.connect(path, timeout=15, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS submissions (
          id TEXT PRIMARY KEY, idempotency_key TEXT NOT NULL UNIQUE, payload_hash TEXT NOT NULL,
          filename TEXT NOT NULL, import_filename TEXT NOT NULL, case_name TEXT NOT NULL,
          original_bytes BLOB NOT NULL, import_bytes BLOB NOT NULL, original_sha256 TEXT NOT NULL,
          case_id TEXT NOT NULL, state TEXT NOT NULL, error_code TEXT, created_at REAL NOT NULL, updated_at REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS dispatch_outbox (
          submission_id TEXT NOT NULL REFERENCES submissions(id), window_id TEXT NOT NULL,
          ordinal INTEGER NOT NULL, payload TEXT NOT NULL, state TEXT NOT NULL, attempts INTEGER NOT NULL,
          next_attempt_at REAL NOT NULL, job_id TEXT, workflow_run_id TEXT, error_code TEXT,
          PRIMARY KEY(submission_id,window_id));
        CREATE TABLE IF NOT EXISTS dify_runs (
          seq INTEGER PRIMARY KEY AUTOINCREMENT, submission_id TEXT NOT NULL, window_id TEXT NOT NULL,
          attempt INTEGER NOT NULL, state TEXT NOT NULL, workflow_run_id TEXT, job_id TEXT,
          error_code TEXT, created_at REAL NOT NULL);
        """)
        columns = {row["name"] for row in self.db.execute("PRAGMA table_info(submissions)")}
        if "owner_id" not in columns:
            self.db.execute("ALTER TABLE submissions ADD COLUMN owner_id TEXT")
        self.db.execute("CREATE INDEX IF NOT EXISTS submissions_created_id ON submissions(created_at DESC,id DESC)")
        self.db.execute("CREATE INDEX IF NOT EXISTS submissions_owner_created_id ON submissions(owner_id,created_at DESC,id DESC)")
        self.db.commit()

    def close(self):
        with self.lock:
            self.db.close()

    def read(self, sql, args=()):
        with self.lock:
            return [dict(row) for row in self.db.execute(sql, args).fetchall()]

    def reserve(self, case_name, filename, content, key, settings, owner_id=None):
        if owner_id is not None:
            key = "user:" + owner_id + ":" + key
        raw, import_filename, import_bytes = prepare_input(content, filename, settings)
        payload_hash = hashlib.sha256(packed({"case_name": case_name, "filename": filename, "content": content}).encode()).hexdigest()
        now = time.time()
        with self.lock, self.db:
            old = self.db.execute("SELECT id,payload_hash FROM submissions WHERE idempotency_key=?", (key,)).fetchone()
            if old:
                if old["payload_hash"] != payload_hash:
                    raise Conflict("idempotency_key_conflict")
                return old["id"], True
            sid = "submission_" + uuid.uuid4().hex
            case_id = "case_" + hashlib.sha256(sid.encode()).hexdigest()[:32]
            self.db.execute("INSERT INTO submissions(id,idempotency_key,payload_hash,filename,import_filename,case_name,original_bytes,import_bytes,original_sha256,case_id,state,error_code,created_at,updated_at,owner_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
                sid, key, payload_hash, filename, import_filename, case_name, raw, import_bytes,
                hashlib.sha256(raw).hexdigest(), case_id, "reserved", None, now, now, owner_id))
            return sid, False

    def recover(self, settings):
        with self.lock, self.db:
            self.db.execute("UPDATE dispatch_outbox SET state=CASE WHEN attempts>=? THEN 'exhausted' ELSE 'pending_retry' END,"
                "error_code='interrupted_delivery_unconfirmed',next_attempt_at=? WHERE state='dispatching'",
                (settings.max_dispatch_attempts, time.time()))

    def one(self, sid):
        rows = self.read("SELECT * FROM submissions WHERE id=?", (sid,))
        if not rows:
            raise Missing("submission")
        return rows[0]


class Engine:
    def __init__(self, store, db, settings, *, client=None):
        self.store, self.db, self.settings, self.client = store, db, settings, client
        self.task = None
        self.dispatch_lock = asyncio.Lock()

    def _ordered_windows_with_units(self, case_id):
        windows = self.store.manifests(case_id)
        unit_ids = sorted({window["unit_id"] for window in windows})
        if not unit_ids:
            return [], {}
        # Use these manifests' unit IDs, rather than a second current-version join.
        # The workbench input limit bounds this set to at most 100 source units.
        placeholders = ",".join("?" for _ in unit_ids)
        units = {unit["id"]: unit for unit in self.store.read(
            "SELECT id,source_record_number,source_unit_ref FROM source_units WHERE id IN (" + placeholders + ")", unit_ids)}
        return sorted(windows, key=lambda row: (units[row["unit_id"]]["source_record_number"], row["ordinal"])), units

    def ordered_windows(self, case_id):
        return self._ordered_windows_with_units(case_id)[0]

    def prepare_reserved(self):
        rows = self.db.read("SELECT id FROM submissions WHERE state='reserved' ORDER BY created_at")
        for ref in rows:
            row = self.db.one(ref["id"])
            try:
                now = time.time()
                self.store.write(lambda connection: connection.execute("INSERT OR IGNORE INTO cases VALUES(?,?,0,?)",
                    (row["case_id"], row["case_name"], now)))
                case = self.store.case(row["case_id"])
                import_hash = hashlib.sha256(row["import_bytes"]).hexdigest()
                if case["sources"]:
                    if len(case["sources"]) != 1 or case["sources"][0]["source_sha256"] != import_hash:
                        raise Conflict("reserved_case_source_conflict")
                else:
                    self.store.import_bytes(row["import_bytes"], row["import_filename"], row["case_name"], row["case_id"], 0)
                windows = self.ordered_windows(row["case_id"])
                if len(windows) > self.settings.max_windows:
                    raise ValueError("input_exceeds_100_windows_no_truncation")
                with self.db.lock, self.db.db:
                    for order, window in enumerate(windows):
                        inputs = {"window_id": window["id"], "input_hash": window["input_hash"],
                            "case_version": window["case_version"], "pipeline_version": self.settings.pipeline_version,
                            "idempotency_key": row["id"] + ":" + window["id"] + ":" + self.settings.pipeline_version}
                        self.db.db.execute("INSERT OR IGNORE INTO dispatch_outbox VALUES(?,?,?,?,?,0,?,NULL,NULL,NULL)",
                            (row["id"], window["id"], order, packed(inputs), "pending", now))
                    self.db.db.execute("UPDATE submissions SET state='prepared',updated_at=? WHERE id=?", (now, row["id"]))
            except (ValueError, Conflict):
                with self.db.lock, self.db.db:
                    self.db.db.execute("UPDATE submissions SET state='import_failed',error_code='invalid_material_structure',updated_at=? WHERE id=?", (time.time(), row["id"]))

    async def start(self):
        self.db.recover(self.settings)
        self.task = asyncio.create_task(self.run(), name="dify-submission-outbox")

    async def stop(self):
        if self.task:
            self.task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.task

    async def run(self):
        while True:
            await self.dispatch_once()
            await asyncio.sleep(0.25)

    def record_attempt(self, row, state, code=None, ack=None, run_id=None, retryable=True):
        ack = ack or {}
        attempt = row["attempts"] + 1
        if state != "acked":
            state = "pending_retry" if retryable and attempt < self.settings.max_dispatch_attempts else ("exhausted" if retryable else "rejected")
        next_at = time.time() + self.settings.retry_seconds * 2 ** max(0, attempt - 1)
        with self.db.lock, self.db.db:
            self.db.db.execute("UPDATE dispatch_outbox SET state=?,next_attempt_at=?,job_id=?,workflow_run_id=?,error_code=? WHERE submission_id=? AND window_id=?",
                (state, next_at, ack.get("job_id"), run_id, code, row["submission_id"], row["window_id"]))
            self.db.db.execute("INSERT INTO dify_runs(submission_id,window_id,attempt,state,workflow_run_id,job_id,error_code,created_at) VALUES(?,?,?,?,?,?,?,?)",
                (row["submission_id"], row["window_id"], attempt, state, run_id, ack.get("job_id"), code, time.time()))

    async def dispatch_once(self):
        async with self.dispatch_lock:
            await asyncio.to_thread(self.prepare_reserved)
            # Finish or explicitly fail earlier windows before submitting the next window.
            rows = self.db.read("SELECT o.* FROM dispatch_outbox o JOIN submissions s ON s.id=o.submission_id "
                "WHERE o.state IN ('pending','pending_retry') AND o.next_attempt_at<=? "
                "AND NOT EXISTS(SELECT 1 FROM dispatch_outbox p WHERE p.submission_id=o.submission_id AND p.ordinal<o.ordinal "
                "AND p.state IN ('pending','pending_retry','dispatching')) ORDER BY s.created_at,o.ordinal LIMIT 1", (time.time(),))
            if not rows:
                return False
            row = rows[0]
            with self.db.lock, self.db.db:
                self.db.db.execute("UPDATE dispatch_outbox SET state='dispatching',attempts=attempts+1 WHERE submission_id=? AND window_id=?",
                    (row["submission_id"], row["window_id"]))
            inputs = json.loads(row["payload"])
            if not self.settings.dify_key:
                self.record_attempt(row, "error", "dify_not_configured", retryable=False)
                return True
            current = self.store.window(row["window_id"])
            if any(current[name] != inputs[name] for name in ("input_hash", "case_version")):
                self.record_attempt(row, "error", "stale_dispatch_references", retryable=False)
                return True
            client = self.client or httpx.AsyncClient(timeout=45, trust_env=False, follow_redirects=False)
            try:
                response = await client.post(self.settings.dify_url.rstrip("/") + "/workflows/run",
                    headers={"Authorization": "Bearer " + self.settings.dify_key},
                    json={"inputs": inputs, "response_mode": "blocking", "user": self.db.one(row["submission_id"]).get("owner_id") or "legacy-workbench"})
                if response.status_code != 200:
                    self.record_attempt(row, "error", "dify_http_" + str(response.status_code),
                        retryable=response.status_code in {408, 429, 500, 502, 503, 504})
                    return True
                try:
                    body = response.json()
                    data = body["data"]
                    outputs = data["outputs"]
                    run_id = body["workflow_run_id"]
                    if data["status"] != "succeeded" or not isinstance(run_id, str) or not run_id:
                        raise ValueError("workflow_not_succeeded")
                    if type(outputs["http_status"]) is not int or outputs["http_status"] != 202:
                        raise ValueError("backend_not_accepted")
                    ack = json.loads(outputs["result"])
                    if not isinstance(ack, dict) or set(ack) != ACK_KEYS:
                        raise ValueError("invalid_ack_schema")
                    if any(ack[name] != inputs[name] for name in ("window_id", "input_hash", "case_version", "pipeline_version")):
                        raise ValueError("ack_reference_mismatch")
                    if type(ack["case_version"]) is not int or ack["case_id"] != current["case_id"]:
                        raise ValueError("ack_reference_mismatch")
                    if not isinstance(ack["job_id"], str) or not ack["job_id"] or ack["status"] not in {"queued", "running", "waiting_model"} | TERMINAL:
                        raise ValueError("invalid_ack_status")
                    saved = self.store.job(ack["job_id"])
                    if any(saved[name] != ack[name] for name in ("window_id", "input_hash", "case_version", "pipeline_version", "case_id")):
                        raise ValueError("ack_saved_job_mismatch")
                except (ValueError, KeyError, TypeError, Missing):
                    self.record_attempt(row, "error", "invalid_dify_acknowledgement", retryable=False)
                    return True
                self.record_attempt(row, "acked", ack=ack, run_id=run_id)
                return True
            except httpx.TransportError:
                self.record_attempt(row, "error", "dify_delivery_unconfirmed")
                return True
            finally:
                if self.client is None:
                    await client.aclose()

    def retry(self, sid):
        self.db.one(sid)
        with self.db.lock, self.db.db:
            count = self.db.db.execute("UPDATE dispatch_outbox SET state='pending_retry',attempts=0,next_attempt_at=?,error_code=NULL "
                "WHERE submission_id=? AND state IN ('exhausted','rejected')", (time.time(), sid)).rowcount
        return {"submission_id": sid, "retry_windows": count}

    def task_snapshot(self, sid, worker):
        row = self.db.one(sid)
        windows, jobs, case = [], [], None
        if row["state"] == "prepared":
            case = self.store.case(row["case_id"])
            case.update({"original_filename": row["filename"], "original_source_sha256": row["original_sha256"]})
            manifests, source_units = self._ordered_windows_with_units(row["case_id"])
            windows = [self.store.window(window["id"]) for window in manifests]
            for window in windows:
                unit = source_units[window["unit_id"]]
                window["source_unit_ref"] = unit["source_unit_ref"]
                window["source_record_number"] = unit["source_record_number"]
            jobs = [public_job(job) for job in self.store.jobs(row["case_id"])]
        outbox = self.db.read("SELECT window_id,ordinal,state,attempts,job_id,workflow_run_id,error_code FROM dispatch_outbox WHERE submission_id=? ORDER BY ordinal", (sid,))
        runs = self.db.read("SELECT window_id,attempt,state,workflow_run_id,job_id,error_code,created_at FROM dify_runs WHERE submission_id=? ORDER BY seq", (sid,))
        errors = [{"window_id": item["window_id"], "stage": "dify_submit", "code": item["error_code"], "retryable": item["state"] in {"pending_retry", "exhausted", "rejected"}} for item in outbox if item["error_code"]]
        errors += [{"window_id": job["window_id"], "stage": "model", "code": job["error_code"], "status": job["status"]} for job in jobs if job.get("error_code")]
        if row["error_code"]:
            errors.append({"stage": "import", "code": row["error_code"]})
        done = sum(job["status"] == "succeeded" for job in jobs)
        failed = sum(job["status"] in TERMINAL - {"succeeded"} for job in jobs)
        submitted = sum(item["state"] == "acked" for item in outbox)
        if row["state"] == "reserved":
            status = "preparing"
        elif row["state"] == "import_failed":
            status = "import_failed"
        elif any(item["state"] in {"pending", "pending_retry", "dispatching"} for item in outbox):
            status = "dispatching"
        elif any(item["state"] in {"exhausted", "rejected"} for item in outbox):
            status = "dispatch_failed"
        elif len(jobs) == len(windows) and all(job["status"] in TERMINAL for job in jobs):
            status = "completed_with_errors" if failed else "completed"
        elif any(job["status"] == "waiting_model" for job in jobs):
            status = "waiting_model"
        else:
            status = "running"
        return {"submission_id": sid, "status": status, "case": case, "windows": windows,
            "jobs": jobs, "dify_runs": runs, "dispatch": outbox, "errors": errors,
            "coverage": {"total_windows": len(windows), "submitted_windows": submitted, "completed_windows": done,
                "error_windows": failed, "model_coverage": done / len(windows) if windows else 0,
                "all_windows_model_completed": bool(windows) and done == len(windows)},
            "model": {"name": self.settings.model_name, "state": worker.model_state,
                "finetuned": False, "inference_concurrency": 1},
            "needs_review": True, "human_gold": None, "legal_determination": None}
