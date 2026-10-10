import hashlib

import pytest

from pagination import PageCursor
from test_multiuser import actor, assign, submit
from test_workbench import BACKEND, running


def reserve_many(app, settings, owner, count, prefix, timestamp=1000):
    ids = [app.state.db.reserve(prefix, "source.txt", "测试材料。", f"{prefix}-{i}", settings, owner)[0] for i in range(count)]
    with app.state.db.lock, app.state.db.db:
        app.state.db.db.executemany("UPDATE submissions SET created_at=? WHERE id=?", [(timestamp, sid) for sid in ids])
    return ids


def assign_many(app, ids, reviewer=None, auditor=None):
    with app.state.auth.lock, app.state.auth.db:
        app.state.auth.db.executemany("INSERT INTO assignments VALUES(?,?,?,?)", [(sid, reviewer, auditor, 1000) for sid in ids])


def remaining_pages(client, path, headers, first, array, limit=30):
    rows, cursor = list(first[array]), first["next_cursor"]
    seen = set()
    while cursor is not None:
        assert cursor not in seen
        seen.add(cursor)
        response = client.get(path, headers=headers, params={"limit": limit, "cursor": cursor})
        assert response.status_code == 200
        page = response.json()
        rows.extend(page[array])
        cursor = page["next_cursor"]
    return rows


def test_session_context_is_condition_and_rejects_changes_before_side_effects(running):
    app, client, _ = running
    a = actor(client, "context-a", "analyst")
    me = client.get("/workbench/auth/me", headers=a["headers"]).json()
    old_token = a["headers"]["X-Workbench-Session"]
    expected = hashlib.sha256(b"workbench-context-v1\0" + old_token.encode()).hexdigest()
    assert me["session_context"] == expected
    assert expected != hashlib.sha256(old_token.encode()).hexdigest()
    another = client.post("/workbench/auth/login", headers=BACKEND,
        json={"username": "context-a", "password": "Current-context-a-456!"}).json()
    assert another["session_context"] != expected
    headers = {**BACKEND, "X-Workbench-Session": another["session_token"], "X-Workbench-Expected-Context": expected}
    before_audit = len(app.state.auth.read("SELECT seq FROM audit"))
    before_users = app.state.auth.read("SELECT * FROM users")
    assert submit(client, {"headers": headers}).json() == {"detail": "session_context_changed"}
    for path, body in (("/workbench/auth/logout", None), ("/workbench/auth/password",
            {"current_password": "Current-context-a-456!", "new_password": "Should-not-change-123!"})):
        denied = client.post(path, headers=headers, json=body)
        assert denied.status_code == 409 and denied.json()["detail"] == "session_context_changed"
    assert app.state.db.read("SELECT id FROM submissions") == []
    assert app.state.auth.read("SELECT * FROM users") == before_users
    assert len(app.state.auth.read("SELECT seq FROM audit")) == before_audit
    assert client.get("/workbench/auth/me", headers={**BACKEND, "X-Workbench-Session": another["session_token"]}).status_code == 200
    for malformed in ("", "A" * 64, "z" * 64, "0" * 63, "0" * 65):
        denied = client.get("/workbench/auth/me", headers={**a["headers"], "X-Workbench-Expected-Context": malformed})
        assert denied.status_code == 409
    fake = client.get("/workbench/auth/me", headers={**headers, "X-Workbench-Session": "not-a-session"})
    assert fake.status_code == 401  # A public context alone cannot authenticate.
    good = {**a["headers"], "X-Workbench-Expected-Context": expected}
    assert client.get("/workbench/health", headers=good).status_code == 200
    changed = client.post("/workbench/auth/password", headers=good,
        json={"current_password": "Current-context-a-456!", "new_password": "Changed-context-a-123!"}).json()
    assert changed["session_context"] == hashlib.sha256(b"workbench-context-v1\0" + changed["session_token"].encode()).hexdigest()
    assert changed["session_context"] != expected


