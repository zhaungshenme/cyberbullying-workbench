from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import sqlite3
import time

import pytest

from auth import AuthDB, AuthError
from orchestration import SubmissionDB
from test_workbench import BACKEND, analyze, dispatch, healthy_dify, running


def actor(client, username, role):
    temporary = "Temporary-test-123!"
    created = client.post("/workbench/users", headers=BACKEND, json={"username": username,
        "display_name": username, "role": role, "password": temporary})
    assert created.status_code == 200
    login = client.post("/workbench/auth/login", headers=BACKEND, json={"username": username, "password": temporary})
    assert login.status_code == 200
    changed = client.post("/workbench/auth/password", headers={**BACKEND, "X-Workbench-Session": login.json()["session_token"]},
        json={"current_password": temporary, "new_password": "Current-" + username + "-456!"})
    assert changed.status_code == 200
    return {"user": changed.json()["user"], "headers": {**BACKEND, "X-Workbench-Session": changed.json()["session_token"]}}


def submit(client, person, key="same-key", text="测试材料正文，按原文判断。"):
    return client.post("/workbench/analyze", headers=person["headers"], json={"case_name": "owned-material",
        "filename": "source.txt", "content": text, "idempotency_key": key})


def assign(client, sid, reviewer=None, auditor=None):
    return client.post("/workbench/tasks/" + sid + "/assign", headers=BACKEND,
        json={"reviewer_id": reviewer["user"]["id"] if reviewer else None, "auditor_id": auditor["user"]["id"] if auditor else None})


def complete(app, client, sid, invalid=False):
    app.state.engine.prepare_reserved()
    healthy_dify(app)
    dispatch(app, client)
    job = app.state.store.claim("multiuser-test", 60)
    if invalid:
        app.state.store.finish(job["id"], "multiuser-test", "invalid_output", None, "UNVALIDATED", "quote_mismatch")
    else:
        answer = {"decision": "not_supported", "observations": ["normal_criticism"], "evidence": [],
            "target_actor_key": None, "rationale": "当前文字不含明确攻击。", "context_flags": [], "missing_info": []}
        app.state.store.finish(job["id"], "multiuser-test", "succeeded", answer, "UNVALIDATED", None)
    return client.get("/workbench/tasks/" + sid, headers=BACKEND).json()


def review(client, sid, person, version, outcome="request_context", key=None, comment="需要核对上下文。"):
    body = {"outcome": outcome, "comment": comment, "case_version": version}
    if key:
        body["idempotency_key"] = key
    return client.post("/workbench/tasks/" + sid + "/review", headers=person["headers"], json=body)


def test_no_session_and_browser_identity_cannot_bypass(running):
    app, client, _ = running
    assert client.get("/workbench/health", headers={**BACKEND, "X-Workbench-Session": ""}).status_code == 401
    forged = client.post("/workbench/analyze", headers=BACKEND, json={"case_name": "fake", "filename": "a.txt", "content": "x",
        "idempotency_key": "k", "owner_id": "forged-user", "role": "admin"})
    assert forged.status_code == 422
    assert not app.state.db.read("SELECT * FROM submissions")


def test_temporary_password_gate_and_rotation(running):
    _, client, _ = running
    created = client.post("/workbench/users", headers=BACKEND, json={"username": "temporary", "display_name": "临时用户",
        "role": "analyst", "password": "Temporary-test-123!"}).json()
    login = client.post("/workbench/auth/login", headers=BACKEND, json={"username": "temporary", "password": "Temporary-test-123!"}).json()
    headers = {**BACKEND, "X-Workbench-Session": login["session_token"]}
    assert created["must_change_password"] and client.get("/workbench/auth/me", headers=headers).status_code == 200
    blocked = client.get("/workbench/health", headers=headers)
    assert blocked.status_code == 403 and blocked.json()["detail"] == "password_change_required"
    changed = client.post("/workbench/auth/password", headers=headers,
        json={"current_password": "Temporary-test-123!", "new_password": "Changed-user-456!"}).json()
    assert not changed["user"]["must_change_password"]
    assert client.get("/workbench/auth/me", headers=headers).status_code == 401
    assert client.get("/workbench/health", headers={**BACKEND, "X-Workbench-Session": changed["session_token"]}).status_code == 200


def test_failed_password_change_is_audited_and_keeps_session(running):
    app, client, _ = running
    denied = client.post("/workbench/auth/password", headers=BACKEND,
        json={"current_password": "Incorrect-current-123!", "new_password": "New-password-456!"})
    assert denied.status_code == 403
    assert client.get("/workbench/auth/me", headers=BACKEND).status_code == 200
    assert app.state.auth.read("SELECT * FROM audit WHERE action='password_change_failed'")


