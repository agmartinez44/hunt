"""Resolve the Hunt data workspace.

Code lives in the public repo. Knowledge, integrity *values*, SQLite, and
rendered PDFs live in ``$HUNT_DATA`` (or ``--data``), never in the source tree.
"""

from __future__ import annotations

import os
from pathlib import Path


class WorkspaceError(SystemExit):
    """Missing or invalid Hunt workspace."""


def resolve_data_dir(explicit: str | Path | None = None) -> Path:
    if explicit:
        path = Path(explicit).expanduser().resolve()
    else:
        env = os.environ.get("HUNT_DATA")
        if not env:
            raise WorkspaceError(
                "Hunt workspace not set. Export HUNT_DATA or pass --data "
                "to a workspace directory (see example-workspace/)."
            )
        path = Path(env).expanduser().resolve()
    if not path.is_dir():
        raise WorkspaceError(f"Hunt workspace is not a directory: {path}")
    return path


def knowledge_dir(data_dir: Path) -> Path:
    kb = data_dir / "knowledge"
    if not kb.is_dir():
        raise WorkspaceError(
            f"Workspace {data_dir} has no knowledge/ directory. "
            "Copy example-workspace/ and edit the fictional YAML."
        )
    return kb


def cv_output_dir(data_dir: Path) -> Path:
    out = data_dir / "attachments" / "cv"
    out.mkdir(parents=True, exist_ok=True)
    return out
