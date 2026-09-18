"""Application event log."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from hunt.core.context import actor as current_actor
from hunt.core.errors import NotFoundError
from hunt.core.ids import new_id, now_iso
from hunt.core.workspace import Workspace

ACTORS = ("ui", "cli", "mcp", "job")


@dataclass
class Event:
    id: str
    application_id: str
    kind: str
    body: str | None
    actor: str
    at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "application_id": self.application_id,
            "kind": self.kind,
            "body": self.body,
            "actor": self.actor,
            "at": self.at,
        }


def _row_to_event(row) -> Event:
    keys = row.keys()
    return Event(
        id=row["id"],
        application_id=row["application_id"],
        kind=row["kind"],
        body=row["body"],
        actor=row["actor"] if "actor" in keys else "cli",
        at=row["at"],
    )


def append_event(
    ws: Workspace,
    application_id: str,
    kind: str,
    body: str | None = None,
    *,
    at: str | None = None,
    actor: str | None = None,
) -> Event:
    event = Event(
        id=new_id(),
        application_id=application_id,
        kind=kind,
        body=body,
        actor=actor or current_actor(),
        at=at or now_iso(),
    )
    ws.conn.execute(
        """
        INSERT INTO events(id, application_id, kind, body, actor, at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            event.id,
            event.application_id,
            event.kind,
            event.body,
            event.actor,
            event.at,
        ),
    )
    return event


def list_events(ws: Workspace, application_id: str) -> list[Event]:
    exists = ws.conn.execute(
        "SELECT 1 FROM applications WHERE id = ?", (application_id,)
    ).fetchone()
    if not exists:
        raise NotFoundError(f"application not found: {application_id}")
    rows = ws.conn.execute(
        """
        SELECT id, application_id, kind, body, actor, at
        FROM events
        WHERE application_id = ?
        ORDER BY at ASC, rowid ASC
        """,
        (application_id,),
    ).fetchall()
    return [_row_to_event(r) for r in rows]
