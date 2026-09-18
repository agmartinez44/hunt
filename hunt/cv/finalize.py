"""Strip producer metadata and set neutral PDF docinfo.

Run after render, before verify. Idempotent.
"""

from __future__ import annotations

from pathlib import Path

import pikepdf
import yaml

from hunt.cv.workspace import knowledge_dir, resolve_data_dir


def default_author(data_dir: str | Path | None = None) -> str:
    try:
        kb = knowledge_dir(resolve_data_dir(data_dir))
        prof = yaml.safe_load((kb / "profile.yaml").read_text())
        return str(prof["name"])
    except Exception:
        return "Curriculum Vitae"


def finalize_pdf(
    pdf_path: str | Path,
    author: str | None = None,
    *,
    data_dir: str | Path | None = None,
) -> Path:
    pdf_path = Path(pdf_path)
    if author is None:
        author = default_author(data_dir)
    with pikepdf.open(pdf_path, allow_overwriting_input=True) as pdf:
        with pdf.open_metadata(set_pikepdf_as_editor=False) as meta:
            meta.load_from_docinfo(pdf.docinfo)
            for key in (
                "xmp:CreatorTool",
                "pdf:Producer",
                "xmp:CreateDate",
                "xmp:ModifyDate",
                "xmp:MetadataDate",
            ):
                if key in meta:
                    del meta[key]
        di = pdf.docinfo
        di["/Title"] = "Curriculum Vitae"
        di["/Author"] = author
        for k in (
            "/Creator",
            "/Producer",
            "/CreationDate",
            "/ModDate",
            "/Keywords",
            "/Subject",
        ):
            if k in di:
                del di[k]
        pdf.save(pdf_path)
    print(f"finalized: {pdf_path}")
    return pdf_path
