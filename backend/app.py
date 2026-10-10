"""Kali workbench BFF. Browser keys terminate at the separate frontend server."""
from contextlib import asynccontextmanager
import hashlib
import hmac
import json
import re
from typing import Literal

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from auth import AuthDB, AuthError, session_context
from pagination import PageCursor

from orchestration import Engine, SubmissionDB, public_job
from settings import Settings
from service.app import create_app as create_service_app
from service.core import Conflict, Missing


class AnalyzeBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    case_name: str = Field(min_length=1, max_length=200)
    filename: str = Field(min_length=1, max_length=200)
    content: str
    idempotency_key: str = Field(min_length=1, max_length=200)


class IntegrationBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    window_id: str = Field(min_length=1, max_length=100)
    input_hash: str = Field(min_length=64, max_length=64)
    case_version: int = Field(ge=1)
    pipeline_version: str = Field(min_length=1, max_length=200)
    idempotency_key: str = Field(min_length=1, max_length=250)


class LoginBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class PasswordBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    current_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=12, max_length=256)


class UserBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str = Field(min_length=3, max_length=64)
    display_name: str = Field(min_length=1, max_length=100)
    role: Literal["admin", "analyst", "reviewer", "auditor"]
    password: str = Field(min_length=12, max_length=256)


class UserPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    display_name: str | None = Field(default=None, min_length=1, max_length=100)
    role: Literal["admin", "analyst", "reviewer", "auditor"] | None = None
    active: bool | None = None
    password: str | None = Field(default=None, min_length=12, max_length=256)


class AssignBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reviewer_id: str | None
    auditor_id: str | None


class ReviewBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    outcome: Literal["confirm_observation", "request_context", "reject_observation"]
    comment: str = Field(min_length=1, max_length=2000)
    case_version: int = Field(ge=1)
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=200)


