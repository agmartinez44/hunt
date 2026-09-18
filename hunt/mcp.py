"""``hunt mcp`` — stdio MCP server. Tools map 1:1 onto hunt.core / CLI nouns."""

from __future__ import annotations

import json
import sys
from typing import Any

from hunt import __version__
from hunt.core.applications import (
    create_application,
    get_application,
    list_applications,
    update_application,
)
from hunt.core.artifacts import add_file
from hunt.core.context import current_actor
from hunt.core.cv import render as render_cv
from hunt.core.errors import HuntError
from hunt.core.events import list_events
from hunt.core.inbox import dismiss, list_inbox, promote
from hunt.core.jobs import JOB_TYPES, enqueue as enqueue_job, get_job, list_jobs
from hunt.core.sources import list_sources, run_source
from hunt.core.worker import drain, run_one
from hunt.core.workspace import Workspace

PROTOCOL_VERSION = "2024-11-05"

APP_PROPS = {
    "company": {"type": "string"},
    "source": {"type": "string"},
    "url": {"type": "string"},
    "title_posted": {"type": "string"},
    "title_ours": {"type": "string"},
    "location_country": {"type": "string"},
    "location_city": {"type": "string"},
    "modality": {"type": "string"},
    "office_days_per_week": {"type": "number"},
    "engagement": {"type": "string"},
    "duration_months": {"type": "integer"},
    "comp_amount": {"type": "number"},
    "comp_currency": {"type": "string"},
    "comp_unit": {"type": "string"},
    "comp_notes": {"type": "string"},
    "tax_home_for_net": {"type": "string"},
    "languages_required": {"type": "string"},
    "recruiter": {"type": "string"},
    "cv_variant_id": {"type": "string"},
    "knockouts": {"type": "string"},
    "extra": {"type": "string"},
    "status": {"type": "string"},
}

_APP_KEYS = tuple(APP_PROPS)


def _app_fields(arguments: dict[str, Any]) -> dict[str, Any]:
    return {key: arguments[key] for key in _APP_KEYS if key in arguments}


def _ws(data_dir: str | None) -> Workspace:
    return Workspace.open(data_dir)


def _ok(payload: Any) -> dict[str, Any]:
    return {
        "content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False)}],
        "isError": False,
    }


def _err(message: str) -> dict[str, Any]:
    return {
        "content": [
            {"type": "text", "text": json.dumps({"error": message}, ensure_ascii=False)}
        ],
        "isError": True,
    }