def test_reviewer_pages_more_than_100_without_assignment_cap_or_acl_gaps(running):
    app, client, settings = running
    owner = actor(client, "paged-owner", "analyst")
    outsider = actor(client, "paged-outsider", "analyst")
    reviewer = actor(client, "paged-reviewer", "reviewer")
    ids = reserve_many(app, settings, owner["user"]["id"], 137, "assigned")
    reserve_many(app, settings, outsider["user"]["id"], 179, "unassigned", 2000)
    legacy = reserve_many(app, settings, None, 1, "legacy", 3000)
    assign_many(app, ids + legacy, reviewer=reviewer["user"]["id"])
    first = client.get("/workbench/tasks", headers=reviewer["headers"]).json()
    assert len(first["tasks"]) == 30 and first["next_cursor"]
    rows = remaining_pages(client, "/workbench/tasks", reviewer["headers"], first, "tasks", limit=41)
    actual = [row["submission_id"] for row in rows]
    assert actual == sorted(ids, reverse=True)
    assert len(actual) == len(set(actual)) == 137
    analyst_first = client.get("/workbench/tasks", headers=owner["headers"], params={"limit": 100}).json()
    analyst_rows = remaining_pages(client, "/workbench/tasks", owner["headers"], analyst_first, "tasks", limit=100)
    assert {row["submission_id"] for row in analyst_rows} == set(ids)
    empty = actor(client, "empty-reviewer", "reviewer")
    assert client.get("/workbench/tasks", headers=empty["headers"]).json() == {"tasks": [], "next_cursor": None}


def test_task_cursor_same_time_order_excludes_new_and_backdated_insertions(running):
    app, client, settings = running
    owner = actor(client, "stable-owner", "analyst")
    initial = reserve_many(app, settings, owner["user"]["id"], 47, "initial")
    first = client.get("/workbench/tasks", headers=owner["headers"], params={"limit": 11}).json()
    reserve_many(app, settings, owner["user"]["id"], 7, "newer", 2000)
    reserve_many(app, settings, owner["user"]["id"], 7, "same", 1000)
    reserve_many(app, settings, owner["user"]["id"], 7, "backdated", 1)
    rows = remaining_pages(client, "/workbench/tasks", owner["headers"], first, "tasks", limit=11)
    assert [row["submission_id"] for row in rows] == sorted(initial, reverse=True)
    refreshed = client.get("/workbench/tasks", headers=owner["headers"], params={"limit": 100}).json()
    assert len(refreshed["tasks"]) == 68 and refreshed["next_cursor"] is None


def test_task_cursor_rechecks_current_assignments_on_each_page(running):
    app, client, settings = running
    owner, reviewer = actor(client, "revoked-owner", "analyst"), actor(client, "revoked-reviewer", "reviewer")
    ids = reserve_many(app, settings, owner["user"]["id"], 8, "revoked")
    assign_many(app, ids, reviewer=reviewer["user"]["id"])
    first = client.get("/workbench/tasks", headers=reviewer["headers"], params={"limit": 2}).json()
    with app.state.auth.lock, app.state.auth.db:
        app.state.auth.db.execute("UPDATE assignments SET reviewer_id=NULL")
    next_page = client.get("/workbench/tasks", headers=reviewer["headers"], params={"cursor": first["next_cursor"]})
    assert next_page.json() == {"tasks": [], "next_cursor": None}


def test_task_cursor_uses_new_assignment_but_preserves_insertion_high_water(running):
    app, client, settings = running
    owner, reviewer = actor(client, "dynamic-owner", "analyst"), actor(client, "dynamic-reviewer", "reviewer")
    initial = reserve_many(app, settings, owner["user"]["id"], 6, "dynamic-initial")
    existing_unassigned = reserve_many(app, settings, owner["user"]["id"], 2, "dynamic-existing", 500)
    assign_many(app, initial, reviewer=reviewer["user"]["id"])
    first = client.get("/workbench/tasks", headers=reviewer["headers"], params={"limit": 2}).json()
    late = reserve_many(app, settings, owner["user"]["id"], 1, "dynamic-late", 1)
    assign_many(app, existing_unassigned + late, reviewer=reviewer["user"]["id"])
    rows = remaining_pages(client, "/workbench/tasks", reviewer["headers"], first, "tasks", limit=2)
    assert [row["submission_id"] for row in rows] == sorted(initial, reverse=True) + sorted(existing_unassigned, reverse=True)


