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
