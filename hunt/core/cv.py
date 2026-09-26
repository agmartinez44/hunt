"""CV render through hunt.cv, recorded as an application artifact when asked."""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path
from typing import Any

from hunt.core.applications import get_application
from hunt.core.artifacts import add_file, application_dir, list_artifacts
from hunt.core.errors import HuntError
from hunt.core.workspace import Workspace


def neutral_cv_stem(author: str) -> str:
    """ASCII ``{Name}_CV`` stem. Never a company, role, or posting."""
    folded = unicodedata.normalize("NFKD", author or "")
    folded = "".join(ch for ch in folded if not unicodedata.combining(ch))
    parts = [part for part in re.split(r"[^A-Za-z0-9]+", folded) if part]
    stem = "_".join(parts) or "CV"
    if not stem.upper().endswith("_CV"):
        stem = f"{stem}_CV"
    stem = stem[:80].strip("_")
    return stem or "CV"


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
    from hunt.cv.finalize import default_author, finalize_pdf
    from hunt.cv.render import render_cv

    emphasis_path = _resolve_emphasis(ws, emphasis, variant, application_id)
    output_dir = None
    output_name = None
    if application_id:
        get_application(ws, application_id)
        output_dir = application_dir(ws, application_id)
        output_name = neutral_cv_stem(default_author(ws.root))
    try:
        pdf = render_cv(
            data_dir=ws.root,
            emphasis_path=emphasis_path,
            output_dir=output_dir,
            output_name=output_name,
            quiet=True,
        )
    except SystemExit as exc:
        message = exc.code if isinstance(exc.code, str) else (str(exc) or "cv render failed")
        raise HuntError(message) from exc
    finalize_pdf(pdf, data_dir=ws.root, quiet=True)

    result: dict[str, Any] = {"path": str(pdf), "application_id": application_id}
    if application_id:
        _discard_other_cvs(ws, application_id, pdf.name)
        artifact = add_file(
            ws,
            application_id,
            pdf,
            kind="cv",
            filename=pdf.name,
            replace=True,
        )
        result["artifact"] = artifact.to_dict()
        result["filename"] = pdf.name
    return result


def _discard_other_cvs(ws: Workspace, application_id: str, keep: str) -> None:
    """One current CV file. Leave job descriptions and notes alone."""
    root = (ws.root / "attachments").resolve()
    for art in list_artifacts(ws, application_id):
        if art.kind != "cv" or art.filename == keep:
            continue
        path = Path(art.path).resolve()
        try:
            path.relative_to(root)
        except ValueError:
            path = None
        if path is not None and path.is_file():
            path.unlink()
        ws.conn.execute("DELETE FROM artifacts WHERE id = ?", (art.id,))
    ws.conn.commit()


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
