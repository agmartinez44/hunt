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
from hunt.core.pay import (
    QuotedPay,
    below_workspace_floor,
    engagement_label,
    normalize_country,
    pay_month_view,
    quoted_from_mapping,
)
from hunt.core.workspace import Workspace

INBOX_STATUSES = ("pending", "promoted", "dismissed")
DEFAULT_PENDING_CAP = 200
INBOX_SORT_KEYS = ("created_at", "company", "role", "source_id", "gross_month")
INBOX_ORDERS = ("asc", "desc")
INBOX_SORT_DEFAULT_DESC = frozenset({"created_at", "gross_month"})

# Remote scope: required location / region, never JustJoin HQ city.
REMOTE_SCOPE_TOKENS = frozenset(
    {
        "europe",
        "european",
        "eea",
        "emea",
        "worldwide",
        "anywhere",
        "eu",
        "global",
    }
    | {key.lower() for key in (
        "CH", "CHE", "SWITZERLAND", "SWISS", "SUISSE", "SCHWEIZ", "SVIZZERA",
        "ES", "ESP", "SPAIN", "ESPANA", "ESPAÑA",
        "PL", "POL", "POLAND", "POLSKA",
        "DE", "GER", "GERMANY", "DEUTSCHLAND",
        "FR", "FRA", "FRANCE",
        "NL", "NLD", "NETHERLANDS", "HOLLAND",
        "PT", "PRT", "PORTUGAL",
        "IE", "IRL", "IRELAND",
        "GB", "UK", "GBR", "UNITED KINGDOM", "ENGLAND",
        "US", "USA", "UNITED STATES",
        "IT", "ITA", "ITALY", "ITALIA",
        "SE", "SWE", "SWEDEN",
        "NO", "NOR", "NORWAY",
        "DK", "DNK", "DENMARK",
        "FI", "FIN", "FINLAND",
        "BE", "BEL", "BELGIUM",
        "AT", "AUT", "AUSTRIA",
        "CZ", "CZE", "CZECH", "CZECHIA",
        "RO", "ROU", "ROMANIA",
        "HU", "HUN", "HUNGARY",
        "UA", "UKR", "UKRAINE",
        "CA", "CAN", "CANADA",
        "AU", "AUS", "AUSTRALIA",
    )}
)


def pending_cap(ws: Workspace) -> int:
    """Positive cap; missing → 200; ``0`` is unlimited (falsy, never ``pending >= 0``)."""
    inbox = ws.config.get("inbox")
    if not isinstance(inbox, dict) or "pending_cap" not in inbox:
        return DEFAULT_PENDING_CAP
    raw = inbox.get("pending_cap")
    try:
        return int(raw)
    except (TypeError, ValueError):
        return DEFAULT_PENDING_CAP


def count_pending(ws: Workspace) -> int:
    row = ws.conn.execute(
        "SELECT COUNT(*) AS n FROM inbox_items WHERE status = 'pending'"
    ).fetchone()
    return int(row["n"] if row else 0)


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


def _is_remote_scope(value: str | None) -> bool:
    if value is None:
        return False
    text = str(value).strip()
    if not text:
        return False
    if normalize_country(text):
        return True
    folded = text.lower().replace(".", "")
    if folded in REMOTE_SCOPE_TOKENS:
        return True
    return any(tok in REMOTE_SCOPE_TOKENS for tok in folded.replace("-", " ").split())


def _days_token(value: Any) -> str | None:
    if value is None or value == "":
        return None
    try:
        n = float(value)
    except (TypeError, ValueError):
        return None
    if n < 0:
        return None
    if n == int(n):
        return f"{int(n)}d"
    return f"{n}d"


def work_location_label(
    payload: dict[str, Any] | None,
    *,
    city: str | None = None,
    country: str | None = None,
) -> tuple[str | None, str | None]:
    """Modality-first inbox location. Returns ``(cell, title)``.

    Remote JustJoin HQ city is not printed. Remotive ``Poland`` / ``Europe``
    is scope. ``title`` is the dropped HQ city when useful.
    """
    data = payload or {}
    city = city if city is not None else data.get("location_city")
    country = country if country is not None else data.get("location_country")
    city_s = str(city).strip() if city else ""
    country_s = str(country).strip() if country else ""
    modality = str(data.get("modality") or "").strip().lower()
    office_days = data.get("office_days_per_week")

    if modality == "remote":
        if _is_remote_scope(city_s):
            scope = city_s
        elif country_s:
            scope = country_s
        else:
            scope = None
        label = f"Remote · {scope}" if scope else "Remote"
        dropped = city_s if city_s and city_s != scope and not _is_remote_scope(city_s) else ""
        if dropped.lower() in {"remote", "hybrid", "onsite", "office"}:
            dropped = ""
        return label, dropped or None

    place = city_s or country_s
    if modality == "hybrid":
        days = _days_token(office_days)
        if days and place:
            return f"Hybrid · {days} {place}", None
        if days:
            return f"Hybrid · {days}", None
        if place:
            return f"Hybrid · {place}", None
        return "Hybrid", None

    if modality == "onsite":
        if place:
            return f"Onsite · {place}", None
        return "Onsite", None

    return location_label(data, city=city_s or None, country=country_s or None), None