def test_normalized_unique_usernames_and_role_boundaries(running):
    _, client, _ = running
    analyst = actor(client, "AnalystOne", "analyst")
    reviewer = actor(client, "reviewer-one", "reviewer")
    auditor = actor(client, "auditor-one", "auditor")
    assert analyst["user"]["username"] == "analystone"
    duplicate = client.post("/workbench/users", headers=BACKEND, json={"username": " analystone ", "display_name": "duplicate",
        "role": "analyst", "password": "Temporary-test-123!"})
    assert duplicate.status_code == 409
    for person in (analyst, reviewer, auditor):
        assert client.get("/workbench/users", headers=person["headers"]).status_code == 403
    assert submit(client, reviewer).status_code == 403
    assert submit(client, auditor).status_code == 403
    assert client.get("/workbench/audit", headers=analyst["headers"]).status_code == 403


def test_cross_user_idempotency_and_task_isolation(running):
    app, client, _ = running
    a, b = actor(client, "analyst-a", "analyst"), actor(client, "analyst-b", "analyst")
    first, repeat, other = submit(client, a), submit(client, a), submit(client, b)
    sid_a, sid_b = first.json()["submission_id"], other.json()["submission_id"]
    assert repeat.json()["submission_id"] == sid_a and repeat.json()["deduplicated"]
    assert sid_a != sid_b
    assert app.state.db.one(sid_a)["owner_id"] == a["user"]["id"]
    assert app.state.db.one(sid_b)["owner_id"] == b["user"]["id"]
    for suffix in ("", "/report"):
        assert client.get("/workbench/tasks/" + sid_a + suffix, headers=b["headers"]).status_code == 404
    assert [row["submission_id"] for row in client.get("/workbench/tasks", headers=a["headers"]).json()["tasks"]] == [sid_a]
    assert [row["submission_id"] for row in client.get("/workbench/tasks", headers=b["headers"]).json()["tasks"]] == [sid_b]


def test_populated_legacy_submissions_migrate_ownerless_and_new_reserve(tmp_path):
    path = tmp_path / "submissions.sqlite3"
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE submissions(id TEXT PRIMARY KEY,idempotency_key TEXT UNIQUE,payload_hash TEXT,filename TEXT,import_filename TEXT,case_name TEXT,original_bytes BLOB,import_bytes BLOB,original_sha256 TEXT,case_id TEXT,state TEXT,error_code TEXT,created_at REAL,updated_at REAL)")
    connection.execute("INSERT INTO submissions VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)", ("old-task", "old-k", "h", "old.txt", "old.json", "old", b"x", b"{}", "h", "old-case", "reserved", None, 1, 1))
    connection.commit()
    connection.close()
    db = SubmissionDB(path)
    try:
        assert db.one("old-task")["owner_id"] is None
        from settings import Settings
        sid, _ = db.reserve("new", "new.txt", "new body", "k", Settings(tmp_path, "a", "b"), "known-user")
        assert db.one(sid)["owner_id"] == "known-user"
    finally:
        db.close()


def test_legacy_ownerless_task_admin_only_and_cannot_assign(running):
    app, client, settings = running
    reviewer = actor(client, "legacy-reviewer", "reviewer")
    sid, _ = app.state.db.reserve("legacy", "legacy.txt", "旧任务", "legacy", settings)
    app.state.engine.prepare_reserved()
    assert client.get("/workbench/tasks/" + sid, headers=BACKEND).status_code == 200
    assert client.get("/workbench/tasks/" + sid, headers=reviewer["headers"]).status_code == 404
    assert assign(client, sid, reviewer).status_code == 400
    retry = client.post("/workbench/tasks/" + sid + "/retry", headers=BACKEND)
    assert retry.status_code == 400 and retry.json()["detail"] == "legacy_ownerless_read_only"


