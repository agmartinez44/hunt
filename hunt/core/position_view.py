"""Same human fields for a role on the board, in the inbox, and on a job."""

from __future__ import annotations

from typing import Any

from hunt.core.applications import Application, get_application
from hunt.core.errors import NotFoundError
from hunt.core.inbox import work_location_label
from hunt.core.jobs import Job
from hunt.core.pay import engagement_label, pay_month_view
from hunt.core.workspace import Workspace


def application_position(ws: Workspace, app: Application) -> dict[str, Any]:
    """Position facts shared with an inbox row."""
    quoted = app.comp_quoted.to_dict() if app.comp_quoted else None
    derived = app.comp_derived.to_dict() if app.comp_derived else None
    payload = {
        "location_city": app.location_city,
        "location_country": app.location_country,
        "modality": app.modality,
        "office_days_per_week": app.office_days_per_week,
    }
    location, location_title = work_location_label(
        payload,
        city=app.location_city,
        country=app.location_country,
    )
    out: dict[str, Any] = {
        "kind": "application",
        "id": app.id,
        "company": app.company,
        "role": app.title_ours or app.title_posted or "",
        "title_posted": app.title_posted,
        "url": app.url,
        "location": location,
        "modality": app.modality,
        "engagement": app.engagement,
        "engagement_label": engagement_label(app.engagement),
        "source": app.source,
        "status": app.status,
        "pay_month": pay_month_view(
            quoted,
            derived,
            display_currency=ws.display_currency,
        ),
    }
    if location_title:
        out["location_title"] = location_title
    return out


def job_subject(ws: Workspace, job: Job) -> dict[str, Any]:
    """Who a queue job is about, in the same shape as a position when it has one."""
    if job.type == "tailor-cv" and job.target_id:
        try:
            app = get_application(ws, job.target_id)
        except NotFoundError:
            return {"kind": "missing", "label": "Application missing", "id": job.target_id}
        return application_position(ws, app)
    if job.type == "source-poll" and job.target_id:
        from hunt.core.sources import get_source

        try:
            source = get_source(ws, job.target_id)
        except NotFoundError:
            return {"kind": "missing", "label": job.target_id, "id": job.target_id}
        return {
            "kind": "source",
            "id": source.id,
            "company": source.name,
            "role": "Source poll",
            "source": source.id,
            "label": source.name,
        }
    if job.type == "screen-inbox":
        return {"kind": "inbox", "label": "Screen inbox", "company": "Inbox", "role": "Screen"}
    if job.type == "triage-inbox":
        return {"kind": "inbox", "label": "Triage inbox", "company": "Inbox", "role": "Triage"}
    return {"kind": "none", "label": job.type}