def _tool(name: str, description: str, properties: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    schema: dict[str, Any] = {"type": "object", "properties": properties}
    if required:
        schema["required"] = required
    return {"name": name, "description": description, "inputSchema": schema}


TOOLS: list[dict[str, Any]] = [
    _tool("applications_list", "List applications", {"status": {"type": "string"}}),
    _tool("applications_get", "Get one application", {"id": {"type": "string"}}, ["id"]),
    _tool(
        "applications_create",
        "Create an application (manual). Listings must use inbox_promote.",
        APP_PROPS,
        ["company"],
    ),
    _tool(
        "applications_update",
        "Update an application",
        {"id": {"type": "string"}, **APP_PROPS},
        ["id"],
    ),
    _tool("inbox_list", "List inbox items", {"status": {"type": "string"}}),
    _tool(
        "inbox_promote",
        "Promote a pending inbox item to an application. This is the only listing → application path.",
        {"id": {"type": "string"}, **APP_PROPS},
        ["id"],
    ),
    _tool("inbox_dismiss", "Dismiss a pending inbox item", {"id": {"type": "string"}}, ["id"]),
    _tool(
        "jobs_list",
        "List jobs",
        {
            "state": {"type": "string"},
            "type": {"type": "string"},
            "target_id": {"type": "string"},
        },
    ),
    _tool(
        "jobs_enqueue",
        "Enqueue source-poll, screen-inbox, or tailor-cv. Never apply/send.",
        {
            "type": {"type": "string", "enum": list(JOB_TYPES)},
            "target_id": {"type": "string"},
            "emphasis": {"type": "string"},
            "variant": {"type": "string"},
            "run": {"type": "boolean"},
        },
        ["type"],
    ),
    _tool("jobs_status", "Get one job", {"id": {"type": "string"}}, ["id"]),
    _tool(
        "jobs_run",
        "Claim and run the next queued job, or a specific id. Does not apply or send mail.",
        {"id": {"type": "string"}, "drain": {"type": "boolean"}},
    ),
    _tool(
        "cv_render",
        "Render a CV PDF into $HUNT_DATA attachments. Presence of a PDF is not a submission.",
        {
            "application_id": {"type": "string"},
            "emphasis": {"type": "string"},
            "variant": {"type": "string"},
        },
    ),
    _tool("sources_list", "List configured sources", {}),
    _tool(
        "sources_run",
        "Enqueue source-poll for a source. Does not create applications.",
        {"id": {"type": "string"}, "run": {"type": "boolean"}},
        ["id"],
    ),
    _tool(
        "artifacts_add",
        "Copy a file into an application attachments dir",
        {
            "application_id": {"type": "string"},
            "file": {"type": "string"},
            "kind": {"type": "string"},
        },
        ["application_id", "file"],
    ),
    _tool(
        "events_list",
        "List application events",
        {"application_id": {"type": "string"}},
        ["application_id"],
    ),
]


def _call(name: str, arguments: dict[str, Any], data_dir: str | None) -> dict[str, Any]:
    token = current_actor.set("mcp")
    try:
        with _ws(data_dir) as ws:
            return _dispatch(name, arguments or {}, ws)
    except HuntError as exc:
        return _err(str(exc))
    except json.JSONDecodeError as exc:
        return _err(f"invalid JSON: {exc}")
    finally:
        current_actor.reset(token)


def _dispatch(name: str, arguments: dict[str, Any], ws: Workspace) -> dict[str, Any]:
    if name == "applications_list":
        apps = [a.to_dict() for a in list_applications(ws, status=arguments.get("status"))]
        return _ok({"applications": apps})
    if name == "applications_get":
        return _ok({"application": get_application(ws, arguments["id"]).to_dict()})
    if name == "applications_create":
        fields = _app_fields(arguments)
        app = create_application(ws, **fields).to_dict()
        return _ok({"application": app})
    if name == "applications_update":
        app_id = arguments["id"]
        fields = _app_fields(arguments)
        app = update_application(ws, app_id, **fields).to_dict()
        return _ok({"application": app})
    if name == "inbox_list":
        status = arguments.get("status", "pending")
        items = [i.to_dict() for i in list_inbox(ws, status=status)]
        return _ok({"inbox": items})
    if name == "inbox_promote":
        item_id = arguments["id"]
        fields = _app_fields(arguments)
        app = promote(ws, item_id, **fields).to_dict()
        return _ok({"application": app})
    if name == "inbox_dismiss":
        item = dismiss(ws, arguments["id"]).to_dict()
        return _ok({"inbox_item": item})
    if name == "jobs_list":
        jobs = [
            j.to_dict()
            for j in list_jobs(
                ws,
                state=arguments.get("state"),
                job_type=arguments.get("type"),
                target_id=arguments.get("target_id"),
            )
        ]
        return _ok({"jobs": jobs})
    if name == "jobs_enqueue":
        job_type = arguments["type"]
        payload: dict[str, Any] = {}
        if arguments.get("emphasis"):
            payload["emphasis"] = arguments["emphasis"]
        if arguments.get("variant"):
            payload["variant"] = arguments["variant"]
        job = enqueue_job(
            ws,
            job_type=job_type,
            target_id=arguments.get("target_id"),
            payload=payload or None,
        )
        if arguments.get("run"):
            out = run_one(ws, job.id)
            return _ok(out)
        return _ok({"job": job.to_dict()})
    if name == "jobs_status":
        return _ok({"job": get_job(ws, arguments["id"]).to_dict()})
    if name == "jobs_run":
        if arguments.get("drain"):
            return _ok({"runs": drain(ws)})
        return _ok(run_one(ws, arguments.get("id")))
    if name == "cv_render":
        result = render_cv(
            ws,
            application_id=arguments.get("application_id"),
            emphasis=arguments.get("emphasis"),
            variant=arguments.get("variant"),
        )
        return _ok(result)
    if name == "sources_list":
        return _ok({"sources": [s.to_dict() for s in list_sources(ws)]})
    if name == "sources_run":
        job = run_source(ws, arguments["id"])
        if arguments.get("run"):
            out = run_one(ws, job.id)
            return _ok(out)
        return _ok({"job": job.to_dict()})
    if name == "artifacts_add":
        art = add_file(
            ws,
            arguments["application_id"],
            arguments["file"],
            kind=arguments.get("kind") or "other",
        )
        return _ok({"artifact": art.to_dict()})
    if name == "events_list":
        events = [e.to_dict() for e in list_events(ws, arguments["application_id"])]
        return _ok({"events": events})
    return _err(f"unknown tool: {name}")


def handle_rpc(message: dict[str, Any], *, data_dir: str | None = None) -> dict[str, Any] | None:
    """Handle one JSON-RPC message. Notifications return None."""
    method = message.get("method")
    msg_id = message.get("id", None)
    params = message.get("params") or {}
    if method == "notifications/initialized" or msg_id is None and method:
        return None
    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "hunt", "version": __version__},
                "instructions": (
                    "Hunt MCP. Promote is the only listing → application path. "
                    "Never invent CV facts. Never apply or send mail. "
                    "Writes go to HUNT_DATA."
                ),
            },
        }
    if method == "ping":
        return {"jsonrpc": "2.0", "id": msg_id, "result": {}}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": msg_id, "result": {"tools": TOOLS}}
    if method == "tools/call":
        name = params.get("name")
        arguments = params.get("arguments") or {}
        result = _call(str(name), arguments, data_dir)
        return {"jsonrpc": "2.0", "id": msg_id, "result": result}
    return {
        "jsonrpc": "2.0",
        "id": msg_id,
        "error": {"code": -32601, "message": f"method not found: {method}"},
    }


