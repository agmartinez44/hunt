"""``agent`` block in ``config.yaml``. Keys live in secrets.env, never here."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

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
