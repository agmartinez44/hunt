"""Locate in-repo Hunt agent packs (``skills/<name>/SKILL.md``)."""

from __future__ import annotations

import os
from pathlib import Path

from hunt.core.errors import HuntError

from hunt.agent.config import ROLE_PACKS, ROLES


def packs_dir() -> Path:
    explicit = os.environ.get("HUNT_PACKS")
    if explicit:
        path = Path(explicit).expanduser().resolve()
        if (path / "hunt-operator" / "SKILL.md").is_file():
            return path
        raise HuntError(f"HUNT_PACKS has no hunt-operator pack: {path}")
    here = Path(__file__).resolve()
    for parent in [here.parent, *here.parents]:
        candidate = parent / "skills"
        if (candidate / "hunt-operator" / "SKILL.md").is_file():
            return candidate
    raise HuntError(
        "Hunt agent packs not found. Expected skills/hunt-operator/SKILL.md "
        "next to the Hunt checkout (or set HUNT_PACKS)."
    )


def pack_skill(name: str) -> Path:
    path = packs_dir() / name / "SKILL.md"
    if not path.is_file():
        raise HuntError(f"Hunt pack missing SKILL.md: {path}")
    return path


def packs_for_roles(roles: tuple[str, ...]) -> list[tuple[str, str, Path]]:
    out: list[tuple[str, str, Path]] = []
    for role in roles:
        if role not in ROLES:
            raise HuntError(f"Unknown role {role!r}")
        pack = ROLE_PACKS[role]
        out.append((role, pack, pack_skill(pack)))
    return out
