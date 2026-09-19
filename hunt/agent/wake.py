"""Start the installed screener harness after ingest. Not a Hunt-owned loop.

``hunt agent run screener`` execs a harness in the foreground. Ingest must
not replace the worker process, so this spawns the same command detached.
Hunt never applies or sends mail.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any, Callable

from hunt.agent.config import OPENCODE_INSTALL_COMMAND
from hunt.agent.run import prepare_run, runner_env
from hunt.core.workspace import Workspace

SpawnFn = Callable[[dict[str, Any], dict[str, str]], Any]

_WAKE_OFF = frozenset({"0", "off", "false", "no"})


def pid_path(ws: Workspace) -> Path:
    return ws.root / "logs" / "screener.pid"


def log_path(ws: Workspace) -> Path:
    return ws.root / "logs" / "screener.log"


def _idle(*, reason: str, **extra: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "triggered": False,
        "started": False,
        "reason": reason,
        "never_apply": True,
        "never_send_mail": True,
    }
    payload.update(extra)
    return payload


def _armed(*, reason: str, started: bool, **extra: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "triggered": True,
        "started": started,
        "reason": reason,
        "never_apply": True,
        "never_send_mail": True,
    }
    payload.update(extra)
    return payload


def _pid_running(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def running_pid(ws: Workspace) -> int | None:
    path = pid_path(ws)
    if not path.is_file():
        return None
    try:
        pid = int(path.read_text(encoding="utf-8").strip())
    except ValueError:
        return None
    if pid <= 0 or not _pid_running(pid):
        return None
    return pid


def default_spawn(plan: dict[str, Any], env: dict[str, str]) -> subprocess.Popen:
    """Detach the harness. Does not wait, apply, or send mail."""
    data = Path(env.get("HUNT_DATA") or ".")
    logs = data / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    handle = open(logs / "screener.log", "ab")
    try:
        return subprocess.Popen(
            plan["command"],
            cwd=plan.get("cwd") or None,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=handle,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            close_fds=True,
        )
    finally:
        handle.close()


def _wake_disabled() -> bool:
    raw = os.environ.get("HUNT_SCREENER_WAKE", "1").strip().lower()
    return raw in _WAKE_OFF


def maybe_wake_screener(
    ws: Workspace,
    *,
    survivors: int,
    spawn: SpawnFn | None = None,
    path_env: str | None = None,
) -> dict[str, Any]:
    """Start the screener harness when this triage job kept+unsure survivors.

    ``survivors == 0`` skips the LLM. Missing harness does not fail the job.
    Only ``run_triage_inbox`` should call this.
    """
    from hunt.agent.config import agent_triage_section

    if survivors <= 0:
        return _idle(reason="no_survivors", survivors=survivors)
    try:
        min_n = int(agent_triage_section(ws).get("wake_min_survivors") or 1)
    except (TypeError, ValueError):
        min_n = 1
    if survivors < min_n:
        return _idle(reason="below_wake_min", survivors=survivors)
    plan = prepare_run(
        ws,
        "screener",
        root=ws.root,
        path_env=path_env,
    )
    if not plan.get("ok"):
        return _armed(
            reason="no_harness",
            started=False,
            survivors=survivors,
            install=plan.get("install") or OPENCODE_INSTALL_COMMAND,
        )
    existing = running_pid(ws)
    if existing is not None:
        return _armed(
            reason="already_running",
            started=False,
            survivors=survivors,
            pid=existing,
            harness=plan.get("harness"),
        )
    if spawn is None and _wake_disabled():
        return _armed(
            reason="wake_disabled",
            started=False,
            survivors=survivors,
            harness=plan.get("harness"),
        )
    env = runner_env(ws)
    env["HUNT_AGENT_ROLE"] = "screener"
    proc = (spawn or default_spawn)(plan, env)
    pid = getattr(proc, "pid", None)
    if isinstance(pid, int) and pid > 0:
        path = pid_path(ws)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(str(pid), encoding="utf-8")
    return _armed(
        reason="survivors",
        started=True,
        survivors=survivors,
        harness=plan.get("harness"),
        pid=pid,
    )
