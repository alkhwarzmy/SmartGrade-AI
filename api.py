"""Authenticated commercial REST API. Same Workspace boundary as the teacher UI."""

from dataclasses import asdict
import json
import logging
import os
from pathlib import Path
from typing import Literal
import sqlite3
from fastapi import Depends, FastAPI, File, HTTPException, UploadFile, Form, Header, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response, FileResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field, SecretStr
from sqlalchemy.exc import SQLAlchemyError
from starlette.concurrency import run_in_threadpool
from config import DATA_DIR, MAX_FILE_BYTES
from modules.auth import AuthService, AuthError, PermissionDenied, CapacityError, LoginRateLimited
from modules.workspace import Workspace
from modules.schemas import ExamSpec, GradeProposal, EssayGradeProposal
from modules.review import RevisionConflict
from modules.http_limits import RequestSizeLimit
from modules.proxy import client_address
from modules.resources import bounded_processing
from modules.essay_grader import propose_grade, ProviderError
from modules.handwriting_ocr import recognize
from modules.reports import frames, excel_bytes, csv_bytes


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Login(Input):
    organization: str = Field(min_length=3, max_length=48)
    email: str = Field(max_length=254)
    password: SecretStr = Field(max_length=128)


class PasswordChange(Input):
    current_password: SecretStr = Field(max_length=128)
    new_password: SecretStr = Field(min_length=12, max_length=128)


class UserCreate(Input):
    email: str = Field(max_length=254)
    name: str = Field(min_length=1, max_length=100)
    temporary_password: SecretStr = Field(min_length=12, max_length=128)
    role: Literal["admin", "teacher", "viewer"]


class UserUpdate(Input):
    role: Literal["admin", "teacher", "viewer"]
    active: bool


class UserReset(Input):
    temporary_password: SecretStr = Field(min_length=12, max_length=128)


class RevisionRequest(Input):
    revision: int = Field(ge=1)


class TextUpdate(RevisionRequest):
    text: str = Field(max_length=30000)


class EssayAnswersUpdate(RevisionRequest):
    answers: dict[str, str]


class Review(Input):
    revision: int = Field(ge=1)
    reviewed: Literal[True]
    reason: str = Field(default="", max_length=1000)


class MCQApproval(Review):
    answers: list[Literal["A", "B", "C", "D"] | None] = Field(min_length=20, max_length=20)


class EssayApproval(Review):
    proposal: GradeProposal | EssayGradeProposal


class AIRequest(Input):
    revision: int = Field(ge=1)
    consent_to_external_processing_and_cost: Literal[True]


class AIPolicy(Input):
    enabled: bool


class ReportJob(Input):
    format: Literal["csv", "xlsx", "pdf"] = "xlsx"


