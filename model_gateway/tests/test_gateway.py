import copy
import io
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import gateway as module
from gateway import Gateway, GatewayError, Handler, PromptTokenizer, allowed_source, authorized, parse_json, generation_schema
from schema import MODEL_OUTPUT_SCHEMA


def request_payload():
    return {"model": "cb-qwen3-4b", "messages": [{"role": "system", "content": "analyse"},
            {"role": "user", "content": json.dumps({"messages": [{"message_id": "m1", "author_key": None, "text": "sample"}]})}], "max_prompt_tokens": 3584,
            "max_output_tokens": 512, "token_budget": 4096, "output_schema": copy.deepcopy(MODEL_OUTPUT_SCHEMA)}


class FakeTokenizer:
    metadata = {"tokenizer_json_sha256": "t" * 64}
    def __init__(self, count=100):
        self.count = count
    def render_and_count(self, messages):
        return "rendered_exact_prompt", self.count


class FakeOllama:
    def __init__(self):
        self.calls = []
        self.result = {"response": '{"safe":true}', "done": True, "done_reason": "stop", "prompt_eval_count": 101, "eval_count": 20}
        self.digest = "a" * 64
        self.identity_checks = 0
    def generate(self, prompt, cap, schema):
        self.calls.append((prompt, cap))
        self.last_schema = copy.deepcopy(schema)
        return copy.deepcopy(self.result)
    def health(self):
        return {"reachable": True, "model_available": True, "model_digest": self.digest, "version": "test"}
    def model_digest(self):
        self.identity_checks += 1
        return self.digest


def new_gateway(count=100):
    return Gateway(FakeTokenizer(count), FakeOllama(), "secret" * 8, {"gguf_sha256": "m" * 64}, "a" * 64)


