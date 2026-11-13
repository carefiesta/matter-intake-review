"""Small local web workbench. Run with python -m intake.web."""

from __future__ import annotations

import hmac
import hashlib
import os
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from intake.access import AccessContext
from intake.constants import (
    CHANNELS,
    DOCUMENT_TYPES,
    DISPOSITIONS,
    FACT_LABELS,
    RELEASED_STATUSES,
)
from intake.errors import AccessDenied, AuthRequired, GateFailed, ValidationError
from intake.masking import mask_fact_value, mask_report_text
from intake.service import IncomingFile, IntakeService

HERE = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(HERE / "templates"))


def session_secret() -> str:
    return os.environ.get("INTAKE_SESSION_SECRET", "dev-session-secret")


def make_token(user_id: str) -> str:
    digest = hmac.new(session_secret().encode(), user_id.encode(), hashlib.sha256).hexdigest()
    return user_id + "." + digest


def read_token(token: str | None) -> str | None:
    if not token or "." not in token:
        return None
    user_id, digest = token.rsplit(".", 1)
    expected = hmac.new(session_secret().encode(), user_id.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, digest):
        return None
    return user_id


def create_app(db_path: str | None = None) -> FastAPI:
    path = db_path or os.environ.get("INTAKE_DB", "data/intake.db")
    app = FastAPI(title="Matter intake review")
    service = IntakeService(path)
    service.init_db()
    app.state.service = service
    app.mount("/static", StaticFiles(directory=str(HERE / "static")), name="static")

    def current_user(request: Request) -> dict | None:
        user_id = read_token(request.cookies.get("session"))
        if not user_id:
            return None
        row = service.conn.execute(
            "SELECT user_id, display_name, role FROM users WHERE user_id = ?",
            (user_id,),
        ).fetchone()
        return dict(row) if row else None

    def context_for(request: Request, matter_id: str | None) -> AccessContext:
        user = current_user(request)
        if user is None:
            raise AuthRequired("Sign in is required")
        return AccessContext(user["user_id"], user["role"], matter_id)

    def page(request: Request, name: str, status_code: int = 200, **extra):
        user = current_user(request)
        payload = {
            "request": request,
            "user": user,
            "message": request.query_params.get("msg"),
            "title": "Matter intake review",
        }
        payload.update(extra)
        return templates.TemplateResponse(request, name, payload, status_code=status_code)

    def go(matter_id: str, message: str) -> RedirectResponse:
        return RedirectResponse(f"/matters/{matter_id}?msg={quote(message)}", status_code=303)

    def fail(request: Request, exc: Exception):
        if isinstance(exc, AuthRequired):
            return RedirectResponse("/login", status_code=303)
        if isinstance(exc, AccessDenied):
            return page(
                request,
                "message.html",
                status_code=403,
                heading="Not allowed",
                text=str(exc),
                link_href="/matters",
                link_label="Back to matters",
            )
        if isinstance(exc, (ValidationError, GateFailed)):
            return page(
                request,
                "message.html",
                status_code=400,
                heading="That did not go through",
                text=str(exc),
                link_href="/matters",
                link_label="Back to matters",
            )
        raise exc

    @app.get("/health")
    def health():
        return {"status": "ok"}

    @app.get("/", response_class=HTMLResponse)
    def root():
        return RedirectResponse("/matters", status_code=303)

    @app.get("/login", response_class=HTMLResponse)
    def login_form(request: Request):
        return page(request, "login.html", title="Sign in")

    @app.post("/login")
    async def login_post(request: Request):
        form = await request.form()
        user = service.authenticate(str(form.get("user_id") or ""), str(form.get("password") or ""))
        if user is None:
            return page(request, "login.html", error="Those credentials were not recognized.", title="Sign in")
        response = RedirectResponse("/matters", status_code=303)
        response.set_cookie("session", make_token(user["user_id"]), httponly=True, samesite="lax")
        return response

    @app.post("/logout")
    def logout():
        response = RedirectResponse("/login", status_code=303)
        response.delete_cookie("session")
        return response

    @app.get("/matters", response_class=HTMLResponse)
    def matter_list(request: Request):
        try:
            ctx = context_for(request, None)
            matters = service.list_matters(ctx)
        except (AuthRequired, AccessDenied) as exc:
            return fail(request, exc)
        return page(
            request,
            "matters.html",
            matters=matters,
            document_types=DOCUMENT_TYPES,
            channels=CHANNELS,
            title="Matters",
        )

    @app.post("/matters/load-samples")
    def load_samples(request: Request):
        try:
            ctx = context_for(request, None)
            service.load_samples(ctx)
        except (AuthRequired, AccessDenied, ValidationError) as exc:
            return fail(request, exc)
        return RedirectResponse("/matters?msg=" + quote("Sample bundles are loaded."), status_code=303)

    @app.post("/matters")
    async def create_matter(request: Request):
        try:
            ctx = context_for(request, None)
            form = await request.form()
            files = []
            for n in range(1, 5):
                upload = form.get(f"file{n}")
                if upload is None or not getattr(upload, "filename", ""):
                    continue
                files.append(
                    IncomingFile(
                        upload.filename,
                        upload.file.read(),
                        str(form.get(f"type{n}") or "") or None,
                        str(form.get(f"channel{n}") or "upload"),
                    )
                )
            matter_id = service.register_bundle(ctx, str(form.get("title") or ""), files, extract_now=True)
        except (AuthRequired, AccessDenied, ValidationError) as exc:
            return fail(request, exc)
        return go(matter_id, "Bundle registered. Candidates are ready for review.")

    def _decorate(facts: list[dict]) -> list[dict]:
        decorated = []
        for fact in facts:
            item = dict(fact)
            item["display_value"] = mask_fact_value(fact["value"], fact["fact_type"])
            item["edit_value"] = "" if fact["sensitive"] else fact["value"]
            item["excerpt_display"] = mask_report_text(fact["excerpt"])
            decorated.append(item)
        return decorated

    @app.get("/matters/{matter_id}", response_class=HTMLResponse)
    def workbench(request: Request, matter_id: str):
        try:
            ctx = context_for(request, matter_id)
            matter = service.get_matter(ctx, matter_id)
            if matter is None:
                return page(
                    request,
                    "message.html",
                    status_code=404,
                    heading="Matter not found",
                    text="That matter is not on your list.",
                    link_href="/matters",
                    link_label="Back to matters",
                )
            gate = None
            if ctx.role != "attorney_reviewer":
                gate = service.evaluate_gate(ctx, matter_id)
            flags = service.list_flags(ctx, matter_id)
            for flag in flags:
                for member in flag["members"]:
                    member["value"] = mask_fact_value(member["value"], flag["fact_type"])
            return page(
                request,
                "matter.html",
                title=matter["title"],
                matter=matter,
                documents=service.list_documents(ctx, matter_id),
                candidates=_decorate(service.list_facts(ctx, matter_id, "candidate")),
                approved=_decorate(service.list_facts(ctx, matter_id, "approved")),
                flags=flags,
                quotes=service.list_quotes(ctx, matter_id),
                gate=gate,
                dispositions=DISPOSITIONS,
                document_types=DOCUMENT_TYPES,
                channels=CHANNELS,
                labels=FACT_LABELS,
                released=RELEASED_STATUSES,
            )
        except (AuthRequired, AccessDenied, ValidationError) as exc:
            return fail(request, exc)

    @app.post("/matters/{matter_id}/accept")
    async def accept(request: Request, matter_id: str):
        try:
            ctx = context_for(request, matter_id)
            form = await request.form()
            fact_ids = [value for value in form.getlist("fact_id") if value]
            if not fact_ids:
                raise ValidationError("Check at least one candidate")
            service.accept_facts(ctx, matter_id, fact_ids, str(form.get("reason") or ""))
        except (AuthRequired, AccessDenied, ValidationError, GateFailed) as exc:
            return fail(request, exc)
        return go(matter_id, "Accepted the checked candidates.")

    @app.post("/matters/{matter_id}/edit")
    async def edit(request: Request, matter_id: str):
        try:
            ctx = context_for(request, matter_id)
            form = await request.form()
            service.edit_fact(
                ctx,
                matter_id,
                str(form.get("fact_id") or ""),
                str(form.get("new_value") or ""),
                str(form.get("reason") or ""),
            )
        except (AuthRequired, AccessDenied, ValidationError) as exc:
            return fail(request, exc)
        return go(matter_id, "Correction saved and approved.")

    @app.post("/matters/{matter_id}/reject")
    async def reject(request: Request, matter_id: str):
        try:
            ctx = context_for(request, matter_id)
            form = await request.form()
            service.reject_fact(
                ctx,
                matter_id,
                str(form.get("fact_id") or ""),
                str(form.get("reason") or ""),
            )
        except (AuthRequired, AccessDenied, ValidationError) as exc:
            return fail(request, exc)
        return go(matter_id, "Candidate rejected. It stays in the history.")

    @app.post("/matters/{matter_id}/split")
    async def split(request: Request, matter_id: str):
        try:
            ctx = context_for(request, matter_id)
            form = await request.form()
            service.split_fact(
                ctx,
                matter_id,
                str(form.get("fact_id") or ""),
                [str(form.get("value_a") or ""), str(form.get("value_b") or "")],
                str(form.get("reason") or ""),
            )
        except (AuthRequired, AccessDenied, ValidationError) as exc:
            return fail(request, exc)
        return go(matter_id, "Split recorded. The new pieces are still candidates.")

    @app.post("/matters/{matter_id}/dispose")
    async def dispose(request: Request, matter_id: str):
        try:
            ctx = context_for(request, matter_id)
            form = await request.form()
            service.dispose_flag(
                ctx,
                matter_id,
                str(form.get("flag_id") or ""),
                str(form.get("disposition") or ""),
                str(form.get("reason") or ""),
            )
        except (AuthRequired, AccessDenied, ValidationError) as exc:
            return fail(request, exc)
        return go(matter_id, "Disposition saved.")

    @app.post("/matters/{matter_id}/gate")
    def check_gate(request: Request, matter_id: str):
        try:
            ctx = context_for(request, matter_id)
            result = service.evaluate_gate(ctx, matter_id)
        except (AuthRequired, AccessDenied, ValidationError) as exc:
            return fail(request, exc)
        if result["passed"]:
            return go(matter_id, "The gate passes.")
        return go(matter_id, "Ready is blocked: " + "; ".join(result["reasons"]))

    @app.post("/matters/{matter_id}/ready")
    async def ready(request: Request, matter_id: str):
        try:
            ctx = context_for(request, matter_id)
            form = await request.form()
            service.mark_ready(ctx, matter_id, str(form.get("reason") or ""))
        except (AuthRequired, AccessDenied, ValidationError, GateFailed) as exc:
            if isinstance(exc, GateFailed):
                return go(matter_id, "Ready is blocked: " + "; ".join(exc.reasons))
            return fail(request, exc)
        return go(matter_id, "Marked ready for attorney.")

    @app.post("/matters/{matter_id}/add")
    async def add_file(request: Request, matter_id: str):
        try:
            ctx = context_for(request, matter_id)
            form = await request.form()
            upload = form.get("extra")
            if upload is None or not getattr(upload, "filename", ""):
                raise ValidationError("Choose a file")
            service.add_document(
                ctx,
                matter_id,
                IncomingFile(
                    upload.filename,
                    upload.file.read(),
                    str(form.get("document_type") or "") or None,
                    str(form.get("channel") or "upload"),
                ),
            )
        except (AuthRequired, AccessDenied, ValidationError) as exc:
            return fail(request, exc)
        return go(matter_id, "File added. Flags were checked again.")

    @app.post("/matters/{matter_id}/reveal")
    async def reveal(request: Request, matter_id: str):
        try:
            ctx = context_for(request, matter_id)
            form = await request.form()
            value = service.reveal(
                ctx,
                matter_id,
                str(form.get("fact_id") or ""),
                str(form.get("reason") or ""),
            )
        except (AuthRequired, AccessDenied, ValidationError) as exc:
            return fail(request, exc)
        return page(
            request,
            "message.html",
            heading="Revealed value",
            text=value,
            link_href=f"/matters/{matter_id}",
            link_label="Back to the matter",
        )

    @app.get("/matters/{matter_id}/report", response_class=HTMLResponse)
    def report(request: Request, matter_id: str, audience: str = "staff"):
        try:
            ctx = context_for(request, matter_id)
            if service.get_matter(ctx, matter_id) is None:
                return page(
                    request,
                    "message.html",
                    status_code=404,
                    heading="Matter not found",
                    text="That matter is not on your list.",
                    link_href="/matters",
                    link_label="Back to matters",
                )
            body = service.compose_report(ctx, matter_id, audience)
        except (AuthRequired, AccessDenied, ValidationError) as exc:
            return fail(request, exc)
        return page(request, "report.html", title="Intake report", matter_id=matter_id, report=body)

    @app.get("/matters/{matter_id}/audit", response_class=HTMLResponse)
    def audit(request: Request, matter_id: str):
        try:
            ctx = context_for(request, matter_id)
            if service.get_matter(ctx, matter_id) is None:
                return page(
                    request,
                    "message.html",
                    status_code=404,
                    heading="Matter not found",
                    text="That matter is not on your list.",
                    link_href="/matters",
                    link_label="Back to matters",
                )
            rows = service.list_audit(ctx, matter_id)
        except (AuthRequired, AccessDenied) as exc:
            return fail(request, exc)
        return page(request, "audit.html", title="Audit log", matter_id=matter_id, rows=rows)

    @app.get("/matters/{matter_id}/source/{document_id}")
    def source(request: Request, matter_id: str, document_id: str):
        try:
            ctx = context_for(request, matter_id)
            opened = service.open_source(ctx, matter_id, document_id)
        except (AuthRequired, AccessDenied) as exc:
            return fail(request, exc)
        if opened is None:
            return PlainTextResponse("Source not available.", status_code=404)
        if opened["text_content"] is not None:
            return PlainTextResponse(opened["text_content"])
        return PlainTextResponse(
            "Stored original bytes. This file type is not shown as text.\n"
            f"Filename: {opened['filename']}\nBytes: {len(opened['content'])}\n"
        )

    @app.post("/matters/{matter_id}/acknowledge")
    def acknowledge(request: Request, matter_id: str):
        try:
            ctx = context_for(request, matter_id)
            service.acknowledge(ctx, matter_id)
        except (AuthRequired, AccessDenied, ValidationError) as exc:
            return fail(request, exc)
        return go(matter_id, "Acknowledgment recorded. No facts were changed.")

    @app.post("/matters/{matter_id}/return")
    async def return_report(request: Request, matter_id: str):
        try:
            ctx = context_for(request, matter_id)
            form = await request.form()
            service.return_report(ctx, matter_id, str(form.get("reason") or ""))
        except (AuthRequired, AccessDenied, ValidationError) as exc:
            return fail(request, exc)
        return go(matter_id, "Report returned for revision.")

    return app


app = create_app()


def main() -> None:
    import uvicorn

    uvicorn.run(
        "intake.web:app",
        host="127.0.0.1",
        port=int(os.environ.get("PORT", "8000")),
        reload=False,
    )


if __name__ == "__main__":
    main()
