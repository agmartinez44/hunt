"""Claim and execute queued jobs. Never apply or send mail."""

from __future__ import annotations

from typing import Any

from hunt.core.context import current_actor
from hunt.core.cv import render as render_cv
from hunt.core.errors import HuntError, NotFoundError
from hunt.core.jobs import Job, claim_job, finish_job, list_jobs
from hunt.core.listings import upsert_listing
from hunt.core.screen import screen_inbox
from hunt.core.sources import get_source, mark_status
from hunt.core.workspace import Workspace


def execute_job(ws: Workspace, job: Job) -> dict[str, Any]:
    if job.type == "source-poll":
        return _run_source_poll(ws, job)
    if job.type == "screen-inbox":
        return screen_inbox(ws)
    if job.type == "tailor-cv":
        return _run_tailor_cv(ws, job)
    raise HuntError(f"unknown job type: {job.type}")


def _run_source_poll(ws: Workspace, job: Job) -> dict[str, Any]:
    source_id = job.target_id or (job.payload or {}).get("source_id")
    if not source_id:
        raise HuntError("source-poll requires a source id")
    source = get_source(ws, source_id)
    if not source.enabled:
        raise HuntError(f"source {source_id} is disabled")
    try:
        from hunt.adapters import poll_source

        fetched = poll_source(ws, source)
        created = 0
        for raw in fetched:
            payload = dict(raw.payload)
            payload["source"] = source.id
            _listing, is_new = upsert_listing(
                ws,
                source_id=source.id,
                external_id=raw.external_id,
                title=raw.title,
                company=raw.company,
                url=raw.url,
                payload=payload,
            )
            if is_new:
                created += 1
        ws.conn.commit()
        mark_status(ws, source.id, "ok")
    except Exception:
        mark_status(ws, source.id, "error")
        raise
    return {
        "source_id": source.id,
        "kind": source.kind,
        "listings": len(fetched),
        "new": created,
    }


def _run_tailor_cv(ws: Workspace, job: Job) -> dict[str, Any]:
    application_id = job.target_id or (job.payload or {}).get("application_id")
    if not application_id:
        raise HuntError("tailor-cv requires an application id")
    payload = job.payload or {}
    return render_cv(
        ws,
        application_id=application_id,
        emphasis=payload.get("emphasis"),
        variant=payload.get("variant"),
    )


def run_one(ws: Workspace, job_id: str | None = None) -> dict[str, Any]:
    token = current_actor.set("job")
    try:
        job = claim_job(ws, job_id)
        try:
            result = execute_job(ws, job)
        except Exception as exc:
            message = str(exc)
            finish_job(ws, job.id, state="failed", error=message)
            if isinstance(exc, HuntError):
                raise
            raise HuntError(f"job {job.id} failed: {message}") from exc
        done = finish_job(ws, job.id, state="done", result=result)
        return {"job": done.to_dict(), "result": result}
    finally:
        current_actor.reset(token)


def drain(ws: Workspace) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    while True:
        queued = list_jobs(ws, state="queued")
        if not queued:
            return out
        oldest = min(queued, key=lambda job: (job.created_at or "", job.id))
        try:
            out.append(run_one(ws, oldest.id))
        except NotFoundError:
            return out
