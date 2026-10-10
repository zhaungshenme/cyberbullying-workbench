"""Persistent local identities, opaque sessions and append-only workflow audit."""
import hashlib
import hmac
import json
import re
import secrets
import sqlite3
import threading
import time
import uuid


ROLES = {"admin", "analyst", "reviewer", "auditor"}
SESSION_SECONDS = 8 * 60 * 60


class AuthError(Exception):
    def __init__(self, status, detail):
        self.status, self.detail = status, detail


def password_hash(password, salt):
    return hashlib.scrypt(password.encode("utf-8"), salt=salt, n=16384, r=8, p=1, dklen=32)


def check_password_policy(password):
    if not isinstance(password, str) or not 12 <= len(password) <= 256:
        raise AuthError(400, "password_must_have_12_to_256_characters")


def public_user(row):
    return {"id": row["id"], "username": row["username"], "display_name": row["display_name"],
        "role": row["role"], "active": bool(row["active"]), "must_change_password": bool(row["must_change_password"])}


def session_context(token):
    """Public request condition, separate from the hash used to authenticate."""
    return hashlib.sha256(b"workbench-context-v1\0" + token.encode()).hexdigest()


class AuthDB:
    def __init__(self, path, bootstrap_password):
        self.lock = threading.RLock()
        self.db = sqlite3.connect(path, timeout=15, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS users (
          id TEXT PRIMARY KEY, username TEXT NOT NULL UNIQUE, display_name TEXT NOT NULL,
          role TEXT NOT NULL, active INTEGER NOT NULL, must_change_password INTEGER NOT NULL,
          password_salt BLOB NOT NULL, password_hash BLOB NOT NULL, created_at REAL NOT NULL, updated_at REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS sessions (
          token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL, expires_at REAL NOT NULL, created_at REAL NOT NULL);
        CREATE INDEX IF NOT EXISTS sessions_user ON sessions(user_id);
        CREATE TABLE IF NOT EXISTS login_attempts (
          username TEXT PRIMARY KEY, failures INTEGER NOT NULL, blocked_until REAL NOT NULL, last_seen REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS assignments (
          submission_id TEXT PRIMARY KEY, reviewer_id TEXT, auditor_id TEXT, updated_at REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS reviews (
          id TEXT PRIMARY KEY, submission_id TEXT NOT NULL, reviewer_id TEXT NOT NULL, outcome TEXT NOT NULL,
          comment TEXT NOT NULL, case_version INTEGER NOT NULL, created_at REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS audit (
          seq INTEGER PRIMARY KEY AUTOINCREMENT, actor_id TEXT, action TEXT NOT NULL,
          submission_id TEXT, metadata TEXT NOT NULL, created_at REAL NOT NULL);
        CREATE INDEX IF NOT EXISTS assignments_reviewer ON assignments(reviewer_id,submission_id);
        CREATE INDEX IF NOT EXISTS assignments_auditor ON assignments(auditor_id,submission_id);
        CREATE INDEX IF NOT EXISTS audit_submission_seq ON audit(submission_id,seq DESC);
        """)
        review_columns = {row["name"] for row in self.db.execute("PRAGMA table_info(reviews)")}
        if "references_json" not in review_columns:
            self.db.execute("ALTER TABLE reviews ADD COLUMN references_json TEXT NOT NULL DEFAULT '[]'")
        if "idempotency_key" not in review_columns:
            self.db.execute("ALTER TABLE reviews ADD COLUMN idempotency_key TEXT")
        self.db.execute("CREATE UNIQUE INDEX IF NOT EXISTS review_idempotency ON reviews(submission_id,reviewer_id,idempotency_key)")
        self.db.commit()
        with self.lock:
            if self.db.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0:
                check_password_policy(bootstrap_password)
                self.create_user(None, "admin", "管理员", "admin", bootstrap_password)

    def attach_submissions(self, path):
        # The database path is configured by the server, never by a cursor or caller.
        with self.lock:
            self.db.execute("ATTACH DATABASE ? AS material_submissions", (str(path),))

    def task_page(self, actor, limit, high_water=None, after=None):
        with self.lock:
            if high_water is None:
                high_water = self.db.execute("SELECT COALESCE(MAX(rowid),0) FROM material_submissions.submissions").fetchone()[0]
            joins, clauses, args = "", ["s.rowid<=?"], [high_water]
            if actor["role"] == "analyst":
                clauses.append("s.owner_id=?")
                args.append(actor["id"])
            elif actor["role"] in {"reviewer", "auditor"}:
                column = "reviewer_id" if actor["role"] == "reviewer" else "auditor_id"
                joins = " JOIN assignments a ON a.submission_id=s.id"
                clauses.extend(["s.owner_id IS NOT NULL", "a." + column + "=?"])
                args.append(actor["id"])
            elif actor["role"] != "admin":
                raise AuthError(403, "role_forbidden")
            if after is not None:
                clauses.append("(s.created_at<? OR (s.created_at=? AND s.id<?))")
                args.extend([after[0], after[0], after[1]])
            rows = [dict(row) for row in self.db.execute("SELECT s.id,s.case_name,s.owner_id,s.created_at FROM material_submissions.submissions s" + joins +
                " WHERE " + " AND ".join(clauses) + " ORDER BY s.created_at DESC,s.id DESC LIMIT ?", (*args, limit + 1)).fetchall()]
            return rows[:limit], high_water, len(rows) > limit

    def audit_page(self, actor, limit, high_water=None, after=None):
        with self.lock:
            if high_water is None:
                high_water = self.db.execute("SELECT COALESCE(MAX(seq),0) FROM audit").fetchone()[0]
            joins, clauses, args = "", ["e.seq<=?"], [high_water]
            if actor["role"] == "auditor":
                joins = " JOIN assignments a ON a.submission_id=e.submission_id JOIN material_submissions.submissions s ON s.id=e.submission_id"
                clauses.extend(["s.owner_id IS NOT NULL", "a.auditor_id=?"])
                args.append(actor["id"])
            elif actor["role"] != "admin":
                raise AuthError(403, "role_forbidden")
            if after is not None:
                clauses.append("e.seq<?")
                args.append(after)
            rows = [dict(row) for row in self.db.execute("SELECT e.* FROM audit e" + joins + " WHERE " + " AND ".join(clauses) +
                " ORDER BY e.seq DESC LIMIT ?", (*args, limit + 1)).fetchall()]
            more = len(rows) > limit
            return self.public_audit(rows[:limit]), high_water, more

    def close(self):
        with self.lock:
            self.db.close()

    def read(self, sql, args=()):
        with self.lock:
            return [dict(row) for row in self.db.execute(sql, args).fetchall()]

    def audit(self, actor_id, action, submission_id=None, metadata=None):
        # Callers pass only bounded IDs, role/status metadata; never credentials or material text.
        with self.lock, self.db:
            self._audit(actor_id, action, submission_id, metadata)

    def _audit(self, actor_id, action, submission_id=None, metadata=None):
        self.db.execute("INSERT INTO audit(actor_id,action,submission_id,metadata,created_at) VALUES(?,?,?,?,?)",
            (actor_id, action, submission_id, json.dumps(metadata or {}, ensure_ascii=False, separators=(",", ":")), time.time()))

    def user(self, user_id):
        rows = self.read("SELECT * FROM users WHERE id=?", (user_id,))
        if not rows:
            raise AuthError(404, "user_not_found")
        return public_user(rows[0])

    def summary(self, user_id):
        if user_id is None:
            return None
        try:
            user = self.user(user_id)
            return {name: user[name] for name in ("id", "username", "display_name", "role", "active")}
        except AuthError:
            return None

    def users(self):
        return [public_user(row) for row in self.read("SELECT * FROM users ORDER BY created_at,id")]

    def create_user(self, actor_id, username, display_name, role, password):
        username = username.strip().casefold()
        if not re.fullmatch(r"[A-Za-z0-9_.-]{3,64}", username) or role not in ROLES or not display_name.strip() or len(display_name) > 100:
            raise AuthError(400, "invalid_user_fields")
        check_password_policy(password)
        salt, now, user_id = secrets.token_bytes(16), time.time(), "user_" + uuid.uuid4().hex
        digest = password_hash(password, salt)
        with self.lock, self.db:
            try:
                self.db.execute("INSERT INTO users VALUES(?,?,?,?,1,1,?,?,?,?)", (user_id, username, display_name, role, salt, digest, now, now))
            except sqlite3.IntegrityError as exc:
                raise AuthError(409, "username_exists") from exc
            self._audit(actor_id, "user_created", metadata={"user_id": user_id, "role": role})
        return self.user(user_id)

    def update_user(self, actor_id, user_id, changes):
        with self.lock, self.db:
            self.db.execute("BEGIN IMMEDIATE")
            row = self.db.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
            if row is None:
                raise AuthError(404, "user_not_found")
            display_name, role, active = changes.get("display_name", row["display_name"]), changes.get("role", row["role"]), changes.get("active", bool(row["active"]))
            if role not in ROLES or not isinstance(active, bool) or not isinstance(display_name, str) or not display_name.strip() or len(display_name) > 100:
                raise AuthError(400, "invalid_user_fields")
            if row["active"] and row["role"] == "admin" and (not active or role != "admin"):
                count = self.db.execute("SELECT COUNT(*) FROM users WHERE active=1 AND role='admin'").fetchone()[0]
                if count <= 1:
                    raise AuthError(409, "last_active_admin_protected")
            password = changes.get("password")
            salt, digest = row["password_salt"], row["password_hash"]
            must_change = row["must_change_password"]
            if password is not None:
                check_password_policy(password)
                salt = secrets.token_bytes(16)
                digest = password_hash(password, salt)
                must_change = 1
            sensitive = role != row["role"] or active != bool(row["active"]) or password is not None
            self.db.execute("UPDATE users SET display_name=?,role=?,active=?,must_change_password=?,password_salt=?,password_hash=?,updated_at=? WHERE id=?",
                (display_name, role, int(active), must_change, salt, digest, time.time(), user_id))
            if sensitive:
                self.db.execute("DELETE FROM sessions WHERE user_id=?", (user_id,))
            self._audit(actor_id, "user_updated", metadata={"user_id": user_id, "fields": sorted(changes), "role": role, "active": active})
            if password is not None:
                self._audit(actor_id, "user_password_reset", metadata={"user_id": user_id})
            if row["active"] and not active:
                self._audit(actor_id, "user_disabled", metadata={"user_id": user_id})
        return self.user(user_id)

    def new_session(self, user_id):
        with self.lock, self.db:
            return self._new_session(user_id)

    def _new_session(self, user_id):
        token = secrets.token_urlsafe(32)
        now = time.time()
        self.db.execute("DELETE FROM sessions WHERE expires_at<=?", (now,))
        self.db.execute("DELETE FROM sessions WHERE user_id=? AND token_hash NOT IN (SELECT token_hash FROM sessions WHERE user_id=? ORDER BY created_at DESC LIMIT 19)", (user_id, user_id))
        self.db.execute("INSERT INTO sessions VALUES(?,?,?,?)", (hashlib.sha256(token.encode()).hexdigest(), user_id, now + SESSION_SECONDS, now))
        return token

    def login(self, username, password):
        username = username.strip().casefold()
        with self.lock:
            row = self.db.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
            # A missing user still performs scrypt so rejection does not skip the expensive path.
            salt = row["password_salt"] if row else b"invalid-user-salt"
            digest = password_hash(password, salt)
            now = time.time()
            attempt = self.db.execute("SELECT * FROM login_attempts WHERE username=?", (username,)).fetchone()
            blocked = attempt is not None and attempt["blocked_until"] > now
            if blocked or row is None or not row["active"] or not hmac.compare_digest(digest, row["password_hash"]):
                failures = attempt["failures"] + 1 if attempt and now - attempt["last_seen"] < 300 else 1
                with self.db:
                    self.db.execute("INSERT INTO login_attempts VALUES(?,?,?,?) ON CONFLICT(username) DO UPDATE SET failures=excluded.failures,blocked_until=excluded.blocked_until,last_seen=excluded.last_seen",
                        (username[:64], min(failures, 100), now + 300 if failures >= 5 else 0, now))
                    self.db.execute("DELETE FROM login_attempts WHERE username NOT IN (SELECT username FROM login_attempts ORDER BY last_seen DESC LIMIT 1000)")
                self.audit(None, "login_failed", metadata={"username": username[:64]})
                raise AuthError(401, "invalid_credentials")
            with self.db:
                self.db.execute("DELETE FROM login_attempts WHERE username=?", (username,))
            self.audit(row["id"], "login_succeeded")
            token = self.new_session(row["id"])
            return {"session_token": token, "user": public_user(row), "session_context": session_context(token)}

    def authenticate(self, token):
        if not isinstance(token, str) or not token or len(token) > 200:
            raise AuthError(401, "authentication_required")
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        with self.lock:
            row = self.db.execute("SELECT u.*,s.expires_at FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token_hash=?", (token_hash,)).fetchone()
            if row is None or not row["active"] or row["expires_at"] <= time.time():
                raise AuthError(401, "session_invalid_or_expired")
            return public_user(row)

    def logout(self, token, actor):
        with self.lock, self.db:
            self.db.execute("DELETE FROM sessions WHERE token_hash=?", (hashlib.sha256(token.encode()).hexdigest(),))
            self._audit(actor["id"], "logout")

    def change_password(self, actor, current, new):
        check_password_policy(new)
        with self.lock, self.db:
            self.db.execute("BEGIN IMMEDIATE")
            row = self.db.execute("SELECT * FROM users WHERE id=?", (actor["id"],)).fetchone()
            if not hmac.compare_digest(password_hash(current, row["password_salt"]), row["password_hash"]):
                self.db.rollback()
                self.audit(actor["id"], "password_change_failed")
                raise AuthError(403, "current_password_invalid")
            if hmac.compare_digest(password_hash(new, row["password_salt"]), row["password_hash"]):
                raise AuthError(400, "new_password_must_differ")
            salt = secrets.token_bytes(16)
            self.db.execute("UPDATE users SET password_salt=?,password_hash=?,must_change_password=0,updated_at=? WHERE id=?",
                (salt, password_hash(new, salt), time.time(), actor["id"]))
            self.db.execute("DELETE FROM sessions WHERE user_id=?", (actor["id"],))
            self._audit(actor["id"], "password_changed")
            token = self._new_session(actor["id"])
            return {"session_token": token, "user": self.user(actor["id"]), "session_context": session_context(token)}

    def assignment(self, sid):
        rows = self.read("SELECT * FROM assignments WHERE submission_id=?", (sid,))
        return rows[0] if rows else {"reviewer_id": None, "auditor_id": None}

    def visible(self, actor, submission):
        if actor["role"] == "admin":
            return True
        owner_id = submission.get("owner_id")
        if owner_id is None:
            return False
        assigned = self.assignment(submission["id"])
        return ((actor["role"] == "analyst" and owner_id == actor["id"]) or
            (actor["role"] == "reviewer" and assigned["reviewer_id"] == actor["id"]) or
            (actor["role"] == "auditor" and assigned["auditor_id"] == actor["id"]))

    def assign(self, actor, sid, owner_id, reviewer_id, auditor_id):
        with self.lock, self.db:
            self.db.execute("BEGIN IMMEDIATE")
            if owner_id is None:
                raise AuthError(400, "legacy_ownerless_assignment_forbidden")
            for user_id, role in ((reviewer_id, "reviewer"), (auditor_id, "auditor")):
                if user_id is not None:
                    user = self.user(user_id)
                    if not user["active"] or user["role"] != role:
                        raise AuthError(400, "assignee_role_or_active_invalid")
                    if user_id == owner_id and role == "reviewer":
                        raise AuthError(400, "owner_cannot_review")
            self.db.execute("INSERT INTO assignments VALUES(?,?,?,?) ON CONFLICT(submission_id) DO UPDATE SET reviewer_id=excluded.reviewer_id,auditor_id=excluded.auditor_id,updated_at=excluded.updated_at",
                (sid, reviewer_id, auditor_id, time.time()))
            self._audit(actor["id"], "task_assigned", sid, {"reviewer_id": reviewer_id, "auditor_id": auditor_id})

    def review(self, actor, submission, outcome, comment, case_version, references, idempotency_key=None):
        sid = submission["id"]
        with self.lock, self.db:
            self.db.execute("BEGIN IMMEDIATE")
            assignment = self.assignment(sid)
            if actor["role"] != "reviewer" or assignment["reviewer_id"] != actor["id"] or submission.get("owner_id") == actor["id"]:
                raise AuthError(403, "assigned_reviewer_required")
            if idempotency_key is not None:
                old = self.db.execute("SELECT * FROM reviews WHERE submission_id=? AND reviewer_id=? AND idempotency_key=?", (sid, actor["id"], idempotency_key)).fetchone()
                if old:
                    if (old["outcome"], old["comment"], old["case_version"], json.loads(old["references_json"])) != (outcome, comment, case_version, references):
                        raise AuthError(409, "review_idempotency_key_conflict")
                    return old["id"]
            review_id = "review_" + uuid.uuid4().hex
            self.db.execute("INSERT INTO reviews(id,submission_id,reviewer_id,outcome,comment,case_version,created_at,references_json,idempotency_key) VALUES(?,?,?,?,?,?,?,?,?)", (review_id, sid, actor["id"], outcome, comment, case_version, time.time(), json.dumps(references, separators=(",", ":")), idempotency_key))
            self._audit(actor["id"], "task_reviewed", sid, {"review_id": review_id, "outcome": outcome, "case_version": case_version})
        return review_id

    def task_context(self, submission, include_audit=False, case_version=None, references=None):
        sid = submission["id"]
        assigned = self.assignment(sid)
        reviews = self.read("SELECT * FROM reviews WHERE submission_id=? ORDER BY created_at,id", (sid,))
        for review in reviews:
            review["reviewer"] = self.summary(review.pop("reviewer_id"))
            review["references"] = json.loads(review.pop("references_json"))
            review.pop("idempotency_key", None)
        current_reviewed = any(review["reviewer"] and review["reviewer"]["id"] == assigned["reviewer_id"]
            and review["case_version"] == case_version and review["references"] == references for review in reviews)
        result = {"owner": self.summary(submission.get("owner_id")), "reviewer": self.summary(assigned["reviewer_id"]),
            "auditor": self.summary(assigned["auditor_id"]), "reviews": reviews,
            "review_status": "reviewed" if current_reviewed else ("pending" if assigned["reviewer_id"] else "unassigned")}
        if include_audit:
            result["audit"] = self.audit_rows([sid])
        return result

    def audit_rows(self, submission_ids=None, limit=100):
        limit = min(max(limit, 1), 100)
        if submission_ids is None:
            rows = self.read("SELECT * FROM audit ORDER BY seq DESC LIMIT ?", (limit,))
        elif not submission_ids:
            rows = []
        else:
            placeholders = ",".join("?" for _ in submission_ids)
            rows = self.read("SELECT * FROM audit WHERE submission_id IN (" + placeholders + ") ORDER BY seq DESC LIMIT ?", (*submission_ids, limit))
        return self.public_audit(rows)

    def public_audit(self, rows):
        for row in rows:
            row["metadata"] = json.loads(row["metadata"])
            row["actor"] = self.summary(row.pop("actor_id"))
        return rows
