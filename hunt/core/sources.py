"""Sources are declared in config.yaml. Run enqueues source-poll; it does
not create applications. Listing → application is inbox.promote only.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from hunt.core.errors import NotFoundError, ValidationError
from hunt.core.ids import now_iso
from hunt.core.workspace import Workspace

SOURCE_KINDS = ("imap_alerts", "http_json")


@dataclass
class Source:
    id: str
    kind: str
    name: str
    enabled: bool
    config: dict[str, Any]
    last_run_at: str | None
    last_status: str | None
    last_error: str | None
    listing_count: int
    inbox_count: int
    created_at: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "name": self.name,
            "enabled": self.enabled,
            "config": dict(self.config),
            "last_run_at": self.last_run_at,
            "last_status": self.last_status,
            "last_error": self.last_error,
            "listing_count": self.listing_count,
            "inbox_count": self.inbox_count,
            "created_at": self.created_at,
        }


def _ensure_row(ws: Workspace, spec: dict[str, Any]) -> None:
    source_id = str(spec.get("id") or "").strip()
    kind = str(spec.get("kind") or "").strip()
    name = str(spec.get("name") or source_id).strip()
    if not source_id or not kind:
        raise ValidationError("each source needs id and kind")
    if kind not in SOURCE_KINDS:
        raise ValidationError(
            f"source kind must be one of {list(SOURCE_KINDS)}, got {kind!r}"
        )
    now = now_iso()
    cfg = spec.get("config") if isinstance(spec.get("config"), dict) else {}
    existing = ws.conn.execute(
        "SELECT id FROM sources WHERE id = ?", (source_id,)
    ).fetchone()
    if existing:
        ws.conn.execute(
            "UPDATE sources SET kind = ?, name = ?, config_json = ? WHERE id = ?",
            (kind, name, json.dumps(cfg), source_id),
        )
        return
    ws.conn.execute(
        """
        INSERT INTO sources(id, kind, name, config_json, last_run_at, last_status, created_at)
        VALUES (?, ?, ?, ?, NULL, NULL, ?)
        """,
        (source_id, kind, name, json.dumps(cfg), now),
    )


def _counts(ws: Workspace, source_id: str) -> tuple[int, int]:
    listings = ws.conn.execute(
        "SELECT COUNT(*) AS n FROM listings WHERE source_id = ?",
        (source_id,),
    ).fetchone()["n"]
    inbox = ws.conn.execute(
        """
        SELECT COUNT(*) AS n
        FROM inbox_items
        JOIN listings ON listings.id = inbox_items.listing_id
        WHERE listings.source_id = ? AND inbox_items.status = 'pending'
        """,
        (source_id,),
    ).fetchone()["n"]
    return int(listings), int(inbox)


def _row_and_spec(ws: Workspace, spec: dict[str, Any]) -> Source:
    source_id = str(spec["id"])
    row = ws.conn.execute(
        "SELECT id, kind, name, config_json, last_run_at, last_status, created_at FROM sources WHERE id = ?",
        (source_id,),
    ).fetchone()
    cfg = spec.get("config") if isinstance(spec.get("config"), dict) else {}
    if row:
        stored = json.loads(row["config_json"] or "{}")
        if isinstance(stored, dict) and stored:
            cfg = stored
    listing_count, inbox_count = _counts(ws, source_id)
    last_status = row["last_status"] if row else None
    last_error = None
    if last_status and last_status not in {"ok", "queued", "running"}:
        last_error = last_status
    enabled = spec.get("enabled")
    if enabled is None:
        enabled = True
    return Source(
        id=source_id,
        kind=str(spec.get("kind") or (row["kind"] if row else "")),
        name=str(spec.get("name") or source_id),
        enabled=bool(enabled),
        config=cfg if isinstance(cfg, dict) else {},
        last_run_at=row["last_run_at"] if row else None,
        last_status=last_status if last_status in {"ok", "queued", "running", "error"} else last_status,
        last_error=last_error,
        listing_count=listing_count,
        inbox_count=inbox_count,
        created_at=row["created_at"] if row else None,
    )


def list_sources(ws: Workspace) -> list[Source]:
    specs = ws.sources_config
    out: list[Source] = []
    for spec in specs:
        _ensure_row(ws, spec)
        out.append(_row_and_spec(ws, spec))
    if specs:
        ws.conn.commit()
    return out


def get_source(ws: Workspace, source_id: str) -> Source:
    for source in list_sources(ws):
        if source.id == source_id:
            return source
    raise NotFoundError(f"source not found: {source_id}")


def mark_queued(ws: Workspace, source_id: str) -> None:
    get_source(ws, source_id)
    ws.conn.execute(
        "UPDATE sources SET last_status = ?, last_run_at = ? WHERE id = ?",
        ("queued", now_iso(), source_id),
    )
    ws.conn.commit()


def mark_status(
    ws: Workspace,
    source_id: str,
    status: str,
    *,
    ran: bool = True,
) -> None:
    get_source(ws, source_id)
    if ran:
        ws.conn.execute(
            "UPDATE sources SET last_status = ?, last_run_at = ? WHERE id = ?",
            (status, now_iso(), source_id),
        )
    else:
        ws.conn.execute(
            "UPDATE sources SET last_status = ? WHERE id = ?",
            (status, source_id),
        )
    ws.conn.commit()


def run_source(ws: Workspace, source_id: str):
    """Enqueue source-poll. Does not promote listings to applications."""
    from hunt.core.jobs import enqueue

    source = get_source(ws, source_id)
    if not source.enabled:
        raise ValidationError(f"source {source_id} is disabled")
    job = enqueue(ws, job_type="source-poll", target_id=source_id)
    mark_queued(ws, source_id)
    return job
