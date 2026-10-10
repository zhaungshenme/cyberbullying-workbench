from dataclasses import dataclass
import os
from pathlib import Path
from urllib.parse import urlparse

from service.settings import Settings as ServiceSettings


@dataclass
class Settings:
    data_dir: Path
    backend_token: str
    service_token: str
    dify_url: str = "http://127.0.0.1/v1"
    dify_key: str = ""
    gateway_url: str = ""
    gateway_token: str = ""
    gateway_ca: str = ""
    model_name: str = "cb-qwen3-4b"
    pipeline_version: str = "cb-workbench-ollama-v1.2"
    enable_worker: bool = True
    enable_dispatcher: bool = True
    max_input_bytes: int = 1024 * 1024
    max_windows: int = 100
    retry_seconds: float = 5
    max_dispatch_attempts: int = 3
    bootstrap_admin_password: str = ""

    @classmethod
    def load(cls):
        return cls(
            data_dir=Path(os.environ.get("WORKBENCH_DATA_DIR", str(Path.home() / ".local/share/cyberbullying-workbench"))).expanduser(),
            backend_token=os.environ.get("WORKBENCH_BACKEND_TOKEN", ""),
            service_token=os.environ.get("CB_SERVICE_TOKEN", ""),
            dify_url=os.environ.get("DIFY_API_URL", "http://127.0.0.1/v1"),
            dify_key=os.environ.get("DIFY_SUBMIT_KEY", ""),
            gateway_url=os.environ.get("CB_MODEL_GATEWAY_URL", ""),
            gateway_token=os.environ.get("CB_MODEL_GATEWAY_TOKEN", ""),
            gateway_ca=os.environ.get("CB_MODEL_GATEWAY_CA", ""),
            model_name=os.environ.get("CB_MODEL_NAME", "cb-qwen3-4b"),
            pipeline_version=os.environ.get("CB_PIPELINE_VERSION", "cb-workbench-ollama-v1.2"),
            enable_worker=os.environ.get("CB_ENABLE_WORKER", "1") != "0",
            enable_dispatcher=os.environ.get("WORKBENCH_ENABLE_DISPATCHER", "1") != "0",
            bootstrap_admin_password=os.environ.get("WORKBENCH_BOOTSTRAP_ADMIN_PASSWORD", ""),
        )

    def validate(self):
        if not self.backend_token or not self.service_token or self.backend_token == self.service_token:
            raise ValueError("Configure distinct WORKBENCH_BACKEND_TOKEN and CB_SERVICE_TOKEN")
        dify = urlparse(self.dify_url)
        if dify.scheme != "http" or dify.hostname not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("DIFY_API_URL must use the Kali loopback HTTP service")
        if dify.username or dify.password or dify.query or dify.fragment:
            raise ValueError("DIFY_API_URL must not contain credentials or query strings")
        if self.gateway_url:
            gateway = urlparse(self.gateway_url)
            if gateway.scheme != "https" or not gateway.hostname or gateway.username or gateway.password or gateway.query or gateway.fragment:
                raise ValueError("CB_MODEL_GATEWAY_URL must be a HTTPS base URL without credentials")
        if not 1 <= self.max_windows <= 100 or not 1 <= self.max_input_bytes <= 1024 * 1024:
            raise ValueError("Input limits may not exceed 1 MiB / 100 windows")
        if not 1 <= self.max_dispatch_attempts <= 5:
            raise ValueError("Dispatch attempts must be in 1..5")

    def service_settings(self):
        return ServiceSettings(
            data_dir=self.data_dir, db_path=self.data_dir / "materials.sqlite3",
            admin_token=self.backend_token, service_token=self.service_token,
            reviewer_id="workbench-reviewer", pipeline_version=self.pipeline_version,
            model_name=self.model_name, enable_worker=self.enable_worker,
            model_gateway_url=self.gateway_url, model_gateway_token=self.gateway_token,
            model_gateway_ca=self.gateway_ca,
        )