class GatewayTests(unittest.TestCase):
    def test_prompt_over_cap_never_calls_ollama(self):
        gateway = new_gateway(3585)
        with self.assertRaisesRegex(GatewayError, "prompt_budget_exceeded"):
            gateway.infer(request_payload())
        self.assertEqual(gateway.client.calls, [])

    def test_exact_count_boundary_allowed_and_actual_usage_preserved(self):
        gateway = new_gateway(3584)
        result = gateway.infer(request_payload())
        self.assertEqual(gateway.client.calls, [("rendered_exact_prompt", 512)])
        self.assertEqual(result["preflight_prompt_tokens"], 3584)
        self.assertEqual(result["prompt_eval_count"], 101)
        self.assertEqual(result["done_reason"], "stop")

    def test_fixed_model_allowlist(self):
        gateway = new_gateway()
        payload = request_payload()
        payload["model"] = "other-model"
        with self.assertRaisesRegex(GatewayError, "model_not_allowed"):
            gateway.infer(payload)
        self.assertEqual(gateway.client.calls, [])

    def test_schema_allowlist_cannot_be_replaced(self):
        gateway = new_gateway()
        payload = request_payload()
        payload["output_schema"]["additionalProperties"] = True
        with self.assertRaisesRegex(GatewayError, "schema_not_allowed"):
            gateway.infer(payload)
        self.assertEqual(gateway.client.calls, [])

    def test_budget_bool_and_inconsistent_caps_rejected(self):
        for field, value in (("max_prompt_tokens", True), ("max_prompt_tokens", 3585),
                             ("max_output_tokens", 513), ("token_budget", 4097), ("token_budget", 4095)):
            payload = request_payload()
            payload[field] = value
            gateway = new_gateway()
            with self.assertRaises(GatewayError):
                gateway.infer(payload)
            self.assertEqual(gateway.client.calls, [])

    def test_unknown_request_fields_and_tool_roles_rejected(self):
        payload = request_payload()
        payload["ollama_url"] = "http://example.invalid"
        with self.assertRaisesRegex(GatewayError, "invalid_request_fields"):
            new_gateway().infer(payload)
        payload = request_payload()
        payload["messages"][-1]["role"] = "tool"
        with self.assertRaisesRegex(GatewayError, "invalid_message"):
            new_gateway().infer(payload)

    def test_busy_is_429_and_no_second_model_request(self):
        gateway = new_gateway()
        gateway.inference_lock.acquire()
        try:
            with self.assertRaises(GatewayError) as error:
                gateway.infer(request_payload())
            self.assertEqual(error.exception.status, 429)
            self.assertEqual(gateway.client.calls, [])
        finally:
            gateway.inference_lock.release()

    def test_missing_upstream_usage_is_error_without_fabrication(self):
        gateway = new_gateway()
        del gateway.client.result["prompt_eval_count"]
        with self.assertRaisesRegex(GatewayError, "model_usage_missing"):
            gateway.infer(request_payload())

    def test_actual_usage_over_cap_cannot_pass_preflight(self):
        for changed in ({"prompt_eval_count": 3585}, {"eval_count": 513}, {"prompt_eval_count": 3584, "eval_count": 513}):
            gateway = new_gateway(100)
            gateway.client.result.update(changed)
            with self.assertRaisesRegex(GatewayError, "model_actual_budget_exceeded"):
                gateway.infer(request_payload())

    def test_actual_token_delta_is_metadata_only(self):
        result = new_gateway(100).infer(request_payload())
        self.assertEqual(result["prompt_token_count_delta"], 1)

    def test_truncated_done_reason_passes_to_business_validator(self):
        gateway = new_gateway()
        gateway.client.result["done_reason"] = "length"
        self.assertEqual(gateway.infer(request_payload())["done_reason"], "length")

    def test_token_is_required_only_exact_bearer(self):
        token = "secret" * 8
        self.assertTrue(authorized("Bearer " + token, token))
        for header in (None, token, "bearer " + token, "Bearer " + token + " ", "Bearer wrong"):
            self.assertFalse(authorized(header, token))
        with self.assertRaisesRegex(GatewayError, "gateway_token_missing_or_weak"):
            Gateway(FakeTokenizer(), FakeOllama(), "too-short", {}, "a" * 64)

    def test_source_and_path_allowlist(self):
        self.assertTrue(allowed_source("192.168.0.129", "/infer"))
        self.assertTrue(allowed_source("192.168.0.129", "/health"))
        self.assertTrue(allowed_source("127.0.0.1", "/health"))
        for address, path in (("127.0.0.1", "/infer"), ("192.168.0.130", "/infer"),
                              ("192.168.0.130", "/health"), ("192.168.0.129", "/api/chat"),
                              ("192.168.0.129", "/infer?token=secret")):
            self.assertFalse(allowed_source(address, path))

    def test_duplicate_keys_and_nonfinite_json_rejected(self):
        for raw in (b'{"model":"one","model":"two"}', b'{"budget":NaN}'):
            with self.assertRaises(GatewayError):
                parse_json(raw)

    def test_upstream_generate_uses_rendered_prompt_raw_schema_and_cap(self):
        client = module.OllamaClient()
        with patch.object(client, "request", return_value={}) as request:
            client.generate("already rendered", 128, generation_schema(request_payload()["messages"]))
        path, payload = request.call_args.args
        self.assertEqual(path, "/api/generate")
        self.assertEqual(payload["prompt"], "already rendered")
        self.assertTrue(payload["raw"])
        self.assertFalse(payload["think"])
        self.assertFalse(payload["stream"])
        self.assertFalse(payload["truncate"])
        self.assertFalse(payload["shift"])
        self.assertEqual(payload["format"], generation_schema(request_payload()["messages"]))
        self.assertEqual(payload["options"]["num_predict"], 128)
        self.assertEqual(payload["options"]["num_ctx"], 4096)
        self.assertEqual(module.OLLAMA_ORIGIN, "http://127.0.0.1:11434")

    def test_upstream_private_error_not_returned(self):
        error = HTTPError("http://127.0.0.1", 500, "PRIVATE INPUT", {}, io.BytesIO(b"PRIVATE INPUT"))
        with patch.object(module, "urlopen", side_effect=error):
            with self.assertRaises(GatewayError) as caught:
                module.OllamaClient().request("/api/generate", {"prompt": "PRIVATE INPUT"})
        self.assertEqual(str(caught.exception), "model_service_unavailable")

    def test_dynamic_schema_unknown_authors_allow_actual_json_null_only(self):
        schema = generation_schema(request_payload()["messages"])
        self.assertEqual(schema["properties"]["target_actor_key"]["enum"], [None])
        self.assertNotIn("null", schema["properties"]["target_actor_key"]["enum"])

    def test_dynamic_schema_uses_actual_ids_and_authors_not_unknown_values(self):
        messages = [{"role": "user", "content": json.dumps({"messages": [
            {"message_id": "known-2", "author_key": "actor-b"},
            {"message_id": "known-1", "author_key": "actor-a"},
            {"message_id": "known-3", "author_key": "actor-b"},
            {"message_id": "known-4", "author_key": None},
        ]})}]
        schema = generation_schema(messages)
        self.assertEqual(schema["properties"]["target_actor_key"]["enum"], [None, "actor-a", "actor-b"])
        self.assertEqual(schema["properties"]["evidence"]["items"]["properties"]["message_id"]["enum"],
                         ["known-1", "known-2", "known-3", "known-4"])

    def test_dynamic_schema_bounds_without_changing_decision_or_labels(self):
        schema = generation_schema(request_payload()["messages"])
        properties = schema["properties"]
        self.assertEqual(properties["decision"], MODEL_OUTPUT_SCHEMA["properties"]["decision"])
        self.assertEqual(properties["observations"]["items"], MODEL_OUTPUT_SCHEMA["properties"]["observations"]["items"])
        self.assertEqual(properties["observations"]["maxItems"], 8)
        self.assertEqual(properties["evidence"]["maxItems"], 4)
        self.assertEqual(properties["rationale"]["maxLength"], 350)
        for field in ("context_flags", "missing_info"):
            self.assertEqual(properties[field]["maxItems"], 4)
            self.assertEqual(properties[field]["items"]["maxLength"], 80)
            self.assertTrue(properties[field]["uniqueItems"])
        self.assertNotIn("maxItems", MODEL_OUTPUT_SCHEMA["properties"]["observations"])

    def test_invalid_analysis_window_never_calls_ollama(self):
        for value in ("not json", json.dumps({"messages": []}),
                      json.dumps({"messages": [{"message_id": "same"}, {"message_id": "same"}]})):
            payload = request_payload()
            payload["messages"][-1]["content"] = value
            gateway = new_gateway()
            with self.assertRaises(GatewayError):
                gateway.infer(payload)
            self.assertEqual(gateway.client.calls, [])

    def test_dynamic_schema_hash_recorded_base_hash_unchanged(self):
        gateway = new_gateway()
        result = gateway.infer(request_payload())
        expected = module.hashlib.sha256(module.canonical_json(gateway.client.last_schema).encode("utf-8")).hexdigest()
        self.assertEqual(result["dynamic_schema_sha256"], expected)
        self.assertEqual(result["output_schema_sha256"], module.SCHEMA_SHA256)
        self.assertNotEqual(result["dynamic_schema_sha256"], result["output_schema_sha256"])

    def test_alias_digest_changed_rejects_before_generate(self):
        gateway = new_gateway()
        gateway.client.digest = "b" * 64
        with self.assertRaisesRegex(GatewayError, "model_identity_mismatch") as caught:
            gateway.infer(request_payload())
        self.assertEqual(caught.exception.status, 503)
        self.assertEqual(gateway.client.calls, [])
        self.assertEqual(gateway.client.identity_checks, 1)
        self.assertFalse(gateway.inference_lock.locked())

    def test_alias_missing_rejects_before_generate(self):
        gateway = new_gateway()
        gateway.client.digest = None
        with self.assertRaisesRegex(GatewayError, "model_identity_mismatch"):
            gateway.infer(request_payload())
        self.assertEqual(gateway.client.calls, [])

    def test_alias_digest_expected_required_and_returned_on_success(self):
        for value in (None, "", "a" * 63, "A" * 64):
            with self.assertRaisesRegex(GatewayError, "expected_ollama_digest_missing_or_invalid"):
                Gateway(FakeTokenizer(), FakeOllama(), "secret" * 8, {}, value)
        result = new_gateway().infer(request_payload())
        self.assertEqual(result["ollama_model_digest"], "a" * 64)

    def test_health_reports_expected_actual_digest_and_unavailable_on_change(self):
        gateway = new_gateway()
        ready = gateway.health()
        self.assertTrue(ready["model_identity_matches"])
        self.assertEqual(ready["status"], "ready")
        self.assertEqual(ready["expected_ollama_model_digest"], "a" * 64)
        gateway.client.digest = "b" * 64
        changed = gateway.health()
        self.assertFalse(changed["model_identity_matches"])
        self.assertEqual(changed["status"], "unavailable")
        self.assertEqual(changed["actual_ollama_model_digest"], "b" * 64)


