"""``hunt <noun> <verb> --json`` — thin client of hunt.core.

Default human output is tables. ``--json`` is the agent contract.
Hunt never submits employer forms or sends mail.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from hunt.core.applications import (
    create_application,
    estimate_for_workspace,
    get_application,
    list_applications,
    restamp_derived,
    update_application,
)
from hunt.core.pay import PAY_UNITS, QuotedPay
from hunt.core.artifacts import add_file
from hunt.core.cv import render as render_cv
from hunt.core.errors import HuntError, ValidationError
from hunt.core.events import list_events
from hunt.core.facts import (
    confirm_achievement,
    confirm_position,
    create_achievement,
    create_position,
    create_project,
    get_achievement,
    get_integrity,
    get_position,
    get_profile,
    get_project,
    get_skills,
    list_achievements,
    list_positions,
    list_projects,
    update_achievement,
    update_integrity,
    update_position,
    update_profile,
    update_project,
    update_skills,
)
from hunt.core.inbox import (
    dismiss,
    list_inbox,
    promote,
    restore,
    serialize_inbox_item,
)
from hunt.core.jobs import JOB_TYPES, enqueue as enqueue_job, get_job, list_jobs
from hunt.core.sources import list_sources, run_source
from hunt.core.worker import drain, run_one
from hunt.agent.config import HARNESS_CHOICES, HARNESSES, OPENCODE_INSTALL_COMMAND
from hunt.core.workspace import Workspace

UNSET = object()


def _emit_error(message: str, as_json: bool) -> None:
    if as_json:
        print(json.dumps({"error": message}, ensure_ascii=False), file=sys.stderr)
    else:
        print(f"hunt: {message}", file=sys.stderr)


def _dump_json(obj: Any) -> None:
    print(json.dumps(obj, indent=2, ensure_ascii=False))


def _cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "yes" if value else "no"
    return str(value)


def print_table(rows: list[dict[str, Any]], columns: list[tuple[str, str]]) -> None:
    if not rows:
        print("(none)")
        return
    rendered = [
        {key: _cell(row.get(key)) for key, _header in columns} for row in rows
    ]
    widths = {
        key: max(len(header), max((len(r[key]) for r in rendered), default=0))
        for key, header in columns
    }
    print("  ".join(header.ljust(widths[key]) for key, header in columns))
    print("  ".join("-" * widths[key] for key, _header in columns))
    for row in rendered:
        print("  ".join(row[key].ljust(widths[key]) for key, _header in columns))


def print_kv(data: dict[str, Any]) -> None:
    rows = []
    for key, value in data.items():
        if isinstance(value, (dict, list)):
            text = json.dumps(value, ensure_ascii=False)
        else:
            text = _cell(value)
        rows.append({"field": key, "value": text})
    print_table(rows, [("field", "FIELD"), ("value", "VALUE")])


def _quoted_label(app: dict[str, Any]) -> str:
    quoted = app.get("comp_quoted") or {}
    if not quoted:
        return ""
    return f"{quoted.get('amount')} {quoted.get('currency')}/{quoted.get('unit')}"


def _derived_net(app: dict[str, Any]) -> str:
    derived = app.get("comp_derived") or {}
    net = app.get("net_month")
    if net is None:
        net = derived.get("net_month")
    if net is None:
        return ""
    currency = (
        app.get("display_currency")
        or derived.get("display_currency")
        or ""
    )
    return f"{net} {currency}".strip()


def _open(args: argparse.Namespace) -> Workspace:
    return Workspace.open(getattr(args, "data", None))


def _add_application_flags(parser: argparse.ArgumentParser, *, update: bool) -> None:
    default = UNSET if update else None
    parser.add_argument("--company", default=default)
    parser.add_argument("--source", default=default)
    parser.add_argument("--url", default=default)
    parser.add_argument("--title-posted", dest="title_posted", default=default)
    parser.add_argument("--title-ours", dest="title_ours", default=default)
    parser.add_argument("--location-country", dest="location_country", default=default)
    parser.add_argument("--location-city", dest="location_city", default=default)
    parser.add_argument("--modality", default=default)
    parser.add_argument(
        "--office-days",
        dest="office_days_per_week",
        type=float,
        default=default,
    )
    parser.add_argument("--engagement", default=default)
    parser.add_argument(
        "--duration-months", dest="duration_months", type=int, default=default
    )
    parser.add_argument("--comp-amount", dest="comp_amount", type=float, default=default)
    parser.add_argument("--comp-currency", dest="comp_currency", default=default)
    parser.add_argument("--comp-unit", dest="comp_unit", default=default)
    parser.add_argument("--comp-notes", dest="comp_notes", default=default)
    parser.add_argument("--tax-home", dest="tax_home_for_net", default=default)
    parser.add_argument(
        "--languages-required", dest="languages_required", default=default
    )
    parser.add_argument("--recruiter", default=default)
    parser.add_argument("--cv-variant", dest="cv_variant_id", default=default)
    parser.add_argument("--knockouts", default=default)
    parser.add_argument("--extra", default=default)
    parser.add_argument("--status", default=default if update else "researching")


_APP_FIELD_NAMES = (
    "company",
    "source",
    "url",
    "title_posted",
    "title_ours",
    "location_country",
    "location_city",
    "modality",
    "office_days_per_week",
    "engagement",
    "duration_months",
    "comp_amount",
    "comp_currency",
    "comp_unit",
    "comp_notes",
    "tax_home_for_net",
    "languages_required",
    "recruiter",
    "cv_variant_id",
    "knockouts",
    "extra",
    "status",
)


def _fields_from_args(args: argparse.Namespace, *, skip_unset: bool) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for name in _APP_FIELD_NAMES:
        if not hasattr(args, name):
            continue
        value = getattr(args, name)
        if skip_unset and value is UNSET:
            continue
        if not skip_unset and value is None and name != "status":
            continue
        out[name] = value
    return out


def cmd_applications_list(args: argparse.Namespace) -> None:
    with _open(args) as ws:
        apps = [a.to_dict() for a in list_applications(ws, status=args.status)]
    if args.json:
        _dump_json({"applications": apps})
        return
    print_table(
        [
            {
                "id": a["id"],
                "company": a["company"],
                "title": a.get("title_ours") or a.get("title_posted") or "",
                "status": a["status"],
                "quoted": _quoted_label(a),
                "net_month": _derived_net(a),
            }
            for a in apps
        ],
        [
            ("id", "ID"),
            ("company", "COMPANY"),
            ("title", "TITLE"),
            ("status", "STATUS"),
            ("quoted", "QUOTED"),
            ("net_month", "NET/MO"),
        ],
    )


def cmd_applications_get(args: argparse.Namespace) -> None:
    with _open(args) as ws:
        app = get_application(ws, args.id).to_dict()
    if args.json:
        _dump_json({"application": app})
        return
    print_kv(app)


def cmd_applications_create(args: argparse.Namespace) -> None:
    fields = _fields_from_args(args, skip_unset=False)
    fields.setdefault("company", None)
    with _open(args) as ws:
        app = create_application(ws, **fields).to_dict()
    if args.json:
        _dump_json({"application": app})
        return
    print_kv(app)


def cmd_applications_update(args: argparse.Namespace) -> None:
    fields = _fields_from_args(args, skip_unset=True)
    with _open(args) as ws:
        app = update_application(ws, args.id, **fields).to_dict()
    if args.json:
        _dump_json({"application": app})
        return
    print_kv(app)


def cmd_inbox_list(args: argparse.Namespace) -> None:
    status = args.status
    with _open(args) as ws:
        items = [
            serialize_inbox_item(ws, i)
            for i in list_inbox(
                ws,
                status=status,
                source_id=args.source,
                knockout=args.knockout,
                triage_action=args.triage,
            )
        ]
    if args.json:
        _dump_json({"inbox": items})
        return
    print_table(
        [
            {
                "id": i["id"],
                "company": i.get("company"),
                "role": i.get("role") or i.get("title"),
                "location": i.get("location") or "",
                "engagement": i.get("engagement_label") or i.get("engagement") or "",
                "net_month": _derived_net(i),
                "triage": ((i.get("triage") or {}) or {}).get("action") or "",
                "knockouts": ", ".join(i.get("knockouts") or []),
                "why_keep": i.get("why_keep") or "",
                "why_risk": i.get("why_risk") or "",
            }
            for i in items
        ],
        [
            ("id", "ID"),
            ("company", "COMPANY"),
            ("role", "ROLE"),
            ("location", "LOCATION"),
            ("engagement", "ENGAGEMENT"),
            ("net_month", "NET/MO"),
            ("triage", "TRIAGE"),
            ("knockouts", "KNOCKOUTS"),
            ("why_keep", "WHY KEEP"),
            ("why_risk", "WHY RISK"),
        ],
    )


def cmd_inbox_promote(args: argparse.Namespace) -> None:
    fields = _fields_from_args(args, skip_unset=True)
    with _open(args) as ws:
        app = promote(ws, args.id, **fields).to_dict()
    if args.json:
        _dump_json({"application": app})
        return
    print_kv(app)


def cmd_inbox_dismiss(args: argparse.Namespace) -> None:
    with _open(args) as ws:
        item = serialize_inbox_item(ws, dismiss(ws, args.id))
    if args.json:
        _dump_json({"inbox_item": item})
        return
    print_kv(item)


def cmd_inbox_restore(args: argparse.Namespace) -> None:
    with _open(args) as ws:
        item = serialize_inbox_item(ws, restore(ws, args.id))
    if args.json:
        _dump_json({"inbox_item": item})
        return
    print_kv(item)


def cmd_artifacts_add(args: argparse.Namespace) -> None:
    with _open(args) as ws:
        art = add_file(
            ws, args.application, args.file, kind=args.kind or "other"
        ).to_dict()
    if args.json:
        _dump_json({"artifact": art})
        return
    print_kv(art)


def cmd_events_list(args: argparse.Namespace) -> None:
    with _open(args) as ws:
        events = [e.to_dict() for e in list_events(ws, args.application)]
    if args.json:
        _dump_json({"events": events})
        return
    print_table(
        events,
        [
            ("at", "AT"),
            ("actor", "ACTOR"),
            ("kind", "KIND"),
            ("body", "BODY"),
            ("id", "ID"),
        ],
    )


def cmd_sources_list(args: argparse.Namespace) -> None:
    with _open(args) as ws:
        sources = [s.to_dict() for s in list_sources(ws)]
    if args.json:
        _dump_json({"sources": sources})
        return
    print_table(
        sources,
        [
            ("id", "ID"),
            ("name", "NAME"),
            ("kind", "KIND"),
            ("enabled", "ENABLED"),
            ("last_run_at", "LAST RUN"),
            ("last_status", "STATUS"),
            ("listing_count", "LISTINGS"),
            ("inbox_count", "INBOX"),
        ],
    )


def cmd_sources_run(args: argparse.Namespace) -> None:
    with _open(args) as ws:
        job = run_source(ws, args.id)
        if args.run:
            out = run_one(ws, job.id)
            if args.json:
                _dump_json(out)
                return
            print_kv(out["job"])
            return
        payload = {"job": job.to_dict()}
    if args.json:
        _dump_json(payload)
        return
    print_kv(payload["job"])


def cmd_jobs_list(args: argparse.Namespace) -> None:
    with _open(args) as ws:
        jobs = [
            j.to_dict()
            for j in list_jobs(
                ws,
                state=args.state,
                job_type=args.type,
                target_id=args.target,
            )
        ]
    if args.json:
        _dump_json({"jobs": jobs})
        return
    print_table(
        jobs,
        [
            ("id", "ID"),
            ("type", "TYPE"),
            ("target_id", "TARGET"),
            ("state", "STATE"),
            ("created_at", "CREATED"),
            ("error", "ERROR"),
        ],
    )


def cmd_jobs_enqueue(args: argparse.Namespace) -> None:
    payload: dict[str, Any] = {}
    if args.payload:
        parsed = json.loads(args.payload)
        if not isinstance(parsed, dict):
            raise HuntError("--payload must be a JSON object")
        payload.update(parsed)
    if args.emphasis:
        payload["emphasis"] = args.emphasis
    if args.variant:
        payload["variant"] = args.variant
    with _open(args) as ws:
        job = enqueue_job(
            ws, job_type=args.type, target_id=args.target, payload=payload or None
        )
        if args.run:
            out = run_one(ws, job.id)
            if args.json:
                _dump_json(out)
                return
            print_kv(out["job"])
            return
        data = job.to_dict()
    if args.json:
        _dump_json({"job": data})
        return
    print_kv(data)


def cmd_jobs_status(args: argparse.Namespace) -> None:
    with _open(args) as ws:
        job = get_job(ws, args.id).to_dict()
    if args.json:
        _dump_json({"job": job})
        return
    print_kv(job)


def cmd_jobs_run(args: argparse.Namespace) -> None:
    with _open(args) as ws:
        out = run_one(ws, args.id)
    if args.json:
        _dump_json(out)
        return
    print_kv(out["job"])


def cmd_jobs_worker(args: argparse.Namespace) -> None:
    with _open(args) as ws:
        runs = drain(ws)
    if args.json:
        _dump_json({"runs": runs})
        return
    if not runs:
        print("(none)")
        return
    print_table(
        [r["job"] for r in runs],
        [
            ("id", "ID"),
            ("type", "TYPE"),
            ("state", "STATE"),
            ("error", "ERROR"),
        ],
    )


def cmd_mcp(args: argparse.Namespace) -> None:
    from hunt.mcp import serve_stdio

    serve_stdio(data_dir=getattr(args, "data", None))


def cmd_serve(args: argparse.Namespace) -> None:
    from hunt.http.app import serve

    serve(data_dir=getattr(args, "data", None), host=args.host, port=args.port)


def cmd_pay_estimate(args: argparse.Namespace) -> None:
    try:
        quoted = QuotedPay(
            amount=float(args.amount),
            currency=str(args.currency),
            unit=str(args.unit),
        )
    except ValueError as exc:
        raise ValidationError(str(exc)) from exc
    with _open(args) as ws:
        estimate = estimate_for_workspace(
            ws,
            quoted,
            country=args.country,
            engagement=args.engagement,
            tax_home=args.tax_home,
        )
    if args.json:
        _dump_json(estimate)
        return
    print_kv(estimate)


def cmd_pay_restamp(args: argparse.Namespace) -> None:
    with _open(args) as ws:
        result = restamp_derived(ws)
    if args.json:
        _dump_json(result)
        return
    print_kv(result)


def _json_object_arg(value: str | None, *, flag: str = "--set") -> dict[str, Any]:
    if not value:
        return {}
    parsed = json.loads(value)
    if not isinstance(parsed, dict):
        raise ValidationError(f"{flag} must be a JSON object")
    return parsed


def _merge_set_and_flags(
    args: argparse.Namespace, names: tuple[str, ...], *, skip_unset: bool
) -> dict[str, Any]:
    fields = _json_object_arg(getattr(args, "set", None))
    for name in names:
        if not hasattr(args, name):
            continue
        value = getattr(args, name)
        if skip_unset and value is UNSET:
            continue
        if not skip_unset and value is None:
            continue
        fields[name] = value
    return fields


def _add_set_and_confirm(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--set",
        dest="set",
        help="JSON object of fields (agent contract for nested values)",
    )
    parser.add_argument(
        "--confirm",
        action="store_true",
        help="Human confirmation: allow verified: true / integrity strengthen",
    )


def _truncate(text: Any, width: int = 56) -> str:
    value = "" if text is None else str(text)
    if len(value) <= width:
        return value
    return value[: width - 1] + "…"


def cmd_profile_get(args: argparse.Namespace) -> None:
    with _open(args) as ws:
        profile = get_profile(ws)
    if args.json:
        _dump_json({"profile": profile})
        return
    public = {k: v for k, v in profile.items() if k != "contact"}
    public["contact"] = ",".join(sorted((profile.get("contact") or {})))
    print_kv(public)


def cmd_profile_update(args: argparse.Namespace) -> None:
    fields = _merge_set_and_flags(
        args,
        ("name", "location", "citizenship", "relocation"),
        skip_unset=True,
    )
    with _open(args) as ws:
        profile = update_profile(ws, fields, confirm=bool(args.confirm))
    if args.json:
        _dump_json({"profile": profile})
        return
    print_kv({k: v for k, v in profile.items() if k != "contact"})


def cmd_positions_list(args: argparse.Namespace) -> None:
    with _open(args) as ws:
        rows = list_positions(ws)
    if args.json:
        _dump_json({"positions": rows})
        return
    print_table(
        [
            {
                "id": r.get("id"),
                "title": r.get("title"),
                "employer": r.get("employer"),
                "start": r.get("start"),
                "end": r.get("end"),
                "verified": r.get("verified"),
            }
            for r in rows
        ],
        [
            ("id", "ID"),
            ("title", "TITLE"),
            ("employer", "EMPLOYER"),
            ("start", "START"),
            ("end", "END"),
            ("verified", "VERIFIED"),
        ],
    )


def cmd_positions_get(args: argparse.Namespace) -> None:
    with _open(args) as ws:
        row = get_position(ws, args.id)
    if args.json:
        _dump_json({"position": row})
        return
    print_kv(row)


def cmd_positions_create(args: argparse.Namespace) -> None:
    fields = _merge_set_and_flags(
        args,
        (
            "id",
            "title",
            "employer",
            "location",
            "start",
            "end",
            "client",
            "scope_facts",
            "default_achievements",
            "verified",
        ),
        skip_unset=False,
    )
    with _open(args) as ws:
        row = create_position(ws, fields, confirm=bool(args.confirm))
    if args.json:
        _dump_json({"position": row})
        return
    print_kv(row)


def cmd_positions_update(args: argparse.Namespace) -> None:
    fields = _merge_set_and_flags(
        args,
        (
            "title",
            "employer",
            "location",
            "start",
            "end",
            "client",
            "scope_facts",
            "default_achievements",
            "verified",
        ),
        skip_unset=True,
    )
    with _open(args) as ws:
        row = update_position(ws, args.id, fields, confirm=bool(args.confirm))
    if args.json:
        _dump_json({"position": row})
        return
    print_kv(row)


def cmd_positions_confirm(args: argparse.Namespace) -> None:
    with _open(args) as ws:
        row = confirm_position(ws, args.id, confirm=True)
    if args.json:
        _dump_json({"position": row})
        return
    print_kv(row)


def cmd_achievements_list(args: argparse.Namespace) -> None:
    with _open(args) as ws:
        rows = list_achievements(ws)
    if args.json:
        _dump_json({"achievements": rows})
        return
    print_table(
        [
            {
                "id": r.get("id"),
                "verified": r.get("verified"),
                "tags": ",".join(r.get("tags") or []),
                "text": _truncate(r.get("text")),
            }
            for r in rows
        ],
        [
            ("id", "ID"),
            ("verified", "VERIFIED"),
            ("tags", "TAGS"),
            ("text", "TEXT"),
        ],
    )


def cmd_achievements_get(args: argparse.Namespace) -> None:
    with _open(args) as ws:
        row = get_achievement(ws, args.id)
    if args.json:
        _dump_json({"achievement": row})
        return
    print_kv(row)


def cmd_achievements_create(args: argparse.Namespace) -> None:
    fields = _merge_set_and_flags(
        args,
        ("id", "text", "tags", "evidence", "verified"),
        skip_unset=False,
    )
    with _open(args) as ws:
        row = create_achievement(ws, fields, confirm=bool(args.confirm))
    if args.json:
        _dump_json({"achievement": row})
        return
    print_kv(row)


def cmd_achievements_update(args: argparse.Namespace) -> None:
    fields = _merge_set_and_flags(
        args,
        ("text", "tags", "evidence", "verified"),
        skip_unset=True,
    )
    with _open(args) as ws:
        row = update_achievement(ws, args.id, fields, confirm=bool(args.confirm))
    if args.json:
        _dump_json({"achievement": row})
        return
    print_kv(row)


def cmd_achievements_confirm(args: argparse.Namespace) -> None:
    with _open(args) as ws:
        row = confirm_achievement(ws, args.id, confirm=True)
    if args.json:
        _dump_json({"achievement": row})
        return
    print_kv(row)


def cmd_projects_list(args: argparse.Namespace) -> None:
    with _open(args) as ws:
        payload = list_projects(ws)
    if args.json:
        _dump_json(payload)
        return
    print_table(
        [
            {"id": r.get("id"), "name": r.get("name")}
            for r in payload["projects"]
        ],
        [("id", "ID"), ("name", "NAME")],
    )


def cmd_projects_get(args: argparse.Namespace) -> None:
    with _open(args) as ws:
        row = get_project(ws, args.id)
    if args.json:
        _dump_json({"project": row})
        return
    print_kv(row)


def cmd_projects_create(args: argparse.Namespace) -> None:
    fields = _merge_set_and_flags(
        args, ("id", "name", "bullets", "note"), skip_unset=False
    )
    with _open(args) as ws:
        row = create_project(ws, fields, confirm=bool(args.confirm))
    if args.json:
        _dump_json({"project": row})
        return
    print_kv(row)


def cmd_projects_update(args: argparse.Namespace) -> None:
    fields = _merge_set_and_flags(
        args, ("name", "bullets", "note"), skip_unset=True
    )
    with _open(args) as ws:
        row = update_project(ws, args.id, fields, confirm=bool(args.confirm))
    if args.json:
        _dump_json({"project": row})
        return
    print_kv(row)


def cmd_skills_get(args: argparse.Namespace) -> None:
    with _open(args) as ws:
        skills = get_skills(ws)
    if args.json:
        _dump_json({"skills": skills})
        return
    rows = []
    for group in skills.get("skill_groups") or []:
        names = ", ".join(s.get("name", "") for s in group.get("skills") or [])
        rows.append({"group": group.get("name"), "skills": names})
    print_table(rows, [("group", "GROUP"), ("skills", "SKILLS")])


def cmd_skills_update(args: argparse.Namespace) -> None:
    fields = _json_object_arg(getattr(args, "set", None))
    with _open(args) as ws:
        skills = update_skills(ws, fields, confirm=bool(args.confirm))
    if args.json:
        _dump_json({"skills": skills})
        return
    print_kv({"groups": len(skills.get("skill_groups") or [])})


def cmd_integrity_get(args: argparse.Namespace) -> None:
    with _open(args) as ws:
        integrity = get_integrity(ws)
    if args.json:
        _dump_json({"integrity": integrity})
        return
    print_kv(
        {
            "forbidden_phrases": len(integrity.get("forbidden_phrases") or []),
            "line_traps": len(integrity.get("line_traps") or []),
            "quantifier_terms": len(integrity.get("quantifier_terms") or []),
        }
    )


def cmd_integrity_update(args: argparse.Namespace) -> None:
    fields = _json_object_arg(getattr(args, "set", None))
    with _open(args) as ws:
        integrity = update_integrity(ws, fields, confirm=bool(args.confirm))
    if args.json:
        _dump_json({"integrity": integrity})
        return
    print_kv(
        {
            "forbidden_phrases": len(integrity.get("forbidden_phrases") or []),
            "line_traps": len(integrity.get("line_traps") or []),
            "quantifier_terms": len(integrity.get("quantifier_terms") or []),
        }
    )


def cmd_agent_install(args: argparse.Namespace) -> None:
    from hunt.agent.install import install as install_agent

    result = install_agent(
        harness=args.harness,
        data_dir=getattr(args, "data", None),
        root=getattr(args, "root", None),
        home=getattr(args, "home", None),
        role=getattr(args, "role", None),
        dry_run=bool(getattr(args, "dry_run", False)),
    )
    if args.json:
        _dump_json(result)
        return
    print_kv(
        {
            "harness": result["harness"],
            "roles": ", ".join(result["roles"]),
            "files": len(result.get("files") or []),
            "note": result["note"],
        }
    )
    rows = result.get("writes") or [{"path": path} for path in result.get("files") or []]
    print_table(
        [{"path": row["path"] if isinstance(row, dict) else row} for row in rows],
        [("path", "DRY-RUN" if getattr(args, "dry_run", False) else "WROTE")],
    )


def cmd_agent_status(args: argparse.Namespace) -> None:
    from hunt.agent.status import agent_status

    with _open(args) as ws:
        payload = agent_status(
            ws,
            harness=getattr(args, "harness", None),
            root=getattr(args, "root", None),
            home=getattr(args, "home", None),
        )
    if args.json:
        _dump_json(payload)
        return
    print_kv(
        {
            "harness": payload["harness"],
            "harness_detected": payload.get("harness_detected"),
            "base_url": payload["model"]["base_url"],
            "model": payload["model"]["model"],
            "api_key_env": payload["model"].get("api_key_env"),
            "api_key_set": payload["api_key_set"],
            "state": payload["state"],
        }
    )


def cmd_agent_config(args: argparse.Namespace) -> None:
    from hunt.agent.status import agent_status, save_agent

    harness = getattr(args, "harness", None)
    model: dict[str, Any] = {}
    if getattr(args, "base_url", None) is not None:
        model["base_url"] = args.base_url
    if getattr(args, "api_key_env", None) is not None:
        model["api_key_env"] = args.api_key_env
    if getattr(args, "model", None) is not None:
        model["model"] = args.model
    with _open(args) as ws:
        if harness is None and not model:
            payload = agent_status(
                ws,
                root=getattr(args, "root", None),
                home=getattr(args, "home", None),
            )
        else:
            payload = save_agent(
                ws,
                harness=harness,
                model=model or None,
                root=getattr(args, "root", None),
                home=getattr(args, "home", None),
            )
    if args.json:
        _dump_json(payload)
        return
    print_kv(
        {
            "harness": payload["harness"],
            "base_url": payload["model"]["base_url"],
            "model": payload["model"]["model"],
            "api_key_env": payload["model"].get("api_key_env"),
            "api_key_set": payload["api_key_set"],
            "state": payload.get("state"),
        }
    )


def cmd_agent_secret_set(args: argparse.Namespace) -> None:
    from hunt.agent.status import set_agent_secret

    value = getattr(args, "value", None)
    if value is None:
        value = sys.stdin.read()
        if value.endswith("\n"):
            value = value[:-1]
    with _open(args) as ws:
        payload = set_agent_secret(ws, args.env, value)
    if args.json:
        _dump_json({"api_key_set": payload["api_key_set"], "env": args.env})
        return
    print_kv({"env": args.env, "api_key_set": payload["api_key_set"]})


def cmd_agent_secret_unset(args: argparse.Namespace) -> None:
    from hunt.agent.status import unset_agent_secret

    with _open(args) as ws:
        payload = unset_agent_secret(ws, args.env)
    if args.json:
        _dump_json({"api_key_set": payload["api_key_set"], "env": args.env})
        return
    print_kv({"env": args.env, "api_key_set": payload["api_key_set"]})


def cmd_agent_doctor(args: argparse.Namespace) -> None:
    from hunt.agent.doctor import doctor as run_doctor

    with _open(args) as ws:
        report = run_doctor(
            ws,
            root=getattr(args, "root", None),
            home=getattr(args, "home", None),
            timeout=float(getattr(args, "timeout", 3.0) or 3.0),
        )
    if args.json:
        _dump_json(report)
    else:
        print_table(
            [
                {"check": row["id"], "ok": row["ok"], "detail": row["detail"]}
                for row in report["checks"]
            ],
            [("check", "CHECK"), ("ok", "OK"), ("detail", "DETAIL")],
        )
        for warning in report.get("warnings") or []:
            print(f"warning  {warning}")
    if not report["ok"]:
        raise HuntError("doctor found problems")


def cmd_agent_run(args: argparse.Namespace) -> None:
    from hunt.agent.run import exec_run, prepare_run, runner_env

    with _open(args) as ws:
        plan = prepare_run(
            ws,
            args.role,
            root=getattr(args, "root", None),
        )
        env = runner_env(ws)
    if args.json:
        _dump_json(plan)
        sys.stdout.flush()
    elif plan.get("ok"):
        print(" ".join(plan["command"]))
    else:
        print(plan.get("install") or OPENCODE_INSTALL_COMMAND)
    if not plan.get("ok"):
        raise HuntError("no supported harness on PATH")
    if getattr(args, "dry_run", False):
        return
    exec_run(plan, env)


def cmd_cv_render(args: argparse.Namespace) -> None:
    with _open(args) as ws:
        result = render_cv(
            ws,
            application_id=args.application,
            emphasis=args.emphasis,
            variant=args.variant,
        )
    if args.json:
        _dump_json(result)
        return
    print_kv(result)


def _split_globals(argv: list[str]) -> tuple[list[str], str | None, bool]:
    """Allow ``--json`` / ``--data`` before or after the noun/verb."""
    rest: list[str] = []
    data = None
    as_json = False
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg == "--json":
            as_json = True
            i += 1
            continue
        if arg == "--data":
            if i + 1 >= len(argv):
                raise HuntError("--data needs a directory")
            data = argv[i + 1]
            i += 2
            continue
        if arg.startswith("--data="):
            data = arg.split("=", 1)[1]
            i += 1
            continue
        rest.append(arg)
        i += 1
    return rest, data, as_json


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="hunt",
        description=(
            "Hunt CLI. One workspace via HUNT_DATA or --data. "
            "Never submits applications or sends mail."
        ),
    )
    parser.add_argument("--data", help="Workspace directory (else $HUNT_DATA)")
    parser.add_argument(
        "--json",
        action="store_true",
        help="Machine-readable output (stable field names, no banners)",
    )
    nouns = parser.add_subparsers(dest="noun", required=True)

    apps = nouns.add_parser("applications", help="Board CRUD")
    app_verbs = apps.add_subparsers(dest="verb", required=True)

    p_list = app_verbs.add_parser("list", help="List applications")
    p_list.add_argument("--status")
    p_list.set_defaults(func=cmd_applications_list)

    p_get = app_verbs.add_parser("get", help="Get one application")
    p_get.add_argument("id")
    p_get.set_defaults(func=cmd_applications_get)

    p_create = app_verbs.add_parser("create", help="Create an application")
    _add_application_flags(p_create, update=False)
    p_create.set_defaults(func=cmd_applications_create)

    p_update = app_verbs.add_parser("update", help="Update an application")
    p_update.add_argument("id")
    _add_application_flags(p_update, update=True)
    p_update.set_defaults(func=cmd_applications_update)

    inbox = nouns.add_parser("inbox", help="Screened listings")
    inbox_verbs = inbox.add_subparsers(dest="verb", required=True)

    p_in_list = inbox_verbs.add_parser("list", help="List inbox items")
    p_in_list.add_argument("--status", default="pending")
    p_in_list.add_argument("--source", help="Filter by listings.source_id")
    p_in_list.add_argument("--knockout", help="Filter by knockout code (use | for OR)")
    p_in_list.add_argument(
        "--triage",
        choices=["dismiss", "keep", "unsure"],
        help="Filter by Layer 2 triage action",
    )
    p_in_list.set_defaults(func=cmd_inbox_list)

    p_promote = inbox_verbs.add_parser(
        "promote", help="Create an application from a pending inbox item"
    )
    p_promote.add_argument("id")
    _add_application_flags(p_promote, update=True)
    p_promote.set_defaults(func=cmd_inbox_promote)

    p_dismiss = inbox_verbs.add_parser("dismiss", help="Dismiss a pending inbox item")
    p_dismiss.add_argument("id")
    p_dismiss.set_defaults(func=cmd_inbox_dismiss)

    p_restore = inbox_verbs.add_parser(
        "restore", help="Restore a dismissed inbox item to pending"
    )
    p_restore.add_argument("id")
    p_restore.set_defaults(func=cmd_inbox_restore)

    artifacts = nouns.add_parser("artifacts", help="Application files")
    art_verbs = artifacts.add_subparsers(dest="verb", required=True)
    p_add = art_verbs.add_parser("add", help="Copy a file into the workspace")
    p_add.add_argument("--application", required=True)
    p_add.add_argument("--file", required=True)
    p_add.add_argument("--kind", default="other")
    p_add.set_defaults(func=cmd_artifacts_add)

    events = nouns.add_parser("events", help="Application log")
    ev_verbs = events.add_subparsers(dest="verb", required=True)
    p_ev = ev_verbs.add_parser("list", help="List events for an application")
    p_ev.add_argument("--application", required=True)
    p_ev.set_defaults(func=cmd_events_list)

    pay = nouns.add_parser("pay", help="Quoted → net / month estimates")
    pay_verbs = pay.add_subparsers(dest="verb", required=True)
    p_est = pay_verbs.add_parser(
        "estimate",
        help="Net / month in display currency from country + engagement",
    )
    p_est.add_argument("--country", help="CH|ES|PL or a common name")
    p_est.add_argument(
        "--engagement",
        help="fte or freelance (b2b / jdg / autonomo / uop map onto those)",
    )
    p_est.add_argument("--amount", type=float, required=True)
    p_est.add_argument("--currency", required=True)
    p_est.add_argument("--unit", required=True, choices=list(PAY_UNITS))
    p_est.add_argument("--tax-home", dest="tax_home", help="Optional override (legacy key or CH.fte)")
    p_est.set_defaults(func=cmd_pay_estimate)
    p_rst = pay_verbs.add_parser(
        "restamp",
        help="Recompute stored derived pay from current tax_homes + FX",
    )
    p_rst.set_defaults(func=cmd_pay_restamp)

    cv = nouns.add_parser("cv", help="Honesty-gated CV pipeline")
    cv_verbs = cv.add_subparsers(dest="verb", required=True)
    p_cv = cv_verbs.add_parser("render", help="Render a PDF into $HUNT_DATA")
    p_cv.add_argument("--application")
    p_cv.add_argument("--emphasis")
    p_cv.add_argument("--variant")
    p_cv.set_defaults(func=cmd_cv_render)

    profile = nouns.add_parser("profile", help="Knowledge profile (contact lives here only)")
    profile_verbs = profile.add_subparsers(dest="verb", required=True)
    profile_verbs.add_parser("get", help="Get profile").set_defaults(func=cmd_profile_get)
    p_prof_up = profile_verbs.add_parser("update", help="Update profile fields")
    p_prof_up.add_argument("--name", default=UNSET)
    p_prof_up.add_argument("--location", default=UNSET)
    p_prof_up.add_argument("--citizenship", default=UNSET)
    p_prof_up.add_argument("--relocation", default=UNSET)
    _add_set_and_confirm(p_prof_up)
    p_prof_up.set_defaults(func=cmd_profile_update)

    positions = nouns.add_parser("positions", help="Employment history in knowledge/")
    pos_verbs = positions.add_subparsers(dest="verb", required=True)
    pos_verbs.add_parser("list", help="List positions").set_defaults(func=cmd_positions_list)
    p_pos_get = pos_verbs.add_parser("get", help="Get one position")
    p_pos_get.add_argument("id")
    p_pos_get.set_defaults(func=cmd_positions_get)
    p_pos_create = pos_verbs.add_parser("create", help="Create a draft position")
    p_pos_create.add_argument("--id")
    p_pos_create.add_argument("--title")
    p_pos_create.add_argument("--employer")
    p_pos_create.add_argument("--location")
    p_pos_create.add_argument("--start")
    p_pos_create.add_argument("--end")
    p_pos_create.add_argument("--client")
    p_pos_create.add_argument("--scope-facts", dest="scope_facts")
    p_pos_create.add_argument("--default-achievements", dest="default_achievements")
    p_pos_create.add_argument(
        "--verified", action=argparse.BooleanOptionalAction, default=None
    )
    _add_set_and_confirm(p_pos_create)
    p_pos_create.set_defaults(func=cmd_positions_create)
    p_pos_up = pos_verbs.add_parser("update", help="Update a position")
    p_pos_up.add_argument("id")
    p_pos_up.add_argument("--title", default=UNSET)
    p_pos_up.add_argument("--employer", default=UNSET)
    p_pos_up.add_argument("--location", default=UNSET)
    p_pos_up.add_argument("--start", default=UNSET)
    p_pos_up.add_argument("--end", default=UNSET)
    p_pos_up.add_argument("--client", default=UNSET)
    p_pos_up.add_argument("--scope-facts", dest="scope_facts", default=UNSET)
    p_pos_up.add_argument(
        "--default-achievements", dest="default_achievements", default=UNSET
    )
    p_pos_up.add_argument(
        "--verified", action=argparse.BooleanOptionalAction, default=UNSET
    )
    _add_set_and_confirm(p_pos_up)
    p_pos_up.set_defaults(func=cmd_positions_update)
    p_pos_cf = pos_verbs.add_parser("confirm", help="Human-only: set verified true")
    p_pos_cf.add_argument("id")
    p_pos_cf.set_defaults(func=cmd_positions_confirm)

    achievements = nouns.add_parser("achievements", help="CV bullet bank")
    ach_verbs = achievements.add_subparsers(dest="verb", required=True)
    ach_verbs.add_parser("list", help="List achievements").set_defaults(
        func=cmd_achievements_list
    )
    p_ach_get = ach_verbs.add_parser("get", help="Get one achievement")
    p_ach_get.add_argument("id")
    p_ach_get.set_defaults(func=cmd_achievements_get)
    p_ach_create = ach_verbs.add_parser("create", help="Create a draft achievement")
    p_ach_create.add_argument("--id")
    p_ach_create.add_argument("--text")
    p_ach_create.add_argument("--tags")
    p_ach_create.add_argument("--evidence")
    p_ach_create.add_argument(
        "--verified", action=argparse.BooleanOptionalAction, default=None
    )
    _add_set_and_confirm(p_ach_create)
    p_ach_create.set_defaults(func=cmd_achievements_create)
    p_ach_up = ach_verbs.add_parser("update", help="Update an achievement")
    p_ach_up.add_argument("id")
    p_ach_up.add_argument("--text", default=UNSET)
    p_ach_up.add_argument("--tags", default=UNSET)
    p_ach_up.add_argument("--evidence", default=UNSET)
    p_ach_up.add_argument(
        "--verified", action=argparse.BooleanOptionalAction, default=UNSET
    )
    _add_set_and_confirm(p_ach_up)
    p_ach_up.set_defaults(func=cmd_achievements_update)
    p_ach_cf = ach_verbs.add_parser("confirm", help="Human-only: set verified true")
    p_ach_cf.add_argument("id")
    p_ach_cf.set_defaults(func=cmd_achievements_confirm)

    projects = nouns.add_parser("projects", help="Personal projects in knowledge/")
    proj_verbs = projects.add_subparsers(dest="verb", required=True)
    proj_verbs.add_parser("list", help="List projects").set_defaults(func=cmd_projects_list)
    p_proj_get = proj_verbs.add_parser("get", help="Get one project")
    p_proj_get.add_argument("id")
    p_proj_get.set_defaults(func=cmd_projects_get)
    p_proj_create = proj_verbs.add_parser("create", help="Create a project")
    p_proj_create.add_argument("--id")
    p_proj_create.add_argument("--name")
    p_proj_create.add_argument("--bullets")
    p_proj_create.add_argument("--note")
    _add_set_and_confirm(p_proj_create)
    p_proj_create.set_defaults(func=cmd_projects_create)
    p_proj_up = proj_verbs.add_parser("update", help="Update a project")
    p_proj_up.add_argument("id")
    p_proj_up.add_argument("--name", default=UNSET)
    p_proj_up.add_argument("--bullets", default=UNSET)
    p_proj_up.add_argument("--note", default=UNSET)
    _add_set_and_confirm(p_proj_up)
    p_proj_up.set_defaults(func=cmd_projects_update)

    skills = nouns.add_parser("skills", help="Skill groups in knowledge/")
    skill_verbs = skills.add_subparsers(dest="verb", required=True)
    skill_verbs.add_parser("get", help="Get skill groups").set_defaults(func=cmd_skills_get)
    p_sk_up = skill_verbs.add_parser("update", help="Update skill_groups / forbidden_claims")
    _add_set_and_confirm(p_sk_up)
    p_sk_up.set_defaults(func=cmd_skills_update)

    integrity = nouns.add_parser("integrity", help="Honesty-gate rules (workspace data)")
    integ_verbs = integrity.add_subparsers(dest="verb", required=True)
    integ_verbs.add_parser("get", help="Get integrity rules").set_defaults(
        func=cmd_integrity_get
    )
    p_in_up = integ_verbs.add_parser(
        "update", help="Human-only: add integrity rules (cannot weaken via agent)"
    )
    _add_set_and_confirm(p_in_up)
    p_in_up.set_defaults(func=cmd_integrity_update)

    sources = nouns.add_parser("sources", help="Configured source adapters")
    src_verbs = sources.add_subparsers(dest="verb", required=True)
    p_src_list = src_verbs.add_parser("list", help="List sources from config.yaml")
    p_src_list.set_defaults(func=cmd_sources_list)
    p_src_run = src_verbs.add_parser("run", help="Enqueue source-poll for a source")
    p_src_run.add_argument("id")
    p_src_run.add_argument(
        "--run",
        action="store_true",
        help="Claim and execute the job immediately (still does not apply)",
    )
    p_src_run.set_defaults(func=cmd_sources_run)

    jobs_p = nouns.add_parser("jobs", help="Job queue")
    job_verbs = jobs_p.add_subparsers(dest="verb", required=True)
    p_job_list = job_verbs.add_parser("list", help="List jobs")
    p_job_list.add_argument("--state")
    p_job_list.add_argument("--type")
    p_job_list.add_argument("--target")
    p_job_list.set_defaults(func=cmd_jobs_list)
    p_job_en = job_verbs.add_parser("enqueue", help="Enqueue a job")
    p_job_en.add_argument("--type", required=True, choices=list(JOB_TYPES))
    p_job_en.add_argument("--target")
    p_job_en.add_argument("--payload", help="JSON object merged into the job payload")
    p_job_en.add_argument("--emphasis")
    p_job_en.add_argument("--variant")
    p_job_en.add_argument(
        "--run",
        action="store_true",
        help="Claim and execute immediately",
    )
    p_job_en.set_defaults(func=cmd_jobs_enqueue)
    p_job_st = job_verbs.add_parser("status", help="Get one job")
    p_job_st.add_argument("id")
    p_job_st.set_defaults(func=cmd_jobs_status)
    p_job_run = job_verbs.add_parser("run", help="Claim and run one queued job")
    p_job_run.add_argument("id", nargs="?")
    p_job_run.set_defaults(func=cmd_jobs_run)
    p_job_w = job_verbs.add_parser("worker", help="Drain the queued jobs (cli backend)")
    p_job_w.set_defaults(func=cmd_jobs_worker)

    nouns.add_parser("mcp", help="stdio MCP server").set_defaults(func=cmd_mcp)

    agent = nouns.add_parser(
        "agent",
        help="Install Hunt packs into an existing harness (not a Hunt runner)",
    )
    agent_verbs = agent.add_subparsers(dest="verb", required=True)
    p_agent_in = agent_verbs.add_parser(
        "install",
        help="Write Hunt MCP + role skills into a harness you already run",
    )
    p_agent_in.add_argument(
        "--harness",
        required=True,
        choices=list(HARNESSES),
        help="claude, cursor, codex, opencode, openclaw, or paperclip",
    )
    p_agent_in.add_argument(
        "--role",
        choices=["operator", "screener", "all"],
        default="all",
        help="Which pack to copy (default: both)",
    )
    p_agent_in.add_argument("--root", help="Project directory for harness files")
    p_agent_in.add_argument(
        "--home",
        help="Override home for user-level files (Codex ~/.codex)",
    )
    p_agent_in.add_argument(
        "--dry-run",
        action="store_true",
        help="List files that would be written without writing them",
    )
    p_agent_in.set_defaults(func=cmd_agent_install)
    p_agent_status = agent_verbs.add_parser(
        "status",
        help="Harness + model settings (no secret values)",
    )
    p_agent_status.add_argument(
        "--harness",
        choices=list(HARNESSES),
        help="Preview writes for this harness",
    )
    p_agent_status.add_argument("--root", help="Project directory to scan")
    p_agent_status.add_argument("--home", help="Override home for user-level files")
    p_agent_status.set_defaults(func=cmd_agent_status)
    p_agent_cfg = agent_verbs.add_parser(
        "config",
        help="Get or set agent.harness / agent.model (keys stay in secrets.env)",
    )
    p_agent_cfg.add_argument("--harness", choices=list(HARNESS_CHOICES))
    p_agent_cfg.add_argument("--base-url", dest="base_url")
    p_agent_cfg.add_argument("--api-key-env", dest="api_key_env")
    p_agent_cfg.add_argument("--model")
    p_agent_cfg.add_argument("--root", help="Project directory to scan after save")
    p_agent_cfg.add_argument("--home", help="Override home for user-level files")
    p_agent_cfg.set_defaults(func=cmd_agent_config)
    p_agent_secret = agent_verbs.add_parser(
        "secret",
        help="Write a model key to secrets.env (never echoes the value)",
    )
    secret_verbs = p_agent_secret.add_subparsers(dest="secret_verb", required=True)
    p_secret_set = secret_verbs.add_parser("set", help="Set env=value in secrets.env")
    p_secret_set.add_argument("--env", required=True)
    p_secret_set.add_argument(
        "--value",
        help="Secret value (prefer stdin). Never logged.",
    )
    p_secret_set.set_defaults(func=cmd_agent_secret_set)
    p_secret_unset = secret_verbs.add_parser("unset", help="Remove a key from secrets.env")
    p_secret_unset.add_argument("--env", required=True)
    p_secret_unset.set_defaults(func=cmd_agent_secret_unset)
    p_agent_doc = agent_verbs.add_parser(
        "doctor",
        help="Check workspace, MCP, skills, and GET {base_url}/models",
    )
    p_agent_doc.add_argument("--root", help="Project directory to scan")
    p_agent_doc.add_argument("--home", help="Override home for user-level files")
    p_agent_doc.add_argument(
        "--timeout",
        type=float,
        default=3.0,
        help="Seconds to wait for GET /v1/models",
    )
    p_agent_doc.set_defaults(func=cmd_agent_doctor)
    p_agent_run = agent_verbs.add_parser(
        "run",
        help="Exec OpenCode, then Claude Code / Codex; does not vendor a loop",
    )
    p_agent_run.add_argument("role", choices=["operator", "screener"])
    p_agent_run.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the runner command without exec",
    )
    p_agent_run.add_argument("--root", help="cwd for the runner")
    p_agent_run.set_defaults(func=cmd_agent_run)

    serve_p = nouns.add_parser("serve", help="HTTP + UI on 127.0.0.1")
    serve_p.add_argument("--host", default=None)
    serve_p.add_argument("--port", type=int, default=None)
    serve_p.set_defaults(func=cmd_serve)

    return parser


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    as_json = "--json" in argv
    parser = build_parser()
    try:
        rest, data, as_json = _split_globals(argv)
        args = parser.parse_args(rest)
        if data:
            args.data = data
        args.json = bool(as_json or args.json)
    except HuntError as exc:
        _emit_error(str(exc), as_json)
        return int(exc.exit_code)
    except SystemExit as exc:
        code = exc.code
        return int(code) if isinstance(code, int) else 2
    as_json = bool(args.json)
    try:
        args.func(args)
        return 0
    except HuntError as exc:
        _emit_error(str(exc), as_json)
        return int(exc.exit_code)
    except json.JSONDecodeError as exc:
        _emit_error(f"invalid JSON: {exc}", as_json)
        return 2
