"""YAML knowledge + emphasis profile -> tagged PDF.

Honesty gate: achievements with ``verified: false`` are excluded from
rendering and reported on stderr. The human must confirm them; the agent
must never flip the flag.
"""

from __future__ import annotations

import datetime
import sys
from pathlib import Path

import yaml
from jinja2 import Environment, FileSystemLoader
from weasyprint import HTML

from hunt.cv.workspace import cv_output_dir, knowledge_dir, resolve_data_dir

PACKAGE_DIR = Path(__file__).resolve().parent
TEMPLATE_DIR = PACKAGE_DIR / "template"

ALLOWED_EMPHASIS_KEYS = {
    "emphasis_profile",
    "profile_text",
    "headline",
    "include_achievements",
    "exclude_achievements",
    "projects",
    "project_bullets",
    "show_clients",
    "show_levels",
    "max_bullets_per_job",
    "output_name",
}


def _load(kb: Path, name: str):
    return yaml.safe_load((kb / name).read_text())


def _fmt_dates(pos: dict) -> str:
    def fmt(d):
        if d == "present":
            return "Present"
        y, m = d.split("-")
        return datetime.date(int(y), int(m), 1).strftime("%b %Y")

    end = pos.get("end", "present")
    return f"{fmt(pos['start'])} – {fmt(end)}"


def _pick_achievements(profile, tag_order, exclude=(), cap=None):
    def score(a):
        for i, t in enumerate(tag_order):
            if t in a.get("tags", []):
                return i
        return len(tag_order) + 1

    cands = [a for a in profile if a["id"] not in exclude]
    cands.sort(key=score)
    return cands[:cap] if cap else cands


def _validate_emphasis(opts, source: Path, known_profiles, ach_ids, pos_ids):
    problems = []
    unknown = set(opts) - ALLOWED_EMPHASIS_KEYS
    if unknown:
        problems.append(f"unknown emphasis keys: {sorted(unknown)}")
    prof = opts.get("emphasis_profile")
    if prof not in known_profiles:
        problems.append(f"emphasis_profile '{prof}' not in emphasis_profiles")
    for pid, ids in opts.get("include_achievements", {}).items():
        if pid not in pos_ids:
            problems.append(f"include_achievements names unknown position '{pid}'")
        for aid in ids:
            if aid not in ach_ids:
                problems.append(
                    f"unknown achievement id '{aid}' (position {pid})"
                )
    for aid in opts.get("exclude_achievements", []):
        if aid not in ach_ids:
            problems.append(f"unknown excluded achievement id '{aid}'")
    if problems:
        raise SystemExit(
            f"{source}: emphasis config failed validation:\n  - "
            + "\n  - ".join(problems)
        )


def _default_opts(emph_profiles, projects_all, prof) -> dict:
    return {
        "emphasis_profile": next(iter(emph_profiles)),
        "projects": [p["id"] for p in projects_all[:1]],
        "project_bullets": 1,
        "output_name": prof["name"].title().replace(" ", "_") + "_CV",
    }


