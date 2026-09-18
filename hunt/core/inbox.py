"""Inbox: screened listings. Promote is explicit; nothing here is an application
until promote runs. Hunt never submits employer forms.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from hunt.core.applications import create_application
from hunt.core.errors import HuntError, NotFoundError, ValidationError
from hunt.core.ids import new_id, now_iso
from hunt.core.workspace import Workspace

INBOX_STATUSES = ("pending", "promoted", "dismissed")


@dataclass
class InboxItem:
    id: str
    listing_id: str | None
    status: str
    company: str | None
    title: str | None
    url: str | None
    why_keep: str | None
    why_risk: str | None
    knockouts: list[str]
    payload: dict[str, Any]
    application_id: str | None
    created_at: str
    updated_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "listing_id": self.listing_id,
            "status": self.status,
            "company": self.company,
            "title": self.title,
            "url": self.url,
            "why_keep": self.why_keep,
            "why_risk": self.why_risk,
            "knockouts": list(self.knockouts),
            "payload": dict(self.payload),
            "application_id": self.application_id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


def _row_to_item(row) -> InboxItem:
    payload = json.loads(row["payload_json"] or "{}")
    knockouts = json.loads(row["knockouts_json"] or "[]")
    return InboxItem(
        id=row["id"],
        listing_id=row["listing_id"],
        status=row["status"],
        company=row["company"],
        title=row["title"],
        url=row["url"],
        why_keep=row["why_keep"],
        why_risk=row["why_risk"],
        knockouts=knockouts if isinstance(knockouts, list) else [],
        payload=payload if isinstance(payload, dict) else {},
        application_id=row["application_id"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


_SELECT = """
SELECT
    inbox_items.id,
    inbox_items.listing_id,
    inbox_items.status,
    inbox_items.why_keep,
    inbox_items.why_risk,
    inbox_items.knockouts_json,
    inbox_items.application_id,
    inbox_items.created_at,
    inbox_items.updated_at,
    listings.company,
    listings.title,
    listings.url,
    listings.payload_json
FROM inbox_items
LEFT JOIN listings ON listings.id = inbox_items.listing_id
"""


def list_inbox(
    ws: Workspace, *, status: str | None = "pending"
) -> list[InboxItem]:
    if status and status != "all":
        if status not in INBOX_STATUSES:
            raise ValidationError(
                f"inbox status must be one of {list(INBOX_STATUSES)} or 'all'"
            )
        rows = ws.conn.execute(
            _SELECT + " WHERE inbox_items.status = ? ORDER BY inbox_items.created_at DESC",
            (status,),
        ).fetchall()
    else:
        rows = ws.conn.execute(
            _SELECT + " ORDER BY inbox_items.created_at DESC"
        ).fetchall()
    return [_row_to_item(r) for r in rows]


def get_inbox_item(ws: Workspace, item_id: str) -> InboxItem:
    row = ws.conn.execute(_SELECT + " WHERE inbox_items.id = ?", (item_id,)).fetchone()
    if not row:
        raise NotFoundError(f"inbox item not found: {item_id}")
    return _row_to_item(row)


def add_item(
    ws: Workspace,
    *,
    company: str,
    title: str,
    url: str | None = None,
    why_keep: str | None = None,
    why_risk: str | None = None,
    knockouts: list[str] | None = None,
    payload: dict[str, Any] | None = None,
    source_id: str | None = None,
    external_id: str | None = None,
) -> InboxItem:
    """Insert a listing + pending inbox row. Used by adapters and tests.

    Not a CLI noun in v1 — promote/dismiss remain the human/agent actions.
    """
    company = (company or "").strip()
    title = (title or "").strip()
    if not company or not title:
        raise ValidationError("inbox items need company and title")
    now = now_iso()
    listing_id = new_id()
    item_id = new_id()
    body = dict(payload or {})
    body.setdefault("company", company)
    body.setdefault("title_posted", title)
    if url:
        body.setdefault("url", url)
    ws.conn.execute(
        """
        INSERT INTO listings(
            id, source_id, external_id, url, title, company, payload_json, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            listing_id,
            source_id,
            external_id,
            url,
            title,
            company,
            json.dumps(body),
            now,
        ),
    )
    ws.conn.execute(
        """
        INSERT INTO inbox_items(
            id, listing_id, status, why_keep, why_risk, knockouts_json,
            application_id, created_at, updated_at
        ) VALUES (?, ?, 'pending', ?, ?, ?, NULL, ?, ?)
        """,
        (
            item_id,
            listing_id,
            why_keep,
            why_risk,
            json.dumps(list(knockouts or [])),
            now,
            now,
        ),
    )
    ws.conn.commit()
    return get_inbox_item(ws, item_id)


