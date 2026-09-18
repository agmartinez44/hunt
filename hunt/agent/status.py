"""Agent settings payload shared by CLI, HTTP, and MCP. No secret values."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from hunt.core.errors import ValidationError
from hunt.core.secrets import secret_is_set, set_secret, unset_secret
from hunt.core.workspace import Workspace

from hunt.agent.config import (
    HARNESS_CHOICES,
    HARNESSES,
    agent_harness,
    agent_model,
    save_agent_config,
)
from hunt.agent.doctor import doctor as run_doctor, load_last_doctor, strip_secrets
from hunt.agent.install import install as install_agent, preview as preview_install
from hunt.agent.run import detect_runner


def _host(base_url: str) -> str:
    return urlparse(base_url or "").hostname or ""


def _preview_harness(name: str | None) -> str | None:
    if not name:
        return None
    value = str(name).strip().lower()
    if value in ("", "auto"):
        return None
    if value not in HARNESSES:
        raise ValidationError(
            f"Unknown harness {name!r}. Use one of: " + ", ".join(HARNESSES)
        )
    return value


def agent_status(
    ws: Workspace,
    *,
    harness: str | None = None,
    root: str | Path | None = None,
    home: str | Path | None = None,
    doctor: dict[str, Any] | None = None,
    run_check: bool = False,
    timeout: float = 3.0,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    model = agent_model(ws)
    key_env = model.get("api_key_env") or ""
    configured = agent_harness(ws)
    detected = detect_runner(ws)
    preview_name = _preview_harness(harness) or (
        configured if configured in HARNESSES else None
    )
    preview = None
    if preview_name:
        preview = preview_install(
            harness=preview_name,
            data_dir=ws.root,
            root=root or ws.root,
            home=home,
        )
        preview = {
            "harness": preview["harness"],
            "writes": preview["writes"],
        }
    report = doctor
    if run_check:
        report = run_doctor(ws, root=root or ws.root, home=home, timeout=timeout)
    elif report is None:
        report = load_last_doctor(ws)
    if report is not None:
        report = strip_secrets(report)
        if "state" not in report:
            report["state"] = "connected" if report.get("ok") else "fail"
    state = "disconnected"
    if report is not None:
        state = "connected" if report.get("ok") else "fail"
    payload: dict[str, Any] = {
        "harness": configured,
        "harness_detected": detected,
        "model": {
            "base_url": model["base_url"],
            "api_key_env": key_env or None,
            "model": model["model"],
            "local": bool((report or {}).get("model", {}).get("local"))
            if report
            else None,
        },
        "api_key_set": bool(key_env and secret_is_set(ws.root, key_env)),
        "preview": preview,
        "doctor": report,
        "state": state,
        "never_apply": True,
    }
    if extra:
        payload.update(extra)
    return strip_secrets(payload)


def save_agent(
    ws: Workspace,
    *,
    harness: str | None = None,
    model: dict[str, Any] | None = None,
    root: str | Path | None = None,
    home: str | Path | None = None,
    timeout: float = 3.0,
) -> dict[str, Any]:
    if harness is None and not model:
        raise ValidationError("expected harness or model fields")
    if harness is not None and str(harness).strip().lower() not in HARNESS_CHOICES:
        raise ValidationError(
            f"Unknown harness {harness!r}. Use one of: " + ", ".join(HARNESS_CHOICES)
        )
    save_agent_config(ws, harness=harness, model=model)
    return agent_status(
        ws, harness=harness, root=root, home=home, run_check=True, timeout=timeout
    )


def set_agent_secret(
    ws: Workspace,
    env_name: str,
    value: str,
    *,
    root: str | Path | None = None,
    home: str | Path | None = None,
    timeout: float = 3.0,
) -> dict[str, Any]:
    set_secret(ws.root, env_name, value)
    return agent_status(
        ws,
        root=root,
        home=home,
        run_check=True,
        timeout=timeout,
        extra={"env": env_name, "api_key_set": True},
    )


def unset_agent_secret(
    ws: Workspace,
    env_name: str,
    *,
    root: str | Path | None = None,
    home: str | Path | None = None,
    timeout: float = 3.0,
) -> dict[str, Any]:
    unset_secret(ws.root, env_name)
    return agent_status(ws, root=root, home=home, run_check=True, timeout=timeout)


def install_and_status(
    ws: Workspace,
    harness: str,
    *,
    root: str | Path | None = None,
    home: str | Path | None = None,
    role: str | None = None,
    timeout: float = 3.0,
) -> dict[str, Any]:
    result = install_agent(
        harness=harness,
        data_dir=ws.root,
        root=root or ws.root,
        home=home,
        role=role,
    )
    status = agent_status(
        ws,
        harness=harness,
        root=root or ws.root,
        home=home,
        run_check=True,
        timeout=timeout,
        extra={"install": {"harness": result["harness"], "files": result["files"], "note": result["note"]}},
    )
    return status
