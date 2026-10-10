import asyncio
import csv
import hashlib
import io
import json

from fastapi.testclient import TestClient
import httpx
import pytest

from app import create_app
from orchestration import public_job
from settings import Settings
from service.schema import MODEL_OUTPUT_SCHEMA
from service.worker import AnalysisWorker


BACKEND = {"Authorization": "Bearer test-workbench-key"}
INTEGRATION = {"Authorization": "Bearer test-integration-key"}


def test_public_provenance_keeps_protocol_hashes_without_content():
    raw = {"model_sha256": "a" * 64, "tokenizer_sha256": "b" * 64,
        "dynamic_schema_sha256": "C" * 64, "output_schema_sha256": "invalid-hash",
        "prompt_token_count_delta": -7, "message": {"content": "unvalidated-secret-content"}}
    safe = public_job({"id": "job-test", "result": None, "raw_output": json.dumps(raw)})
    assert safe["model_provenance"] == {"model_sha256": "a" * 64, "tokenizer_sha256": "b" * 64,
        "dynamic_schema_sha256": "c" * 64, "prompt_token_count_delta": -7}
    assert "unvalidated-secret-content" not in json.dumps(safe)


@pytest.fixture
def running(tmp_path):
    settings = Settings(tmp_path, "test-workbench-key", "test-integration-key",
        dify_key="test-dify-key", gateway_url="https://gateway.invalid:8444", gateway_token="test-gateway-key",
        enable_worker=False, enable_dispatcher=False, retry_seconds=0, bootstrap_admin_password="Bootstrap-only-123!")
    app = create_app(settings)
    with TestClient(app) as client:
        login = client.post("/workbench/auth/login", headers=BACKEND, json={"username": "admin", "password": "Bootstrap-only-123!"}).json()
        changed = client.post("/workbench/auth/password", headers={**BACKEND, "X-Workbench-Session": login["session_token"]},
            json={"current_password": "Bootstrap-only-123!", "new_password": "Admin-current-456!"})
        assert changed.status_code == 200
        client.headers["X-Workbench-Session"] = changed.json()["session_token"]
        yield app, client, settings


def analyze(client, text="请就事论事。", key="click-1", filename="material.txt"):
    return client.post("/workbench/analyze", headers=BACKEND, json={"case_name": "test-material", "filename": filename,
        "content": text, "idempotency_key": key})


def prepare(app, client, **kwargs):
    response = analyze(client, **kwargs)
    assert response.status_code == 202
    sid = response.json()["submission_id"]
    app.state.engine.prepare_reserved()
    return sid


def healthy_dify(app, mutate=None):
    def handle(request):
        assert str(request.url) == "http://127.0.0.1/v1/workflows/run"
        payload = json.loads(request.content)
        inputs = payload["inputs"]
        assert payload["response_mode"] == "blocking"
        assert "text" not in inputs and "messages" not in inputs
        saved = app.state.store.enqueue(inputs["window_id"], inputs["pipeline_version"], inputs["idempotency_key"], inputs["input_hash"], inputs["case_version"])
        ack = {"job_id": saved["id"], **{name: saved[name] for name in ("window_id", "case_id", "case_version", "input_hash", "pipeline_version", "status")},
               "error_code": None, "review_ref": None}
        if mutate:
            mutate(ack)
        return httpx.Response(200, json={"workflow_run_id": "run-test", "data": {"status": "succeeded",
            "outputs": {"http_status": 202, "result": json.dumps(ack)}}})
    app.state.engine.client = httpx.AsyncClient(transport=httpx.MockTransport(handle))


def dispatch(app, client):
    return client.portal.call(app.state.engine.dispatch_once)


def test_auth_boundaries_do_not_expose_management(running):
    _, client, _ = running
    assert client.get("/workbench/health").status_code == 403
    assert client.get("/workbench/health", headers=INTEGRATION).status_code == 403
    assert client.get("/integration/health", headers=BACKEND).status_code == 403
    assert client.get("/integration/health", headers=INTEGRATION).status_code == 200
    assert client.get("/internal/api/cases", headers=BACKEND).status_code == 404
    assert client.get("/api/cases", headers=BACKEND).status_code == 404


def test_double_click_uses_one_submission_and_one_import(running):
    app, client, _ = running
    first, second = analyze(client), analyze(client)
    assert first.json()["submission_id"] == second.json()["submission_id"]
    assert second.json()["deduplicated"] is True
    app.state.engine.prepare_reserved()
    app.state.engine.prepare_reserved()
    assert len(app.state.store.cases()) == 1
    assert len(app.state.store.read("SELECT * FROM imports")) == 1
    assert len(app.state.db.read("SELECT * FROM dispatch_outbox")) == 1
    conflict = analyze(client, text="changed")
    assert conflict.status_code == 409


