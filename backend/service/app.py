from contextlib import asynccontextmanager
import hashlib
import hmac
import json
from pathlib import Path
from typing import Literal

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from .core import Conflict, Missing, Store
from .schema import MODEL_OUTPUT_SCHEMA, OBSERVATION_SCHEMA
from .settings import Settings
from .worker import AnalysisWorker, SingleInstanceLock


class ImportBody(BaseModel):
    case_name: str = Field(min_length=1, max_length=200)
    filename: str
    content: str
    case_id: str | None = None
    expected_version: int | None = None


class JobBody(BaseModel):
    window_id: str | None = None
    case_id: str | None = None
    pipeline_version: str | None = None
    idempotency_key: str | None = None
    input_hash: str | None = None
    case_version: int | None = None


class IntegrationJobBody(BaseModel):
    window_id: str
    input_hash: str
    case_version: int
    pipeline_version: str
    idempotency_key: str


class ReviewBody(BaseModel):
    job_id: str


class DecisionBody(BaseModel):
    decision: Literal["supported", "not_supported", "insufficient_context"]
    evidence_message_ids: list[str] = Field(default_factory=list)
    rationale: str = Field(min_length=1, max_length=2000)
    case_version: int
    input_hash: str


def create_app(settings=None):
    settings = settings or Settings.load()

    @asynccontextmanager
    async def lifespan(app):
        process_lock = SingleInstanceLock(settings.db_path)
        process_lock.acquire()
        store = None
        try:
            store = Store(settings.db_path, settings.token_budget)
            store.recover_after_exclusive_start()
            worker = AnalysisWorker(store, settings)
            app.state.store, app.state.worker = store, worker
            if settings.enable_worker:
                await worker.start()
            yield
        finally:
            if hasattr(app.state, "worker"):
                await app.state.worker.stop()
            if store:
                store.close()
            process_lock.release()

    app = FastAPI(title="中文语言证据研判与单人审核台", version="1.0", lifespan=lifespan)
    app.state.settings = settings

    def require_token(request, expected, kind):
        if not expected:
            raise HTTPException(503, detail=kind + "_token_not_configured")
        header = request.headers.get("Authorization", "")
        candidate = header[7:] if header.startswith("Bearer ") else ""
        if not candidate or not hmac.compare_digest(candidate, expected):
            raise HTTPException(403, detail="forbidden")

    def human(request: Request):
        require_token(request, settings.admin_token, "admin")
        return settings.reviewer_id

    def integration(request: Request):
        require_token(request, settings.service_token, "service")

    def store(request: Request):
        return request.app.state.store

    @app.exception_handler(Conflict)
    async def conflict_handler(request, exc):
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    @app.exception_handler(Missing)
    async def missing_handler(request, exc):
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=404, content={"detail": "not_found"})

    @app.exception_handler(ValueError)
    async def value_handler(request, exc):
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    def safe_health(request):
        worker = request.app.state.worker
        return {"status": "ok", "scope": "language_observation_only",
                "pipeline_version": settings.pipeline_version,
                "model_state": worker.model_state, "worker_enabled": settings.enable_worker,
                "inference_concurrency": 1, "human_gold_enabled": False,
                "prompt_token_cap": settings.max_prompt_tokens, "output_token_cap": settings.max_output_tokens}

    def safe_job(value, persistence):
        reviews = persistence.read("SELECT id FROM review_tasks WHERE job_id=?", (value["id"],))
        return {
            "job_id": value["id"], "window_id": value["window_id"], "case_id": value["case_id"],
            "case_version": value["case_version"], "input_hash": value["input_hash"],
            "pipeline_version": value["pipeline_version"], "status": value["status"],
            "error_code": value.get("error_code"), "review_ref": reviews[0]["id"] if reviews else None,
        }

    @app.get("/")
    def index():
        return FileResponse(Path(__file__).parent / "static" / "index.html")

    @app.get("/health")
    def health(request: Request):
        return safe_health(request)

    @app.get("/api/health", dependencies=[Depends(human)])
    def private_health(request: Request):
        value = safe_health(request)
        value["admin_token_configured"] = bool(settings.admin_token)
        value["integration_token_configured"] = bool(settings.service_token)
        return value

    @app.get("/integration/health", dependencies=[Depends(integration)])
    def integration_health(request: Request):
        return safe_health(request)

    @app.get("/api/schema", dependencies=[Depends(human)])
    def schema():
        return OBSERVATION_SCHEMA

    @app.get("/api/model-schema", dependencies=[Depends(human)])
    def model_schema():
        return MODEL_OUTPUT_SCHEMA

    @app.get("/api/cases", dependencies=[Depends(human)])
    def cases(persistence=Depends(store)):
        return persistence.cases()

    @app.get("/api/cases/{case_id}", dependencies=[Depends(human)])
    def case(case_id: str, persistence=Depends(store)):
        return persistence.case(case_id)

    @app.post("/api/imports", dependencies=[Depends(human)])
    def import_text(payload: ImportBody, persistence=Depends(store)):
        return persistence.import_bytes(payload.content.encode("utf-8"), payload.filename, payload.case_name,
                                        payload.case_id, payload.expected_version)

    @app.post("/api/imports/upload", dependencies=[Depends(human)])
    async def import_file(file: UploadFile = File(...), case_name: str = Form(...),
                          case_id: str | None = Form(None), expected_version: int | None = Form(None),
                          persistence=Depends(store)):
        data = await file.read(32 * 1024 * 1024 + 1)
        return persistence.import_bytes(data, file.filename or "upload.jsonl", case_name, case_id, expected_version)

    @app.get("/api/window-manifests", dependencies=[Depends(human)])
    def manifests(case_id: str, persistence=Depends(store)):
        return {"case_id": case_id, "window_size": 8, "overlap": 2,
                "token_estimate": "conservative_character_count_plus_overhead",
                "token_budget": settings.token_budget, "windows": persistence.manifests(case_id)}

    @app.get("/api/windows/{window_id}", dependencies=[Depends(human)])
    def window(window_id: str, persistence=Depends(store)):
        return persistence.window(window_id)

    @app.post("/api/analysis-jobs", status_code=202, dependencies=[Depends(human)])
    def enqueue(payload: JobBody, persistence=Depends(store)):
        if bool(payload.case_id) == bool(payload.window_id):
            raise HTTPException(400, detail="Provide exactly one of case_id or window_id.")
        pipeline = payload.pipeline_version or settings.pipeline_version
        if pipeline != settings.pipeline_version:
            raise Conflict("pipeline_version_conflict")
        if payload.case_id:
            windows = persistence.manifests(payload.case_id)
            return {"jobs": [safe_job(persistence.enqueue(window["id"], pipeline), persistence) for window in windows]}
        return safe_job(persistence.enqueue(payload.window_id, pipeline, payload.idempotency_key,
                                            payload.input_hash, payload.case_version), persistence)

    @app.get("/api/analysis-jobs", dependencies=[Depends(human)])
    def jobs(case_id: str | None = None, persistence=Depends(store)):
        return persistence.jobs(case_id)

    @app.get("/api/analysis-jobs/{job_id}", dependencies=[Depends(human)])
    def job(job_id: str, persistence=Depends(store)):
        return persistence.job(job_id)

    @app.post("/integration/analysis-jobs", status_code=202, dependencies=[Depends(integration)])
    def integration_enqueue(payload: IntegrationJobBody, persistence=Depends(store)):
        if payload.pipeline_version != settings.pipeline_version:
            raise Conflict("pipeline_version_conflict")
        value = persistence.enqueue(payload.window_id, payload.pipeline_version, payload.idempotency_key,
                                    payload.input_hash, payload.case_version)
        return safe_job(value, persistence)

    @app.get("/integration/analysis-jobs/{job_id}", dependencies=[Depends(integration)])
    def integration_job(job_id: str, persistence=Depends(store)):
        return safe_job(persistence.job(job_id), persistence)

    @app.post("/api/review-tasks", dependencies=[Depends(human)])
    def create_review(payload: ReviewBody, persistence=Depends(store)):
        return persistence.create_review(payload.job_id)

    @app.get("/api/review-tasks", dependencies=[Depends(human)])
    def reviews(case_id: str | None = None, persistence=Depends(store)):
        return persistence.reviews(case_id)

    @app.post("/api/review-tasks/{review_id}/decisions")
    def decision(review_id: str, payload: DecisionBody, reviewer_id=Depends(human), persistence=Depends(store)):
        return persistence.decide(review_id, reviewer_id, payload.decision, payload.evidence_message_ids,
                                  payload.rationale, payload.case_version, payload.input_hash)

    @app.get("/api/reports/{case_id}", dependencies=[Depends(human)])
    def report(case_id: str, persistence=Depends(store)):
        return persistence.report(case_id)

    return app


app = create_app()
