import asyncio
import contextlib
import json
import os
from pathlib import Path
import ssl
import time
import uuid

import httpx

from .core import Conflict, packed
from .schema import InvalidObservation, MODEL_OUTPUT_SCHEMA, SYSTEM_PROMPT, parse_model_observation


class SingleInstanceLock:
    """One process per database; protects the one-GPU worker and startup recovery."""
    def __init__(self, db_path):
        self.path = Path(str(db_path) + ".process-lock")
        self.handle = None

    def acquire(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = self.path.open("a+b")
        self.handle.seek(0, os.SEEK_END)
        if self.handle.tell() == 0:
            self.handle.write(b"0")
            self.handle.flush()
        self.handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.handle.close()
            self.handle = None
            raise RuntimeError("Another service process already owns this database.")

    def release(self):
        if self.handle is not None:
            self.handle.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
            self.handle.close()
            self.handle = None


class AnalysisWorker:
    def __init__(self, store, settings):
        self.store = store
        self.settings = settings
        self.owner = "worker-" + uuid.uuid4().hex
        self.task = None
        self.model_state = "not_checked"
        self.pause_until = 0
        self.current_job_id = None
        self.last_error_code = None

    async def start(self):
        self.task = asyncio.create_task(self.run(), name="one-model-worker")

    async def stop(self):
        if self.task:
            self.task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.task

    async def heartbeat(self, job_id):
        while True:
            await asyncio.sleep(20)
            count = await asyncio.to_thread(self.store.extend_lease, job_id, self.owner, self.settings.lease_seconds)
            if not count:
                return

    async def infer(self, window):
        if not self.settings.model_gateway_url or not self.settings.model_gateway_token:
            return "failed", None, None, "model_gateway_not_configured"
        endpoint = self.settings.model_gateway_url.rstrip("/") + "/infer"
        message_input = [{
            "message_id": m["message_id"], "author_key": m["author_key"], "reply_to": m["reply_to"],
            "timestamp": m["timestamp"], "relative_time_seconds": m["relative_time_seconds"], "text": m["text"],
        } for m in window["messages"]]
        payload = {
            "model": self.settings.model_name,
            "messages": [{"role": "system", "content": SYSTEM_PROMPT},
                         {"role": "user", "content": packed({"messages": message_input, "focus_message_id": window["focus_message_id"]})}],
            "max_prompt_tokens": self.settings.max_prompt_tokens,
            "max_output_tokens": self.settings.max_output_tokens,
            "token_budget": self.settings.token_budget,
            "output_schema": MODEL_OUTPUT_SCHEMA,
        }
        verification = ssl.create_default_context(cafile=self.settings.model_gateway_ca) if self.settings.model_gateway_ca else True
        async with httpx.AsyncClient(timeout=self.settings.model_timeout, trust_env=False,
                                     verify=verification, follow_redirects=False) as client:
            response = await client.post(endpoint, json=payload,
                headers={"Authorization": "Bearer " + self.settings.model_gateway_token})
            if response.status_code == 422:
                return "context_required", None, response.text, "input_budget_unverified_or_exceeded"
            response.raise_for_status()
        raw = response.text
        try:
            response_json = response.json()
        except ValueError:
            return "invalid_output", None, raw, "invalid_response_json"
        if not isinstance(response_json, dict):
            return "invalid_output", None, raw, "invalid_response_envelope"
        if response_json.get("model") != self.settings.model_name:
            return "invalid_output", None, raw, "model_identity_mismatch"
        if response_json.get("done") is not True:
            return "invalid_output", None, raw, "incomplete_model_response"
        prompt_count = response_json.get("prompt_eval_count")
        preflight = response_json.get("preflight_prompt_tokens")
        if type(prompt_count) is not int or prompt_count <= 0 or type(preflight) is not int or preflight <= 0:
            return "context_required", None, raw, "input_budget_unverified"
        if max(prompt_count, preflight) > self.settings.max_prompt_tokens:
            return "context_required", None, raw, "input_budget_exceeded"
        if response_json.get("done_reason") == "length":
            return "truncated_output", None, raw, "generation_length_limit"
        if response_json.get("done_reason") != "stop":
            return "invalid_output", None, raw, "unknown_generation_stop"
        message = response_json.get("message")
        if not isinstance(message, dict):
            return "invalid_output", None, raw, "invalid_response_message"
        content = message.get("content")
        try:
            result = parse_model_observation(content, window["messages"])
        except InvalidObservation as exc:
            return ("inconsistent_output" if str(exc) == "inconsistent_output" else "invalid_output"), None, raw, str(exc)
        return "succeeded", result, raw, None

    async def run(self):
        while True:
            if time.time() < self.pause_until:
                await asyncio.sleep(0.5)
                continue
            job = await asyncio.to_thread(self.store.claim, self.owner, self.settings.lease_seconds)
            if job is None:
                await asyncio.sleep(0.5)
                continue
            self.current_job_id = job["id"]
            heartbeat = asyncio.create_task(self.heartbeat(job["id"]))
            try:
                window = await asyncio.to_thread(self.store.window, job["window_id"])
                if job["pipeline_version"] != self.settings.pipeline_version:
                    status, result, raw, error = "failed", None, None, "pipeline_version_changed"
                elif window["token_budget_exceeded"]:
                    status, result, raw, error = "context_required", None, None, "estimated_token_budget_exceeded"
                else:
                    status, result, raw, error = await self.infer(window)
                    self.model_state = "unavailable" if error == "model_gateway_not_configured" else "available"
                await asyncio.to_thread(self.store.finish, job["id"], self.owner, status, result, raw, error)
                self.last_error_code = error
            except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout, httpx.RemoteProtocolError):
                self.model_state = "unavailable"
                self.last_error_code = "model_unavailable"
                with contextlib.suppress(Conflict):
                    await asyncio.to_thread(self.store.finish, job["id"], self.owner, "waiting_model", None, None,
                                            "model_unavailable", self.settings.model_retry_seconds)
                self.pause_until = time.time() + self.settings.model_retry_seconds
            except httpx.HTTPStatusError as exc:
                code = exc.response.status_code
                transient = code in {429, 500, 502, 503, 504}
                status = "waiting_model" if transient else "failed"
                self.last_error_code = "model_http_" + str(code)
                with contextlib.suppress(Conflict):
                    await asyncio.to_thread(self.store.finish, job["id"], self.owner, status, None, exc.response.text,
                                            self.last_error_code, self.settings.model_retry_seconds if transient else 0)
                if transient:
                    self.pause_until = time.time() + self.settings.model_retry_seconds
            except Conflict:
                self.last_error_code = "stale_or_reclaimed_job"
            except asyncio.CancelledError:
                raise
            except Exception:
                self.last_error_code = "analysis_exception"
                with contextlib.suppress(Conflict):
                    await asyncio.to_thread(self.store.finish, job["id"], self.owner, "failed", None, None, "analysis_exception")
            finally:
                heartbeat.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await heartbeat
                self.current_job_id = None
