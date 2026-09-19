"""Claim and execute queued jobs. Never apply or send mail."""

from __future__ import annotations

import json
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

from hunt.core.context import current_actor
from hunt.core.cv import render as render_cv
from hunt.core.errors import HuntError, NotFoundError
from hunt.core.jobs import Job, claim_job, finish_job, get_job, list_jobs
from hunt.core.listings import upsert_listing
from hunt.core.screen import screen_inbox
from hunt.core.sources import get_source, mark_status
from hunt.core.workspace import Workspace


@dataclass
class _DrainIngest:
    new: int = 0
    polls: int = 0


_drain_ingest: ContextVar[_DrainIngest | None] = ContextVar(
    "hunt_drain_ingest", default=None
)


def _screener_idle() -> dict[str, Any]:
    return {
        "triggered": False,
        "started": False,
        "reason": "no_new_listings",
        "never_apply": True,
        "never_send_mail": True,
    }


def _screener_deferred() -> dict[str, Any]:
    return {
        "triggered": True,
        "started": False,
        "reason": "deferred_to_drain",
        "never_apply": True,
        "never_send_mail": True,
    }


def _attach_screener(ws: Workspace, result: dict[str, Any], new: int) -> None:
    from hunt.agent.wake import maybe_wake_screener

    state = _drain_ingest.get()
    if state is not None:
        state.polls += 1
        state.new += int(new or 0)
        result["screener"] = _screener_deferred() if new > 0 else _screener_idle()
        return
    result["screener"] = maybe_wake_screener(ws, new=new)


def _store_job_result(ws: Workspace, job_id: str, result: dict[str, Any]) -> dict[str, Any]:
    ws.conn.execute(
        "UPDATE jobs SET result_json = ? WHERE id = ?",
        (json.dumps(result), job_id),
    )
    ws.conn.commit()
    return get_job(ws, job_id).to_dict()


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
    knockouts = screen_inbox(ws)
    result = {
        "source_id": source.id,
        "kind": source.kind,
        "listings": len(fetched),
        "new": created,
        "knockouts": knockouts,
    }
    _attach_screener(ws, result, created)
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
    from hunt.agent.wake import maybe_wake_screener

    state = _DrainIngest()
    token = _drain_ingest.set(state)
    out: list[dict[str, Any]] = []
    try:
        while True:
            queued = list_jobs(ws, state="queued")
            if not queued:
                break
            oldest = min(queued, key=lambda job: (job.created_at or "", job.id))
            try:
                out.append(run_one(ws, oldest.id))
            except NotFoundError:
                break
        if state.polls:
            wake = maybe_wake_screener(ws, new=state.new)
            for item in out:
                job = item.get("job") or {}
                if job.get("type") != "source-poll":
                    continue
                result = item.setdefault("result", {})
                if int(result.get("new") or 0) > 0:
                    result["screener"] = wake
                    item["job"] = _store_job_result(ws, job["id"], result)
                elif "screener" not in result:
                    result["screener"] = _screener_idle()
        return out
    finally:
        _drain_ingest.reset(token)