def create_app(settings=None, *, dify_client=None):
    settings = settings or Settings.load()
    cursors = PageCursor(settings.backend_token)
    internal = create_service_app(settings.service_settings())

    @asynccontextmanager
    async def lifespan(app):
        settings.validate()
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        async with internal.router.lifespan_context(internal):
            db = SubmissionDB(settings.data_dir / "submissions.sqlite3")
            identities = AuthDB(settings.data_dir / "auth.sqlite3", settings.bootstrap_admin_password)
            identities.attach_submissions(settings.data_dir / "submissions.sqlite3")
            engine = Engine(internal.state.store, db, settings, client=dify_client)
            app.state.engine, app.state.db = engine, db
            app.state.store, app.state.worker = internal.state.store, internal.state.worker
            app.state.auth = identities
            if settings.enable_dispatcher:
                await engine.start()
            try:
                yield
            finally:
                await engine.stop()
                db.close()
                identities.close()

    app = FastAPI(title="网暴行为溯源追踪工作台 API", version="1.0", lifespan=lifespan,
                  docs_url=None, redoc_url=None, openapi_url=None)
    app.state.settings = settings

    @app.middleware("http")
    async def guard_internal(request, call_next):
        if request.url.path.startswith("/internal") and (request.client is None or request.client.host not in {"127.0.0.1", "::1"}):
            return JSONResponse(status_code=404, content={"detail": "not_found"})
        if request.headers.get("content-length"):
            try:
                if int(request.headers["content-length"]) > 8 * 1024 * 1024:
                    return JSONResponse(status_code=413, content={"detail": "request_body_too_large"})
            except ValueError:
                return JSONResponse(status_code=400, content={"detail": "invalid_content_length"})
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    def auth(request, expected):
        value = request.headers.get("Authorization", "")
        presented = value[7:] if value.startswith("Bearer ") else ""
        if not expected or not presented or not hmac.compare_digest(presented, expected):
            raise HTTPException(403, detail="forbidden")

    def service_workbench(request: Request):
        auth(request, settings.backend_token)

    def session_actor(request: Request):
        service_workbench(request)
        token = request.headers.get("X-Workbench-Session", "")
        actor = app.state.auth.authenticate(token)
        context = session_context(token)
        expected = request.headers.get("X-Workbench-Expected-Context")
        if expected is not None and (not re.fullmatch(r"[a-f0-9]{64}", expected) or not hmac.compare_digest(expected, context)):
            raise HTTPException(409, detail="session_context_changed")
        request.state.session_context = context
        return actor

    def workbench(request: Request):
        actor = session_actor(request)
        if actor["must_change_password"]:
            raise HTTPException(403, detail="password_change_required")
        return actor

    def role_required(actor, *roles):
        if actor["role"] not in roles:
            raise HTTPException(403, detail="role_forbidden")

    def accessible(actor, sid):
        submission = app.state.db.one(sid)
        if not app.state.auth.visible(actor, submission):
            raise Missing("submission")
        return submission

    def review_references(snapshot):
        current_version = snapshot["case"]["current_version"] if snapshot["case"] else None
        return [{"window_id": window["id"], "input_hash": window["input_hash"], "case_version": current_version,
            "job_id": next((job["id"] for job in snapshot["jobs"] if job["window_id"] == window["id"] and job["case_version"] == current_version), None),
            "status": next((job["status"] for job in snapshot["jobs"] if job["window_id"] == window["id"] and job["case_version"] == current_version), "not_submitted"),
            "validated_result_sha256": next((hashlib.sha256(json.dumps(job["result"], ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()).hexdigest() for job in snapshot["jobs"] if job["window_id"] == window["id"] and job["case_version"] == current_version and job.get("result") is not None), None),
            "pipeline_version": next((job["pipeline_version"] for job in snapshot["jobs"] if job["window_id"] == window["id"] and job["case_version"] == current_version), settings.pipeline_version)} for window in snapshot["windows"]]

    def snapshot_with_context(sid):
        snapshot = app.state.engine.task_snapshot(sid, app.state.worker)
        snapshot["case_version"] = snapshot["case"]["current_version"] if snapshot["case"] else None
        snapshot.update(app.state.auth.task_context(app.state.db.one(sid), include_audit=True,
            case_version=snapshot["case_version"], references=review_references(snapshot)))
        return snapshot

    def integration(request: Request):
        auth(request, settings.service_token)

    @app.exception_handler(Conflict)
    async def conflicts(request, exc):
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    @app.exception_handler(Missing)
    async def missing(request, exc):
        return JSONResponse(status_code=404, content={"detail": "not_found"})

    @app.exception_handler(ValueError)
    async def invalid(request, exc):
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    @app.exception_handler(AuthError)
    async def authentication_error(request, exc):
        return JSONResponse(status_code=exc.status, content={"detail": exc.detail})

    @app.post("/workbench/auth/login", dependencies=[Depends(service_workbench)])
    def login(body: LoginBody):
        return app.state.auth.login(body.username, body.password)

    @app.get("/workbench/auth/me")
    def me(request: Request, actor=Depends(session_actor)):
        return {"user": actor, "session_context": request.state.session_context}

    @app.post("/workbench/auth/logout")
    def logout(request: Request, actor=Depends(session_actor)):
        app.state.auth.logout(request.headers["X-Workbench-Session"], actor)
        return {"status": "logged_out"}

    @app.post("/workbench/auth/password")
    def password(body: PasswordBody, actor=Depends(session_actor)):
        return app.state.auth.change_password(actor, body.current_password, body.new_password)

    @app.get("/workbench/users")
    def users(actor=Depends(workbench)):
        role_required(actor, "admin")
        return {"users": app.state.auth.users()}

    @app.post("/workbench/users")
    def create_user(body: UserBody, actor=Depends(workbench)):
        role_required(actor, "admin")
        return app.state.auth.create_user(actor["id"], body.username, body.display_name, body.role, body.password)

    @app.patch("/workbench/users/{user_id}")
    def update_user(user_id: str, body: UserPatch, actor=Depends(workbench)):
        role_required(actor, "admin")
        changes = body.model_dump(exclude_unset=True)
        if any(value is None for value in changes.values()):
            raise HTTPException(400, detail="null_user_fields_not_allowed")
        return app.state.auth.update_user(actor["id"], user_id, changes)

    def health_value():
        return {"status": "ok", "scope": "language_observation_only", "model": settings.model_name,
            "model_state": app.state.worker.model_state, "worker_enabled": settings.enable_worker,
            "dify_configured": bool(settings.dify_key), "model_gateway_configured": bool(settings.gateway_url and settings.gateway_token),
            "pipeline_version": settings.pipeline_version, "input_byte_limit": settings.max_input_bytes,
            "window_limit": settings.max_windows, "human_gold_enabled": False}

    @app.get("/workbench/health", dependencies=[Depends(workbench)])
    def health():
        return health_value()

    @app.post("/workbench/analyze", status_code=202)
    def analyze(body: AnalyzeBody, actor=Depends(workbench)):
        role_required(actor, "admin", "analyst")
        sid, deduplicated = app.state.db.reserve(body.case_name, body.filename, body.content, body.idempotency_key, settings, actor["id"])
        app.state.auth.audit(actor["id"], "task_submitted", sid, {"deduplicated": deduplicated})
        return {"submission_id": sid, "status": "accepted", "deduplicated": deduplicated}

    @app.get("/workbench/tasks")
    def tasks(limit: int = Query(default=30, ge=1, le=100), cursor: str | None = Query(default=None), actor=Depends(workbench)):
        high_water, after = cursors.decode(cursor, "tasks", actor) if cursor is not None else (None, None)
        # Keep local assignment changes serialized through selection and hydration.
        with app.state.auth.lock:
            rows, high_water, more = app.state.auth.task_page(actor, limit, high_water, after)
            result = []
            for row in rows:
                submission = app.state.db.one(row["id"])
                snapshot = app.state.engine.task_snapshot(row["id"], app.state.worker)
                context = app.state.auth.task_context(submission, case_version=snapshot["case"]["current_version"] if snapshot["case"] else None, references=review_references(snapshot))
                result.append({"submission_id": row["id"], "case_name": row["case_name"], "status": snapshot["status"],
                    "created_at": row["created_at"], **{name: context[name] for name in ("owner", "reviewer", "auditor", "review_status")}})
        app.state.auth.audit(actor["id"], "task_list_read", metadata={"count": len(result)})
        next_cursor = cursors.encode("tasks", actor, high_water, [rows[-1]["created_at"], rows[-1]["id"]]) if more else None
        return {"tasks": result, "next_cursor": next_cursor}

    @app.get("/workbench/tasks/{submission_id}")
    def task(submission_id: str, actor=Depends(workbench)):
        accessible(actor, submission_id)
        app.state.auth.audit(actor["id"], "task_read", submission_id)
        return snapshot_with_context(submission_id)

    @app.post("/workbench/tasks/{submission_id}/retry")
    def retry(submission_id: str, actor=Depends(workbench)):
        submission = accessible(actor, submission_id)
        role_required(actor, "admin", "analyst")
        if submission.get("owner_id") is None:
            raise HTTPException(400, detail="legacy_ownerless_read_only")
        app.state.auth.audit(actor["id"], "task_retry", submission_id)
        return app.state.engine.retry(submission_id)

    @app.get("/workbench/tasks/{submission_id}/report")
    def report(submission_id: str, actor=Depends(workbench)):
        accessible(actor, submission_id)
        app.state.auth.audit(actor["id"], "task_exported", submission_id)
        snapshot = snapshot_with_context(submission_id)
        details = app.state.store.report(snapshot["case"]["id"]) if snapshot["case"] else None
        if details:
            details["jobs"] = [public_job(job) for job in details["jobs"]]
        return {"schema_version": "workbench-1", "scope": "language_observation_only",
            **snapshot, "material_report": details, "human_gold": None, "legal_determination": None}

    @app.post("/workbench/tasks/{submission_id}/assign")
    def assign(submission_id: str, body: AssignBody, actor=Depends(workbench)):
        role_required(actor, "admin")
        submission = app.state.db.one(submission_id)
        app.state.auth.assign(actor, submission_id, submission.get("owner_id"), body.reviewer_id, body.auditor_id)
        context = snapshot_with_context(submission_id)
        return {name: context[name] for name in ("owner", "reviewer", "auditor", "reviews", "review_status")}

    @app.post("/workbench/tasks/{submission_id}/review")
    def review(submission_id: str, body: ReviewBody, actor=Depends(workbench)):
        submission = accessible(actor, submission_id)
        role_required(actor, "reviewer")
        snapshot = app.state.engine.task_snapshot(submission_id, app.state.worker)
        if snapshot["status"] not in {"completed", "completed_with_errors", "dispatch_failed"} or not snapshot["case"]:
            raise HTTPException(409, detail="task_not_terminal")
        current_version = snapshot["case"]["current_version"]
        if body.case_version != current_version:
            raise HTTPException(409, detail="case_version_conflict")
        if body.outcome == "confirm_observation" and not snapshot["coverage"]["all_windows_model_completed"]:
            raise HTTPException(409, detail="cannot_confirm_incomplete_model_coverage")
        if not body.comment.strip():
            raise HTTPException(400, detail="review_comment_required")
        references = review_references(snapshot)
        review_id = app.state.auth.review(actor, submission, body.outcome, body.comment, current_version, references, body.idempotency_key)
        return {"review_id": review_id, **app.state.auth.task_context(submission, case_version=current_version, references=references)}

    @app.get("/workbench/audit")
    def audit(limit: int = Query(default=30, ge=1, le=100), cursor: str | None = Query(default=None), actor=Depends(workbench)):
        role_required(actor, "admin", "auditor")
        high_water, after = cursors.decode(cursor, "audit", actor) if cursor is not None else (None, None)
        rows, high_water, more = app.state.auth.audit_page(actor, limit, high_water, after)
        app.state.auth.audit(actor["id"], "audit_read", metadata={"scope": "global" if actor["role"] == "admin" else "assigned_tasks"})
        next_cursor = cursors.encode("audit", actor, high_water, rows[-1]["seq"]) if more else None
        return {"audit": rows, "next_cursor": next_cursor}

    @app.get("/integration/health", dependencies=[Depends(integration)])
    def integration_health():
        return {key: health_value()[key] for key in ("status", "scope", "model_state", "pipeline_version", "worker_enabled")}

    def safe_job(value):
        reviews = app.state.store.read("SELECT id FROM review_tasks WHERE job_id=?", (value["id"],))
        return {"job_id": value["id"], "window_id": value["window_id"], "case_id": value["case_id"],
            "case_version": value["case_version"], "input_hash": value["input_hash"], "pipeline_version": value["pipeline_version"],
            "status": value["status"], "error_code": value.get("error_code"), "review_ref": reviews[0]["id"] if reviews else None}

    @app.post("/integration/analysis-jobs", status_code=202, dependencies=[Depends(integration)])
    def integration_submit(body: IntegrationBody):
        if body.pipeline_version != settings.pipeline_version:
            raise Conflict("pipeline_version_conflict")
        return safe_job(app.state.store.enqueue(body.window_id, body.pipeline_version, body.idempotency_key, body.input_hash, body.case_version))

    @app.get("/integration/analysis-jobs/{job_id}", dependencies=[Depends(integration)])
    def integration_job(job_id: str):
        return safe_job(app.state.store.job(job_id))

    # The copied service provides its lifespan/store only. Its fixed single-reviewer API is not mounted.
    return app


app = create_app()