def _promote_kwargs(item: InboxItem, overrides: dict[str, Any]) -> dict[str, Any]:
    payload = dict(item.payload)
    quoted = payload.get("comp_quoted") if isinstance(payload.get("comp_quoted"), dict) else {}
    kwargs: dict[str, Any] = {
        "company": overrides.get("company") or payload.get("company") or item.company,
        "source": overrides.get("source") or payload.get("source") or "inbox",
        "url": overrides.get("url") if "url" in overrides else (payload.get("url") or item.url),
        "title_posted": overrides.get("title_posted")
        or payload.get("title_posted")
        or item.title,
        "title_ours": overrides.get("title_ours", payload.get("title_ours")),
        "location_country": overrides.get(
            "location_country", payload.get("location_country")
        ),
        "location_city": overrides.get("location_city", payload.get("location_city")),
        "modality": overrides.get("modality", payload.get("modality")),
        "office_days_per_week": overrides.get(
            "office_days_per_week", payload.get("office_days_per_week")
        ),
        "engagement": overrides.get("engagement", payload.get("engagement")),
        "duration_months": overrides.get("duration_months", payload.get("duration_months")),
        "comp_amount": overrides.get("comp_amount", quoted.get("amount")),
        "comp_currency": overrides.get("comp_currency", quoted.get("currency")),
        "comp_unit": overrides.get("comp_unit", quoted.get("unit")),
        "comp_notes": overrides.get("comp_notes", payload.get("comp_notes")),
        "tax_home_for_net": overrides.get(
            "tax_home_for_net", payload.get("tax_home_for_net")
        ),
        "languages_required": overrides.get(
            "languages_required", payload.get("languages_required")
        ),
        "recruiter": overrides.get("recruiter", payload.get("recruiter")),
        "cv_variant_id": overrides.get("cv_variant_id", payload.get("cv_variant_id")),
        "knockouts": overrides.get("knockouts", payload.get("knockouts") or item.knockouts),
        "extra": overrides.get("extra", payload.get("extra")),
        "status": overrides.get("status") or "researching",
    }
    return {k: v for k, v in kwargs.items() if v is not None}


def promote(
    ws: Workspace,
    item_id: str,
    **overrides: Any,
) -> Any:
    item = get_inbox_item(ws, item_id)
    if item.status != "pending":
        raise HuntError(f"inbox item {item_id} is {item.status}, not pending")
    kwargs = _promote_kwargs(item, overrides)
    app = create_application(
        ws,
        event_kind="promoted",
        event_body=f"inbox:{item.id}",
        commit=False,
        **kwargs,
    )
    now = now_iso()
    ws.conn.execute(
        """
        UPDATE inbox_items
        SET status = 'promoted', application_id = ?, updated_at = ?
        WHERE id = ?
        """,
        (app.id, now, item.id),
    )
    ws.conn.commit()
    return app


def dismiss(ws: Workspace, item_id: str) -> InboxItem:
    item = get_inbox_item(ws, item_id)
    if item.status != "pending":
        raise HuntError(f"inbox item {item_id} is {item.status}, not pending")
    now = now_iso()
    ws.conn.execute(
        """
        UPDATE inbox_items
        SET status = 'dismissed', updated_at = ?
        WHERE id = ?
        """,
        (now, item.id),
    )
    ws.conn.commit()
    return get_inbox_item(ws, item_id)
