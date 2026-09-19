"""MCP tools share hunt.core with the CLI. Promote is still explicit."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from hunt.mcp import TOOLS, handle_rpc

ROOT = Path(__file__).resolve().parent.parent
EXAMPLE = ROOT / "example-workspace"


@pytest.fixture
def data_dir(tmp_path: Path):
    data = tmp_path / "workspace"
    shutil.copytree(EXAMPLE, data, ignore=shutil.ignore_patterns("attachments"))
    return data


def _call(name: str, arguments: dict | None = None, data_dir: Path | None = None):
    reply = handle_rpc(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments or {}},
        },
        data_dir=str(data_dir) if data_dir else None,
    )
    assert reply is not None
    result = reply["result"]
    payload = json.loads(result["content"][0]["text"])
    return result, payload


def test_tools_list_covers_cli_nouns():
    reply = handle_rpc(
        {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, data_dir=None
    )
    names = {t["name"] for t in reply["result"]["tools"]}
    assert names == {t["name"] for t in TOOLS}
    for required in (
        "applications_list",
        "applications_get",
        "applications_create",
        "applications_update",
        "inbox_list",
        "inbox_promote",
        "inbox_dismiss",
        "inbox_restore",
        "jobs_enqueue",
        "jobs_status",
        "jobs_run",
        "cv_render",
        "sources_list",
        "sources_run",
        "profile_get",
        "positions_list",
        "positions_update",
        "achievements_list",
        "achievements_update",
        "projects_list",
        "skills_get",
        "integrity_get",
    ):
        assert required in names
    enqueue = next(t for t in reply["result"]["tools"] if t["name"] == "jobs_enqueue")
    enum = enqueue["inputSchema"]["properties"]["type"]["enum"]
    assert "triage-inbox" in enum
    assert "force" in enqueue["inputSchema"]["properties"]
    inbox = next(t for t in reply["result"]["tools"] if t["name"] == "inbox_list")
    props = inbox["inputSchema"]["properties"]
    assert "source" in props and "knockout" in props and "triage" in props


def test_mcp_promote_is_only_listing_path(data_dir: Path):
    _, poll = _call(
        "sources_run", {"id": "justjoin-sample", "run": True}, data_dir
    )
    assert poll["job"]["state"] == "done"
    assert poll["result"]["knockouts"]["inbox_added"] == 2
    assert poll["result"]["screener"]["triggered"] is False
    _, apps = _call("applications_list", {}, data_dir)
    assert apps["applications"] == []

    _, screened = _call(
        "jobs_enqueue", {"type": "screen-inbox", "run": True}, data_dir
    )
    assert screened["result"]["inbox_added"] == 0
    assert screened["result"]["refreshed"] == 2
    _, inbox = _call("inbox_list", {}, data_dir)
    acme = next(row for row in inbox["inbox"] if row["company"] == "Acme Radar")

    _, still_empty = _call("applications_list", {}, data_dir)
    assert still_empty["applications"] == []

    _, promoted = _call("inbox_promote", {"id": acme["id"]}, data_dir)
    assert promoted["application"]["company"] == "Acme Radar"
    app_id = promoted["application"]["id"]

    _, rendered = _call("cv_render", {"application_id": app_id}, data_dir)
    pdf = Path(rendered["path"])
    assert pdf.is_file()
    assert pdf.parent == data_dir / "attachments" / "applications" / app_id
    assert rendered["artifact"]["kind"] == "cv"

    err, payload = _call("inbox_promote", {"id": acme["id"]}, data_dir)
    assert err["isError"] is True
    assert "pending" in payload["error"]

    assert not (ROOT / "store.sqlite").exists()
    assert not (ROOT / "attachments").exists()


def test_restore_sets_keep_restored(data_dir: Path):
    from hunt.core.inbox import add_item
    from hunt.core.workspace import Workspace

    with Workspace.open(data_dir) as ws:
        item = add_item(
            ws,
            company="Acme Radar",
            title="Staff SWE",
            why_risk="experience",
            knockouts=["experience"],
        )
        item_id = item.id
    err, payload = _call("inbox_restore", {"id": item_id}, data_dir)
    assert err["isError"] is True
    _, dismissed = _call("inbox_dismiss", {"id": item_id}, data_dir)
    assert dismissed["inbox_item"]["status"] == "dismissed"
    _, restored = _call("inbox_restore", {"id": item_id}, data_dir)
    row = restored["inbox_item"]
    assert row["status"] == "pending"
    assert row["triage"]["action"] == "keep"
    assert row["triage"]["restored"] is True
    assert row["triage"]["codes"] == ["experience"]
    err, payload = _call("inbox_restore", {"id": item_id}, data_dir)
    assert err["isError"] is True
    assert "dismissed" in payload["error"]
