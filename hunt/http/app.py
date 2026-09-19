"""FastAPI bound to 127.0.0.1 by default. Optional token from config.

Routes map 1:1 onto hunt.core. The browser is not a second write path.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, File, Form, Header, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware

from hunt.core.applications import (
    APPLICATION_STATUSES,
    create_application,
    get_application,
    list_applications,
    update_application,
)
from hunt.core.artifacts import add_file, list_artifacts
from hunt.core.context import current_actor
from hunt.core.errors import ConflictError, HuntError, NotFoundError, ValidationError
from hunt.core.events import list_events
from hunt.core.facts import (
    ACHIEVEMENTS_FILE,
    INTEGRITY_FILE,
    POSITIONS_FILE,
    PROFILE_FILE,
    PROJECTS_FILE,
    SKILLS_FILE,
    confirm_achievement,
    confirm_position,
    create_achievement,
    create_position,
    create_project,
    file_revision,
    get_achievement,
    get_integrity,
    get_position,
    get_profile,
    get_project,
    get_skills,
    list_achievements,
    list_positions,
    list_projects,
    require_revision,
    update_achievement,
    update_certifications,
    update_integrity,
    update_position,
    update_profile,
    update_project,
    update_skills,
)
from hunt.core.inbox import (
    dismiss,
    list_inbox,
    promote,
    restore,
    serialize_inbox_item,
)
from hunt.core.jobs import enqueue as enqueue_job, get_job, list_jobs
from hunt.core.sources import list_sources, run_source
from hunt.core.workspace import Workspace, WorkspaceError, resolve_data_dir

STATIC_DIR = Path(__file__).resolve().parent / "static"

CREATE_FIELDS = (
    "company",
    "source",
    "url",
    "title_posted",
    "title_ours",
    "location_country",
    "location_city",
    "modality",
    "office_days_per_week",
    "engagement",
    "duration_months",
    "comp_amount",
    "comp_currency",
    "comp_unit",
    "comp_notes",
    "tax_home_for_net",
    "languages_required",
    "recruiter",
    "cv_variant_id",
    "knockouts",
    "extra",
    "status",
)
UPDATE_FIELDS = CREATE_FIELDS
FORBIDDEN_CREATE_KEYS = {
    "listing_id",
    "listingId",
    "inbox_id",
    "inbox_item_id",
    "application_id",
}


class ActorMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        token = current_actor.set("ui")
        try:
            return await call_next(request)
        finally:
            current_actor.reset(token)


def _error(status: int, message: str) -> JSONResponse:
    return JSONResponse({"error": message}, status_code=status)


def _workspace_meta(ws: Workspace) -> dict[str, Any]:
    return {
        "workspace": ws.root.name,
        "profile_name": ws.profile_name(),
        "bind": ws.bind,
        "port": ws.port,
        "auth_required": bool(ws.auth_token),
        "display_currency": ws.display_currency,
        "fx_as_of": ws.fx_as_of,
    }


def _quoted_from_body(body: dict[str, Any]) -> dict[str, Any]:
    quoted = body.get("comp_quoted")
    if isinstance(quoted, dict):
        if "comp_amount" not in body:
            body["comp_amount"] = quoted.get("amount")
        if "comp_currency" not in body:
            body["comp_currency"] = quoted.get("currency")
        if "comp_unit" not in body:
            body["comp_unit"] = quoted.get("unit")
    return body


def _pick(body: dict[str, Any], allowed: tuple[str, ...]) -> dict[str, Any]:
    return {key: body[key] for key in allowed if key in body}


def create_app(data_dir: str | Path | None = None) -> FastAPI:
    resolved = resolve_data_dir(data_dir) if data_dir else None

    app = FastAPI(title="Hunt", docs_url=None, redoc_url=None)
    app.add_middleware(ActorMiddleware)

    def open_ws() -> Workspace:
        return Workspace.open(resolved)

    def require_auth(
        authorization: str | None = Header(default=None),
    ) -> None:
        with open_ws() as ws:
            expected = ws.auth_token
        if not expected:
            return
        if not authorization or not authorization.startswith("Bearer "):
            raise HuntError("authorization required")
        got = authorization.removeprefix("Bearer ").strip()
        if got != expected:
            raise HuntError("invalid token")

    @app.exception_handler(NotFoundError)
    async def _not_found(_request: Request, exc: NotFoundError) -> JSONResponse:
        return _error(404, str(exc))

    @app.exception_handler(ValidationError)
    async def _validation(_request: Request, exc: ValidationError) -> JSONResponse:
        return _error(400, str(exc))

    @app.exception_handler(ConflictError)
    async def _conflict(_request: Request, exc: ConflictError) -> JSONResponse:
        body: dict[str, Any] = {"error": str(exc)}
        if exc.revision:
            body["revision"] = exc.revision
        return JSONResponse(body, status_code=409)

    @app.exception_handler(WorkspaceError)
    async def _workspace(_request: Request, exc: WorkspaceError) -> JSONResponse:
        return _error(500, str(exc))

    @app.exception_handler(HuntError)
    async def _hunt(_request: Request, exc: HuntError) -> JSONResponse:
        message = str(exc)
        status = 401 if "authorization" in message or "token" in message else 400
        return _error(status, message)

    @app.get("/api/meta")
    def api_meta() -> dict[str, Any]:
        with open_ws() as ws:
            return _workspace_meta(ws)

    @app.get("/api/applications")
    def api_list_applications(
        status: str | None = None, _: None = Depends(require_auth)
    ) -> dict[str, Any]:
        with open_ws() as ws:
            apps = [a.to_dict() for a in list_applications(ws, status=status)]
        return {"applications": apps}

    @app.post("/api/applications")
    async def api_create_application(
        request: Request, _: None = Depends(require_auth)
    ) -> dict[str, Any]:
        body = await request.json()
        if not isinstance(body, dict):
            raise ValidationError("expected a JSON object")
        forbidden = FORBIDDEN_CREATE_KEYS.intersection(body)
        if forbidden:
            raise ValidationError(
                "applications.create does not accept a listing id; "
                "promote from inbox"
            )
        unknown = set(body) - set(CREATE_FIELDS) - {"comp_quoted"}
        if unknown:
            raise ValidationError(f"unknown application fields: {sorted(unknown)}")
        body = _quoted_from_body(body)
        fields = _pick(body, CREATE_FIELDS)
        fields["status"] = "researching"
        if not fields.get("company"):
            raise ValidationError("company is required")
        with open_ws() as ws:
            app = create_application(ws, **fields).to_dict()
        return {"application": app}

    @app.get("/api/applications/{application_id}")
    def api_get_application(
        application_id: str, _: None = Depends(require_auth)
    ) -> dict[str, Any]:
        with open_ws() as ws:
            app = get_application(ws, application_id).to_dict()
        return {"application": app}

    @app.patch("/api/applications/{application_id}")
    async def api_update_application(
        application_id: str, request: Request, _: None = Depends(require_auth)
    ) -> dict[str, Any]:
        body = await request.json()
        if not isinstance(body, dict):
            raise ValidationError("expected a JSON object")
        if FORBIDDEN_CREATE_KEYS.intersection(body):
            raise ValidationError(
                "applications.update does not accept a listing id; "
                "promote from inbox"
            )
        unknown = set(body) - set(UPDATE_FIELDS) - {"comp_quoted"}
        if unknown:
            raise ValidationError(f"unknown application fields: {sorted(unknown)}")
        body = _quoted_from_body(body)
        fields = _pick(body, UPDATE_FIELDS)
        if "status" in fields and fields["status"] not in APPLICATION_STATUSES:
            raise ValidationError(
                f"status must be one of {list(APPLICATION_STATUSES)}"
            )
        with open_ws() as ws:
            app = update_application(ws, application_id, **fields).to_dict()
        return {"application": app}

    @app.get("/api/applications/{application_id}/events")
    def api_list_events(
        application_id: str, _: None = Depends(require_auth)
    ) -> dict[str, Any]:
        with open_ws() as ws:
            events = [e.to_dict() for e in list_events(ws, application_id)]
        return {"events": events}

    @app.get("/api/applications/{application_id}/artifacts")
    def api_list_artifacts(
        application_id: str, _: None = Depends(require_auth)
    ) -> dict[str, Any]:
        with open_ws() as ws:
            arts = [a.to_dict() for a in list_artifacts(ws, application_id)]
        return {"artifacts": arts}

    @app.post("/api/applications/{application_id}/artifacts")
    async def api_add_artifact(
        application_id: str,
        file: UploadFile = File(...),
        kind: str = Form("other"),
        _: None = Depends(require_auth),
    ) -> dict[str, Any]:
        data = await file.read()
        name = Path(file.filename or "upload").name
        with open_ws() as ws:
            dest_dir = ws.root / "attachments" / "applications" / application_id
            dest_dir.mkdir(parents=True, exist_ok=True)
            tmp = dest_dir / f".upload-{name}"
            tmp.write_bytes(data)
            try:
                art = add_file(
                    ws, application_id, tmp, kind=kind or "other", filename=name
                ).to_dict()
            finally:
                if tmp.exists():
                    tmp.unlink()
        return {"artifact": art}

    @app.get("/api/applications/{application_id}/artifacts/{artifact_id}/file")
    def api_artifact_file(
        application_id: str, artifact_id: str, _: None = Depends(require_auth)
    ) -> FileResponse:
        with open_ws() as ws:
            arts = list_artifacts(ws, application_id)
            match = next((a for a in arts if a.id == artifact_id), None)
            if not match:
                raise NotFoundError(f"artifact not found: {artifact_id}")
            path = Path(match.path)
            if not path.is_file():
                raise NotFoundError(f"artifact file missing: {match.filename}")
            return FileResponse(path, filename=match.filename)

    @app.get("/api/inbox")
    def api_list_inbox(
        status: str | None = "pending", _: None = Depends(require_auth)
    ) -> dict[str, Any]:
        with open_ws() as ws:
            items = [serialize_inbox_item(ws, i) for i in list_inbox(ws, status=status)]
        return {"inbox": items}

    @app.post("/api/inbox/{item_id}/promote")
    async def api_promote(
        item_id: str, request: Request, _: None = Depends(require_auth)
    ) -> dict[str, Any]:
        raw = await request.body()
        body = json.loads(raw) if raw else {}
        if not isinstance(body, dict):
            raise ValidationError("expected a JSON object")
        body = _quoted_from_body(body)
        fields = _pick(body, UPDATE_FIELDS)
        with open_ws() as ws:
            app = promote(ws, item_id, **fields).to_dict()
        return {"application": app}

    @app.post("/api/inbox/{item_id}/dismiss")
    def api_dismiss(item_id: str, _: None = Depends(require_auth)) -> dict[str, Any]:
        with open_ws() as ws:
            item = serialize_inbox_item(ws, dismiss(ws, item_id))
        return {"inbox_item": item}

    @app.post("/api/inbox/{item_id}/restore")
    def api_restore(item_id: str, _: None = Depends(require_auth)) -> dict[str, Any]:
        with open_ws() as ws:
            item = serialize_inbox_item(ws, restore(ws, item_id))
        return {"inbox_item": item}

    @app.get("/api/sources")
    def api_list_sources(_: None = Depends(require_auth)) -> dict[str, Any]:
        with open_ws() as ws:
            sources = [s.to_dict() for s in list_sources(ws)]
        return {"sources": sources}

    @app.post("/api/sources/{source_id}/run")
    def api_run_source(
        source_id: str, _: None = Depends(require_auth)
    ) -> dict[str, Any]:
        with open_ws() as ws:
            job = run_source(ws, source_id).to_dict()
        return {"job": job}

    @app.get("/api/jobs")
    def api_list_jobs(
        state: str | None = None,
        type: str | None = None,
        target: str | None = None,
        _: None = Depends(require_auth),
    ) -> dict[str, Any]:
        with open_ws() as ws:
            jobs = [
                j.to_dict()
                for j in list_jobs(ws, state=state, job_type=type, target_id=target)
            ]
        return {"jobs": jobs}

    @app.post("/api/jobs")
    async def api_enqueue_job(
        request: Request, _: None = Depends(require_auth)
    ) -> dict[str, Any]:
        body = await request.json()
        if not isinstance(body, dict):
            raise ValidationError("expected a JSON object")
        job_type = body.get("type")
        if not job_type:
            raise ValidationError("type is required")
        with open_ws() as ws:
            job = enqueue_job(
                ws,
                job_type=str(job_type),
                target_id=body.get("target_id") or body.get("target"),
                payload=body.get("payload") if isinstance(body.get("payload"), dict) else None,
            ).to_dict()
        return {"job": job}

    @app.get("/api/jobs/{job_id}")
    def api_get_job(job_id: str, _: None = Depends(require_auth)) -> dict[str, Any]:
        with open_ws() as ws:
            job = get_job(ws, job_id).to_dict()
        return {"job": job}

    def _json_object(body: Any) -> dict[str, Any]:
        if not isinstance(body, dict):
            raise ValidationError("expected a JSON object")
        return body

    def _if_match(request: Request) -> str | None:
        raw = request.headers.get("if-match")
        if not raw:
            return None
        return raw.strip().strip('"')

    def _rev(ws: Workspace, name: str) -> str:
        return file_revision(ws, name)

    @app.get("/api/profile")
    def api_get_profile(_: None = Depends(require_auth)) -> dict[str, Any]:
        with open_ws() as ws:
            return {"profile": get_profile(ws), "revision": _rev(ws, PROFILE_FILE)}

    @app.patch("/api/profile")
    async def api_update_profile(
        request: Request, _: None = Depends(require_auth)
    ) -> dict[str, Any]:
        body = _json_object(await request.json())
        with open_ws() as ws:
            require_revision(ws, PROFILE_FILE, _if_match(request))
            profile = update_profile(ws, body)
            return {"profile": profile, "revision": _rev(ws, PROFILE_FILE)}

    @app.get("/api/positions")
    def api_list_positions(_: None = Depends(require_auth)) -> dict[str, Any]:
        with open_ws() as ws:
            return {
                "positions": list_positions(ws),
                "revision": _rev(ws, POSITIONS_FILE),
            }

    @app.post("/api/positions")
    async def api_create_position(
        request: Request, _: None = Depends(require_auth)
    ) -> dict[str, Any]:
        body = _json_object(await request.json())
        with open_ws() as ws:
            require_revision(ws, POSITIONS_FILE, _if_match(request))
            position = create_position(ws, body)
            return {"position": position, "revision": _rev(ws, POSITIONS_FILE)}

    @app.get("/api/positions/{position_id}")
    def api_get_position(
        position_id: str, _: None = Depends(require_auth)
    ) -> dict[str, Any]:
        with open_ws() as ws:
            return {
                "position": get_position(ws, position_id),
                "revision": _rev(ws, POSITIONS_FILE),
            }

    @app.patch("/api/positions/{position_id}")
    async def api_update_position(
        position_id: str, request: Request, _: None = Depends(require_auth)
    ) -> dict[str, Any]:
        body = _json_object(await request.json())
        with open_ws() as ws:
            require_revision(ws, POSITIONS_FILE, _if_match(request))
            position = update_position(ws, position_id, body)
            return {"position": position, "revision": _rev(ws, POSITIONS_FILE)}

    @app.post("/api/positions/{position_id}/confirm")
    async def api_confirm_position(
        position_id: str, request: Request, _: None = Depends(require_auth)
    ) -> dict[str, Any]:
        with open_ws() as ws:
            require_revision(ws, POSITIONS_FILE, _if_match(request))
            position = confirm_position(ws, position_id)
            return {"position": position, "revision": _rev(ws, POSITIONS_FILE)}

    @app.get("/api/achievements")
    def api_list_achievements(_: None = Depends(require_auth)) -> dict[str, Any]:
        with open_ws() as ws:
            return {
                "achievements": list_achievements(ws),
                "revision": _rev(ws, ACHIEVEMENTS_FILE),
            }

    @app.post("/api/achievements")
    async def api_create_achievement(
        request: Request, _: None = Depends(require_auth)
    ) -> dict[str, Any]:
        body = _json_object(await request.json())
        with open_ws() as ws:
            require_revision(ws, ACHIEVEMENTS_FILE, _if_match(request))
            achievement = create_achievement(ws, body)
            return {
                "achievement": achievement,
                "revision": _rev(ws, ACHIEVEMENTS_FILE),
            }

    @app.get("/api/achievements/{achievement_id}")
    def api_get_achievement(
        achievement_id: str, _: None = Depends(require_auth)
    ) -> dict[str, Any]:
        with open_ws() as ws:
            return {
                "achievement": get_achievement(ws, achievement_id),
                "revision": _rev(ws, ACHIEVEMENTS_FILE),
            }

    @app.patch("/api/achievements/{achievement_id}")
    async def api_update_achievement(
        achievement_id: str, request: Request, _: None = Depends(require_auth)
    ) -> dict[str, Any]:
        body = _json_object(await request.json())
        with open_ws() as ws:
            require_revision(ws, ACHIEVEMENTS_FILE, _if_match(request))
            achievement = update_achievement(ws, achievement_id, body)
            return {
                "achievement": achievement,
                "revision": _rev(ws, ACHIEVEMENTS_FILE),
            }

    @app.post("/api/achievements/{achievement_id}/confirm")
    async def api_confirm_achievement(
        achievement_id: str, request: Request, _: None = Depends(require_auth)
    ) -> dict[str, Any]:
        with open_ws() as ws:
            require_revision(ws, ACHIEVEMENTS_FILE, _if_match(request))
            achievement = confirm_achievement(ws, achievement_id)
            return {
                "achievement": achievement,
                "revision": _rev(ws, ACHIEVEMENTS_FILE),
            }

    @app.get("/api/projects")
    def api_list_projects(_: None = Depends(require_auth)) -> dict[str, Any]:
        with open_ws() as ws:
            payload = list_projects(ws)
            payload["revision"] = _rev(ws, PROJECTS_FILE)
            return payload

    @app.post("/api/projects")
    async def api_create_project(
        request: Request, _: None = Depends(require_auth)
    ) -> dict[str, Any]:
        body = _json_object(await request.json())
        with open_ws() as ws:
            require_revision(ws, PROJECTS_FILE, _if_match(request))
            project = create_project(ws, body)
            return {"project": project, "revision": _rev(ws, PROJECTS_FILE)}

    @app.get("/api/projects/{project_id}")
    def api_get_project(
        project_id: str, _: None = Depends(require_auth)
    ) -> dict[str, Any]:
        with open_ws() as ws:
            return {
                "project": get_project(ws, project_id),
                "revision": _rev(ws, PROJECTS_FILE),
            }

    @app.patch("/api/projects/{project_id}")
    async def api_update_project(
        project_id: str, request: Request, _: None = Depends(require_auth)
    ) -> dict[str, Any]:
        body = _json_object(await request.json())
        with open_ws() as ws:
            require_revision(ws, PROJECTS_FILE, _if_match(request))
            project = update_project(ws, project_id, body)
            return {"project": project, "revision": _rev(ws, PROJECTS_FILE)}

    @app.patch("/api/certifications")
    async def api_update_certifications(
        request: Request, _: None = Depends(require_auth)
    ) -> dict[str, Any]:
        body = _json_object(await request.json())
        certs = body.get("certifications")
        if not isinstance(certs, list):
            raise ValidationError("certifications must be a list")
        with open_ws() as ws:
            require_revision(ws, PROJECTS_FILE, _if_match(request))
            payload = update_certifications(ws, certs)
            payload["revision"] = _rev(ws, PROJECTS_FILE)
            return payload

    @app.get("/api/skills")
    def api_get_skills(_: None = Depends(require_auth)) -> dict[str, Any]:
        with open_ws() as ws:
            return {"skills": get_skills(ws), "revision": _rev(ws, SKILLS_FILE)}

    @app.patch("/api/skills")
    async def api_update_skills(
        request: Request, _: None = Depends(require_auth)
    ) -> dict[str, Any]:
        body = _json_object(await request.json())
        with open_ws() as ws:
            require_revision(ws, SKILLS_FILE, _if_match(request))
            skills = update_skills(ws, body)
            return {"skills": skills, "revision": _rev(ws, SKILLS_FILE)}

    @app.get("/api/integrity")
    def api_get_integrity(_: None = Depends(require_auth)) -> dict[str, Any]:
        with open_ws() as ws:
            return {
                "integrity": get_integrity(ws),
                "revision": _rev(ws, INTEGRITY_FILE),
            }

    @app.patch("/api/integrity")
    async def api_update_integrity(
        request: Request, _: None = Depends(require_auth)
    ) -> dict[str, Any]:
        body = _json_object(await request.json())
        with open_ws() as ws:
            require_revision(ws, INTEGRITY_FILE, _if_match(request))
            integrity = update_integrity(ws, body)
            return {"integrity": integrity, "revision": _rev(ws, INTEGRITY_FILE)}

    @app.get("/api/agent")
    def api_agent_status(
        harness: str | None = None, _: None = Depends(require_auth)
    ) -> dict[str, Any]:
        from hunt.agent.status import agent_status

        with open_ws() as ws:
            return agent_status(ws, harness=harness)

    @app.patch("/api/agent")
    async def api_agent_config(
        request: Request, _: None = Depends(require_auth)
    ) -> dict[str, Any]:
        from hunt.agent.status import save_agent

        body = _json_object(await request.json())
        unknown = set(body) - {"harness", "model"}
        if unknown:
            raise ValidationError(f"unknown agent fields: {sorted(unknown)}")
        model = body.get("model")
        if model is not None and not isinstance(model, dict):
            raise ValidationError("model must be an object")
        if isinstance(model, dict):
            unknown_model = set(model) - {"base_url", "api_key_env", "model"}
            if unknown_model:
                raise ValidationError(f"unknown model fields: {sorted(unknown_model)}")
        with open_ws() as ws:
            return save_agent(
                ws, harness=body.get("harness"), model=model
            )

    @app.post("/api/agent/install")
    async def api_agent_install(
        request: Request, _: None = Depends(require_auth)
    ) -> dict[str, Any]:
        from hunt.agent.status import install_and_status

        body = _json_object(await request.json())
        harness = body.get("harness")
        if not harness:
            raise ValidationError("harness is required")
        with open_ws() as ws:
            return install_and_status(
                ws,
                str(harness),
                role=body.get("role"),
            )

    @app.post("/api/agent/doctor")
    async def api_agent_doctor(
        request: Request, _: None = Depends(require_auth)
    ) -> dict[str, Any]:
        from hunt.agent.status import agent_status

        raw = await request.body()
        body = json.loads(raw) if raw else {}
        if body and not isinstance(body, dict):
            raise ValidationError("expected a JSON object")
        timeout = 3.0
        if isinstance(body, dict) and body.get("timeout") is not None:
            timeout = float(body["timeout"])
        with open_ws() as ws:
            return agent_status(ws, run_check=True, timeout=timeout)

    @app.put("/api/agent/secret")
    async def api_agent_secret_set(
        request: Request, _: None = Depends(require_auth)
    ) -> dict[str, Any]:
        from hunt.agent.status import set_agent_secret

        body = _json_object(await request.json())
        env_name = body.get("env")
        if not env_name:
            raise ValidationError("env is required")
        if "value" not in body:
            raise ValidationError("value is required")
        with open_ws() as ws:
            return set_agent_secret(ws, str(env_name), str(body.get("value") or ""))

    @app.delete("/api/agent/secret")
    def api_agent_secret_unset(
        env: str, _: None = Depends(require_auth)
    ) -> dict[str, Any]:
        from hunt.agent.status import unset_agent_secret

        if not env:
            raise ValidationError("env is required")
        with open_ws() as ws:
            return unset_agent_secret(ws, env)

    if STATIC_DIR.is_dir():
        app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/{path:path}")
    def spa(path: str) -> FileResponse:
        if path.startswith("api/"):
            raise NotFoundError("not found")
        static_file = STATIC_DIR / path
        if static_file.is_file():
            return FileResponse(static_file)
        return FileResponse(STATIC_DIR / "index.html")

    return app


def serve(
    data_dir: str | Path | None = None,
    host: str | None = None,
    port: int | None = None,
) -> None:
    import uvicorn

    ws = Workspace.open(data_dir)
    bind = host or ws.bind or "127.0.0.1"
    listen = int(port if port is not None else ws.port)
    root = str(ws.root)
    ws.close()
    os.environ.setdefault("HUNT_DATA", root)
    app = create_app(root)
    uvicorn.run(app, host=bind, port=listen, log_level="info")
