"""Application artifacts. Files live under ``$HUNT_DATA/attachments``."""

from __future__ import annotations

import hashlib
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from hunt.core.applications import get_application
from hunt.core.errors import HuntError, ValidationError
from hunt.core.events import append_event
from hunt.core.ids import new_id, now_iso
from hunt.core.workspace import Workspace


@dataclass
class Artifact:
    id: str
    application_id: str
    kind: str
    filename: str
    sha256: str
    path: str
    created_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "application_id": self.application_id,
            "kind": self.kind,
            "filename": self.filename,
            "sha256": self.sha256,
            "path": self.path,
            "created_at": self.created_at,
        }


def application_dir(ws: Workspace, application_id: str) -> Path:
    path = ws.root / "attachments" / "applications" / application_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def add_file(
    ws: Workspace,
    application_id: str,
    file: str | Path,
    *,
    kind: str = "other",
    filename: str | None = None,
) -> Artifact:
    get_application(ws, application_id)
    src = Path(file).expanduser().resolve()
    if not src.is_file():
        raise ValidationError(f"not a file: {src}")
    try:
        src.relative_to(ws.root.resolve())
        inside_workspace = True
    except ValueError:
        inside_workspace = False
    dest_name = Path(filename or src.name).name
    if not dest_name or dest_name in {".", ".."}:
        raise ValidationError("invalid artifact filename")
    dest_dir = application_dir(ws, application_id)
    dest = dest_dir / dest_name
    if dest.exists() and dest.resolve() != src:
        raise HuntError(f"artifact already exists: {dest_name}")
    if not dest.exists():
        shutil.copy2(src, dest)
    elif not inside_workspace:
        shutil.copy2(src, dest)
    digest = hashlib.sha256(dest.read_bytes()).hexdigest()
    now = now_iso()
    art_id = new_id()
    try:
        ws.conn.execute(
            """
            INSERT INTO artifacts(id, application_id, kind, filename, sha256, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (art_id, application_id, kind or "other", dest_name, digest, now),
        )
    except Exception as exc:
        raise HuntError(f"could not record artifact {dest_name}: {exc}") from exc
    append_event(ws, application_id, "artifact_added", f"{kind}:{dest_name}")
    ws.conn.commit()
    return Artifact(
        id=art_id,
        application_id=application_id,
        kind=kind or "other",
        filename=dest_name,
        sha256=digest,
        path=str(dest),
        created_at=now,
    )


def list_artifacts(ws: Workspace, application_id: str) -> list[Artifact]:
    get_application(ws, application_id)
    rows = ws.conn.execute(
        """
        SELECT id, application_id, kind, filename, sha256, created_at
        FROM artifacts
        WHERE application_id = ?
        ORDER BY created_at ASC
        """,
        (application_id,),
    ).fetchall()
    dest_dir = application_dir(ws, application_id)
    return [
        Artifact(
            id=row["id"],
            application_id=row["application_id"],
            kind=row["kind"],
            filename=row["filename"],
            sha256=row["sha256"],
            path=str(dest_dir / row["filename"]),
            created_at=row["created_at"],
        )
        for row in rows
    ]
