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
        "jobs_enqueue",
        "jobs_status",
        "jobs_run",
        "cv_render",
        "sources_list",
        "sources_run",
    ):
        assert required in names


def test_mcp_promote_is_only_listing_path(data_dir: Path):
    _, poll = _call(
        "sources_run", {"id": "justjoin-sample", "run": True}, data_dir
    )
    assert poll["job"]["state"] == "done"
    _, apps = _call("applications_list", {}, data_dir)
    assert apps["applications"] == []

    _, screened = _call(
        "jobs_enqueue", {"type": "screen-inbox", "run": True}, data_dir
    )
    assert screened["result"]["inbox_added"] == 2
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
