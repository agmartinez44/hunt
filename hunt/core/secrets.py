"""Load ``$HUNT_DATA/secrets.env``. Never log values."""

from __future__ import annotations

import os
from pathlib import Path


def load_secrets(root: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    path = root / "secrets.env"
    if path.is_file():
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.removeprefix("export ").strip()
            out[key] = value.strip().strip("'\"")
    for key, value in os.environ.items():
        if key in out or key.startswith("IMAP_") or key.startswith("HUNT_"):
            if value:
                out[key] = value
    return out


def secret(store: dict[str, str], *keys: str) -> str | None:
    for key in keys:
        if not key:
            continue
        value = store.get(key) or os.environ.get(key)
        if value:
            return value
    return None
