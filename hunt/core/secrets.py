"""Load ``$HUNT_DATA/secrets.env``. Never log values."""

from __future__ import annotations

import os
import re
from pathlib import Path

from hunt.core.errors import ValidationError

ENV_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


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


def secret_is_set(root: Path, env_name: str) -> bool:
    if not env_name:
        return False
    return bool(secret(load_secrets(root), env_name))


def _validate_env_name(env_name: str) -> str:
    name = (env_name or "").strip()
    if not name or not ENV_NAME_RE.match(name):
        raise ValidationError("env name must be an identifier like XAI_API_KEY")
    return name


def _rewrite_secrets_file(path: Path, updates: dict[str, str | None]) -> None:
    """Update keys in secrets.env. ``None`` deletes. Never log values."""
    raw_lines = path.read_text(encoding="utf-8").splitlines() if path.is_file() else []
    seen: set[str] = set()
    out: list[str] = []
    for line in raw_lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            out.append(line)
            continue
        key = stripped.partition("=")[0].removeprefix("export ").strip()
        if key in updates:
            value = updates[key]
            if value is None:
                continue
            out.append(f"{key}={value}")
            seen.add(key)
            continue
        out.append(line)
    for key, value in updates.items():
        if key in seen or value is None:
            continue
        out.append(f"{key}={value}")
    path.parent.mkdir(parents=True, exist_ok=True)
    text = "\n".join(out)
    if text:
        text += "\n"
    path.write_text(text, encoding="utf-8")
    os.chmod(path, 0o600)


def set_secret(root: Path, env_name: str, value: str) -> None:
    name = _validate_env_name(env_name)
    if value is None or str(value) == "":
        raise ValidationError("secret value is empty")
    _rewrite_secrets_file(Path(root) / "secrets.env", {name: str(value)})


def unset_secret(root: Path, env_name: str) -> None:
    name = _validate_env_name(env_name)
    _rewrite_secrets_file(Path(root) / "secrets.env", {name: None})
