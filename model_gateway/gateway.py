"""Narrow TLS gateway: approved schema + exact rendered Qwen3 prompt only.

This service does not train, download, start Ollama, or store request text.
Only the configured business server can call /infer. Loopback may inspect /health.
"""

from __future__ import annotations

import hashlib
import hmac
import importlib.metadata
from dataclasses import dataclass
from ipaddress import IPv4Address
import json
import os
from pathlib import Path
import ssl
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from schema import MODEL_OUTPUT_SCHEMA

GATEWAY_VERSION = "1.2.0"
MODEL_NAME = "cb-qwen3-4b"
MODEL_GGUF_SHA256 = "7485fe6f11af29433bc51cab58009521f205840f5b4ae3a32fa7f92e8534fdf5"
TOKENIZER_REVISION = "1cfa9a7208912126459214e8b04321603b3df60c"
TOKENIZER_JSON_SHA256 = "aeb13307a71acd8fe81861d94ad54ab689df773318809eed3cbe794b4492dae4"
TOKENIZER_CONFIG_SHA256 = "d5d09f07b48c3086c508b30d1c9114bd1189145b74e982a265350c923acd8101"
OLLAMA_ORIGIN = "http://127.0.0.1:11434"
KALI_ADDRESS = "192.168.0.129"
MAX_BODY_BYTES = 64 * 1024
MAX_UPSTREAM_BYTES = 256 * 1024
MAX_CONTEXT_TOKENS = 4096
MAX_OUTPUT_TOKENS = 512
MAX_PROMPT_TOKENS = MAX_CONTEXT_TOKENS - MAX_OUTPUT_TOKENS
REQUEST_FIELDS = {"model", "messages", "max_prompt_tokens", "max_output_tokens", "token_budget", "output_schema"}


class GatewayError(Exception):
    def __init__(self, code: str, status: int = 422):
        self.code, self.status = code, status
        super().__init__(code)


def validate_address(value: str, field: str) -> None:
    try:
        if not isinstance(value, str):
            raise ValueError
        address = IPv4Address(value)
        if address.is_unspecified or address.is_multicast or value == "255.255.255.255":
            raise ValueError
    except (ValueError, TypeError) as exc:
        raise GatewayError("invalid_gateway_" + field + "_address", 503) from exc


@dataclass(frozen=True)
class NetworkConfig:
    """Load once at startup; request handling never reads mutable environment."""
    bind_address: str = "192.168.0.1"
    client_address: str = KALI_ADDRESS
    port: int = 8444

    def __post_init__(self):
        validate_address(self.bind_address, "bind")
        validate_address(self.client_address, "client")
        if type(self.port) is not int or not 1 <= self.port <= 65535:
            raise GatewayError("invalid_gateway_port", 503)

    @classmethod
    def load(cls, environ=None):
        values = os.environ if environ is None else environ
        port = values.get("CB_GATEWAY_PORT", "8444")
        if (not isinstance(port, str) or not port.isascii() or not port.isdigit()
                or len(port.lstrip("0")) > 5):
            raise GatewayError("invalid_gateway_port", 503)
        return cls(
            bind_address=values.get("CB_GATEWAY_BIND_ADDRESS", "192.168.0.1"),
            client_address=values.get("CB_GATEWAY_CLIENT_ADDRESS", KALI_ADDRESS),
            port=int(port.lstrip("0") or "0"),
        )


# Legacy defaults are static and preserve older callers/tests. Actual servers
# attach the validated, immutable startup configuration before serving requests.
DEFAULT_NETWORK_CONFIG = NetworkConfig()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def reject_duplicate_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise GatewayError("duplicate_json_key", 400)
        result[key] = value
    return result


def parse_json(raw: bytes):
    try:
        return json.loads(raw.decode("utf-8"), object_pairs_hook=reject_duplicate_keys,
                          parse_constant=lambda _: (_ for _ in ()).throw(GatewayError("invalid_json", 400)))
    except (UnicodeError, ValueError) as exc:
        raise GatewayError("invalid_json", 400) from exc


