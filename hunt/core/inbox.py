"""Inbox: screened listings. Promote is explicit; nothing here is an application
until promote runs. Hunt never submits employer forms.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from hunt.core.applications import create_application, derive_for_workspace
from hunt.core.errors import HuntError, NotFoundError, ValidationError
from hunt.core.ids import new_id, now_iso
from hunt.core.pay import QuotedPay, below_workspace_floor, engagement_label
from hunt.core.workspace import Workspace

INBOX_STATUSES = ("pending", "promoted", "dismissed")


@dataclass
class InboxItem:
    id: str
    listing_id: str | None
    source_id: str | None
    status: str
    company: str | None
    title: str | None
    url: str | None
    why_keep: str | None
    why_risk: str | None
    knockouts: list[str]
    triage: dict[str, Any] | None
    payload: dict[str, Any]
    application_id: str | None
    created_at: str
    updated_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "listing_id": self.listing_id,
            "source_id": self.source_id,
            "status": self.status,
            "company": self.company,
            "title": self.title,
            "url": self.url,
            "why_keep": self.why_keep,
            "why_risk": self.why_risk,
            "knockouts": list(self.knockouts),
            "triage": dict(self.triage) if self.triage else None,
            "payload": dict(self.payload),
            "application_id": self.application_id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


def location_label(
    payload: dict[str, Any] | None,
    *,
    city: str | None = None,
    country: str | None = None,
) -> str | None:
    data = payload or {}
    city = city or data.get("location_city")
    country = country or data.get("location_country")
    parts = [str(p).strip() for p in (city, country) if p]
    if parts:
        return ", ".join(parts)
    loc = data.get("location")
    return str(loc).strip() if loc else None


def _quoted_pay(payload: dict[str, Any]) -> QuotedPay | None:
    quoted = payload.get("comp_quoted")
    if not isinstance(quoted, dict) or quoted.get("amount") is None:
        return None
    try:
        return QuotedPay(
            amount=float(quoted["amount"]),
            currency=str(quoted.get("currency") or "EUR"),
            unit=str(quoted.get("unit") or "month"),
        )
    except (TypeError, ValueError):
        return None


def serialize_inbox_item(ws: Workspace, item: InboxItem) -> dict[str, Any]:
    """Human + agent inbox row. Net estimate is first-class; floor stays in derived JSON."""
    data = item.to_dict()
    payload = item.payload
    quoted = _quoted_pay(payload)
    derived = derive_for_workspace(
        ws,
        quoted,
        payload.get("tax_home_for_net"),
        country=payload.get("location_country"),
        engagement=payload.get("engagement"),
    )
    derived_dict = derived.to_dict() if derived else None
    location = location_label(payload)
    data["role"] = item.title
    data["location"] = location
    data["location_city"] = payload.get("location_city")
    data["location_country"] = payload.get("location_country")
    data["engagement"] = payload.get("engagement")
    data["engagement_label"] = engagement_label(payload.get("engagement"))
    data["modality"] = payload.get("modality")
    data["experience_level"] = payload.get("experience_level")
    data["source_id"] = item.source_id
    data["triage"] = item.triage
    data["triage_json"] = item.triage
    data["comp_quoted"] = quoted.to_dict() if quoted else payload.get("comp_quoted")
    data["comp_derived"] = derived_dict
    data["net_month"] = derived.net_month if derived else None
    data["clears_floor"] = derived.clears_floor if derived else None
    data["below_floor"] = below_workspace_floor(ws, derived)
    data["display_currency"] = ws.display_currency
    return data


def _parse_triage(raw: Any) -> dict[str, Any] | None:
    if raw is None or raw == "":
        return None
    if isinstance(raw, dict):
        return dict(raw)
    if not isinstance(raw, str):
        return None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _row_to_item(row) -> InboxItem:
    payload = json.loads(row["payload_json"] or "{}")
    knockouts = json.loads(row["knockouts_json"] or "[]")
    return InboxItem(
        id=row["id"],
        listing_id=row["listing_id"],
        source_id=row["source_id"],
        status=row["status"],
        company=row["company"],
        title=row["title"],
        url=row["url"],
        why_keep=row["why_keep"],
        why_risk=row["why_risk"],
        knockouts=knockouts if isinstance(knockouts, list) else [],
        triage=_parse_triage(row["triage_json"]),
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
    inbox_items.triage_json,
    inbox_items.application_id,
    inbox_items.created_at,
    inbox_items.updated_at,
    listings.company,
    listings.title,
    listings.url,
    listings.source_id,
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


def add_inbox_for_listing(
    ws: Workspace,
    listing_id: str,
    *,
    why_keep: str | None = None,
    why_risk: str | None = None,
    knockouts: list[str] | None = None,
    commit: bool = True,
) -> InboxItem:
    existing = ws.conn.execute(
        "SELECT id FROM inbox_items WHERE listing_id = ?", (listing_id,)
    ).fetchone()
    if existing:
        return get_inbox_item(ws, existing["id"])
    listing = ws.conn.execute(
        "SELECT id FROM listings WHERE id = ?", (listing_id,)
    ).fetchone()
    if not listing:
        raise NotFoundError(f"listing not found: {listing_id}")
    now = now_iso()
    item_id = new_id()
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
    if commit:
        ws.conn.commit()
    return get_inbox_item(ws, item_id)


def refresh_inbox_for_listing(
    ws: Workspace,
    listing_id: str,
    *,
    why_keep: str | None = None,
    why_risk: str | None = None,
    knockouts: list[str] | None = None,
    commit: bool = True,
) -> InboxItem:
    """Update knockouts on a pending inbox row. Does not duplicate or promote.

    Writes the given why_keep / why_risk. Screening preserves non-boilerplate
    notes before calling this.
    """
    existing = ws.conn.execute(
        "SELECT id, status FROM inbox_items WHERE listing_id = ?",
        (listing_id,),
    ).fetchone()
    if not existing:
        return add_inbox_for_listing(
            ws,
            listing_id,
            why_keep=why_keep,
            why_risk=why_risk,
            knockouts=knockouts,
            commit=commit,
        )
    if existing["status"] != "pending":
        return get_inbox_item(ws, existing["id"])
    now = now_iso()
    ws.conn.execute(
        """
        UPDATE inbox_items
        SET why_keep = ?, why_risk = ?, knockouts_json = ?, updated_at = ?
        WHERE id = ?
        """,
        (
            why_keep,
            why_risk,
            json.dumps(list(knockouts or [])),
            now,
            existing["id"],
        ),
    )
    if commit:
        ws.conn.commit()
    return get_inbox_item(ws, existing["id"])


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
        event_kind="promoted_from_inbox",
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


def dismiss(ws: Workspace, item_id: str, *, commit: bool = True) -> InboxItem:
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
    if commit:
        ws.conn.commit()
    return get_inbox_item(ws, item.id)


def restore(ws: Workspace, item_id: str, *, commit: bool = True) -> InboxItem:
    """Dismissed → pending. Marks triage keep/restored; codes stay for audit."""
    item = get_inbox_item(ws, item_id)
    if item.status != "dismissed":
        raise HuntError(f"inbox item {item_id} is {item.status}, not dismissed")
    now = now_iso()
    triage = dict(item.triage or {})
    triage["action"] = "keep"
    triage["restored"] = True
    triage["restored_at"] = now
    if "codes" not in triage:
        triage["codes"] = list(item.knockouts)
    if not triage.get("reason") and item.why_risk:
        triage["reason"] = item.why_risk
    ws.conn.execute(
        """
        UPDATE inbox_items
        SET status = 'pending', triage_json = ?, updated_at = ?
        WHERE id = ?
        """,
        (json.dumps(triage), now, item.id),
    )
    if commit:
        ws.conn.commit()
    return get_inbox_item(ws, item.id)
