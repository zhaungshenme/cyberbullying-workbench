from dataclasses import dataclass
import json
import os
from pathlib import Path
from urllib.parse import urlparse


APP_ROOT = Path(__file__).resolve().parent.parent


@dataclass
class Settings:
    data_dir: Path
    db_path: Path
    model_url: str = "http://127.0.0.1:8081"
    model_name: str = "qwen3-4b"
    admin_token: str = ""
    service_token: str = ""
    reviewer_id: str = "local-reviewer"
    pipeline_version: str = "language-observation-v1"
    token_budget: int = 2048
    max_prompt_tokens: int = 1536
    max_output_tokens: int = 512
    model_timeout: float = 180.0
    lease_seconds: float = 300.0
    model_retry_seconds: float = 30.0
    enable_worker: bool = True
    model_gateway_url: str = ""
    model_gateway_token: str = ""
    model_gateway_ca: str = ""

    @classmethod
    def load(cls):
        config_path = APP_ROOT / "config.json"
        config = json.loads(config_path.read_text(encoding="utf-8-sig")) if config_path.exists() else {}
        service = config.get("service", {}) if isinstance(config.get("service"), dict) else {}
        values = {**config, **service}
        data_dir = Path(os.environ.get("CB_DATA_DIR", values.get("data_dir", str(APP_ROOT / "data" / "local"))))
        db_path = Path(os.environ.get("CB_DB_PATH", values.get("db_path", str(data_dir / "case_review.sqlite3"))))
        settings = cls(
            data_dir=data_dir, db_path=db_path,
            model_url=os.environ.get("CB_MODEL_URL", values.get("model_url", "http://127.0.0.1:8081")),
            model_name=os.environ.get("CB_MODEL_NAME", values.get("model_name", "qwen3-4b")),
            admin_token=os.environ.get("CB_ADMIN_TOKEN", ""),
            service_token=os.environ.get("CB_SERVICE_TOKEN", ""),
            reviewer_id=os.environ.get("CB_REVIEWER_ID", "local-reviewer"),
            pipeline_version=os.environ.get("CB_PIPELINE_VERSION", "language-observation-v1"),
            token_budget=int(os.environ.get("CB_TOKEN_BUDGET", values.get("token_budget", 2048))),
            max_prompt_tokens=int(os.environ.get("CB_MAX_PROMPT_TOKENS", values.get("max_prompt_tokens", 1536))),
            max_output_tokens=int(os.environ.get("CB_MAX_OUTPUT_TOKENS", values.get("max_output_tokens", 512))),
            model_timeout=float(os.environ.get("CB_MODEL_TIMEOUT", 180)),
            enable_worker=os.environ.get("CB_ENABLE_WORKER", "1") != "0",
        )
        parsed = urlparse(settings.model_url)
        if parsed.scheme not in {"http", "https"} or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("CB_MODEL_URL must point to a loopback model server.")
        if settings.admin_token and settings.service_token and settings.admin_token == settings.service_token:
            raise ValueError("Human and integration tokens must be different.")
        if min(settings.max_prompt_tokens, settings.max_output_tokens) < 1 or settings.max_prompt_tokens + settings.max_output_tokens > settings.token_budget:
            raise ValueError("Prompt and output token caps must fit CB_TOKEN_BUDGET.")
        return settings
