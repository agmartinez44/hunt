"""Claim and execute queued jobs. Never apply or send mail."""

from __future__ import annotations

from typing import Any

from hunt.agent.config import triage_enabled
from hunt.core.context import current_actor
from hunt.core.cv import render as render_cv
from hunt.core.errors import HuntError, NotFoundError
from hunt.core.jobs import (
    Job,
    claim_job,
    drop_on_codes,
    enqueue_triage_if_needed,
    finish_job,
    list_jobs,
)
from hunt.core.inbox import count_pending, pending_cap
from hunt.core.listings import upsert_listing
from hunt.core.screen import screen_inbox
from hunt.core.sources import get_source, mark_status
from hunt.core.workspace import Workspace


def _poll_screener(ws: Workspace, job: Job | None) -> dict[str, Any]:
    if job:
        reason = "deferred_to_triage"
    elif not triage_enabled(ws):
        reason = "triage_disabled"
    elif not drop_on_codes(ws):
        reason = "no_drop_on"
    else:
        reason = "no_untriaged"
    return {
        "triggered": False,
        "started": False,
        "reason": reason,
        "never_apply": True,
        "never_send_mail": True,
        "triage_job_id": job.id if job else None,
    }


def execute_job(ws: Workspace, job: Job) -> dict[str, Any]:
    if job.type == "source-poll":
        return _run_source_poll(ws, job)
    if job.type == "screen-inbox":
        return screen_inbox(ws)
    if job.type == "triage-inbox":
        from hunt.core.triage import run_triage_inbox

        return run_triage_inbox(ws, job)
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
    knockouts = screen_inbox(ws)
    triage_job = enqueue_triage_if_needed(ws, skip_if_idle=True)
    cap = pending_cap(ws)
    pending_at_end = count_pending(ws)
    skipped_cap_n = int(knockouts.get("skipped_cap") or 0)
    result = {
        "source_id": source.id,
        "kind": source.kind,
        "listings": len(fetched),
        "new": created,
        "knockouts": knockouts,
        "skipped": "cap" if cap and skipped_cap_n > 0 else None,
        "at_cap": bool(cap) and pending_at_end >= cap,
        "triage_job_id": triage_job.id if triage_job else None,
        "screener": _poll_screener(ws, triage_job),
    }
    return result


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
            break
        # Polls first so a drain of N source-polls enqueues at most one triage.
        polls = [job for job in queued if job.type == "source-poll"]
        pool = polls or queued
        oldest = min(pool, key=lambda job: (job.created_at or "", job.id))
        try:
            out.append(run_one(ws, oldest.id))
        except NotFoundError:
            break
    return out