def create_app(root: Path = DATA_DIR):
    service = AuthService(root)
    api = FastAPI(
        title="SmartGrade Commercial API",
        version="1.0.0-rc2",
        docs_url="/docs" if os.getenv("SMARTGRADE_API_DOCS") == "true" else None,
        redoc_url=None,
        openapi_url="/openapi.json" if os.getenv("SMARTGRADE_API_DOCS") == "true" else None,
    )
    api.state.auth = service
    api.state.metrics = {}
    api.add_middleware(RequestSizeLimit)
    security = HTTPBearer(auto_error=False)

    @api.middleware("http")
    async def security_headers(request, call_next):
        import time
        from uuid import uuid4

        started = time.monotonic()
        request_id = uuid4().hex
        response = await call_next(request)
        elapsed = round(time.monotonic() - started, 4)
        organization_id = getattr(request.state, "organization_id", None)
        if organization_id:
            metrics = api.state.metrics.setdefault(
                organization_id, {"requests": 0, "errors": 0, "last_request_seconds": 0}
            )
            metrics["requests"] += 1
            metrics["errors"] += int(response.status_code >= 500)
            metrics["last_request_seconds"] = elapsed
        logging.getLogger("uvicorn.error").info(
            json.dumps(
                {
                    "request_id": request_id,
                    "method": request.method,
                    "route": getattr(request.scope.get("route"), "path", "unmatched"),
                    "status": response.status_code,
                    "seconds": elapsed,
                }
            )
        )
        response.headers["X-Request-ID"] = request_id
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    @api.exception_handler(RequestValidationError)
    async def invalid_request(request, exc):
        # FastAPI defaults may echo input values; never return submitted passwords or tokens.
        return JSONResponse(
            {
                "detail": "راجع الحقول المطلوبة وحدود القيم",
                "fields": [{"location": list(e["loc"]), "type": e["type"]} for e in exc.errors()],
            },
            status_code=422,
        )

    @api.exception_handler(PermissionDenied)
    async def forbidden(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=403)

    @api.exception_handler(LoginRateLimited)
    async def login_throttled(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=429, headers={"Retry-After": "900"})

    @api.exception_handler(AuthError)
    async def unauthenticated(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=401, headers={"WWW-Authenticate": "Bearer"})

    @api.exception_handler(CapacityError)
    async def exhausted(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=429)

    @api.exception_handler(RevisionConflict)
    async def stale_revision(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=409)

    @api.exception_handler(ValueError)
    async def invalid_value(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=400)

    @api.exception_handler(SQLAlchemyError)
    @api.exception_handler(sqlite3.Error)
    async def database_error(request, exc):
        return JSONResponse(
            {"detail": "التخزين غير متاح مؤقتًا؛ أعد المحاولة بالمفتاح نفسه"},
            status_code=503,
            headers={"Retry-After": "5"},
        )

    def token(credentials: HTTPAuthorizationCredentials | None = Depends(security)):
        if not credentials or credentials.scheme.lower() != "bearer":
            raise HTTPException(401, "يلزم تسجيل الدخول", headers={"WWW-Authenticate": "Bearer"})
        return credentials.credentials

    def space(request: Request, value: str = Depends(token)):
        workspace = Workspace(service, value)
        request.state.organization_id = workspace.organization_id
        return workspace

    @api.get("/health")
    def health():
        return {"status": "ok", "version": "1.0.0-rc2"}

    @api.get("/ready")
    def ready():
        from modules.operations import storage_ready

        try:
            available = service.ready() and storage_ready(service.root)
        except (sqlite3.Error, OSError):
            available = False
        if not available:
            raise HTTPException(503, "التخزين غير جاهز")
        return {"status": "ready"}

    @api.post("/auth/login")
    def login(body: Login, request: Request):
        raw = service.login(
            body.organization,
            body.email,
            body.password.get_secret_value(),
            client_id=client_address(request),
        )
        return {
            "access_token": raw,
            "token_type": "bearer",
            "expires_in": service.session_seconds,
            "must_change_password": service.actor(raw, allow_password_change=True).must_change_password,
        }

    @api.post("/auth/logout", status_code=204)
    def logout(value: str = Depends(token)):
        service.logout(value)
        return Response(status_code=204)

    @api.get("/auth/me")
    def me(value: str = Depends(token)):
        return asdict(service.actor(value, allow_password_change=True))

    @api.post("/auth/change-password")
    def password(body: PasswordChange, value: str = Depends(token)):
        service.change_password(
            value, body.current_password.get_secret_value(), body.new_password.get_secret_value()
        )
        return {"sign_in_again": True}

    @api.get("/organization/usage")
    def usage(workspace: Workspace = Depends(space)):
        return workspace.usage()

    def exam_info(exam, include_spec):
        spec = ExamSpec.model_validate(exam.spec)
        return {
            "id": exam.id, "title": exam.title, "kind": exam.kind, "maximum": spec.maximum,
            "question_count": 20 if spec.kind == "mcq" else len(spec.essay_questions()),
            **({"spec": exam.spec} if include_spec else {}),
        }

    @api.get("/exams")
    def exams(workspace: Workspace = Depends(space), limit: int = Query(50, ge=1, le=200),
              offset: int = Query(0, ge=0), include_spec: bool = True):
        actor = service.actor(workspace.token)
        return [exam_info(e, include_spec and actor.can("read")) for e in workspace.exams(limit, offset)]

    @api.get("/exams/{exam_id}")
    def exam_detail(exam_id: str, workspace: Workspace = Depends(space)):
        actor = service.actor(workspace.token)
        return {**exam_info(workspace.exam(exam_id), actor.can("read")), "counts": workspace.counts(exam_id)}

    @api.post("/exams", status_code=201)
    def create_exam(body: ExamSpec, workspace: Workspace = Depends(space)):
        return {"id": workspace.create_exam(body)}

    @api.post("/exams/{exam_id}/submissions", status_code=201)
    async def upload(
        exam_id: str,
        student_code: str = Form(max_length=100),
        idempotency_key: str | None = Header(None, min_length=1, max_length=128),
        file: UploadFile = File(),
        workspace: Workspace = Depends(space),
    ):
        raw = await file.read(MAX_FILE_BYTES + 1)
        await file.close()
        if len(raw) > MAX_FILE_BYTES:
            raise HTTPException(413, "حجم الصورة أكبر من الحد المسموح")
        sid = await run_in_threadpool(
            workspace.upload, exam_id, student_code, raw, idempotency_key=idempotency_key
        )
        return {"id": sid}

    @api.get("/exams/{exam_id}/submissions")
    def submissions(
        exam_id: str,
        workspace: Workspace = Depends(space),
        limit: int = Query(50, ge=1, le=200),
        offset: int = Query(0, ge=0),
        status: Literal["review", "approved", "failed"] | None = None,
    ):
        return [
            {
                "id": r.id,
                "student_code": r.student_code,
                "status": r.status,
                "total": r.total,
                "revision": r.revision,
            }
            for r in workspace.submissions(exam_id, limit=limit, offset=offset, status=status)
        ]

    @api.get("/submissions/{sid}")
    def submission(sid: str, workspace: Workspace = Depends(space)):
        row = workspace.submission(sid)
        return {
            "id": row.id,
            "exam_id": row.exam_id,
            "student_code": row.student_code,
            "text": row.text,
            "status": row.status,
            "revision": row.revision,
            "original": row.original,
            "current": row.current,
            "proposal": row.proposal,
            "approved": row.approved,
            "total": row.total,
        }

    @api.get("/submissions/{sid}/image")
    @bounded_processing
    def image(sid: str, preview_width: int | None = Query(None, ge=200, le=1600),
              workspace: Workspace = Depends(space)):
        row = workspace.submission(sid)
        path = workspace.image_path(row.aligned_name or row.image_name)
        if preview_width:
            import io
            from PIL import Image
            with Image.open(path) as picture:
                picture.thumbnail((preview_width, preview_width * 2))
                output = io.BytesIO()
                picture.save(output, format="PNG")
            return Response(output.getvalue(), media_type="image/png")
        return Response(path.read_bytes(), media_type="image/png")

    @api.put("/submissions/{sid}/text")
    def text(sid: str, body: TextUpdate, workspace: Workspace = Depends(space)):
        workspace.save_text(sid, body.text, "", body.revision)
        return {"revision": workspace.submission(sid).revision}

    @api.put("/submissions/{sid}/essay-answers")
    def essay_answers(sid: str, body: EssayAnswersUpdate, workspace: Workspace = Depends(space)):
        workspace.save_answers(sid, body.answers, "", body.revision)
        return {"revision": workspace.submission(sid).revision}

    @api.post("/submissions/{sid}/ocr")
    @bounded_processing
    def ocr(sid: str, body: RevisionRequest, workspace: Workspace = Depends(space)):
        service.actor(workspace.token, permission="write")
        row = workspace.submission(sid)
        if row.revision != body.revision:
            raise RevisionConflict("تغيرت الإجابة؛ أعد تحميلها قبل تشغيل OCR")
        if workspace.exam(row.exam_id).kind != "essay":
            raise ValueError("استخراج النص مخصص للإجابات المقالية")
        try:
            extracted = recognize(workspace.image_path(row.image_name).read_bytes())
        except ValueError:
            workspace.record_processing_failure(sid, "ocr", body.revision)
            raise
        workspace.save_text(sid, extracted, "", body.revision, source="ocr")
        return {"text": extracted, "revision": workspace.submission(sid).revision}

    @api.post("/submissions/{sid}/proposal")
    def proposal(sid: str, body: AIRequest, workspace: Workspace = Depends(space)):
        service.actor(workspace.token, permission="write")
        if not service.organization(workspace.token)["external_ai_allowed"]:
            raise PermissionDenied("المؤسسة لم تسمح باستخدام مزود خارجي")
        row = workspace.submission(sid)
        if row.revision != body.revision:
            raise RevisionConflict("تغيرت الإجابة؛ أعد تحميلها قبل الطلب")
        spec = ExamSpec.model_validate(workspace.exam(row.exam_id).spec)
        if spec.kind != "essay":
            raise ValueError("اقتراح الدرجات الآلي مخصص للأسئلة المقالية")
        try:
            grade = propose_grade(spec, row.text, current=row.current, consent=body.consent_to_external_processing_and_cost)
        except ProviderError:
            workspace.record_processing_failure(sid, "llm", body.revision)
            raise
        workspace.save_proposal(sid, grade.model_dump(), body.revision)
        return {"proposal": grade.model_dump(), "revision": workspace.submission(sid).revision}

    @api.post("/submissions/{sid}/ocr-jobs", status_code=202)
    def queue_ocr(sid: str, body: RevisionRequest, workspace: Workspace = Depends(space)):
        from modules.jobs import Jobs

        return {"id": Jobs(workspace).create("ocr", {"sid": sid, "revision": body.revision})}

    @api.post("/exams/{exam_id}/report-jobs", status_code=202)
    def queue_report(exam_id: str, body: ReportJob, workspace: Workspace = Depends(space)):
        from modules.jobs import Jobs

        return {"id": Jobs(workspace).create("report", {"exam_id": exam_id, "format": body.format})}

    @api.get("/jobs/{job_id}")
    def job_status(job_id: str, workspace: Workspace = Depends(space)):
        from modules.jobs import Jobs

        row = Jobs(workspace).get(job_id)
        return {
            "id": row.id,
            "kind": row.kind,
            "state": row.state,
            "progress": row.progress,
            "error": row.error,
            "result": row.result,
        }

    @api.post("/jobs/{job_id}/{action}")
    def job_action(job_id: str, action: Literal["cancel", "retry"], workspace: Workspace = Depends(space)):
        from modules.jobs import Jobs

        Jobs(workspace).change(job_id, action)
        return {"updated": True}

    @api.get("/jobs/{job_id}/result")
    def job_result(job_id: str, workspace: Workspace = Depends(space)):
        from modules.jobs import Jobs

        path = Jobs(workspace).result_path(job_id)
        return FileResponse(path, filename=path.name)

    @api.post("/submissions/{sid}/approve-mcq")
    def approve_mcq(sid: str, body: MCQApproval, workspace: Workspace = Depends(space)):
        workspace.approve_mcq(sid, body.answers, "", body.reason, body.revision, body.reviewed)
        row = workspace.submission(sid)
        return {"total": row.total, "revision": row.revision, "status": row.status}

    @api.post("/submissions/{sid}/approve-essay")
    def approve_essay(sid: str, body: EssayApproval, workspace: Workspace = Depends(space)):
        workspace.approve_essay(
            sid, body.proposal.model_dump(), "", body.reason, body.revision, body.reviewed
        )
        row = workspace.submission(sid)
        return {"total": row.total, "revision": row.revision, "status": row.status}

    @api.delete("/submissions/{sid}", status_code=204)
    def delete(sid: str, revision: int, workspace: Workspace = Depends(space)):
        complete = workspace.delete_submission(sid, revision)
        return (
            Response(status_code=204)
            if complete
            else JSONResponse({"status": "deleting", "id": sid}, status_code=202)
        )

    @api.get("/exams/{exam_id}/report.{kind}")
    @bounded_processing
    def report(exam_id: str, kind: Literal["xlsx", "csv", "pdf"], workspace: Workspace = Depends(space)):
        workspace.exam(exam_id)
        if workspace.counts(exam_id)["approved"] > 200:
            raise HTTPException(409, "استخدم report-jobs لإعداد تقرير كبير في الخلفية")
        summary, details = frames(workspace, exam_id)
        if kind == "xlsx":
            content, mime = (
                excel_bytes(summary, details),
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        elif kind == "pdf":
            from modules.pdf_reports import pdf_bytes

            content, mime = (
                pdf_bytes(summary, service.actor(workspace.token).organization_name),
                "application/pdf",
            )
        else:
            content, mime = csv_bytes(summary), "text/csv"
        workspace.record_event("report.exported", exam_id)
        return Response(
            content,
            media_type=mime,
            headers={"Content-Disposition": f'attachment; filename="smartgrade-results.{kind}"'},
        )

    @api.get("/admin/operations")
    def operations(workspace: Workspace = Depends(space)):
        service.actor(workspace.token, permission="audit")
        from modules.operations import tenant_status

        return {
            "http": dict(api.state.metrics.get(workspace.organization_id, {})),
            **tenant_status(workspace._core),
        }

    @api.get("/admin/users")
    def users(value: str = Depends(token)):
        return service.list_users(value)

    @api.post("/admin/users", status_code=201)
    def add_user(body: UserCreate, value: str = Depends(token)):
        return {
            "id": service.create_user(
                value, body.email, body.name, body.temporary_password.get_secret_value(), body.role
            )
        }

    @api.patch("/admin/users/{user_id}")
    def update_user(user_id: str, body: UserUpdate, value: str = Depends(token)):
        service.update_user(value, user_id, role=body.role, active=body.active)
        return {"updated": True, "sessions_revoked": True}

    @api.post("/admin/users/{user_id}/reset-password")
    def reset_user(user_id: str, body: UserReset, value: str = Depends(token)):
        service.reset_password(value, user_id, body.temporary_password.get_secret_value())
        return {"updated": True, "sessions_revoked": True}

    @api.put("/admin/ai-policy")
    def ai_policy(body: AIPolicy, value: str = Depends(token)):
        service.set_ai_policy(value, body.enabled)
        return {"updated": True}

    @api.get("/admin/audit")
    def audit(value: str = Depends(token)):
        return {"events": service.audit_events(value), "chain_valid": service.verify_audit(value)}

    return api


app = create_app()
