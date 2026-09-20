"""``hunt mcp`` — stdio MCP server. Tools map 1:1 onto hunt.core / CLI nouns."""

from __future__ import annotations

import json
import sys
from typing import Any

from hunt import __version__
from hunt.core.applications import (
    create_application,
    estimate_for_workspace,
    get_application,
    list_applications,
    restamp_derived,
    update_application,
)
from hunt.core.pay import QuotedPay
from hunt.core.artifacts import add_file
from hunt.core.context import current_actor
from hunt.core.cv import render as render_cv
from hunt.core.errors import HuntError, ValidationError
from hunt.core.events import list_events
from hunt.core.facts import (
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
    serialize_inbox_list,
)
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
    _tool(
        "inbox_list",
        "List inbox items. Filter with status, source, knockout, triage (keep|unsure|dismiss). Sort with sort/order.",
        {
            "status": {"type": "string"},
            "source": {"type": "string"},
            "knockout": {"type": "string"},
            "triage": {"type": "string"},
            "sort": {"type": "string"},
            "order": {"type": "string"},
        },
    ),
    _tool(
        "inbox_promote",
        "Promote a pending inbox item to an application. This is the only listing → application path.",
        {"id": {"type": "string"}, **APP_PROPS},
        ["id"],
    ),
    _tool("inbox_dismiss", "Dismiss a pending inbox item", {"id": {"type": "string"}}, ["id"]),
    _tool(
        "inbox_restore",
        "Restore a dismissed inbox item to pending (keep / restored).",
        {"id": {"type": "string"}},
        ["id"],
    ),
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
        "Enqueue source-poll, screen-inbox, triage-inbox, or tailor-cv. Never apply/send.",
        {
            "type": {"type": "string", "enum": list(JOB_TYPES)},
            "target_id": {"type": "string"},
            "emphasis": {"type": "string"},
            "variant": {"type": "string"},
            "force": {"type": "boolean"},
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
    _tool(
        "pay_estimate",
        "Net / month estimate in display currency. Country + engagement is enough. Estimate, not tax advice. Never apply.",
        {
            "country": {"type": "string"},
            "engagement": {"type": "string"},
            "amount": {"type": "number"},
            "currency": {"type": "string"},
            "unit": {"type": "string"},
            "tax_home": {"type": "string"},
        },
        ["amount", "currency", "unit"],
    ),
    _tool(
        "pay_restamp",
        "Recompute stored derived pay from current tax_homes + FX.",
        {},
    ),
    _tool("profile_get", "Get knowledge profile. Contact lives only here.", {}),
    _tool(
        "profile_update",
        "Update profile fields. Agents cannot change contact; do not invent facts.",
        {
            "name": {"type": "string"},
            "location": {"type": "string"},
            "citizenship": {"type": "string"},
            "relocation": {"type": "string"},
            "headlines": {"type": "object"},
            "languages": {"type": "array"},
            "education": {"type": "array"},
        },
    ),
    _tool("positions_list", "List employment positions from knowledge YAML", {}),
    _tool("positions_get", "Get one position", {"id": {"type": "string"}}, ["id"]),
    _tool(
        "positions_create",
        "Create a draft position (verified false). Never invent employers or scope.",
        {
            "id": {"type": "string"},
            "title": {"type": "string"},
            "employer": {"type": "string"},
            "location": {"type": "string"},
            "start": {"type": "string"},
            "end": {"type": "string"},
            "client": {"type": "string"},
            "scope_facts": {"type": "array"},
            "default_achievements": {"type": "array"},
        },
        ["title", "employer", "start"],
    ),
    _tool(
        "positions_update",
        "Update a position. Agent writes become draft (verified false). Cannot weaken scope_facts.",
        {
            "id": {"type": "string"},
            "title": {"type": "string"},
            "employer": {"type": "string"},
            "location": {"type": "string"},
            "start": {"type": "string"},
            "end": {"type": "string"},
            "client": {"type": "string"},
            "scope_facts": {"type": "array"},
            "default_achievements": {"type": "array"},
        },
        ["id"],
    ),
    _tool("achievements_list", "List achievements from knowledge YAML", {}),
    _tool(
        "achievements_get",
        "Get one achievement",
        {"id": {"type": "string"}},
        ["id"],
    ),
    _tool(
        "achievements_create",
        "Create a draft achievement (verified false). Do not invent metrics. Do not flip verified.",
        {
            "id": {"type": "string"},
            "text": {"type": "string"},
            "tags": {"type": "array"},
            "evidence": {"type": "string"},
        },
        ["text", "evidence"],
    ),
    _tool(
        "achievements_update",
        "Update an achievement. Agent writes set verified false. The human must confirm.",
        {
            "id": {"type": "string"},
            "text": {"type": "string"},
            "tags": {"type": "array"},
            "evidence": {"type": "string"},
        },
        ["id"],
    ),
    _tool("projects_list", "List projects and certifications", {}),
    _tool("projects_get", "Get one project", {"id": {"type": "string"}}, ["id"]),
    _tool(
        "projects_create",
        "Create a project. Personal-project scope must stay honest.",
        {
            "id": {"type": "string"},
            "name": {"type": "string"},
            "bullets": {"type": "array"},
            "note": {"type": "string"},
        },
        ["name"],
    ),
    _tool(
        "projects_update",
        "Update a project",
        {
            "id": {"type": "string"},
            "name": {"type": "string"},
            "bullets": {"type": "array"},
            "note": {"type": "string"},
        },
        ["id"],
    ),
    _tool("skills_get", "Get skill groups and forbidden_claims", {}),
    _tool(
        "skills_update",
        "Update skill_groups. Agents cannot remove forbidden_claims.",
        {
            "skill_groups": {"type": "array"},
            "forbidden_claims": {"type": "array"},
        },
    ),
    _tool("integrity_get", "Get integrity rules. Agents cannot write this file.", {}),
    _tool(
        "agent_install",
        "Write Hunt MCP + role skills into an existing harness. Hunt does not run a tool loop.",
        {
            "harness": {
                "type": "string",
                "enum": [
                    "claude",
                    "cursor",
                    "codex",
                    "opencode",
                    "openclaw",
                    "paperclip",
                ],
            },
            "role": {
                "type": "string",
                "enum": ["operator", "screener", "all"],
            },
            "root": {"type": "string"},
            "home": {"type": "string"},
        },
        ["harness"],
    ),
    _tool(
        "agent_doctor",
        "Check workspace, installed MCP/skill, and GET /v1/models on agent.model.base_url.",
        {
            "root": {"type": "string"},
            "home": {"type": "string"},
            "timeout": {"type": "number"},
        },
    ),
    _tool(
        "agent_run",
        "Detect OpenCode → Claude Code / Codex and return the exec plan. Does not start a Hunt loop.",
        {
            "role": {"type": "string", "enum": ["operator", "screener"]},
            "root": {"type": "string"},
        },
        ["role"],
    ),
    _tool(
        "agent_status",
        "Harness + model settings. Never returns secret values.",
        {
            "harness": {
                "type": "string",
                "enum": [
                    "claude",
                    "cursor",
                    "codex",
                    "opencode",
                    "openclaw",
                    "paperclip",
                ],
            },
        },
    ),
    _tool(
        "agent_config",
        "Set agent.harness / agent.model. Keys stay in secrets.env.",
        {
            "harness": {
                "type": "string",
                "enum": [
                    "auto",
                    "claude",
                    "cursor",
                    "codex",
                    "opencode",
                    "openclaw",
                    "paperclip",
                ],
            },
            "base_url": {"type": "string"},
            "api_key_env": {"type": "string"},
            "model": {"type": "string"},
        },
    ),
    _tool(
        "agent_secret_set",
        "Write a model key to secrets.env. Never echoes the value.",
        {
            "env": {"type": "string"},
            "value": {"type": "string"},
        },
        ["env", "value"],
    ),
    _tool(
        "agent_secret_unset",
        "Remove a model key from secrets.env.",
        {"env": {"type": "string"}},
        ["env"],
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
        items = serialize_inbox_list(
            ws,
            list_inbox(
                ws,
                status=status,
                source_id=arguments.get("source"),
                knockout=arguments.get("knockout"),
                triage_action=arguments.get("triage"),
            ),
            sort=arguments.get("sort"),
            order=arguments.get("order"),
        )
        return _ok({"inbox": items})
    if name == "inbox_promote":
        item_id = arguments["id"]
        fields = _app_fields(arguments)
        app = promote(ws, item_id, **fields).to_dict()
        return _ok({"application": app})
    if name == "inbox_dismiss":
        item = serialize_inbox_item(ws, dismiss(ws, arguments["id"]))
        return _ok({"inbox_item": item})
    if name == "inbox_restore":
        item = serialize_inbox_item(ws, restore(ws, arguments["id"]))
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
        if arguments.get("force"):
            payload["force"] = True
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
    if name == "pay_estimate":
        try:
            quoted = QuotedPay(
                amount=float(arguments["amount"]),
                currency=str(arguments["currency"]),
                unit=str(arguments["unit"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValidationError(str(exc)) from exc
        estimate = estimate_for_workspace(
            ws,
            quoted,
            country=arguments.get("country"),
            engagement=arguments.get("engagement"),
            tax_home=arguments.get("tax_home"),
        )
        return _ok(estimate)
    if name == "pay_restamp":
        return _ok(restamp_derived(ws))
    if name == "profile_get":
        return _ok({"profile": get_profile(ws)})
    if name == "profile_update":
        fields = {
            key: arguments[key]
            for key in (
                "name",
                "location",
                "citizenship",
                "relocation",
                "headlines",
                "languages",
                "education",
            )
            if key in arguments
        }
        return _ok({"profile": update_profile(ws, fields)})
    if name == "positions_list":
        return _ok({"positions": list_positions(ws)})
    if name == "positions_get":
        return _ok({"position": get_position(ws, arguments["id"])})
    if name == "positions_create":
        fields = {
            key: arguments[key]
            for key in (
                "id",
                "title",
                "employer",
                "location",
                "start",
                "end",
                "client",
                "scope_facts",
                "default_achievements",
            )
            if key in arguments
        }
        return _ok({"position": create_position(ws, fields)})
    if name == "positions_update":
        fields = {
            key: arguments[key]
            for key in (
                "title",
                "employer",
                "location",
                "start",
                "end",
                "client",
                "scope_facts",
                "default_achievements",
            )
            if key in arguments
        }
        return _ok(
            {"position": update_position(ws, arguments["id"], fields)}
        )
    if name == "achievements_list":
        return _ok({"achievements": list_achievements(ws)})
    if name == "achievements_get":
        return _ok({"achievement": get_achievement(ws, arguments["id"])})
    if name == "achievements_create":
        fields = {
            key: arguments[key]
            for key in ("id", "text", "tags", "evidence")
            if key in arguments
        }
        return _ok({"achievement": create_achievement(ws, fields)})
    if name == "achievements_update":
        fields = {
            key: arguments[key]
            for key in ("text", "tags", "evidence")
            if key in arguments
        }
        return _ok(
            {"achievement": update_achievement(ws, arguments["id"], fields)}
        )
    if name == "projects_list":
        return _ok(list_projects(ws))
    if name == "projects_get":
        return _ok({"project": get_project(ws, arguments["id"])})
    if name == "projects_create":
        fields = {
            key: arguments[key]
            for key in ("id", "name", "bullets", "note")
            if key in arguments
        }
        return _ok({"project": create_project(ws, fields)})
    if name == "projects_update":
        fields = {
            key: arguments[key]
            for key in ("name", "bullets", "note")
            if key in arguments
        }
        return _ok({"project": update_project(ws, arguments["id"], fields)})
    if name == "skills_get":
        return _ok({"skills": get_skills(ws)})
    if name == "skills_update":
        fields = {
            key: arguments[key]
            for key in ("skill_groups", "forbidden_claims")
            if key in arguments
        }
        return _ok({"skills": update_skills(ws, fields)})
    if name == "integrity_get":
        return _ok({"integrity": get_integrity(ws)})
    if name == "agent_install":
        from hunt.agent.install import install as install_agent

        return _ok(
            install_agent(
                harness=str(arguments["harness"]),
                data_dir=ws.root,
                root=arguments.get("root"),
                home=arguments.get("home"),
                role=arguments.get("role"),
            )
        )
    if name == "agent_doctor":
        from hunt.agent.doctor import doctor as run_doctor

        timeout = arguments.get("timeout")
        return _ok(
            run_doctor(
                ws,
                root=arguments.get("root"),
                home=arguments.get("home"),
                timeout=float(timeout) if timeout is not None else 3.0,
            )
        )
    if name == "agent_run":
        from hunt.agent.run import prepare_run

        return _ok(
            prepare_run(
                ws,
                str(arguments.get("role") or "operator"),
                root=arguments.get("root"),
            )
        )
    if name == "agent_status":
        from hunt.agent.status import agent_status

        return _ok(agent_status(ws, harness=arguments.get("harness")))
    if name == "agent_config":
        from hunt.agent.status import save_agent

        model = {}
        if "base_url" in arguments:
            model["base_url"] = arguments["base_url"]
        if "api_key_env" in arguments:
            model["api_key_env"] = arguments["api_key_env"]
        if "model" in arguments:
            model["model"] = arguments["model"]
        return _ok(
            save_agent(
                ws,
                harness=arguments.get("harness"),
                model=model or None,
            )
        )
    if name == "agent_secret_set":
        from hunt.agent.status import set_agent_secret

        payload = set_agent_secret(
            ws, str(arguments["env"]), str(arguments.get("value") or "")
        )
        return _ok({"api_key_set": payload["api_key_set"], "env": arguments["env"]})
    if name == "agent_secret_unset":
        from hunt.agent.status import unset_agent_secret

        payload = unset_agent_secret(ws, str(arguments["env"]))
        return _ok({"api_key_set": payload["api_key_set"], "env": arguments["env"]})
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