class TokenizerTests(unittest.TestCase):
    def test_configured_files_template_hash_no_automatic_truncation(self):
        from tokenizers import Tokenizer
        from tokenizers.models import WordLevel
        from tokenizers.pre_tokenizers import Whitespace
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            tokenizer = Tokenizer(WordLevel({"hello": 0, "[UNK]": 1}, unk_token="[UNK]"))
            tokenizer.pre_tokenizer = Whitespace()
            tokenizer.enable_truncation(max_length=1)
            tokenizer.save(str(path / "tokenizer.json"))
            template = "{% for message in messages %}{{ message.content }} {% endfor %}{% if enable_thinking is false %}hello{% endif %}"
            (path / "tokenizer_config.json").write_text(json.dumps({"chat_template": template}), encoding="utf-8")
            counter = PromptTokenizer(path, "pinned-test-revision")
            rendered, count = counter.render_and_count([{"role": "user", "content": "hello hello"}])
            self.assertEqual(rendered, "hello hello hello")
            self.assertEqual(count, 3)
            self.assertEqual(counter.metadata["revision"], "pinned-test-revision")
            self.assertEqual(counter.metadata["tokenizer_json_sha256"], module.sha256_file(path / "tokenizer.json"))
            self.assertFalse(counter.metadata["truncation_enabled"])


