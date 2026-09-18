"""Knowledge YAML as the single biography — structured read/write.

Facts live in ``$HUNT_DATA/knowledge/*.yaml``. hunt.cv keeps reading those
files. This module does not grow a second store.

Honesty:
- Agent writes (MCP, jobs) create/update **draft** records (``verified: false``).
- Only a human (HTTP UI, or CLI with ``confirm=True``) may set ``verified: true``.
- Agents cannot weaken ``scope_facts``, skills ``forbidden_claims``, or
  integrity rules.
- Contact details stay on ``profile.contact``.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from pathlib import Path
from typing import Any

import yaml

from hunt.core.context import actor
from hunt.core.errors import NotFoundError, ValidationError
from hunt.core.workspace import Workspace, WorkspaceError

ID_RE = re.compile(r"^[a-z][a-z0-9-]*$")
CONTACT_KEYS = frozenset({"email", "phone", "linkedin"})
PROFILE_FILE = "profile.yaml"
POSITIONS_FILE = "positions.yaml"
ACHIEVEMENTS_FILE = "achievements.yaml"
PROJECTS_FILE = "projects.yaml"
SKILLS_FILE = "skills.yaml"
INTEGRITY_FILE = "integrity.yaml"

POSITION_FIELDS = frozenset(
    {
        "title",
        "employer",
        "location",
        "start",
        "end",
        "client",
        "scope_facts",
        "default_achievements",
        "verified",
    }
)
ACHIEVEMENT_FIELDS = frozenset({"text", "tags", "evidence", "verified"})
PROJECT_FIELDS = frozenset({"name", "bullets", "note"})
PROFILE_FIELDS = frozenset(
    {
        "name",
        "headlines",
        "location",
        "citizenship",
        "relocation",
        "languages",
        "contact",
        "education",
    }
)
SKILL_DOC_FIELDS = frozenset({"skill_groups", "forbidden_claims"})

_AGENT_ACTORS = frozenset({"mcp", "job"})


def _kb(ws: Workspace) -> Path:
    kb = ws.root / "knowledge"
    if not kb.is_dir():
        raise WorkspaceError(
            f"Workspace {ws.root} has no knowledge/ directory. "
            "Copy example-workspace/ and edit the fictional YAML."
        )
    return kb


def _path(ws: Workspace, name: str) -> Path:
    path = _kb(ws) / name
    if not path.is_file():
        raise WorkspaceError(f"knowledge file missing: {path}")
    return path


def _jsonable(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_jsonable(v) for v in value]
    return value


def _load(ws: Workspace, name: str) -> dict[str, Any]:
    raw = yaml.safe_load(_path(ws, name).read_text(encoding="utf-8"))
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ValidationError(f"{name} must be a YAML mapping")
    return _jsonable(raw)


def _dump(ws: Workspace, name: str, data: dict[str, Any]) -> None:
    path = _path(ws, name)
    text = yaml.safe_dump(
        data,
        sort_keys=False,
        allow_unicode=True,
        default_flow_style=False,
        width=88,
    )
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def _human_write(confirm: bool) -> bool:
    """True when this write is allowed to confirm / keep verified facts."""
    who = actor()
    if who in _AGENT_ACTORS:
        return False
    if who == "ui":
        return True
    if who == "cli":
        return bool(confirm)
    return False


def _validate_id(value: str, *, kind: str) -> str:
    text = str(value).strip()
    if not ID_RE.match(text):
        raise ValidationError(
            f"{kind} id must be a lowercase slug [a-z][a-z0-9-]* (got {value!r})"
        )
    return text


def _as_str_list(value: Any, *, field: str) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        if text.startswith("["):
            parsed = yaml.safe_load(text)
            if not isinstance(parsed, list):
                raise ValidationError(f"{field} must be a list")
            return [str(v) for v in parsed]
        return [part.strip() for part in text.split(",") if part.strip()]
    if isinstance(value, list):
        return [str(v) for v in value]
    raise ValidationError(f"{field} must be a list")


def _as_bool(value: Any, *, field: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "yes", "1"}:
            return True
        if lowered in {"false", "no", "0"}:
            return False
    raise ValidationError(f"{field} must be a boolean")


def _reject_contact_leak(fields: dict[str, Any], *, where: str) -> None:
    leaked = sorted(CONTACT_KEYS.intersection(fields))
    if leaked:
        raise ValidationError(
            f"contact details belong in profile.contact, not {where}: {leaked}"
        )


def _reject_unknown(fields: dict[str, Any], allowed: frozenset[str], *, kind: str) -> None:
    extra = sorted(k for k in fields if k not in allowed and k != "id")
    if extra:
        raise ValidationError(f"unknown {kind} fields: {extra}")


def _freeze(value: Any) -> Any:
    if isinstance(value, dict):
        return tuple(sorted((str(k), _freeze(v)) for k, v in value.items()))
    if isinstance(value, list):
        return tuple(_freeze(v) for v in value)
    return value


def _cannot_weaken(old: list[Any], new: list[Any], *, label: str, confirm: bool) -> None:
    if _human_write(confirm):
        return
    missing = {_freeze(item) for item in old} - {_freeze(item) for item in new}
    if missing:
        raise ValidationError(
            f"agents cannot weaken {label}; only the human may remove rules"
        )


def _apply_verified(
    record: dict[str, Any],
    fields: dict[str, Any],
    *,
    confirm: bool,
    kind: str,
) -> None:
    human = _human_write(confirm)
    if "verified" in fields:
        want = _as_bool(fields["verified"], field="verified")
        if want and not human:
            raise ValidationError(
                f"agents cannot set {kind} verified: true; "
                "leave it as a draft or confirm from the UI / CLI --confirm"
            )
        record["verified"] = want
        return
    if not human:
        record["verified"] = False


def _slug(text: str, *, kind: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    if not slug or not ID_RE.match(slug):
        raise ValidationError(f"could not derive a {kind} id from {text!r}")
    return slug[:48]


# --- profile ---


def get_profile(ws: Workspace) -> dict[str, Any]:
    return _load(ws, PROFILE_FILE)


def update_profile(
    ws: Workspace, fields: dict[str, Any], *, confirm: bool = False
) -> dict[str, Any]:
    if not fields:
        raise ValidationError("no profile fields to update")
    _reject_unknown(fields, PROFILE_FIELDS, kind="profile")
    if "contact" in fields and not _human_write(confirm):
        raise ValidationError("only the human may update profile contact details")
    doc = _load(ws, PROFILE_FILE)
    for key, value in fields.items():
        if key == "contact":
            if not isinstance(value, dict):
                raise ValidationError("contact must be an object")
            leaked = [k for k in value if k not in CONTACT_KEYS]
            if leaked:
                raise ValidationError(f"unknown contact fields: {sorted(leaked)}")
            contact = dict(doc.get("contact") or {})
            contact.update(value)
            doc["contact"] = contact
        else:
            doc[key] = value
    _dump(ws, PROFILE_FILE, doc)
    return get_profile(ws)


# --- positions ---


def _positions_doc(ws: Workspace) -> dict[str, Any]:
    doc = _load(ws, POSITIONS_FILE)
    if "positions" not in doc:
        doc["positions"] = []
    if not isinstance(doc["positions"], list):
        raise ValidationError("positions.yaml must contain a positions list")
    return doc


def _find_position(doc: dict[str, Any], position_id: str) -> dict[str, Any]:
    for row in doc["positions"]:
        if isinstance(row, dict) and row.get("id") == position_id:
            return row
    raise NotFoundError(f"position not found: {position_id}")


def list_positions(ws: Workspace) -> list[dict[str, Any]]:
    return [dict(row) for row in _positions_doc(ws)["positions"] if isinstance(row, dict)]


def get_position(ws: Workspace, position_id: str) -> dict[str, Any]:
    return dict(_find_position(_positions_doc(ws), position_id))


def create_position(
    ws: Workspace, fields: dict[str, Any], *, confirm: bool = False
) -> dict[str, Any]:
    _reject_contact_leak(fields, where="positions")
    payload = dict(fields)
    raw_id = payload.pop("id", None)
    _reject_unknown(payload, POSITION_FIELDS, kind="position")
    title = payload.get("title")
    employer = payload.get("employer")
    start = payload.get("start")
    if not title or not employer or not start:
        raise ValidationError("position create requires title, employer, and start")
    position_id = _validate_id(raw_id, kind="position") if raw_id else _slug(
        str(employer), kind="position"
    )
    doc = _positions_doc(ws)
    if any(row.get("id") == position_id for row in doc["positions"] if isinstance(row, dict)):
        raise ValidationError(f"position id already exists: {position_id}")
    record: dict[str, Any] = {
        "id": position_id,
        "title": str(title),
        "employer": str(employer),
        "location": payload.get("location") or "",
        "start": str(start),
        "end": str(payload.get("end") or "present"),
        "scope_facts": _as_str_list(payload.get("scope_facts"), field="scope_facts"),
        "default_achievements": _as_str_list(
            payload.get("default_achievements"), field="default_achievements"
        ),
    }
    if "client" in payload and payload["client"]:
        record["client"] = payload["client"]
    _apply_verified(record, payload, confirm=confirm, kind="position")
    if "verified" not in record:
        record["verified"] = bool(_human_write(confirm) and payload.get("verified"))
    doc["positions"].append(record)
    _dump(ws, POSITIONS_FILE, doc)
    return dict(record)


def update_position(
    ws: Workspace,
    position_id: str,
    fields: dict[str, Any],
    *,
    confirm: bool = False,
) -> dict[str, Any]:
    if not fields:
        raise ValidationError("no position fields to update")
    _reject_contact_leak(fields, where="positions")
    payload = dict(fields)
    payload.pop("id", None)
    _reject_unknown(payload, POSITION_FIELDS, kind="position")
    doc = _positions_doc(ws)
    record = _find_position(doc, position_id)
    if "scope_facts" in payload:
        new_facts = _as_str_list(payload["scope_facts"], field="scope_facts")
        _cannot_weaken(
            record.get("scope_facts") or [],
            new_facts,
            label="scope_facts",
            confirm=confirm,
        )
        record["scope_facts"] = new_facts
    if "default_achievements" in payload:
        record["default_achievements"] = _as_str_list(
            payload["default_achievements"], field="default_achievements"
        )
    for key in ("title", "employer", "location", "start", "end", "client"):
        if key in payload:
            record[key] = payload[key]
    _apply_verified(record, payload, confirm=confirm, kind="position")
    _dump(ws, POSITIONS_FILE, doc)
    return dict(record)


def confirm_position(ws: Workspace, position_id: str, *, confirm: bool = True) -> dict[str, Any]:
    if not _human_write(confirm):
        raise ValidationError("only the human may confirm a position")
    return update_position(ws, position_id, {"verified": True}, confirm=True)


# --- achievements ---


def _achievements_doc(ws: Workspace) -> dict[str, Any]:
    doc = _load(ws, ACHIEVEMENTS_FILE)
    if "achievements" not in doc:
        doc["achievements"] = []
    if not isinstance(doc["achievements"], list):
        raise ValidationError("achievements.yaml must contain an achievements list")
    return doc


def _find_achievement(doc: dict[str, Any], achievement_id: str) -> dict[str, Any]:
    for row in doc["achievements"]:
        if isinstance(row, dict) and row.get("id") == achievement_id:
            return row
    raise NotFoundError(f"achievement not found: {achievement_id}")


def list_achievements(ws: Workspace) -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in _achievements_doc(ws)["achievements"]
        if isinstance(row, dict)
    ]


def get_achievement(ws: Workspace, achievement_id: str) -> dict[str, Any]:
    return dict(_find_achievement(_achievements_doc(ws), achievement_id))


def get_emphasis_profiles(ws: Workspace) -> dict[str, Any]:
    doc = _achievements_doc(ws)
    profiles = doc.get("emphasis_profiles") or {}
    if not isinstance(profiles, dict):
        raise ValidationError("emphasis_profiles must be a mapping")
    return dict(profiles)


def create_achievement(
    ws: Workspace, fields: dict[str, Any], *, confirm: bool = False
) -> dict[str, Any]:
    _reject_contact_leak(fields, where="achievements")
    payload = dict(fields)
    raw_id = payload.pop("id", None)
    _reject_unknown(payload, ACHIEVEMENT_FIELDS, kind="achievement")
    text = payload.get("text")
    evidence = payload.get("evidence")
    if not text or not evidence:
        raise ValidationError("achievement create requires text and evidence")
    achievement_id = (
        _validate_id(raw_id, kind="achievement")
        if raw_id
        else _slug(str(text), kind="achievement")
    )
    doc = _achievements_doc(ws)
    if any(
        row.get("id") == achievement_id
        for row in doc["achievements"]
        if isinstance(row, dict)
    ):
        raise ValidationError(f"achievement id already exists: {achievement_id}")
    record: dict[str, Any] = {
        "id": achievement_id,
        "text": str(text),
        "tags": _as_str_list(payload.get("tags"), field="tags"),
        "evidence": str(evidence),
    }
    _apply_verified(record, payload, confirm=confirm, kind="achievement")
    if "verified" not in record:
        record["verified"] = False
    doc["achievements"].append(record)
    _dump(ws, ACHIEVEMENTS_FILE, doc)
    return dict(record)


def update_achievement(
    ws: Workspace,
    achievement_id: str,
    fields: dict[str, Any],
    *,
    confirm: bool = False,
) -> dict[str, Any]:
    if not fields:
        raise ValidationError("no achievement fields to update")
    _reject_contact_leak(fields, where="achievements")
    payload = dict(fields)
    payload.pop("id", None)
    _reject_unknown(payload, ACHIEVEMENT_FIELDS, kind="achievement")
    doc = _achievements_doc(ws)
    record = _find_achievement(doc, achievement_id)
    if "tags" in payload:
        record["tags"] = _as_str_list(payload["tags"], field="tags")
    for key in ("text", "evidence"):
        if key in payload:
            record[key] = payload[key]
    _apply_verified(record, payload, confirm=confirm, kind="achievement")
    _dump(ws, ACHIEVEMENTS_FILE, doc)
    return dict(record)


def confirm_achievement(
    ws: Workspace, achievement_id: str, *, confirm: bool = True
) -> dict[str, Any]:
    if not _human_write(confirm):
        raise ValidationError("only the human may confirm an achievement")
    return update_achievement(ws, achievement_id, {"verified": True}, confirm=True)


# --- projects ---


def _projects_doc(ws: Workspace) -> dict[str, Any]:
    doc = _load(ws, PROJECTS_FILE)
    if "projects" not in doc:
        doc["projects"] = []
    if not isinstance(doc["projects"], list):
        raise ValidationError("projects.yaml must contain a projects list")
    if "certifications" in doc and not isinstance(doc["certifications"], list):
        raise ValidationError("certifications must be a list")
    return doc


def _find_project(doc: dict[str, Any], project_id: str) -> dict[str, Any]:
    for row in doc["projects"]:
        if isinstance(row, dict) and row.get("id") == project_id:
            return row
    raise NotFoundError(f"project not found: {project_id}")


def list_projects(ws: Workspace) -> dict[str, Any]:
    doc = _projects_doc(ws)
    return {
        "projects": [dict(row) for row in doc["projects"] if isinstance(row, dict)],
        "certifications": list(doc.get("certifications") or []),
    }


def get_project(ws: Workspace, project_id: str) -> dict[str, Any]:
    return dict(_find_project(_projects_doc(ws), project_id))


def create_project(
    ws: Workspace, fields: dict[str, Any], *, confirm: bool = False
) -> dict[str, Any]:
    del confirm  # projects have no verified flag
    _reject_contact_leak(fields, where="projects")
    payload = dict(fields)
    raw_id = payload.pop("id", None)
    _reject_unknown(payload, PROJECT_FIELDS, kind="project")
    name = payload.get("name")
    if not name:
        raise ValidationError("project create requires name")
    project_id = _validate_id(raw_id, kind="project") if raw_id else _slug(
        str(name), kind="project"
    )
    doc = _projects_doc(ws)
    if any(row.get("id") == project_id for row in doc["projects"] if isinstance(row, dict)):
        raise ValidationError(f"project id already exists: {project_id}")
    record: dict[str, Any] = {
        "id": project_id,
        "name": str(name),
        "bullets": _as_str_list(payload.get("bullets"), field="bullets"),
    }
    if payload.get("note"):
        record["note"] = payload["note"]
    doc["projects"].append(record)
    _dump(ws, PROJECTS_FILE, doc)
    return dict(record)


def update_project(
    ws: Workspace, project_id: str, fields: dict[str, Any], *, confirm: bool = False
) -> dict[str, Any]:
    del confirm
    if not fields:
        raise ValidationError("no project fields to update")
    _reject_contact_leak(fields, where="projects")
    payload = dict(fields)
    payload.pop("id", None)
    _reject_unknown(payload, PROJECT_FIELDS, kind="project")
    doc = _projects_doc(ws)
    record = _find_project(doc, project_id)
    if "bullets" in payload:
        record["bullets"] = _as_str_list(payload["bullets"], field="bullets")
    for key in ("name", "note"):
        if key in payload:
            record[key] = payload[key]
    _dump(ws, PROJECTS_FILE, doc)
    return dict(record)


def update_certifications(
    ws: Workspace, certifications: list[Any], *, confirm: bool = False
) -> dict[str, Any]:
    del confirm
    if not isinstance(certifications, list):
        raise ValidationError("certifications must be a list")
    doc = _projects_doc(ws)
    doc["certifications"] = certifications
    _dump(ws, PROJECTS_FILE, doc)
    return list_projects(ws)


# --- skills ---


def get_skills(ws: Workspace) -> dict[str, Any]:
    return _load(ws, SKILLS_FILE)


def update_skills(
    ws: Workspace, fields: dict[str, Any], *, confirm: bool = False
) -> dict[str, Any]:
    if not fields:
        raise ValidationError("no skills fields to update")
    _reject_unknown(fields, SKILL_DOC_FIELDS, kind="skills")
    doc = get_skills(ws)
    if "forbidden_claims" in fields:
        new_claims = fields["forbidden_claims"]
        if not isinstance(new_claims, list):
            raise ValidationError("forbidden_claims must be a list")
        _cannot_weaken(
            doc.get("forbidden_claims") or [],
            new_claims,
            label="forbidden_claims",
            confirm=confirm,
        )
        doc["forbidden_claims"] = new_claims
    if "skill_groups" in fields:
        groups = fields["skill_groups"]
        if not isinstance(groups, list):
            raise ValidationError("skill_groups must be a list")
        doc["skill_groups"] = groups
    _dump(ws, SKILLS_FILE, doc)
    return get_skills(ws)


# --- integrity (read; human-only strengthen) ---


def get_integrity(ws: Workspace) -> dict[str, Any]:
    return _load(ws, INTEGRITY_FILE)


def update_integrity(
    ws: Workspace, fields: dict[str, Any], *, confirm: bool = False
) -> dict[str, Any]:
    """Human may add rules. Agents cannot write integrity at all."""
    if not _human_write(confirm):
        raise ValidationError("agents cannot write integrity rules")
    if not fields:
        raise ValidationError("no integrity fields to update")
    allowed = frozenset({"forbidden_phrases", "line_traps", "quantifier_terms"})
    _reject_unknown(fields, allowed, kind="integrity")
    doc = get_integrity(ws)
    for key in ("forbidden_phrases", "line_traps", "quantifier_terms"):
        if key not in fields:
            continue
        new_val = fields[key]
        if not isinstance(new_val, list):
            raise ValidationError(f"{key} must be a list")
        _cannot_weaken(doc.get(key) or [], new_val, label=key, confirm=confirm)
        doc[key] = new_val
    _dump(ws, INTEGRITY_FILE, doc)
    return get_integrity(ws)
