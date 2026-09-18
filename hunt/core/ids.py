"""Short ids and UTC timestamps."""

from __future__ import annotations

import secrets
from datetime import datetime, timezone


def new_id() -> str:
    return secrets.token_hex(6)


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