def canonical_json(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


SCHEMA_SHA256 = hashlib.sha256(canonical_json(MODEL_OUTPUT_SCHEMA).encode("utf-8")).hexdigest()


def allowed_source(address: str, path: str, network_config: NetworkConfig = DEFAULT_NETWORK_CONFIG) -> bool:
    if path == "/health":
        return address in {network_config.client_address, "127.0.0.1", "::1"}
    return path == "/infer" and address == network_config.client_address


def authorized(header: str | None, token: str) -> bool:
    if not header or not header.startswith("Bearer "):
        return False
    candidate = header[7:]
    try:
        return hmac.compare_digest(candidate.encode("utf-8"), token.encode("utf-8"))
    except UnicodeError:
        return False


def validate_request(payload):
    if not isinstance(payload, dict) or set(payload) != REQUEST_FIELDS:
        raise GatewayError("invalid_request_fields")
    if payload["model"] != MODEL_NAME:
        raise GatewayError("model_not_allowed")
    if canonical_json(payload["output_schema"]) != canonical_json(MODEL_OUTPUT_SCHEMA):
        raise GatewayError("schema_not_allowed")
    caps = (("max_prompt_tokens", MAX_PROMPT_TOKENS), ("max_output_tokens", MAX_OUTPUT_TOKENS),
            ("token_budget", MAX_CONTEXT_TOKENS))
    for name, maximum in caps:
        value = payload[name]
        if type(value) is not int or not 1 <= value <= maximum:
            raise GatewayError("invalid_" + name)
    if payload["max_prompt_tokens"] + payload["max_output_tokens"] > payload["token_budget"]:
        raise GatewayError("inconsistent_token_budget")
    messages = payload["messages"]
    if not isinstance(messages, list) or not 1 <= len(messages) <= 32:
        raise GatewayError("invalid_messages")
    for index, message in enumerate(messages):
        if (not isinstance(message, dict) or set(message) != {"role", "content"}
                or not isinstance(message["role"], str) or message["role"] not in {"system", "user", "assistant"}
                or not isinstance(message["content"], str) or not message["content"]):
            raise GatewayError("invalid_message")
        if message["role"] == "system" and index != 0:
            raise GatewayError("invalid_system_position")
    if messages[-1]["role"] != "user":
        raise GatewayError("last_message_must_be_user")
    return payload


def generation_schema(messages):
    """Constrain identifiers and output size from actual analysis data only.

    The externally accepted Schema stays byte-for-byte equivalent to the base
    schema. This derivative does not change decision or observation meanings.
    """
    try:
        analysis = parse_json(messages[-1]["content"].encode("utf-8"))
    except GatewayError as exc:
        raise GatewayError("invalid_analysis_window") from exc
    if not isinstance(analysis, dict) or not isinstance(analysis.get("messages"), list):
        raise GatewayError("invalid_analysis_window")
    records = analysis["messages"]
    if not 1 <= len(records) <= 128:
        raise GatewayError("invalid_analysis_window")
    identifiers, authors = set(), set()
    for record in records:
        if not isinstance(record, dict):
            raise GatewayError("invalid_analysis_message")
        identifier = record.get("message_id")
        author = record.get("author_key")
        if not isinstance(identifier, str) or not identifier.strip() or identifier in identifiers:
            raise GatewayError("invalid_analysis_message_id")
        if author is not None and not isinstance(author, str):
            raise GatewayError("invalid_analysis_author")
        identifiers.add(identifier)
        if isinstance(author, str) and author.strip():
            authors.add(author)
    schema = json.loads(json.dumps(MODEL_OUTPUT_SCHEMA))
    properties = schema["properties"]
    properties["target_actor_key"]["enum"] = [None] + sorted(authors)
    properties["evidence"]["items"]["properties"]["message_id"]["enum"] = sorted(identifiers)
    properties["observations"]["maxItems"] = 8
    properties["evidence"]["maxItems"] = 4
    properties["rationale"]["maxLength"] = 350
    for field in ("context_flags", "missing_info"):
        properties[field]["maxItems"] = 4
        properties[field]["uniqueItems"] = True
        properties[field]["items"]["maxLength"] = 80
    return schema


class PromptTokenizer:
    """Use the pinned official tokenizer and chat template, without torch."""
    def __init__(self, directory: Path, revision: str):
        from jinja2.sandbox import ImmutableSandboxedEnvironment
        from tokenizers import Tokenizer

        directory = directory.resolve(strict=True)
        config_path = directory / "tokenizer_config.json"
        tokenizer_path = directory / "tokenizer.json"
        config = json.loads(config_path.read_text(encoding="utf-8"))
        template = config.get("chat_template")
        if not isinstance(template, str) or not template:
            raise GatewayError("tokenizer_chat_template_missing", 503)
        self.tokenizer = Tokenizer.from_file(str(tokenizer_path))
        self.tokenizer.no_truncation()
        self.tokenizer.no_padding()
        # Match HF's default Undefined semantics. Qwen's official template
        # checks optional message.tool_calls even when tools are not supplied.
        # Request validation above still strictly controls all input fields.
        environment = ImmutableSandboxedEnvironment(trim_blocks=True, lstrip_blocks=True)
        environment.filters["tojson"] = lambda value, **kwargs: json.dumps(value, ensure_ascii=False, **kwargs)
        environment.globals["raise_exception"] = lambda _: (_ for _ in ()).throw(GatewayError("invalid_chat_template_input"))
        self.template = environment.from_string(template)
        self.special_tokens = {key: value for key, value in config.items()
                               if key.endswith("_token") and isinstance(value, str)}
        self.metadata = {
            "revision": revision,
            "tokenizer_json_sha256": sha256_file(tokenizer_path),
            "tokenizer_config_sha256": sha256_file(config_path),
            "chat_template_sha256": hashlib.sha256(template.encode("utf-8")).hexdigest(),
            "renderer": "official_chat_template_enable_thinking_false",
            "tokenizers_version": importlib.metadata.version("tokenizers"),
            "jinja2_version": importlib.metadata.version("Jinja2"),
            "truncation_enabled": False,
        }

    def render_and_count(self, messages):
        try:
            prompt = self.template.render(messages=messages, add_generation_prompt=True,
                                          enable_thinking=False, tools=None, **self.special_tokens)
            token_count = len(self.tokenizer.encode(prompt, add_special_tokens=False).ids)
        except GatewayError:
            raise
        except Exception as exc:
            raise GatewayError("prompt_tokenization_failed") from exc
        if not prompt or token_count < 1:
            raise GatewayError("empty_rendered_prompt")
        return prompt, token_count


class OllamaClient:
    def request(self, path, payload=None, timeout=180):
        raw = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = Request(OLLAMA_ORIGIN + path, data=raw, headers={"Content-Type": "application/json"},
                          method="GET" if raw is None else "POST")
        try:
            with urlopen(request, timeout=timeout) as response:
                body = response.read(MAX_UPSTREAM_BYTES + 1)
        except (HTTPError, URLError, TimeoutError, OSError) as exc:
            raise GatewayError("model_service_unavailable", 502) from exc
        if len(body) > MAX_UPSTREAM_BYTES:
            raise GatewayError("model_response_too_large", 502)
        try:
            result = parse_json(body)
        except GatewayError as exc:
            raise GatewayError("invalid_model_response", 502) from exc
        if not isinstance(result, dict):
            raise GatewayError("invalid_model_response", 502)
        return result

    def generate(self, prompt, output_tokens, schema):
        # raw:true skips Ollama's model template. The string token-counted above
        # is exactly the string sent to inference, including the no-thinking prefix.
        return self.request("/api/generate", {
            "model": MODEL_NAME, "prompt": prompt, "raw": True, "stream": False, "think": False,
            "truncate": False, "shift": False,
            "format": schema,
            "options": {"temperature": 0, "seed": 42, "num_predict": output_tokens,
                        "num_ctx": MAX_CONTEXT_TOKENS},
        })

    def model_digest(self):
        tags = self.request("/api/tags", timeout=3)
        models = tags.get("models", [])
        if not isinstance(models, list):
            raise GatewayError("invalid_model_identity_response", 503)
        matching = [entry for entry in models if isinstance(entry, dict)
                    and entry.get("name") in {MODEL_NAME, MODEL_NAME + ":latest"}]
        # Ambiguous duplicate alias records cannot establish the loaded identity.
        if len(matching) != 1:
            return None
        digest = matching[0].get("digest")
        if not isinstance(digest, str) or len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
            return None
        return digest

    def health(self):
        try:
            digest = self.model_digest()
            available = digest is not None
            version = self.request("/api/version", timeout=3).get("version")
            return {"reachable": True, "model_available": available, "model_digest": digest if isinstance(digest, str) else None,
                    "version": version if isinstance(version, str) else None}
        except GatewayError:
            return {"reachable": False, "model_available": False, "model_digest": None, "version": None}


class Gateway:
    def __init__(self, tokenizer, client, token, model_metadata, expected_ollama_digest):
        if not isinstance(token, str) or len(token) < 32 or any(character.isspace() for character in token):
            raise GatewayError("gateway_token_missing_or_weak", 503)
        self.tokenizer, self.client, self.token = tokenizer, client, token
        self.model_metadata = model_metadata
        if (not isinstance(expected_ollama_digest, str) or len(expected_ollama_digest) != 64
                or any(char not in "0123456789abcdef" for char in expected_ollama_digest)):
            raise GatewayError("expected_ollama_digest_missing_or_invalid", 503)
        self.expected_ollama_digest = expected_ollama_digest
        self.inference_lock = threading.Lock()

    def infer(self, payload):
        payload = validate_request(payload)
        schema = generation_schema(payload["messages"])
        dynamic_schema_sha256 = hashlib.sha256(canonical_json(schema).encode("utf-8")).hexdigest()
        prompt, count = self.tokenizer.render_and_count(payload["messages"])
        if count > payload["max_prompt_tokens"] or count + payload["max_output_tokens"] > payload["token_budget"]:
            raise GatewayError("prompt_budget_exceeded")
        if not self.inference_lock.acquire(blocking=False):
            raise GatewayError("model_busy", 429)
        try:
            actual_digest = self.client.model_digest()
            if actual_digest != self.expected_ollama_digest:
                raise GatewayError("model_identity_mismatch", 503)
            result = self.client.generate(prompt, payload["max_output_tokens"], schema)
        finally:
            self.inference_lock.release()
        if (not isinstance(result, dict) or not isinstance(result.get("response"), str)
                or result.get("done") is not True):
            raise GatewayError("invalid_model_response", 502)
        # These are the actual Ollama fields, not substitutions for preflight counts.
        for name in ("prompt_eval_count", "eval_count"):
            if type(result.get(name)) is not int or result[name] < 0:
                raise GatewayError("model_usage_missing", 502)
        if not isinstance(result.get("done_reason"), str):
            raise GatewayError("model_finish_reason_missing", 502)
        if (result["prompt_eval_count"] > payload["max_prompt_tokens"]
                or result["eval_count"] > payload["max_output_tokens"]
                or result["prompt_eval_count"] + result["eval_count"] > payload["token_budget"]):
            raise GatewayError("model_actual_budget_exceeded", 502)
        return {
            "model": MODEL_NAME, "message": {"role": "assistant", "content": result["response"]},
            "done": result["done"], "done_reason": result["done_reason"],
            "prompt_eval_count": result["prompt_eval_count"], "eval_count": result["eval_count"],
            "preflight_prompt_tokens": count, "token_budget": payload["token_budget"],
            "prompt_token_count_delta": result["prompt_eval_count"] - count,
            "model_sha256": self.model_metadata["gguf_sha256"],
            "tokenizer_sha256": self.tokenizer.metadata["tokenizer_json_sha256"],
            "output_schema_sha256": SCHEMA_SHA256,
            "dynamic_schema_sha256": dynamic_schema_sha256,
            "ollama_model_digest": actual_digest,
        }

    def health(self):
        ollama = self.client.health()
        actual_digest = ollama.get("model_digest")
        identity_matches = actual_digest == self.expected_ollama_digest
        return {"status": "ready" if ollama["reachable"] and ollama["model_available"] and identity_matches else "unavailable",
                "gateway_version": GATEWAY_VERSION, "model": self.model_metadata,
                "model_identity_matches": identity_matches,
                "expected_ollama_model_digest": self.expected_ollama_digest,
                "actual_ollama_model_digest": actual_digest,
                "tokenizer": self.tokenizer.metadata, "ollama": ollama,
                "policy": {"context_tokens": MAX_CONTEXT_TOKENS, "max_prompt_tokens": MAX_PROMPT_TOKENS,
                           "max_output_tokens": MAX_OUTPUT_TOKENS, "max_request_bytes": MAX_BODY_BYTES,
                           "concurrency": 1, "output_schema_sha256": SCHEMA_SHA256,
                           "ollama_prompt_mode": "generate_raw", "actual_usage_budget_enforced": True,
                           "generation_schema_policy": "window_identifiers_and_size_caps_v1",
                           "ollama_prompt_truncation_enabled": False, "ollama_context_shift_enabled": False,
                           "training_performed": False}}


class Handler(BaseHTTPRequestHandler):
    server_version = "ModelGateway/" + GATEWAY_VERSION
    sys_version = ""
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        # Disable request/body/auth logs. Errors return only fixed codes.
        pass

    def setup(self):
        super().setup()
        self.connection.settimeout(15)

    def respond(self, status, payload):
        raw = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(raw)
        self.close_connection = True

    def do_GET(self):
        network_config = getattr(self.server, "network_config", DEFAULT_NETWORK_CONFIG)
        if not allowed_source(self.client_address[0], self.path, network_config):
            return self.respond(403, {"error": "source_or_path_not_allowed"})
        if self.path != "/health":
            return self.respond(405, {"error": "method_not_allowed"})
        try:
            health = self.server.gateway.health()
            self.respond(200 if health["status"] == "ready" else 503, health)
        except Exception:
            self.respond(503, {"error": "health_unavailable"})

    def do_POST(self):
        network_config = getattr(self.server, "network_config", DEFAULT_NETWORK_CONFIG)
        if not allowed_source(self.client_address[0], self.path, network_config):
            return self.respond(403, {"error": "source_or_path_not_allowed"})
        if self.path != "/infer":
            return self.respond(405, {"error": "method_not_allowed"})
        authorizations = self.headers.get_all("Authorization", [])
        if len(authorizations) != 1 or not authorized(authorizations[0], self.server.gateway.token):
            return self.respond(401, {"error": "unauthorized"})
        if self.headers.get("Transfer-Encoding"):
            return self.respond(400, {"error": "transfer_encoding_not_allowed"})
        if self.headers.get("Content-Type", "").split(";")[0].strip().lower() != "application/json":
            return self.respond(415, {"error": "content_type_not_allowed"})
        lengths = self.headers.get_all("Content-Length", [])
        if len(lengths) != 1 or not lengths[0].isascii() or not lengths[0].isdigit() or len(lengths[0]) > 6:
            return self.respond(411, {"error": "content_length_required"})
        length = int(lengths[0])
        if not 1 <= length <= MAX_BODY_BYTES:
            return self.respond(413, {"error": "request_too_large"})
        try:
            raw = self.rfile.read(length)
            if len(raw) != length:
                raise GatewayError("incomplete_body", 400)
            result = self.server.gateway.infer(parse_json(raw))
            self.respond(200, result)
        except GatewayError as exc:
            self.respond(exc.status, {"error": exc.code})
        except (TimeoutError, OSError):
            self.respond(408, {"error": "request_timeout"})
        except Exception:
            self.respond(500, {"error": "internal_error"})


class TLSServer(ThreadingHTTPServer):
    """Bound the TLS handshake before a request handler is created."""
    def __init__(self, address, handler, context):
        self.tls_context = context
        super().__init__(address, handler)

    def get_request(self):
        connection, address = super().get_request()
        connection.settimeout(10)
        try:
            return self.tls_context.wrap_socket(connection, server_side=True), address
        except Exception:
            connection.close()
            raise


def main():
    network_config = NetworkConfig.load()
    required = ("CB_TOKENIZER_DIR", "CB_MODEL_GATEWAY_TOKEN", "CB_GATEWAY_TLS_CERT", "CB_GATEWAY_TLS_KEY", "CB_MODEL_FILE_PATH",
                "CB_MODEL_OLLAMA_DIGEST")
    if any(not os.environ.get(key) for key in required):
        raise SystemExit("Missing required gateway environment configuration.")
    if os.environ.get("CB_TOKENIZER_REVISION", TOKENIZER_REVISION) != TOKENIZER_REVISION:
        raise GatewayError("tokenizer_revision_not_allowed", 503)
    tokenizer = PromptTokenizer(Path(os.environ["CB_TOKENIZER_DIR"]), TOKENIZER_REVISION)
    if (tokenizer.metadata["tokenizer_json_sha256"] != TOKENIZER_JSON_SHA256
            or tokenizer.metadata["tokenizer_config_sha256"] != TOKENIZER_CONFIG_SHA256):
        raise GatewayError("tokenizer_hash_verification_failed", 503)
    model_path = Path(os.environ["CB_MODEL_FILE_PATH"]).resolve(strict=True)
    model_sha = sha256_file(model_path)
    if model_sha != MODEL_GGUF_SHA256:
        raise GatewayError("model_hash_verification_failed", 503)
    model_metadata = {"name": MODEL_NAME, "gguf_sha256": model_sha, "bytes": model_path.stat().st_size,
                      "revision": os.environ.get("CB_MODEL_REVISION", "sha256:" + MODEL_GGUF_SHA256),
                      "source_repo_revision_pinned": False, "format": "GGUF", "fine_tuned": False}
    gateway = Gateway(tokenizer, OllamaClient(), os.environ["CB_MODEL_GATEWAY_TOKEN"], model_metadata,
                      os.environ["CB_MODEL_OLLAMA_DIGEST"])
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(os.environ["CB_GATEWAY_TLS_CERT"], os.environ["CB_GATEWAY_TLS_KEY"])
    # Bind one explicit interface; never trust proxy headers for caller identity.
    server = TLSServer((network_config.bind_address, network_config.port), Handler, context)
    server.daemon_threads = True
    server.gateway = gateway
    server.network_config = network_config
    print("Model gateway listening on the configured TLS endpoint; request logging disabled.", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, GatewayError):
        raise SystemExit("Gateway startup failed; check local environment configuration and artifacts.")