def test_recovery_after_import_before_outbox_commit_reuses_source(running):
    app, client, _ = running
    sid = prepare(app, client)
    original_window = app.state.engine.task_snapshot(sid, app.state.worker)["windows"][0]
    with app.state.db.lock, app.state.db.db:
        app.state.db.db.execute("UPDATE submissions SET state='reserved' WHERE id=?", (sid,))
        app.state.db.db.execute("DELETE FROM dispatch_outbox WHERE submission_id=?", (sid,))
    app.state.engine.prepare_reserved()
    recovered_window = app.state.engine.task_snapshot(sid, app.state.worker)["windows"][0]
    assert original_window["id"] == recovered_window["id"]
    assert original_window["input_hash"] == recovered_window["input_hash"]
    assert len(app.state.store.read("SELECT * FROM imports")) == 1


def test_txt_keeps_unicode_whitespace_unknowns_and_json_content_type(running):
    app, client, _ = running
    text = "  <script>alert('x')</script>\n引述💬保持原样  "
    sid = prepare(app, client, text=text)
    response = client.get("/workbench/tasks/" + sid, headers=BACKEND)
    assert response.headers["content-type"].startswith("application/json")
    assert response.headers["x-content-type-options"] == "nosniff"
    snapshot = response.json()
    message = snapshot["windows"][0]["messages"][0]
    assert message["text"] == text
    assert message["author_key"] is message["reply_to"] is message["timestamp"] is message["relative_time_seconds"] is None
    assert snapshot["case"]["original_source_sha256"] == hashlib.sha256(text.encode()).hexdigest()
    assert snapshot["human_gold"] is snapshot["legal_determination"] is None


def test_dify_ack_then_real_backend_success_controls_status(running):
    app, client, _ = running
    sid = prepare(app, client)
    healthy_dify(app)
    assert dispatch(app, client) is True
    snapshot = client.get("/workbench/tasks/" + sid, headers=BACKEND).json()
    assert snapshot["status"] == "running"
    assert snapshot["coverage"]["submitted_windows"] == 1
    assert snapshot["coverage"]["completed_windows"] == 0
    job = app.state.store.claim("test-worker", 60)
    message = snapshot["windows"][0]["messages"][0]
    result = {"decision": "not_supported", "observations": ["normal_criticism"], "evidence": [], "target_actor_key": None,
        "rationale": "仅描述当前文字。", "context_flags": [], "missing_info": []}
    app.state.store.finish(job["id"], "test-worker", "succeeded", result, '{"message":{"content":"UNVALIDATED-HIDDEN"}}', None)
    final = client.get("/workbench/tasks/" + sid, headers=BACKEND).json()
    assert final["status"] == "completed" and final["coverage"]["all_windows_model_completed"]
    assert final["jobs"][0]["result"] == result
    report = client.get("/workbench/tasks/" + sid + "/report", headers=BACKEND)
    assert "UNVALIDATED-HIDDEN" not in report.text
    assert "raw_output" not in report.text
    assert report.json()["human_gold"] is report.json()["legal_determination"] is None


@pytest.mark.parametrize("field,value", [("input_hash", "0" * 64), ("case_version", 9), ("window_id", "wrong-window"), ("pipeline_version", "wrong-pipeline"), ("case_id", "wrong-case")])
def test_mixed_references_never_acknowledged(running, field, value):
    app, client, _ = running
    sid = prepare(app, client)
    healthy_dify(app, lambda ack: ack.update({field: value}))
    dispatch(app, client)
    snapshot = client.get("/workbench/tasks/" + sid, headers=BACKEND).json()
    assert snapshot["status"] == "dispatch_failed"
    assert snapshot["coverage"]["submitted_windows"] == 0
    assert snapshot["dify_runs"][0]["state"] == "rejected"
    assert snapshot["errors"][0]["code"] == "invalid_dify_acknowledgement"


def test_transient_dify_failure_is_persisted_and_retried(running):
    app, client, _ = running
    sid = prepare(app, client)
    app.state.engine.client = httpx.AsyncClient(transport=httpx.MockTransport(lambda req: httpx.Response(503)))
    dispatch(app, client)
    first = client.get("/workbench/tasks/" + sid, headers=BACKEND).json()
    assert first["dispatch"][0]["state"] == "pending_retry"
    healthy_dify(app)
    dispatch(app, client)
    final = client.get("/workbench/tasks/" + sid, headers=BACKEND).json()
    assert final["dispatch"][0]["state"] == "acked"
    assert len(final["dify_runs"]) == 2
    assert len(app.state.store.jobs()) == 1


