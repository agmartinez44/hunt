"""CV render through hunt.cv, recorded as an application artifact when asked."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from hunt.core.applications import get_application
from hunt.core.artifacts import add_file, application_dir
from hunt.core.errors import HuntError
from hunt.core.workspace import Workspace


def render(
    ws: Workspace,
    *,
    application_id: str | None = None,
    emphasis: str | Path | None = None,
    variant: str | None = None,
) -> dict[str, Any]:
    """Render a PDF into the workspace. Never writes the source tree.

    Hunt does not submit the PDF anywhere. Presence of a file is not an
    application.
    """
    from hunt.cv.render import render_cv

    emphasis_path = _resolve_emphasis(ws, emphasis, variant, application_id)
    output_dir = None
    if application_id:
        get_application(ws, application_id)
        output_dir = application_dir(ws, application_id)
    try:
        pdf = render_cv(
            data_dir=ws.root,
            emphasis_path=emphasis_path,
            output_dir=output_dir,
            quiet=True,
        )
    except SystemExit as exc:
        message = exc.code if isinstance(exc.code, str) else (str(exc) or "cv render failed")
        raise HuntError(message) from exc

    result: dict[str, Any] = {"path": str(pdf), "application_id": application_id}
    if application_id:
        artifact = add_file(
            ws,
            application_id,
            pdf,
            kind="cv",
            filename=pdf.name,
        )
        result["artifact"] = artifact.to_dict()
    return result


def _resolve_emphasis(
    ws: Workspace,
    emphasis: str | Path | None,
    variant: str | None,
    application_id: str | None,
) -> str | None:
    if emphasis:
        return str(emphasis)
    name = variant
    if not name and application_id:
        app = get_application(ws, application_id)
        name = app.cv_variant_id
    if not name:
        return None
    candidates = [
        ws.root / "emphasis" / f"{name}.yaml",
        ws.root / "knowledge" / f"emphasis-{name}.yaml",
        ws.root / "knowledge" / f"{name}.yaml",
    ]
    for path in candidates:
        if path.is_file():
            return str(path)
    raise HuntError(
        f"cv variant {name!r} has no emphasis file "
        f"(looked in emphasis/{name}.yaml)"
    )
