"""Exec an installed harness. Hunt does not vendor a tool loop."""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any

from hunt.core.secrets import secret
from hunt.core.workspace import Workspace

from hunt.agent.config import (
    OPENCODE_INSTALL_COMMAND,
    RUN_ORDER,
    RUNNER_BINS,
    agent_harness,
    agent_model,
    resolve_roles,
)
from hunt.agent.packs import pack_skill


def _which_runners(path_env: str | None = None) -> dict[str, str]:
    found: dict[str, str] = {}
    extra = os.environ.copy()
    if path_env is not None:
        extra["PATH"] = path_env
    for harness, binary in RUNNER_BINS.items():
        located = shutil.which(binary, path=extra.get("PATH"))
        if located:
            found[harness] = located
    return found


def detect_runner(ws: Workspace, *, path_env: str | None = None) -> str | None:
    found = _which_runners(path_env)
    preferred = agent_harness(ws)
    order = list(RUN_ORDER)
    if preferred in RUNNER_BINS and preferred not in order:
        order.insert(0, preferred)
    elif preferred in RUNNER_BINS:
        order = [preferred, *[item for item in order if item != preferred]]
    for name in order:
        if name in found:
            return name
    return None


def runner_env(ws: Workspace) -> dict[str, str]:
    env = {key: value for key, value in os.environ.items() if value is not None}
    env["HUNT_DATA"] = str(ws.root)
    model = agent_model(ws)
    env["OPENAI_BASE_URL"] = model["base_url"]
    env["HUNT_AGENT_MODEL"] = model["model"]
    key_env = model.get("api_key_env") or ""
    if key_env:
        value = secret(ws.secrets(), key_env)
        if value:
            env[key_env] = value
            env.setdefault("OPENAI_API_KEY", value)
    else:
        env.setdefault("OPENAI_API_KEY", "local")
    return env


def prepare_run(
    ws: Workspace,
    role: str,
    *,
    root: str | Path | None = None,
    path_env: str | None = None,
    extra_args: list[str] | None = None,
) -> dict[str, Any]:
    roles = resolve_roles(role)
    chosen = roles[0]
    pack = "hunt-operator" if chosen == "operator" else "hunt-screener"
    skill = pack_skill(pack)
    cwd = str(Path(root or os.getcwd()).expanduser().resolve())
    harness = detect_runner(ws, path_env=path_env)
    env = runner_env(ws)
    env["HUNT_AGENT_ROLE"] = chosen
    public_env = sorted(
        key
        for key in env
        if key in {"HUNT_DATA", "HUNT_AGENT_ROLE", "HUNT_AGENT_MODEL", "OPENAI_BASE_URL"}
        or key.endswith("_API_KEY")
        or key == (agent_model(ws).get("api_key_env") or "")
    )
    if not harness:
        return {
            "ok": False,
            "role": chosen,
            "pack": pack,
            "skill": str(skill),
            "cwd": cwd,
            "install": OPENCODE_INSTALL_COMMAND,
            "env_keys": public_env,
            "never_apply": True,
        }
    binary = _which_runners(path_env)[harness]
    command = [binary, *(extra_args or [])]
    return {
        "ok": True,
        "role": chosen,
        "pack": pack,
        "skill": str(skill),
        "cwd": cwd,
        "harness": harness,
        "command": command,
        "env_keys": public_env,
        "never_apply": True,
    }


def exec_run(plan: dict[str, Any], env: dict[str, str]) -> None:
    """Replace this process with the detected runner. Does not implement a loop."""
    command = plan["command"]
    cwd = plan.get("cwd")
    if cwd:
        os.chdir(cwd)
    os.execvpe(command[0], command, env)
