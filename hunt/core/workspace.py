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

    def reload_config(self) -> None:
        config_path = self.root / "config.yaml"
        loaded = yaml.safe_load(config_path.read_text()) or {}
        if not isinstance(loaded, dict):
            raise WorkspaceError(f"{config_path} must be a YAML mapping")
        self.config = loaded

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

    @property
    def worker_backend(self) -> str:
        worker = self.config.get("worker") or {}
        if not isinstance(worker, dict):
            return "none"
        backend = str(worker.get("backend") or "none").lower()
        if backend not in {"none", "cli", "hermes"}:
            return "none"
        return backend

    def knockout_rules(self) -> dict[str, Any]:
        rules = self.config.get("knockouts") or {}
        return rules if isinstance(rules, dict) else {}

    def secrets(self) -> dict[str, str]:
        from hunt.core.secrets import load_secrets

        return load_secrets(self.root)

    @property
    def bind(self) -> str:
        http = self.config.get("http") if isinstance(self.config.get("http"), dict) else {}
        value = self.config.get("bind") or http.get("bind") or "127.0.0.1"
        return str(value)

    @property
    def port(self) -> int:
        http = self.config.get("http") if isinstance(self.config.get("http"), dict) else {}
        value = self.config.get("port") if self.config.get("port") is not None else http.get("port")
        try:
            return int(value if value is not None else 8787)
        except (TypeError, ValueError):
            return 8787

    @property
    def auth_token(self) -> str | None:
        token = self.config.get("auth_token")
        if token:
            return str(token)
        auth = self.config.get("auth")
        if isinstance(auth, dict) and auth.get("token"):
            return str(auth["token"])
        http = self.config.get("http")
        if isinstance(http, dict) and http.get("token"):
            return str(http["token"])
        return None

    @property
    def sources_config(self) -> list[dict[str, Any]]:
        raw = self.config.get("sources") or []
        if not isinstance(raw, list):
            return []
        out: list[dict[str, Any]] = []
        for item in raw:
            if isinstance(item, dict):
                out.append(item)
        return out

    def profile_name(self) -> str | None:
        path = self.root / "knowledge" / "profile.yaml"
        if not path.is_file():
            return None
        loaded = yaml.safe_load(path.read_text()) or {}
        if not isinstance(loaded, dict):
            return None
        name = loaded.get("name")
        return str(name) if name else None

    def filter_view(self) -> dict[str, Any]:
        from hunt.core.inbox import pending_cap, DEFAULT_PENDING_CAP, count_pending

        rules = self.knockout_rules()
        inbox_cfg = self.config.get("inbox") if isinstance(self.config.get("inbox"), dict) else {}
        floor = self.comp_floor
        floor_view = None
        if isinstance(floor, dict) and floor.get("amount") is not None:
            floor_view = {
                "amount": floor.get("amount"),
                "currency": floor.get("currency"),
                "unit": floor.get("unit"),
            }
        return {
            "knockouts": {
                "title_include": list(rules.get("title_include") or []),
                "title_exclude": list(rules.get("title_exclude") or []),
                "experience_block": list(rules.get("experience_block") or []),
                "modality_block": list(rules.get("modality_block") or []),
                "engagement_allow": list(rules.get("engagement_allow") or []),
                "languages_block": list(rules.get("languages_block") or []),
                "drop_on": list(rules.get("drop_on") or []),
            },
            "floor": floor_view,
            "inbox": {
                "pending_cap": pending_cap(self),
                "pending_cap_default": DEFAULT_PENDING_CAP,
                "pending_count": count_pending(self),
                "pending_cap_in_yaml": "pending_cap" in inbox_cfg,
            },
            "poll": {
                "kind": "operator_crontab",
                "help": "Cadence is operator crontab plus ad-hoc hunt sources run; not a Hunt field.",
            },
            "edit": {
                "path": "$HUNT_DATA/config.yaml",
                "hint": "v1 has no source or knockouts editor; edit the YAML.",
            },
        }
