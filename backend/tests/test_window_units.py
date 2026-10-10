import json

from test_workbench import BACKEND, prepare, running


def events(prefix, counts):
    return [{"case_id": f"{prefix}-{number}", "messages": [
        {"message_id": f"message-{position}", "text": f"{prefix}/{number}/{position}"}
        for position in range(count)]} for number, count in enumerate(counts)]


def record_unit_queries(monkeypatch, store):
    reads, original = [], store.read
    def read(sql, args=()):
        if "FROM source_units" in sql:
            reads.append((sql, tuple(args)))
        return original(sql, args)
    monkeypatch.setattr(store, "read", read)
    return reads


def test_detail_reads_only_its_manifest_units_once_and_keeps_source_order(running, monkeypatch):
    app, client, _ = running
    own_events = events("own-event", [14, 1, 3])
    sid = prepare(app, client, text=json.dumps(own_events), key="own-events", filename="own.json")
    other = prepare(app, client, text=json.dumps(events("other-event", [1, 1, 1, 1])), key="other-events", filename="other.json")
    case_id = app.state.db.one(sid)["case_id"]
    other_case_id = app.state.db.one(other)["case_id"]
    manifests = app.state.store.manifests(case_id)
    expected_units = {window["unit_id"] for window in manifests}
    other_units = {window["unit_id"] for window in app.state.store.manifests(other_case_id)}
    assert expected_units.isdisjoint(other_units)
    reads = record_unit_queries(monkeypatch, app.state.store)
    snapshot = client.get("/workbench/tasks/" + sid, headers=BACKEND).json()
    assert len(reads) == 1
    assert "WHERE id IN (" in reads[0][0] and set(reads[0][1]) == expected_units
    assert len(reads[0][1]) == len(expected_units)  # Multiple windows from one unit read it once.
    source_order = [(window["source_record_number"], window["ordinal"]) for window in snapshot["windows"]]
    assert source_order == sorted(source_order)
    assert all(window["source_unit_ref"] == f"own-event-{window['source_record_number'] - 1}" for window in snapshot["windows"])
    assert all(message["text"].startswith("own-event/") for window in snapshot["windows"] for message in window["messages"])
    reads.clear()
    ordered = app.state.engine.ordered_windows(case_id)
    assert [window["id"] for window in ordered] == [window["id"] for window in snapshot["windows"]]
    assert len(reads) == 1 and set(reads[0][1]) == expected_units


def test_task_list_never_reads_global_source_units(running, monkeypatch):
    app, client, _ = running
    ids = [prepare(app, client, text=json.dumps(events(f"list-{i}", [1, 2])), key=f"list-{i}", filename="events.json") for i in range(3)]
    expected = [{window["unit_id"] for window in app.state.store.manifests(app.state.db.one(sid)["case_id"])} for sid in ids]
    unrelated_case = app.state.store.import_bytes(json.dumps(events("unrelated", [1] * 7)).encode(), "unrelated.json", "unrelated")["case_id"]
    unrelated = {window["unit_id"] for window in app.state.store.manifests(unrelated_case)}
    reads = record_unit_queries(monkeypatch, app.state.store)
    response = client.get("/workbench/tasks", headers=BACKEND, params={"limit": 30})
    assert response.status_code == 200 and len(response.json()["tasks"]) == 3
    assert len(reads) == 3
    assert {frozenset(args) for _, args in reads} == {frozenset(units) for units in expected}
    assert all("WHERE id IN (" in sql and unrelated.isdisjoint(args) for sql, args in reads)


def test_empty_manifests_do_not_query_source_units(running, monkeypatch):
    app, _, _ = running
    monkeypatch.setattr(app.state.store, "manifests", lambda case_id: [])
    reads = record_unit_queries(monkeypatch, app.state.store)
    assert app.state.engine._ordered_windows_with_units("empty-case") == ([], {})
    assert app.state.engine.ordered_windows("empty-case") == []
    assert reads == []