def test_disable_reset_role_change_logout_and_expiry_revoke_sessions(running):
    app, client, _ = running
    person = actor(client, "revoke-person", "analyst")
    sid = submit(client, person).json()["submission_id"]
    changed = client.patch("/workbench/users/" + person["user"]["id"], headers=BACKEND, json={"role": "reviewer"})
    assert changed.status_code == 200
    assert client.get("/workbench/auth/me", headers=person["headers"]).status_code == 401
    login = client.post("/workbench/auth/login", headers=BACKEND, json={"username": "revoke-person", "password": "Current-revoke-person-456!"}).json()
    person["headers"]["X-Workbench-Session"] = login["session_token"]
    assert client.get("/workbench/tasks/" + sid, headers=person["headers"]).status_code == 404
    assert client.patch("/workbench/users/" + person["user"]["id"], headers=BACKEND, json={"password": "Reset-password-123!"}).status_code == 200
    assert client.get("/workbench/auth/me", headers=person["headers"]).status_code == 401
    login = client.post("/workbench/auth/login", headers=BACKEND, json={"username": "revoke-person", "password": "Reset-password-123!"}).json()
    person["headers"]["X-Workbench-Session"] = login["session_token"]
    assert login["user"]["must_change_password"]
    assert client.post("/workbench/auth/logout", headers=person["headers"]).status_code == 200
    assert client.get("/workbench/auth/me", headers=person["headers"]).status_code == 401
    login = client.post("/workbench/auth/login", headers=BACKEND, json={"username": "revoke-person", "password": "Reset-password-123!"}).json()
    person["headers"]["X-Workbench-Session"] = login["session_token"]
    with app.state.auth.lock, app.state.auth.db:
        app.state.auth.db.execute("UPDATE sessions SET expires_at=? WHERE user_id=?", (time.time() - 1, person["user"]["id"]))
    assert client.get("/workbench/auth/me", headers=person["headers"]).status_code == 401
    client.patch("/workbench/users/" + person["user"]["id"], headers=BACKEND, json={"active": False})
    denied = client.post("/workbench/auth/login", headers=BACKEND, json={"username": "revoke-person", "password": "Reset-password-123!"})
    assert denied.status_code == 401 and denied.json()["detail"] == "invalid_credentials"


def test_last_active_admin_guard_is_atomic_across_connections(running):
    app, client, settings = running
    second = actor(client, "second-admin", "admin")
    first = client.get("/workbench/auth/me", headers=BACKEND).json()["user"]
    other_db = AuthDB(settings.data_dir / "auth.sqlite3", "")
    def demote(database, uid):
        try:
            database.update_user(first["id"], uid, {"role": "analyst"})
            return "changed"
        except AuthError as exc:
            return exc.detail
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            a = pool.submit(demote, app.state.auth, first["id"])
            b = pool.submit(demote, other_db, second["user"]["id"])
            results = [a.result(), b.result()]
        assert sorted(results) == ["changed", "last_active_admin_protected"]
        assert len([user for user in app.state.auth.users() if user["active"] and user["role"] == "admin"]) == 1
    finally:
        other_db.close()


def test_assigned_review_append_version_idempotency_and_reassignment(running):
    app, client, _ = running
    owner = actor(client, "review-owner", "analyst")
    r1, r2 = actor(client, "reviewer-r1", "reviewer"), actor(client, "reviewer-r2", "reviewer")
    sid = submit(client, owner).json()["submission_id"]
    assert assign(client, sid, r1).status_code == 200
    assert review(client, sid, r1, 1).status_code == 409
    snapshot = complete(app, client, sid)
    version = snapshot["case_version"]
    assert review(client, sid, r1, version + 1).status_code == 409
    assert review(client, sid, owner, version).status_code == 403
    admin = {"headers": BACKEND}
    assert review(client, sid, admin, version).status_code == 403
    assert review(client, sid, r2, version).status_code == 404
    first = review(client, sid, r1, version, "confirm_observation", "review-click")
    assert first.status_code == 200 and first.json()["review_status"] == "reviewed"
    repeat = review(client, sid, r1, version, "confirm_observation", "review-click")
    assert repeat.json()["review_id"] == first.json()["review_id"]
    assert len(repeat.json()["reviews"]) == 1
    assert review(client, sid, r1, version, "confirm_observation", "review-click", "changed comment").status_code == 409
    assert review(client, sid, r1, version, "request_context").status_code == 200
    after = client.get("/workbench/tasks/" + sid, headers=owner["headers"]).json()
    assert after["jobs"][0]["result"] == snapshot["jobs"][0]["result"]
    assert len(after["reviews"]) == 2
    refs = after["reviews"][0]["references"][0]
    assert refs["case_version"] == version and refs["input_hash"] == snapshot["windows"][0]["input_hash"]
    assert refs["job_id"] == snapshot["jobs"][0]["id"] and len(refs["validated_result_sha256"]) == 64
    assert assign(client, sid, r2).json()["review_status"] == "pending"
    assert client.get("/workbench/tasks/" + sid, headers=r1["headers"]).status_code == 404
    assert client.get("/workbench/tasks/" + sid, headers=r2["headers"]).json()["review_status"] == "pending"
    assert client.patch("/workbench/tasks/" + sid + "/review", headers=r2["headers"], json={}).status_code == 405