def test_interrupted_delivery_recovery_and_explicit_retry(running):
    app, client, settings = running
    sid = prepare(app, client)
    with app.state.db.lock, app.state.db.db:
        app.state.db.db.execute("UPDATE dispatch_outbox SET state='dispatching',attempts=3")
    app.state.db.recover(settings)
    snapshot = client.get("/workbench/tasks/" + sid, headers=BACKEND).json()
    assert snapshot["dispatch"][0]["state"] == "exhausted"
    assert snapshot["errors"][0]["code"] == "interrupted_delivery_unconfirmed"
    assert client.post("/workbench/tasks/" + sid + "/retry", headers=BACKEND).json()["retry_windows"] == 1
    healthy_dify(app)
    dispatch(app, client)
    assert len(app.state.store.jobs()) == 1


def test_separate_events_submit_in_source_order_without_merging(running):
    app, client, _ = running
    events = [{"case_id": "original-" + str(i), "messages": [{"message_id": "m", "text": str(i), "author_key": None,
        "reply_to": None, "timestamp": None}]} for i in range(12)]
    sid = prepare(app, client, text=json.dumps(events), filename="events.json")
    healthy_dify(app)
    for _ in events:
        dispatch(app, client)
    snapshot = client.get("/workbench/tasks/" + sid, headers=BACKEND).json()
    assert [window["messages"][0]["text"] for window in snapshot["windows"]] == [str(i) for i in range(12)]
    assert [window["source_unit_ref"] for window in snapshot["windows"]] == ["original-" + str(i) for i in range(12)]
    assert [run["window_id"] for run in snapshot["dify_runs"]] == [window["id"] for window in snapshot["windows"]]
    assert all(len(window["messages"]) == 1 for window in snapshot["windows"])


def test_limits_reject_without_truncating_or_importing(running):
    app, client, _ = running
    assert analyze(client, text="x" * (1024 * 1024 + 1)).status_code == 400
    records = [{"messages": [{"text": "x"}]} for _ in range(101)]
    assert analyze(client, text=json.dumps(records), filename="events.json").status_code == 400
    assert not app.state.store.cases()
    assert not app.state.db.read("SELECT * FROM submissions")


def test_csv_preserves_known_relative_time_and_reply_chain(running):
    app, client, _ = running
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=["conversation_id", "case_id", "message_id", "text", "author_key", "reply_to", "relative_time_seconds"])
    writer.writeheader()
    writer.writerow({"conversation_id": "conversation-1", "case_id": "source-event", "message_id": "a", "text": "请停止。", "author_key": "A", "relative_time_seconds": "0"})
    writer.writerow({"conversation_id": "conversation-1", "case_id": "source-event", "message_id": "b", "text": "继续回复。", "author_key": "B", "reply_to": "a", "relative_time_seconds": "60"})
    sid = prepare(app, client, text=buffer.getvalue(), filename="messages.csv")
    window = app.state.engine.task_snapshot(sid, app.state.worker)["windows"][0]
    a, b = window["messages"]
    assert a["relative_time_seconds"] == 0 and b["relative_time_seconds"] == 60
    assert b["reply_to"] == a["message_id"] and a["timestamp"] is b["timestamp"] is None


def test_failed_model_does_not_become_normal_decision(running):
    app, client, _ = running
    sid = prepare(app, client)
    healthy_dify(app)
    dispatch(app, client)
    job = app.state.store.claim("test-worker", 60)
    app.state.store.finish(job["id"], "test-worker", "invalid_output", None, "hidden bad output", "quote_mismatch")
    snapshot = client.get("/workbench/tasks/" + sid, headers=BACKEND).json()
    assert snapshot["status"] == "completed_with_errors"
    assert snapshot["jobs"][0]["result"] is None
    assert snapshot["coverage"]["model_coverage"] == 0
    assert "hidden bad output" not in json.dumps(snapshot)


def mock_gateway(monkeypatch, envelope=None, code=200):
    original_client = httpx.AsyncClient
    captured = []
    def handle(request):
        payload = json.loads(request.content)
        captured.append(payload)
        assert str(request.url) == "https://gateway.invalid:8444/infer"
        assert request.headers["Authorization"] == "Bearer test-gateway-key"
        assert payload["output_schema"] == MODEL_OUTPUT_SCHEMA
        assert payload["max_prompt_tokens"] == 1536 and payload["max_output_tokens"] == 512 and payload["token_budget"] == 2048
        return httpx.Response(code, json=envelope or {"error": "input_budget_exceeded"})
    monkeypatch.setattr("service.worker.httpx.AsyncClient", lambda **kwargs: original_client(transport=httpx.MockTransport(handle), timeout=kwargs["timeout"]))
    return captured