def test_audit_pages_high_water_and_current_authorization(running):
    app, client, settings = running
    owner, auditor = actor(client, "events-owner", "analyst"), actor(client, "events-auditor", "auditor")
    sid = reserve_many(app, settings, owner["user"]["id"], 1, "events")[0]
    other = reserve_many(app, settings, owner["user"]["id"], 1, "other-events")[0]
    assign_many(app, [sid], auditor=auditor["user"]["id"])
    for i in range(143):
        app.state.auth.audit(owner["user"]["id"], "test_event", sid, {"position": i})
        app.state.auth.audit(owner["user"]["id"], "other_event", other, {"position": i})
    expected = [row["seq"] for row in app.state.auth.read("SELECT seq FROM audit WHERE submission_id=? ORDER BY seq DESC", (sid,))]
    first = client.get("/workbench/audit", headers=auditor["headers"], params={"limit": 19}).json()
    app.state.auth.audit(owner["user"]["id"], "late_event", sid)
    rows = remaining_pages(client, "/workbench/audit", auditor["headers"], first, "audit", limit=29)
    assert [row["seq"] for row in rows] == expected
    assert all(row["submission_id"] == sid for row in rows)
    # The old cursor cannot retain access after admin cancels assignment.
    assert assign(client, sid).status_code == 200
    revoked = client.get("/workbench/audit", headers=auditor["headers"], params={"cursor": first["next_cursor"]})
    assert revoked.json() == {"audit": [], "next_cursor": None}
    global_first = client.get("/workbench/audit", headers=BACKEND, params={"limit": 100}).json()
    global_expected = [row["seq"] for row in app.state.auth.read("SELECT seq FROM audit WHERE seq<=? ORDER BY seq DESC", (global_first["audit"][0]["seq"],))]
    app.state.auth.audit(None, "new_global_event")
    global_rows = remaining_pages(client, "/workbench/audit", BACKEND, global_first, "audit", limit=100)
    assert [row["seq"] for row in global_rows] == global_expected


def test_cursor_tampering_and_scope_and_limit_validation(running):
    app, client, settings = running
    owner, other = actor(client, "cursor-owner", "analyst"), actor(client, "cursor-other", "analyst")
    reserve_many(app, settings, owner["user"]["id"], 3, "cursor")
    cursor = client.get("/workbench/tasks", headers=owner["headers"], params={"limit": 1}).json()["next_cursor"]
    for value in ("", "bad", "x" * 2049, cursor[:-1] + ("0" if cursor[-1] != "0" else "1")):
        response = client.get("/workbench/tasks", headers=owner["headers"], params={"cursor": value})
        assert response.status_code == 400 and response.json()["detail"] == "invalid_cursor"
    cross_user = client.get("/workbench/tasks", headers=other["headers"], params={"cursor": cursor})
    assert cross_user.status_code == 400 and cross_user.json()["detail"] == "invalid_cursor"
    admin_cursor = client.get("/workbench/tasks", headers=BACKEND, params={"limit": 1}).json()["next_cursor"]
    cross_type = client.get("/workbench/audit", headers=BACKEND, params={"cursor": admin_cursor})
    assert cross_type.status_code == 400 and cross_type.json()["detail"] == "invalid_cursor"
    cursor_codec = PageCursor(settings.backend_token)
    with pytest.raises(ValueError, match="invalid_cursor"):
        cursor_codec.decode(cursor, "tasks", {**owner["user"], "role": "reviewer"})
    assert client.patch("/workbench/users/" + owner["user"]["id"], headers=BACKEND, json={"role": "auditor"}).status_code == 200
    rotated = client.post("/workbench/auth/login", headers=BACKEND,
        json={"username": "cursor-owner", "password": "Current-cursor-owner-456!"}).json()
    new_role = client.get("/workbench/tasks", headers={**BACKEND, "X-Workbench-Session": rotated["session_token"]}, params={"cursor": cursor})
    assert new_role.status_code == 400 and new_role.json()["detail"] == "invalid_cursor"
    for limit in (0, 101):
        assert client.get("/workbench/tasks", headers=other["headers"], params={"limit": limit}).status_code == 422
        assert client.get("/workbench/audit", headers=BACKEND, params={"limit": limit}).status_code == 422


@pytest.mark.parametrize("endpoint,high_water,after", [
    ("tasks", True, [1, "id"]), ("tasks", -1, [1, "id"]), ("tasks", 2**63, [1, "id"]),
    ("tasks", 1, [True, "id"]), ("tasks", 1, [float("inf"), "id"]), ("tasks", 1, [1, ""]),
    ("audit", 3, True), ("audit", 3, 0), ("audit", 3, 4),
])
def test_signed_cursor_still_checks_position_types_and_bounds(endpoint, high_water, after):
    codec, person = PageCursor("test-key"), {"id": "test-user", "role": "admin"}
    with pytest.raises(ValueError, match="invalid_cursor"):
        codec.decode(codec.encode(endpoint, person, high_water, after), endpoint, person)
