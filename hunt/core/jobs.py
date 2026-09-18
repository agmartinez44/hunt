"""Job queue. HTTP/CLI/MCP enqueue the same rows; workers claim them.

v1 types: source-poll, screen-inbox, tailor-cv. Hunt never enqueues apply
or send-mail.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from hunt.core.applications import get_application
from hunt.core.errors import HuntError, NotFoundError, ValidationError
from hunt.core.events import append_event
from hunt.core.ids import new_id, now_iso
from hunt.core.workspace import Workspace

JOB_TYPES = ("source-poll", "screen-inbox", "tailor-cv")
JOB_STATES = ("queued", "running", "done", "failed")


@dataclass
class Job:
    id: str
    type: str
    state: str
    target_id: str | None
    payload: dict[str, Any]
    error: str | None
    result: dict[str, Any]
    created_at: str
    started_at: str | None
    finished_at: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": self.type,
            "state": self.state,
            "target_id": self.target_id,
            "payload": dict(self.payload),
            "error": self.error,
            "result": dict(self.result),
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }


def _row_to_job(row) -> Job:
    payload = json.loads(row["payload_json"] or "{}")
    keys = row.keys()
    result_raw = row["result_json"] if "result_json" in keys else "{}"
    result = json.loads(result_raw or "{}")
    return Job(
        id=row["id"],
        type=row["type"],
        state=row["state"],
        target_id=row["target_id"],
        payload=payload if isinstance(payload, dict) else {},
        error=row["error"],
        result=result if isinstance(result, dict) else {},
        created_at=row["created_at"],
        started_at=row["started_at"],
        finished_at=row["finished_at"],
    )


_SELECT = """
SELECT id, type, state, target_id, payload_json, error, result_json,
       created_at, started_at, finished_at
FROM jobs
"""


def list_jobs(
    ws: Workspace,
    *,
    state: str | None = None,
    job_type: str | None = None,
    target_id: str | None = None,
) -> list[Job]:
    clauses: list[str] = []
    params: list[Any] = []
    if state:
        if state not in JOB_STATES:
            raise ValidationError(f"job state must be one of {list(JOB_STATES)}")
        clauses.append("state = ?")
        params.append(state)
    if job_type:
        if job_type not in JOB_TYPES:
            raise ValidationError(f"job type must be one of {list(JOB_TYPES)}")
        clauses.append("type = ?")
        params.append(job_type)
    if target_id:
        clauses.append("target_id = ?")
        params.append(target_id)
    sql = _SELECT
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY created_at DESC, id DESC"
    rows = ws.conn.execute(sql, params).fetchall()
    return [_row_to_job(r) for r in rows]


def get_job(ws: Workspace, job_id: str) -> Job:
    row = ws.conn.execute(_SELECT + " WHERE id = ?", (job_id,)).fetchone()
    if not row:
        raise NotFoundError(f"job not found: {job_id}")
    return _row_to_job(row)


def enqueue(
    ws: Workspace,
    *,
    job_type: str,
    target_id: str | None = None,
    payload: dict[str, Any] | None = None,
) -> Job:
    if job_type not in JOB_TYPES:
        raise ValidationError(
            f"job type must be one of {list(JOB_TYPES)}, got {job_type!r}"
        )
    if job_type == "tailor-cv":
        if not target_id:
            raise ValidationError("tailor-cv requires an application id")
        get_application(ws, target_id)
    elif job_type == "source-poll":
        if not target_id:
            raise ValidationError("source-poll requires a source id")
        from hunt.core.sources import get_source

        get_source(ws, target_id)
    elif target_id:
        raise ValidationError("screen-inbox does not take a target")

    now = now_iso()
    job_id = new_id()
    body = dict(payload or {})
    ws.conn.execute(
        """
        INSERT INTO jobs(
            id, type, state, target_id, payload_json, error, result_json,
            created_at, started_at, finished_at
        ) VALUES (?, ?, 'queued', ?, ?, NULL, '{}', ?, NULL, NULL)
        """,
        (job_id, job_type, target_id, json.dumps(body), now),
    )
    if job_type == "tailor-cv" and target_id:
        append_event(ws, target_id, "job_enqueued", f"{job_type}:{job_id}")
    ws.conn.commit()
    return get_job(ws, job_id)


def active_for(ws: Workspace, job_type: str, target_id: str) -> list[Job]:
    return [
        job
        for job in list_jobs(ws, job_type=job_type, target_id=target_id)
        if job.state in {"queued", "running"}
    ]


def counts(ws: Workspace) -> dict[str, int]:
    queued = ws.conn.execute(
        "SELECT COUNT(*) AS n FROM jobs WHERE state IN ('queued', 'running')"
    ).fetchone()["n"]
    return {"active": int(queued)}


def claim_job(ws: Workspace, job_id: str | None = None) -> Job:
    """Mark a queued job running. ``job_id`` None claims the oldest queued."""
    try:
        ws.conn.execute("BEGIN IMMEDIATE")
        if job_id:
            row = ws.conn.execute(
                _SELECT + " WHERE id = ?", (job_id,)
            ).fetchone()
            if not row:
                ws.conn.rollback()
                raise NotFoundError(f"job not found: {job_id}")
            job = _row_to_job(row)
            if job.state != "queued":
                ws.conn.rollback()
                raise HuntError(f"job {job_id} is {job.state}, not queued")
        else:
            row = ws.conn.execute(
                _SELECT + " WHERE state = 'queued' ORDER BY created_at ASC, id ASC LIMIT 1"
            ).fetchone()
            if not row:
                ws.conn.rollback()
                raise NotFoundError("no queued jobs")
            job = _row_to_job(row)
        now = now_iso()
        ws.conn.execute(
            "UPDATE jobs SET state = 'running', started_at = ? WHERE id = ?",
            (now, job.id),
        )
        ws.conn.commit()
    except HuntError:
        raise
    except Exception:
        ws.conn.rollback()
        raise
    return get_job(ws, job.id)


def finish_job(
    ws: Workspace,
    job_id: str,
    *,
    state: str,
    result: dict[str, Any] | None = None,
    error: str | None = None,
) -> Job:
    if state not in {"done", "failed"}:
        raise ValidationError("job finish state must be done or failed")
    get_job(ws, job_id)
    now = now_iso()
    ws.conn.execute(
        """
        UPDATE jobs
        SET state = ?, finished_at = ?, error = ?, result_json = ?
        WHERE id = ?
        """,
        (state, now, error, json.dumps(result or {}), job_id),
    )
    ws.conn.commit()
    return get_job(ws, job_id)
