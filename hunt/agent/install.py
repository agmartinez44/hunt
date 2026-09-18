"""Write Hunt MCP + role skills into an existing harness. Not a runner."""

from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any

from hunt.core.workspace import resolve_data_dir

from hunt.agent.config import RECORD_NAME, resolve_harness, resolve_roles
from hunt.agent.packs import packs_for_roles

PAPERCLIP_PREVIEW_NOTE = (
    "Import hunt-operator and hunt-screener as company skills. "
    "Paperclip is optional — Hunt users do not need it."
)


def hunt_mcp_command() -> tuple[str, list[str]]:
    hunt_bin = shutil.which("hunt")
    if hunt_bin:
        return hunt_bin, ["mcp"]
    sibling = Path(sys.executable).resolve().parent / "hunt"
    if sibling.is_file() and os.access(sibling, os.X_OK):
        return str(sibling), ["mcp"]
    return sys.executable, ["-m", "hunt", "mcp"]


def mcp_spec(data_dir: Path) -> dict[str, Any]:
    command, args = hunt_mcp_command()
    return {
        "command": command,
        "args": args,
        "env": {"HUNT_DATA": str(data_dir)},
    }


def _write_json(path: Path, data: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    loaded = json.loads(path.read_text(encoding="utf-8"))
    return loaded if isinstance(loaded, dict) else {}


def _copy_skill(src: Path, dest_dir: Path, pack: str) -> Path:
    dest = dest_dir / pack / "SKILL.md"
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dest)
    return dest


def _write_skills(dest_dir: Path, roles: tuple[str, ...]) -> list[str]:
    written: list[str] = []
    for _role, pack, src in packs_for_roles(roles):
        written.append(str(_copy_skill(src, dest_dir, pack)))
    return written


def _merge_mcp_servers(path: Path, spec: dict[str, Any], *, key: str = "mcpServers") -> Path:
    data = _read_json(path)
    servers = data.get(key) if isinstance(data.get(key), dict) else {}
    servers = dict(servers)
    servers["hunt"] = {
        "command": spec["command"],
        "args": list(spec["args"]),
        "env": dict(spec["env"]),
    }
    data[key] = servers
    return _write_json(path, data)


def _install_claude(root: Path, spec: dict[str, Any], roles: tuple[str, ...]) -> list[str]:
    files = [str(_merge_mcp_servers(root / ".mcp.json", spec))]
    files.extend(_write_skills(root / ".claude" / "skills", roles))
    return files


def _install_cursor(root: Path, spec: dict[str, Any], roles: tuple[str, ...]) -> list[str]:
    files = [str(_merge_mcp_servers(root / ".cursor" / "mcp.json", spec))]
    files.extend(_write_skills(root / ".cursor" / "skills", roles))
    return files