def _read_message(stdin) -> tuple[dict[str, Any] | None, bool]:
    """Return (message, framed). framed True means Content-Length transport."""
    first = stdin.readline()
    if first == "":
        return None, False
    if first.lower().startswith("content-length:"):
        length = int(first.split(":", 1)[1].strip())
        while True:
            line = stdin.readline()
            if line in ("", "\n", "\r\n"):
                break
        body = stdin.read(length)
        if isinstance(body, str):
            return json.loads(body), True
        return json.loads(body.decode("utf-8")), True
    line = first.strip()
    if not line:
        return _read_message(stdin)
    return json.loads(line), False


def _write_message(stdout, message: dict[str, Any], *, framed: bool) -> None:
    body = json.dumps(message, ensure_ascii=False)
    if framed:
        encoded = body.encode("utf-8")
        stdout.write(f"Content-Length: {len(encoded)}\r\n\r\n")
        stdout.write(body)
    else:
        stdout.write(body + "\n")
    stdout.flush()


def serve_stdio(data_dir: str | None = None) -> None:
    stdin = sys.stdin
    stdout = sys.stdout
    framed = False
    while True:
        try:
            message, this_framed = _read_message(stdin)
        except json.JSONDecodeError as exc:
            _write_message(
                stdout,
                {
                    "jsonrpc": "2.0",
                    "id": None,
                    "error": {"code": -32700, "message": f"parse error: {exc}"},
                },
                framed=framed,
            )
            continue
        if message is None:
            return
        framed = framed or this_framed
        reply = handle_rpc(message, data_dir=data_dir)
        if reply is not None:
            _write_message(stdout, reply, framed=framed)
