"""Honesty-gated CV component.

Facts live in ``$HUNT_DATA/knowledge``. Unverified achievements never render.
Integrity rules are workspace data, not code.
"""

from hunt.cv.finalize import finalize_pdf
from hunt.cv.render import render_cv
from hunt.cv.workspace import knowledge_dir, resolve_data_dir
from hunt.cv.verify import lint_knowledge, verify_pdf

__all__ = [
    "finalize_pdf",
    "knowledge_dir",
    "lint_knowledge",
    "render_cv",
    "resolve_data_dir",
    "verify_pdf",
]