def _toml_str(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _strip_codex_hunt(text: str) -> str:
    lines = text.splitlines()
    out: list[str] = []
    skipping = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            skipping = stripped == "[mcp_servers.hunt]" or stripped.startswith(
                "[mcp_servers.hunt."
            )
        if skipping:
            continue
        out.append(line)
    return "\n".join(out).rstrip()


def _install_codex(home: Path, spec: dict[str, Any], roles: tuple[str, ...]) -> list[str]:
    config_path = home / ".codex" / "config.toml"
    existing = config_path.read_text(encoding="utf-8") if config_path.is_file() else ""
    body = _strip_codex_hunt(existing)
    args = ", ".join(_toml_str(a) for a in spec["args"])
    env_lines = "\n".join(
        f"{key} = {_toml_str(str(value))}" for key, value in spec["env"].items()
    )
    section = (
        f"[mcp_servers.hunt]\n"
        f"command = {_toml_str(spec['command'])}\n"
        f"args = [{args}]\n"
        f"\n"
        f"[mcp_servers.hunt.env]\n"
        f"{env_lines}\n"
    )
    merged = (body + "\n\n" + section).strip() + "\n"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(merged, encoding="utf-8")
    files = [str(config_path)]
    files.extend(_write_skills(home / ".codex" / "skills", roles))
    return files


def _install_opencode(root: Path, spec: dict[str, Any], roles: tuple[str, ...]) -> list[str]:
    path = root / "opencode.json"
    data = _read_json(path)
    data.setdefault("$schema", "https://opencode.ai/config.json")
    mcp = data.get("mcp") if isinstance(data.get("mcp"), dict) else {}
    mcp = dict(mcp)
    mcp["hunt"] = {
        "type": "local",
        "command": [spec["command"], *spec["args"]],
        "enabled": True,
        "environment": dict(spec["env"]),
    }
    data["mcp"] = mcp
    files = [str(_write_json(path, data))]
    files.extend(_write_skills(root / ".opencode" / "skills", roles))
    return files


def _install_openclaw(root: Path, spec: dict[str, Any], roles: tuple[str, ...]) -> list[str]:
    path = root / ".openclaw" / "openclaw.json"
    data = _read_json(path)
    mcp = data.get("mcp") if isinstance(data.get("mcp"), dict) else {}
    mcp = dict(mcp)
    mcp["hunt"] = {
        "command": spec["command"],
        "args": list(spec["args"]),
        "env": dict(spec["env"]),
    }
    data["mcp"] = mcp
    files = [str(_write_json(path, data))]
    files.extend(_write_skills(root / ".openclaw" / "skills", roles))
    return files


def _install_paperclip(root: Path, spec: dict[str, Any], roles: tuple[str, ...]) -> list[str]:
    """Write importable skill dirs. Paperclip is optional — not a Hunt runtime."""
    skills_root = root / ".paperclip" / "skills"
    files = _write_skills(skills_root, roles)
    imports = [
        {"source": str((skills_root / pack).resolve()), "pack": pack}
        for _role, pack, _src in packs_for_roles(roles)
    ]
    manifest = {
        "paperclip_required": False,
        "note": (
            "Hunt does not require Paperclip. Optional company skill import: "
            "POST /api/companies/{companyId}/skills/import with source set to "
            "each pack directory below."
        ),
        "mcp": spec,
        "import": imports,
    }
    files.append(str(_write_json(root / ".paperclip" / "hunt-import.json", manifest)))
    return files


_INSTALLERS = {
    "claude": lambda root, home, spec, roles: _install_claude(root, spec, roles),
    "cursor": lambda root, home, spec, roles: _install_cursor(root, spec, roles),
    "codex": lambda root, home, spec, roles: _install_codex(home, spec, roles),
    "opencode": lambda root, home, spec, roles: _install_opencode(root, spec, roles),
    "openclaw": lambda root, home, spec, roles: _install_openclaw(root, spec, roles),
    "paperclip": lambda root, home, spec, roles: _install_paperclip(root, spec, roles),
}


def _record_install(
    data_dir: Path,
    *,
    harness: str,
    root: Path,
    home: Path,
    roles: tuple[str, ...],
    files: list[str],
) -> Path:
    path = data_dir / RECORD_NAME
    payload: dict[str, Any] = {}
    if path.is_file():
        loaded = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            payload = loaded
    installs = payload.get("installs") if isinstance(payload.get("installs"), list) else []
    entry = {
        "harness": harness,
        "root": str(root),
        "home": str(home),
        "roles": list(roles),
        "files": files,
    }
    rest = [item for item in installs if not (
        isinstance(item, dict) and item.get("harness") == harness
        and item.get("root") == str(root)
    )]
    rest.append(entry)
    payload["installs"] = rest
    return _write_json(path, payload)


def planned_writes(
    harness: str,
    *,
    root: Path,
    home: Path,
    roles: tuple[str, ...],
) -> list[dict[str, Any]]:
    """Paths ``install`` would write. Paperclip is a note, not a file list."""
    name = resolve_harness(harness)
    if name == "paperclip":
        return [
            {
                "path": PAPERCLIP_PREVIEW_NOTE,
                "kind": "note",
                "exists": False,
            }
        ]
    files: list[tuple[Path, str]] = []
    if name == "claude":
        files.append((root / ".mcp.json", "mcp"))
        skill_root = root / ".claude" / "skills"
    elif name == "cursor":
        files.append((root / ".cursor" / "mcp.json", "mcp"))
        skill_root = root / ".cursor" / "skills"
    elif name == "codex":
        files.append((home / ".codex" / "config.toml", "config"))
        skill_root = home / ".codex" / "skills"
    elif name == "opencode":
        files.append((root / "opencode.json", "mcp"))
        skill_root = root / ".opencode" / "skills"
    elif name == "openclaw":
        files.append((root / ".openclaw" / "openclaw.json", "mcp"))
        skill_root = root / ".openclaw" / "skills"
    else:
        return []
    for _role, pack, _src in packs_for_roles(roles):
        files.append((skill_root / pack / "SKILL.md", "skill"))
    return [
        {"path": str(path), "kind": kind, "exists": path.is_file()}
        for path, kind in files
    ]


def preview(
    *,
    harness: str,
    data_dir: str | Path | None = None,
    root: str | Path | None = None,
    home: str | Path | None = None,
    role: str | None = None,
) -> dict[str, Any]:
    name = resolve_harness(harness)
    roles = resolve_roles(role)
    workspace = resolve_data_dir(data_dir)
    project = Path(root or workspace).expanduser().resolve()
    user_home = Path(home or Path.home()).expanduser().resolve()
    writes = planned_writes(name, root=project, home=user_home, roles=roles)
    return {
        "harness": name,
        "roles": list(roles),
        "writes": writes,
        "root": str(project),
        "home": str(user_home),
        "never_apply": True,
    }


def install(
    *,
    harness: str,
    data_dir: str | Path | None = None,
    root: str | Path | None = None,
    home: str | Path | None = None,
    role: str | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    name = resolve_harness(harness)
    roles = resolve_roles(role)
    workspace = resolve_data_dir(data_dir)
    project = Path(root or workspace).expanduser().resolve()
    user_home = Path(home or Path.home()).expanduser().resolve()
    spec = mcp_spec(workspace)
    if dry_run:
        writes = planned_writes(name, root=project, home=user_home, roles=roles)
        return {
            "harness": name,
            "roles": list(roles),
            "files": [row["path"] for row in writes],
            "writes": writes,
            "mcp": spec,
            "note": "dry-run; no files written",
            "never_apply": True,
        }
    files = _INSTALLERS[name](project, user_home, spec, roles)
    record = _record_install(
        workspace, harness=name, root=project, home=user_home, roles=roles, files=files
    )
    note = {
        "claude": "Wrote .mcp.json (equivalent: claude mcp add). Skills under .claude/skills/.",
        "cursor": "Wrote .cursor/mcp.json and .cursor/skills/.",
        "codex": "Wrote MCP into ~/.codex/config.toml and skills under ~/.codex/skills/.",
        "opencode": "Wrote opencode.json MCP + .opencode/skills/.",
        "openclaw": "Wrote .openclaw/openclaw.json MCP + .openclaw/skills/.",
        "paperclip": (
            "Wrote importable packs under .paperclip/skills/. "
            "Paperclip is optional; Hunt does not require it."
        ),
    }[name]
    return {
        "harness": name,
        "roles": list(roles),
        "files": files,
        "record": str(record),
        "mcp": spec,
        "note": note,
        "never_apply": True,
    }
