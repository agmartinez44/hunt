"""``agent`` block in ``config.yaml``. Keys live in secrets.env, never here."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml

from hunt.core.errors import ValidationError
from hunt.core.secrets import secret
from hunt.core.workspace import Workspace

HARNESSES = ("claude", "cursor", "codex", "opencode", "openclaw", "paperclip")
HARNESS_CHOICES = ("auto", *HARNESSES)
ROLES = ("operator", "screener")
ROLE_PACKS = {
    "operator": "hunt-operator",
    "screener": "hunt-screener",
}
RUN_ORDER = ("opencode", "claude", "codex")
RUNNER_BINS = {
    "opencode": "opencode",
    "claude": "claude",
    "codex": "codex",
}

DEFAULT_HARNESS = "auto"
DEFAULT_BASE_URL = "https://api.x.ai/v1"
DEFAULT_API_KEY_ENV = "XAI_API_KEY"
DEFAULT_MODEL = "grok-4.5"
OPENCODE_INSTALL_COMMAND = "curl -fsSL https://opencode.ai/install | bash"

SMALL_LOCAL_MARKERS = (
    "gemma",
    "e4b",
    "llama",
    "qwen",
    "phi-",
    "phi3",
    "tinyllama",
    "mistral-7b",
    "olmo",
)

RECORD_NAME = "agent-install.json"


def resolve_roles(role: str | None) -> tuple[str, ...]:
    if role in (None, "", "all"):
        return ROLES
    if role not in ROLES:
        raise ValidationError(
            f"Unknown role {role!r}. Use operator, screener, or all."
        )
    return (role,)


def resolve_harness(name: str) -> str:
    value = (name or "").strip().lower()
    if value not in HARNESSES:
        raise ValidationError(
            f"Unknown harness {name!r}. Use one of: " + ", ".join(HARNESSES)
        )
    return value


def agent_section(ws: Workspace) -> dict[str, Any]:
    raw = ws.config.get("agent") or {}
    return raw if isinstance(raw, dict) else {}


def agent_harness(ws: Workspace) -> str:
    section = agent_section(ws)
    value = str(section.get("harness") or DEFAULT_HARNESS).strip().lower()
    if value not in HARNESS_CHOICES:
        return DEFAULT_HARNESS
    return value


def agent_model(ws: Workspace) -> dict[str, str]:
    section = agent_section(ws)
    model = section.get("model") if isinstance(section.get("model"), dict) else {}
    base_url = str(model.get("base_url") or DEFAULT_BASE_URL).strip()
    api_key_env = str(model.get("api_key_env") or "").strip()
    if "api_key_env" not in model:
        api_key_env = DEFAULT_API_KEY_ENV
    name = str(model.get("model") or DEFAULT_MODEL).strip()
    return {
        "base_url": base_url or DEFAULT_BASE_URL,
        "api_key_env": api_key_env,
        "model": name or DEFAULT_MODEL,
    }


def models_url(base_url: str) -> str:
    """GET path for OpenAI-compat ``/v1/models`` given a configured base_url."""
    base = (base_url or "").strip().rstrip("/")
    if not base:
        base = DEFAULT_BASE_URL
    if base.endswith("/v1"):
        return base + "/models"
    return base + "/v1/models"


def api_key_for(ws: Workspace, model: dict[str, str] | None = None) -> str | None:
    cfg = model or agent_model(ws)
    env_name = cfg.get("api_key_env") or ""
    if not env_name:
        return None
    return secret(ws.secrets(), env_name)


def is_small_local(model_name: str, base_url: str, discovered: list[str] | None = None) -> bool:
    blob = " ".join(
        [
            (model_name or "").lower(),
            (base_url or "").lower(),
            " ".join(discovered or []).lower(),
        ]
    )
    host = (urlparse(base_url).hostname or "").lower()
    local_host = host in {"127.0.0.1", "localhost", "0.0.0.0", "::1"}
    marked = any(token in blob for token in SMALL_LOCAL_MARKERS)
    return marked and (local_host or "gemma" in blob or "e4b" in blob)


def small_local_warning(model_name: str, base_url: str, discovered: list[str] | None = None) -> str | None:
    if not is_small_local(model_name, base_url, discovered):
        return None
    return (
        "Screening quality may still want Grok or Claude; "
        "this local model looks small (Gemma E4B class)."
    )


def is_local_base_url(base_url: str) -> bool:
    host = (urlparse(base_url or "").hostname or "").lower()
    return host in {"127.0.0.1", "localhost", "0.0.0.0", "::1"}


def validate_base_url(base_url: str | None) -> str:
    raw = (base_url or "").strip()
    if not raw:
        raise ValidationError("Need a base URL.")
    parsed = urlparse(raw)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValidationError("Need an http(s) URL.")
    return raw.rstrip("/")


def validate_env_name(env_name: str | None, *, allow_empty: bool = True) -> str:
    raw = "" if env_name is None else str(env_name).strip()
    if not raw:
        if allow_empty:
            return ""
        raise ValidationError("env name must be an identifier like XAI_API_KEY")
    if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", raw):
        raise ValidationError("env name must be an identifier like XAI_API_KEY")
    return raw


def validate_model_id(model: str | None) -> str:
    raw = (model or "").strip()
    if not raw:
        raise ValidationError("Need a model id (for SpaceXAI, grok-4.5).")
    return raw


def write_agent_section(path: Path, agent: dict[str, Any]) -> None:
    """Replace the top-level ``agent:`` block. Does not write secret values."""
    dumped = yaml.safe_dump({"agent": agent}, sort_keys=False, allow_unicode=True)
    if not dumped.endswith("\n"):
        dumped += "\n"
    text = path.read_text(encoding="utf-8") if path.is_file() else ""
    lines = text.splitlines(keepends=True)
    start: int | None = None
    end = len(lines)
    for i, line in enumerate(lines):
        stripped = line.rstrip("\n")
        if stripped == "agent:" or stripped.startswith("agent:"):
            if start is None:
                start = i
            continue
        if start is None:
            continue
        if not stripped or stripped.lstrip().startswith("#"):
            continue
        if line[:1] in " \t":
            continue
        end = i
        break
    if start is None:
        prefix = text
        if prefix and not prefix.endswith("\n"):
            prefix += "\n"
        path.write_text(prefix + dumped, encoding="utf-8")
        return
    path.write_text("".join(lines[:start]) + dumped + "".join(lines[end:]), encoding="utf-8")


def save_agent_config(
    ws: Workspace,
    *,
    harness: str | None = None,
    model: dict[str, Any] | None = None,
) -> dict[str, str]:
    current = agent_section(ws)
    next_harness = agent_harness(ws)
    if harness is not None:
        value = str(harness).strip().lower()
        if value not in HARNESS_CHOICES:
            raise ValidationError(
                "Unknown harness "
                f"{harness!r}. Use one of: " + ", ".join(HARNESS_CHOICES)
            )
        next_harness = value
    current_model = agent_model(ws)
    next_model = dict(current_model)
    if isinstance(model, dict):
        if "base_url" in model:
            next_model["base_url"] = validate_base_url(model.get("base_url"))
        if "api_key_env" in model:
            next_model["api_key_env"] = validate_env_name(model.get("api_key_env"))
        if "model" in model:
            next_model["model"] = validate_model_id(model.get("model"))
    payload: dict[str, Any] = {"harness": next_harness, "model": next_model}
    for key, value in current.items():
        if key not in payload:
            payload[key] = value
    write_agent_section(ws.root / "config.yaml", payload)
    ws.reload_config()
    return agent_model(ws)