def response_envelope(result):
    return {"model": "cb-qwen3-4b", "message": {"role": "assistant", "content": json.dumps(result)},
        "done": True, "done_reason": "stop", "prompt_eval_count": 100, "preflight_prompt_tokens": 100}


def test_gateway_validates_quote_and_converts_unicode_span(running, monkeypatch):
    app, client, _ = running
    sid = prepare(app, client, text="💬你真蠢")
    message = app.state.engine.task_snapshot(sid, app.state.worker)["windows"][0]["messages"][0]
    answer = {"decision": "supported", "observations": ["direct_personal_insult"],
        "evidence": [{"message_id": message["message_id"], "quote": "你真蠢", "occurrence": 1}],
        "target_actor_key": None, "rationale": "直接辱骂。", "context_flags": [], "missing_info": []}
    captured = mock_gateway(monkeypatch, response_envelope(answer))
    window = app.state.engine.task_snapshot(sid, app.state.worker)["windows"][0]
    state, result, _, error = asyncio.run(app.state.worker.infer(window))
    assert state == "succeeded" and error is None
    assert result["evidence"][0]["start"] == 1 and result["evidence"][0]["end"] == 4
    assert json.loads(captured[0]["messages"][1]["content"])["messages"][0]["author_key"] is None


@pytest.mark.parametrize("change,expected", [
    ({"done_reason": "length"}, "truncated_output"),
    ({"model": "wrong-model"}, "invalid_output"),
    ({"prompt_eval_count": 1537}, "context_required"),
    ({"preflight_prompt_tokens": None}, "context_required"),
    ({"done": False}, "invalid_output"),
])
def test_gateway_rejects_incomplete_wrong_model_or_budget(running, monkeypatch, change, expected):
    app, client, _ = running
    sid = prepare(app, client)
    answer = {"decision": "not_supported", "observations": ["normal_criticism"], "evidence": [],
        "target_actor_key": None, "rationale": "正常表达。", "context_flags": [], "missing_info": []}
    envelope = response_envelope(answer)
    envelope.update(change)
    mock_gateway(monkeypatch, envelope)
    window = app.state.engine.task_snapshot(sid, app.state.worker)["windows"][0]
    state, result, _, error = asyncio.run(app.state.worker.infer(window))
    assert state == expected and result is None and error


def test_gateway_422_and_fabricated_quote_do_not_produce_decision(running, monkeypatch):
    app, client, _ = running
    sid = prepare(app, client)
    window = app.state.engine.task_snapshot(sid, app.state.worker)["windows"][0]
    mock_gateway(monkeypatch, code=422)
    assert asyncio.run(app.state.worker.infer(window))[0] == "context_required"
    monkeypatch.undo()
    answer = {"decision": "supported", "observations": ["direct_personal_insult"],
        "evidence": [{"message_id": window["messages"][0]["message_id"], "quote": "invented", "occurrence": 1}],
        "target_actor_key": None, "rationale": "invalid", "context_flags": [], "missing_info": []}
    mock_gateway(monkeypatch, response_envelope(answer))
    state, result, _, error = asyncio.run(app.state.worker.infer(window))
    assert state == "invalid_output" and result is None and error == "quote_mismatch"


@pytest.mark.parametrize("variant,expected_state,expected_error", [
    ("string_null", "invalid_output", "unknown_target"),
    ("attack_without_evidence", "invalid_output", "supported_without_evidence"),
    ("attack_declared_safe", "inconsistent_output", "inconsistent_output"),
])
def test_gateway_preserves_null_evidence_and_attack_consistency(running, monkeypatch, variant, expected_state, expected_error):
    app, client, _ = running
    sid = prepare(app, client, text="对方说‘蠢货’，我想咨询怎么投诉。")
    window = app.state.engine.task_snapshot(sid, app.state.worker)["windows"][0]
    answer = {"decision": "not_supported", "observations": ["quoted_insult", "help_or_report", "reported_attack_unverified"],
        "evidence": [], "target_actor_key": None, "rationale": "当前发言人引用他人表达以求助。",
        "context_flags": [], "missing_info": []}
    if variant == "string_null":
        answer["target_actor_key"] = "null"
    elif variant == "attack_without_evidence":
        answer.update({"decision": "supported", "observations": ["direct_personal_insult"]})
    else:
        answer["observations"].append("direct_personal_insult")
        answer["evidence"] = [{"message_id": window["messages"][0]["message_id"], "quote": "蠢货", "occurrence": 1}]
    mock_gateway(monkeypatch, response_envelope(answer))
    state, result, _, error = asyncio.run(app.state.worker.infer(window))
    assert state == expected_state and result is None and error == expected_error
