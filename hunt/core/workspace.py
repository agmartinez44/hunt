"""One process, one workspace. Data lives in ``HUNT_DATA`` / ``--data``."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from hunt.core.db import connect
from hunt.core.errors import HuntError
from hunt.core.pay import DEFAULT_HOURS_PER_MONTH

DEFAULT_DAYS_PER_MONTH = 21.0


class WorkspaceError(HuntError):
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


@dataclass
class Workspace:
    root: Path
    config: dict[str, Any]
    conn: Any

    @classmethod
    def open(cls, data_dir: str | Path | None = None) -> Workspace:
        root = resolve_data_dir(data_dir)
        config_path = root / "config.yaml"
        if not config_path.is_file():
            raise WorkspaceError(
                f"Workspace {root} has no config.yaml. "
                "Copy example-workspace/ to a private $HUNT_DATA."
            )
        loaded = yaml.safe_load(config_path.read_text()) or {}
        if not isinstance(loaded, dict):
            raise WorkspaceError(f"{config_path} must be a YAML mapping")
        db_path = root / "store.sqlite"
        conn = connect(db_path)
        (root / "attachments" / "applications").mkdir(parents=True, exist_ok=True)
        return cls(root=root, config=loaded, conn=conn)

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> Workspace:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    @property
    def display_currency(self) -> str:
        return str(self.config.get("display_currency") or "EUR").upper()

    @property
    def hours_per_month(self) -> float:
        return float(self.config.get("hours_per_month") or DEFAULT_HOURS_PER_MONTH)

    @property
    def days_per_month(self) -> float:
        return float(self.config.get("days_per_month") or DEFAULT_DAYS_PER_MONTH)

    @property
    def fx_as_of(self) -> str | None:
        fx = self.config.get("fx") or {}
        as_of = fx.get("as_of") if isinstance(fx, dict) else None
        return str(as_of) if as_of else None

    @property
    def fx_rates(self) -> dict[str, float]:
        fx = self.config.get("fx") or {}
        rates = fx.get("rates") if isinstance(fx, dict) else None
        if not isinstance(rates, dict):
            return {}
        out: dict[str, float] = {}
        for key, value in rates.items():
            try:
                out[str(key).upper()] = float(value)
            except (TypeError, ValueError):
                continue
        return out

    @property
    def tax_homes(self) -> dict[str, Any]:
        homes = self.config.get("tax_homes") or {}
        return homes if isinstance(homes, dict) else {}

    @property
    def comp_floor(self) -> dict[str, Any] | None:
        floor = self.config.get("comp_floor")
        return floor if isinstance(floor, dict) else None
