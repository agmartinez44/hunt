"""Workspace + harness + OpenAI-compat ``GET /v1/models`` checks."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from hunt.core.secrets import secret
from hunt.core.workspace import Workspace

from hunt.agent.config import (
    RECORD_NAME,
    ROLE_PACKS,
    agent_model,
    models_url,
    small_local_warning,
)


def _check(cid: str, ok: bool, detail: str, **extra: Any) -> dict[str, Any]:
    row = {"id": cid, "ok": ok, "detail": detail}
    row.update(extra)
    return row


def _json_load(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _hunt_in_mcp_payload(data: dict[str, Any]) -> bool:
    servers = data.get("mcpServers")
    if isinstance(servers, dict) and "hunt" in servers:
        return True
    mcp = data.get("mcp")
    if isinstance(mcp, dict) and "hunt" in mcp:
        return True
    return False


def _codex_has_hunt(path: Path) -> bool:
    if not path.is_file():
        return False
    text = path.read_text(encoding="utf-8")
    return "[mcp_servers.hunt]" in text


def _scan_mcp(root: Path, home: Path) -> list[Path]:
    candidates = [
        root / ".mcp.json",
        root / ".cursor" / "mcp.json",
        root / "opencode.json",
        root / ".openclaw" / "openclaw.json",
        home / ".codex" / "config.toml",
        root / ".paperclip" / "hunt-import.json",
    ]
    found: list[Path] = []
    for path in candidates:
        if path.suffix == ".toml":
            if _codex_has_hunt(path):
                found.append(path)
            continue
        data = _json_load(path)
        if _hunt_in_mcp_payload(data) or (
            path.name == "hunt-import.json" and data.get("mcp")
        ):
            found.append(path)
    return found


def _scan_skills(root: Path, home: Path) -> list[Path]:
    bases = [
        root / ".claude" / "skills",
        root / ".cursor" / "skills",
        root / ".opencode" / "skills",
        root / ".openclaw" / "skills",
        root / ".paperclip" / "skills",
        home / ".codex" / "skills",
    ]
    found: list[Path] = []
    for base in bases:
        for pack in ROLE_PACKS.values():
            path = base / pack / "SKILL.md"
            if path.is_file():
                found.append(path)
    return found


def _recorded_files(ws: Workspace) -> list[Path]:
    path = ws.root / RECORD_NAME
    if not path.is_file():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    if not isinstance(payload, dict):
        return []
    out: list[Path] = []
    for item in payload.get("installs") or []:
        if not isinstance(item, dict):
            continue
        for raw in item.get("files") or []:
            out.append(Path(str(raw)))
    return out


def ping_models(
    base_url: str,
    api_key: str | None,
    *,
    timeout: float = 3.0,
) -> dict[str, Any]:
    url = models_url(base_url)
    req = urllib.request.Request(url, method="GET")
    req.add_header("Accept", "application/json")
    if api_key:
        req.add_header("Authorization", f"Bearer {api_key}")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            status = int(getattr(resp, "status", 200) or 200)
            raw = resp.read()
    except urllib.error.HTTPError as exc:
        return {
            "ok": False,
            "url": url,
            "detail": f"HTTP {exc.code}",
            "models": [],
        }
    except urllib.error.URLError as exc:
        reason = getattr(exc, "reason", exc)
        return {
            "ok": False,
            "url": url,
            "detail": f"unreachable ({type(reason).__name__})",
            "models": [],
        }
    except TimeoutError:
        return {"ok": False, "url": url, "detail": "timeout", "models": []}
    except OSError as exc:
        return {
            "ok": False,
            "url": url,
            "detail": f"os error ({type(exc).__name__})",
            "models": [],
        }
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {
            "ok": False,
            "url": url,
            "detail": f"HTTP {status} (not JSON)",
            "models": [],
        }
    names: list[str] = []
    data = payload.get("data") if isinstance(payload, dict) else None
    if isinstance(data, list):
        for item in data:
            if isinstance(item, dict):
                ident = item.get("id") or item.get("name")
                if ident:
                    names.append(str(ident))
    return {
        "ok": True,
        "url": url,
        "detail": f"HTTP {status}",
        "models": names,
    }


def doctor(
    ws: Workspace,
    *,
    root: str | Path | None = None,
    home: str | Path | None = None,
    timeout: float = 3.0,
) -> dict[str, Any]:
    project = Path(root or os.getcwd()).expanduser().resolve()
    user_home = Path(home or Path.home()).expanduser().resolve()
    checks: list[dict[str, Any]] = []
    warnings: list[str] = []

    knowledge = ws.root / "knowledge"
    config = ws.root / "config.yaml"
    readable = config.is_file() and knowledge.is_dir()
    checks.append(
        _check(
            "workspace",
            readable,
            str(ws.root) if readable else f"workspace not readable: {ws.root}",
        )
    )

    recorded = [p for p in _recorded_files(ws) if p.is_file()]
    mcp_files = _scan_mcp(project, user_home)
    for path in recorded:
        if path.name in {".mcp.json", "mcp.json", "opencode.json", "openclaw.json", "config.toml", "hunt-import.json"}:
            if path not in mcp_files:
                mcp_files.append(path)
    mcp_ok = bool(mcp_files)
    checks.append(
        _check(
            "mcp",
            mcp_ok,
            ", ".join(str(p) for p in mcp_files) if mcp_ok else "no Hunt MCP entry in harness config",
            paths=[str(p) for p in mcp_files],
        )
    )

    skill_files = _scan_skills(project, user_home)
    for path in recorded:
        if path.name == "SKILL.md" and path.is_file() and path not in skill_files:
            skill_files.append(path)
    skill_ok = bool(skill_files)
    checks.append(
        _check(
            "skill",
            skill_ok,
            ", ".join(str(p) for p in skill_files) if skill_ok else "no Hunt role skill installed",
            paths=[str(p) for p in skill_files],
        )
    )

    model = agent_model(ws)
    key_env = model.get("api_key_env") or ""
    api_key = secret(ws.secrets(), key_env) if key_env else None
    ping = ping_models(model["base_url"], api_key, timeout=timeout)
    checks.append(
        _check(
            "models",
            bool(ping["ok"]),
            f"{ping['url']} — {ping['detail']}",
            url=ping["url"],
            models=ping.get("models") or [],
            api_key_env=key_env or None,
            api_key_present=bool(api_key),
        )
    )
    warn = small_local_warning(
        model["model"], model["base_url"], ping.get("models") or []
    )
    if warn:
        warnings.append(warn)

    ok = all(item["ok"] for item in checks)
    return {
        "ok": ok,
        "checks": checks,
        "warnings": warnings,
        "model": {
            "base_url": model["base_url"],
            "api_key_env": key_env or None,
            "model": model["model"],
        },
        "never_apply": True,
    }