class HandlerTests(unittest.TestCase):
    def setUp(self):
        self.server = module.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.gateway = new_gateway()
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = "http://127.0.0.1:" + str(self.server.server_address[1])

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def test_localhost_infer_forbidden_in_production_handler(self):
        request = Request(self.base + "/infer", data=b"{}", headers={"Authorization": "Bearer secret"}, method="POST")
        with self.assertRaises(HTTPError) as caught:
            urlopen(request, timeout=3)
        self.assertEqual(caught.exception.code, 403)
        self.assertEqual(self.server.gateway.client.calls, [])

    def test_64_kib_max_before_parse(self):
        # Transport test emulates the authorised source only for this request.
        with patch.object(module, "allowed_source", return_value=True):
            request = Request(self.base + "/infer", data=b"x" * (module.MAX_BODY_BYTES + 1),
                              headers={"Authorization": "Bearer " + self.server.gateway.token,
                                       "Content-Type": "application/json"}, method="POST")
            with self.assertRaises(HTTPError) as caught:
                urlopen(request, timeout=3)
        self.assertEqual(caught.exception.code, 413)
        self.assertEqual(self.server.gateway.client.calls, [])

    def test_token_in_query_is_not_an_authorized_path(self):
        with self.assertRaises(HTTPError) as caught:
            urlopen(self.base + "/health?token=secret", timeout=3)
        self.assertEqual(caught.exception.code, 403)


if __name__ == "__main__":
    unittest.main()