def render_cv(
    *,
    data_dir: str | Path | None = None,
    emphasis_path: str | Path | None = None,
    output_dir: str | Path | None = None,
    output_name: str | None = None,
    quiet: bool = False,
) -> Path:
    """Render a tagged PDF into the workspace (never the source tree)."""
    data = resolve_data_dir(data_dir)
    kb = knowledge_dir(data)

    prof = _load(kb, "profile.yaml")
    positions = _load(kb, "positions.yaml")["positions"]
    ach_doc = _load(kb, "achievements.yaml")
    ach_list = ach_doc["achievements"]
    emph_profiles = ach_doc["emphasis_profiles"]
    skills = _load(kb, "skills.yaml")
    extra = _load(kb, "projects.yaml")
    certifications = extra["certifications"]
    projects_all = extra["projects"]

    if emphasis_path:
        source = Path(emphasis_path)
        opts = yaml.safe_load(source.read_text()) or {}
    else:
        source = kb / "emphasis.yaml"
        opts = _default_opts(emph_profiles, projects_all, prof)

    _validate_emphasis(
        opts,
        source,
        set(emph_profiles),
        {a["id"] for a in ach_list},
        {p["id"] for p in positions},
    )

    emph = opts["emphasis_profile"]
    tag_order = emph_profiles[emph]["order"]
    ach_by_id = {a["id"]: a for a in ach_list}

    held_back = []
    for pos in positions:
        for aid in opts.get("include_achievements", {}).get(
            pos["id"], pos.get("default_achievements", [])
        ):
            a = ach_by_id.get(aid)
            if a and not a.get("verified", False):
                held_back.append(aid)
    if held_back:
        print(
            "NOTE: excluded unverified achievements (human must confirm): "
            f"{sorted(set(held_back))}",
            file=sys.stderr,
        )

    jobs = []
    for pos in positions:
        ids = [
            i
            for i in opts.get("include_achievements", {}).get(
                pos["id"], pos.get("default_achievements", [])
            )
            if i in ach_by_id and ach_by_id[i].get("verified", False)
        ]
        bullets = _pick_achievements(
            [ach_by_id[i] for i in ids],
            tag_order,
            exclude=set(opts.get("exclude_achievements", [])),
            cap=opts.get("max_bullets_per_job"),
        )
        employer = pos["employer"] + (
            f" ({pos['client']})"
            if pos.get("client") and opts.get("show_clients", True)
            else ""
        )
        jobs.append(
            {
                "title": pos["title"],
                "employer": employer,
                "location": pos["location"],
                "dates": _fmt_dates(pos),
                "bullets": bullets,
            }
        )

    skill_lines = []
    for grp in skills["skill_groups"]:
        visible = [s for s in grp["skills"] if not s.get("employer_scope")]
        items = ", ".join(
            s["name"]
            + (
                f" ({s['level']})"
                if s["level"] in ("working", "limited")
                and opts.get("show_levels", True)
                else ""
            )
            for s in visible
        )
        skill_lines.append({"group": grp["name"], "skills_text": items})

    proj_ids = opts.get("projects", [])
    n_proj = opts.get("project_bullets", 2)
    projects = [
        {"name": p["name"], "summary": " ".join(p["bullets"][:n_proj])}
        for p in projects_all
        if p["id"] in proj_ids
    ]

    c = prof["contact"]
    ctx = {
        "name": prof["name"],
        "headline": opts.get(
            "headline",
            prof.get("headlines", {}).get(emph_profiles[emph]["headline_key"], ""),
        ),
        "contact_items": [c[k] for k in ("linkedin", "email", "phone") if k in c],
        "profile_text": opts.get("profile_text"),
        "jobs": jobs,
        "projects": projects,
        "skill_lines": skill_lines,
        "certifications": certifications,
        "education": prof.get("education", []),
        "languages": prof.get("languages"),
    }

    env = Environment(loader=FileSystemLoader(TEMPLATE_DIR), autoescape=False)
    html_doc = env.get_template("cv.html.j2").render(**ctx)

    out_dir = Path(output_dir).expanduser().resolve() if output_dir else cv_output_dir(data)
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = output_name or opts.get("output_name") or (
        prof["name"].title().replace(" ", "_") + "_CV"
    )
    stem = Path(str(stem)).name
    if stem.lower().endswith(".pdf"):
        stem = stem[:-4]
    if not stem or stem in {".", ".."}:
        stem = "CV"
    out = out_dir / f"{stem}.pdf"

    html_doc = html_doc.replace(
        "<head>",
        "<head>\n<title>Curriculum Vitae</title>\n"
        f'<meta name="author" content="{prof["name"]}">\n'
        '<meta name="description" content="Curriculum vitae">\n',
        1,
    )
    HTML(string=html_doc).write_pdf(out, pdf_variant="pdf/ua-1")
    if not quiet:
        print(out)
    return out
