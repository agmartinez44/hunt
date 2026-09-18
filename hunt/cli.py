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
    get_application,
    list_applications,
    update_application,
)
from hunt.core.artifacts import add_file
from hunt.core.cv import render as render_cv
from hunt.core.errors import HuntError
from hunt.core.events import list_events
from hunt.core.inbox import dismiss, list_inbox, promote
from hunt.core.jobs import JOB_TYPES, enqueue as enqueue_job, get_job, list_jobs
from hunt.core.sources import list_sources, run_source
from hunt.core.worker import drain, run_one
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
    net = derived.get("net_month")
    if net is None:
        return ""
    currency = derived.get("display_currency") or ""
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
                "clears_floor": (a.get("comp_derived") or {}).get("clears_floor"),
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
            ("clears_floor", "FLOOR"),
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
        items = [i.to_dict() for i in list_inbox(ws, status=status)]
    if args.json:
        _dump_json({"inbox": items})
        return
    print_table(
        [
            {
                "id": i["id"],
                "company": i.get("company"),
                "title": i.get("title"),
                "status": i["status"],
                "knockouts": ",".join(i.get("knockouts") or []),
            }
            for i in items
        ],
        [
            ("id", "ID"),
            ("company", "COMPANY"),
            ("title", "TITLE"),
            ("status", "STATUS"),
            ("knockouts", "KNOCKOUTS"),
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
        item = dismiss(ws, args.id).to_dict()
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

    cv = nouns.add_parser("cv", help="Honesty-gated CV pipeline")
    cv_verbs = cv.add_subparsers(dest="verb", required=True)
    p_cv = cv_verbs.add_parser("render", help="Render a PDF into $HUNT_DATA")
    p_cv.add_argument("--application")
    p_cv.add_argument("--emphasis")
    p_cv.add_argument("--variant")
    p_cv.set_defaults(func=cmd_cv_render)

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
