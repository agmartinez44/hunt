"""Pytest defaults. Do not spawn a real screener harness from ingest tests."""

from __future__ import annotations

import os

os.environ.setdefault("HUNT_SCREENER_WAKE", "0")