def test_failed_or_undispatched_model_cannot_be_confirmed(running):
    app, client, _ = running
    owner, reviewer = actor(client, "failure-owner", "analyst"), actor(client, "failure-reviewer", "reviewer")
    sid = submit(client, owner).json()["submission_id"]
    assign(client, sid, reviewer)
    snapshot = complete(app, client, sid, invalid=True)
    denied = review(client, sid, reviewer, snapshot["case_version"], "confirm_observation")
    assert denied.status_code == 409 and denied.json()["detail"] == "cannot_confirm_incomplete_model_coverage"
    assert review(client, sid, reviewer, snapshot["case_version"], "request_context").status_code == 200
    other = submit(client, owner, key="dispatch-failed").json()["submission_id"]
    assign(client, other, reviewer)
    app.state.engine.prepare_reserved()
    with app.state.db.lock, app.state.db.db:
        app.state.db.db.execute("UPDATE dispatch_outbox SET state='rejected',error_code='test_failed' WHERE submission_id=?", (other,))
    rejected = client.get("/workbench/tasks/" + other, headers=BACKEND).json()
    assert review(client, other, reviewer, rejected["case_version"], "confirm_observation").status_code == 409
    assert review(client, other, reviewer, rejected["case_version"], "request_context").status_code == 200


def test_auditor_scope_and_safe_append_only_audit(running):
    app, client, _ = running
    owner, outsider = actor(client, "audit-owner", "analyst"), actor(client, "audit-outsider", "analyst")
    auditor = actor(client, "assigned-auditor", "auditor")
    sid, other = submit(client, owner).json()["submission_id"], submit(client, outsider).json()["submission_id"]
    assign(client, sid, auditor=auditor)
    assert client.get("/workbench/tasks/" + sid + "/report", headers=auditor["headers"]).status_code == 200
    assert client.get("/workbench/tasks/" + other + "/report", headers=auditor["headers"]).status_code == 404
    client.post("/workbench/auth/login", headers=BACKEND, json={"username": "unknown-login", "password": "Wrong-password-123!"})
    limited = client.get("/workbench/audit", headers=auditor["headers"]).json()["audit"]
    assert limited and all(row["submission_id"] == sid for row in limited)
    assert not any(row["action"] in {"login_failed", "user_created"} for row in limited)
    global_rows = client.get("/workbench/audit", headers=BACKEND).json()["audit"]
    assert any(row["action"] == "login_failed" for row in global_rows)
    packed = json.dumps(global_rows)
    assert "Wrong-password-123!" not in packed and "测试材料正文" not in packed
    assert auditor["headers"]["X-Workbench-Session"] not in packed
    assign(client, sid)
    assert client.get("/workbench/tasks/" + sid, headers=auditor["headers"]).status_code == 404
    assert client.get("/workbench/audit", headers=auditor["headers"]).json()["audit"] == []


def test_password_hash_salt_session_storage_and_persistent_login_limiter(running):
    app, client, settings = running
    person = actor(client, "limiter-user", "analyst")
    rows = app.state.auth.read("SELECT password_salt,password_hash FROM users")
    assert len({row["password_salt"] for row in rows}) == len(rows)
    assert all(len(row["password_hash"]) == 32 for row in rows)
    sessions = app.state.auth.read("SELECT token_hash FROM sessions")
    assert person["headers"]["X-Workbench-Session"] not in json.dumps(sessions)
    for _ in range(5):
        wrong = client.post("/workbench/auth/login", headers=BACKEND, json={"username": "limiter-user", "password": "Wrong-password-123!"})
        assert wrong.status_code == 401 and wrong.json()["detail"] == "invalid_credentials"
    restarted = AuthDB(settings.data_dir / "auth.sqlite3", "ignored-bootstrap-value")
    try:
        with pytest.raises(AuthError) as error:
            restarted.login("LIMITER-USER", "Current-limiter-user-456!")
        assert error.value.status == 401 and error.value.detail == "invalid_credentials"
        assert restarted.read("SELECT blocked_until FROM login_attempts WHERE username='limiter-user'")[0]["blocked_until"] > time.time()
    finally:
        restarted.close()
