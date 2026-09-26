"""Open a workspace file in the local file manager."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

from hunt.core.errors import HuntError, ValidationError


def path_inside(root: Path, path: Path) -> Path:
    resolved = path.resolve()
    base = root.resolve()
    try:
        resolved.relative_to(base)
    except ValueError as exc:
        raise ValidationError("artifact is outside the workspace") from exc
    return resolved


def reveal_file(path: Path) -> None:
    """Open the folder that contains ``path``. Select the file when possible."""
    path = path.resolve()
    if not path.is_file():
        raise HuntError(f"not a file: {path.name}")
    cmd = _reveal_command(path)
    if not cmd:
        raise HuntError(
            "No file manager on this machine. Copy the path and open it yourself."
        )
    subprocess.Popen(
        cmd,
        start_new_session=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _reveal_command(path: Path) -> list[str] | None:
    if sys.platform == "darwin":
        return ["open", "-R", str(path)]
    if sys.platform == "win32":
        return ["explorer", f"/select,{path}"]
    file_path = str(path)
    folder = str(path.parent)
    if shutil.which("nautilus"):
        return ["nautilus", "--select", file_path]
    if shutil.which("dolphin"):
        return ["dolphin", "--select", file_path]
    if shutil.which("nemo"):
        return ["nemo", file_path]
    if shutil.which("xdg-open"):
        return ["xdg-open", folder]
    return None