def _quoted_pay(payload: dict[str, Any]) -> QuotedPay | None:
    return quoted_from_mapping(payload.get("comp_quoted"))


def _quoted_out(payload: dict[str, Any], quoted: QuotedPay | None) -> dict[str, Any] | None:
    raw = payload.get("comp_quoted")
    if quoted:
        return quoted.to_dict()
    if isinstance(raw, dict):
        out = dict(raw)
        out.setdefault("kind", "undisclosed" if raw.get("currency") else "unknown")
        return out
    return None


def serialize_inbox_item(ws: Workspace, item: InboxItem) -> dict[str, Any]:
    """Human + agent inbox row. Gross month is first-class; net stays in derived JSON."""
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
    quoted_dict = _quoted_out(payload, quoted)
    location, location_title = work_location_label(payload)
    data["role"] = item.title
    data["location"] = location
    if location_title:
        data["location_title"] = location_title
    data["location_city"] = payload.get("location_city")
    data["location_country"] = payload.get("location_country")
    data["engagement"] = payload.get("engagement")
    data["engagement_label"] = engagement_label(payload.get("engagement"))
    data["modality"] = payload.get("modality")
    data["office_days_per_week"] = payload.get("office_days_per_week")
    data["experience_level"] = payload.get("experience_level")
    data["source_id"] = item.source_id
    data["triage"] = item.triage
    data["triage_json"] = item.triage
    data["comp_quoted"] = quoted_dict
    data["comp_derived"] = derived_dict
    data["gross_month"] = derived.month if derived else None
    data["net_month"] = derived.net_month if derived else None
    data["clears_floor"] = derived.clears_floor if derived else None
    data["below_floor"] = below_workspace_floor(ws, derived)
    data["display_currency"] = ws.display_currency
    data["pay_month"] = pay_month_view(
        quoted_dict,
        derived_dict,
        display_currency=ws.display_currency,
    )
    return data


def resolve_inbox_sort(
    sort: str | None,
    order: str | None,
) -> tuple[str, str]:
    key = (sort or "").strip() or "created_at"
    if key not in INBOX_SORT_KEYS:
        raise ValidationError(f"inbox sort must be one of {list(INBOX_SORT_KEYS)}")
    raw = (order or "").strip().lower()
    if not raw:
        direction = "desc" if key in INBOX_SORT_DEFAULT_DESC else "asc"
    else:
        direction = raw
        if direction not in INBOX_ORDERS:
            raise ValidationError(f"inbox order must be one of {list(INBOX_ORDERS)}")
    return key, direction


def _sort_value(row: dict[str, Any], key: str) -> Any:
    if key == "role":
        val = row.get("role") or row.get("title")
    else:
        val = row.get(key)
    if val is None:
        return None
    if isinstance(val, str) and val.strip() == "":
        return ""
    return val


def sort_serialized_inbox(
    rows: list[dict[str, Any]],
    *,
    sort: str | None = None,
    order: str | None = None,
) -> list[dict[str, Any]]:
    key, direction = resolve_inbox_sort(sort, order)
    reverse = direction == "desc"
    present, missing = [], []
    for row in rows:
        val = _sort_value(row, key)
        (missing if val is None or val == "" else present).append(row)
    if key == "gross_month":
        present.sort(key=lambda r: float(r["gross_month"]), reverse=reverse)
    elif key == "created_at":
        present.sort(key=lambda r: r.get("created_at") or "", reverse=reverse)
    else:
        present.sort(key=lambda r: str(_sort_value(r, key)).lower(), reverse=reverse)
    return present + missing  # None / empty always last, both directions


def serialize_inbox_list(
    ws: Workspace,
    items: list[InboxItem],
    *,
    sort: str | None = None,
    order: str | None = None,
) -> list[dict[str, Any]]:
    rows = [serialize_inbox_item(ws, i) for i in items]
    return sort_serialized_inbox(rows, sort=sort, order=order)


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
    ws: Workspace,
    *,
    status: str | None = "pending",
    source_id: str | None = None,
    knockout: str | None = None,
    triage_action: str | None = None,
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
    items = [_row_to_item(r) for r in rows]
    if source_id:
        items = [item for item in items if item.source_id == source_id]
    if knockout:
        codes = {part.strip() for part in str(knockout).split("|") if part.strip()}
        items = [item for item in items if codes & set(item.knockouts)]
    if triage_action:
        allowed = {"dismiss", "keep", "unsure"}
        if triage_action not in allowed:
            raise ValidationError(
                f"inbox triage must be one of {sorted(allowed)}"
            )
        items = [
            item
            for item in items
            if (item.triage or {}).get("action") == triage_action
        ]
    return items


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
