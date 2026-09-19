"""Raw listings from source adapters. Not applications."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from hunt.core.ids import new_id, now_iso
from hunt.core.workspace import Workspace


@dataclass
class Listing:
    id: str
    source_id: str | None
    external_id: str | None
    url: str | None
    title: str | None
    company: str | None
    payload: dict[str, Any]
    created_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "source_id": self.source_id,
            "external_id": self.external_id,
            "url": self.url,
            "title": self.title,
            "company": self.company,
            "payload": dict(self.payload),
            "created_at": self.created_at,
        }


def _row_to_listing(row) -> Listing:
    payload = json.loads(row["payload_json"] or "{}")
    return Listing(
        id=row["id"],
        source_id=row["source_id"],
        external_id=row["external_id"],
        url=row["url"],
        title=row["title"],
        company=row["company"],
        payload=payload if isinstance(payload, dict) else {},
        created_at=row["created_at"],
    )


def get_listing(ws: Workspace, listing_id: str) -> Listing | None:
    row = ws.conn.execute(
        "SELECT * FROM listings WHERE id = ?", (listing_id,)
    ).fetchone()
    return _row_to_listing(row) if row else None


def upsert_listing(
    ws: Workspace,
    *,
    source_id: str,
    external_id: str,
    title: str,
    company: str,
    url: str | None = None,
    payload: dict[str, Any] | None = None,
    commit: bool = False,
) -> tuple[Listing, bool]:
    """Insert or update a listing. Never creates an application."""
    body = dict(payload or {})
    body.setdefault("company", company)
    body.setdefault("title_posted", title)
    if url:
        body.setdefault("url", url)
    existing = ws.conn.execute(
        """
        SELECT * FROM listings
        WHERE source_id = ? AND external_id = ?
        """,
        (source_id, external_id),
    ).fetchone()
    if existing:
        url = url or existing["url"]
        title = title or existing["title"]
        company = company or existing["company"]
        if url:
            body.setdefault("url", url)
        old = json.loads(existing["payload_json"] or "{}")
        if isinstance(old, dict):
            for key in (
                "engagement",
                "modality",
                "location_city",
                "location_country",
                "comp_quoted",
                "experience_level",
            ):
                if not body.get(key) and old.get(key) not in (None, ""):
                    body[key] = old[key]
        ws.conn.execute(
            """
            UPDATE listings
            SET url = ?, title = ?, company = ?, payload_json = ?
            WHERE id = ?
            """,
            (url, title, company, json.dumps(body), existing["id"]),
        )
        if commit:
            ws.conn.commit()
        row = ws.conn.execute(
            "SELECT * FROM listings WHERE id = ?", (existing["id"],)
        ).fetchone()
        return _row_to_listing(row), False
    listing_id = new_id()
    now = now_iso()
    ws.conn.execute(
        """
        INSERT INTO listings(
            id, source_id, external_id, url, title, company, payload_json, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (listing_id, source_id, external_id, url, title, company, json.dumps(body), now),
    )
    if commit:
        ws.conn.commit()
    row = ws.conn.execute(
        "SELECT * FROM listings WHERE id = ?", (listing_id,)
    ).fetchone()
    return _row_to_listing(row), True


def _listing_text_blob(listing: Listing) -> str:
    parts: list[str] = [
        str(listing.title or ""),
        str(listing.company or ""),
        str(listing.url or ""),
    ]
    for value in listing.payload.values():
        if isinstance(value, str):
            parts.append(value)
        elif isinstance(value, dict):
            parts.append(json.dumps(value))
    return "\n".join(part for part in parts if part)


def apply_stated_engagement(ws: Workspace, listing: Listing) -> Listing:
    """Fill payload.engagement from stated posting text. Never invent."""
    payload = dict(listing.payload)
    if payload.get("engagement"):
        return listing
    from hunt.adapters.linkedin_alert import extract_engagement

    found = extract_engagement(_listing_text_blob(listing))
    if not found:
        return listing
    payload["engagement"] = found
    ws.conn.execute(
        "UPDATE listings SET payload_json = ? WHERE id = ?",
        (json.dumps(payload), listing.id),
    )
    return get_listing(ws, listing.id) or listing


def backfill_engagement(ws: Workspace, *, commit: bool = True) -> dict[str, Any]:
    """Fill missing listing engagement when stored text states FTE/freelance."""
    updated = 0
    already = 0
    missing = 0
    for listing in list_listings(ws):
        if (listing.payload or {}).get("engagement"):
            already += 1
            continue
        refreshed = apply_stated_engagement(ws, listing)
        if (refreshed.payload or {}).get("engagement"):
            updated += 1
        else:
            missing += 1
    if commit:
        ws.conn.commit()
    return {"updated": updated, "already": already, "missing": missing}


def list_listings(ws: Workspace) -> list[Listing]:
    rows = ws.conn.execute(
        "SELECT * FROM listings ORDER BY created_at ASC"
    ).fetchall()
    return [_row_to_listing(r) for r in rows]


def listings_without_inbox(ws: Workspace) -> list[Listing]:
    rows = ws.conn.execute(
        """
        SELECT listings.*
        FROM listings
        LEFT JOIN inbox_items ON inbox_items.listing_id = listings.id
        WHERE inbox_items.id IS NULL
        ORDER BY listings.created_at ASC
        """
    ).fetchall()
    return [_row_to_listing(r) for r in rows]
