"""screen-inbox refreshes knockouts without wiping agent/human why notes."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from hunt.core.inbox import get_inbox_item
from hunt.core.listings import get_listing, upsert_listing
from hunt.core.screen import (
    PASSED_WORKSPACE_KNOCKOUTS,
    is_boilerplate_why,
    screen_inbox,
    screen_listing,
)
from hunt.core.sources import list_sources
from hunt.core.workspace import Workspace

ROOT = Path(__file__).resolve().parent.parent
EXAMPLE = ROOT / "example-workspace"


def _run(args, env, check=True):
    r = subprocess.run(
        [sys.executable, "-m", "hunt", *args],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    if check and r.returncode != 0:
        print(r.stdout, r.stderr, sep="\n")
        raise AssertionError(f"FAILED ({r.returncode}): hunt {' '.join(args)}")
    return r


def _json(args, env, check=True):
    r = _run(["--json", *args], env, check=check)
    if r.returncode != 0:
        return r, json.loads(r.stderr) if r.stderr.strip().startswith("{") else None
    return r, json.loads(r.stdout)


@pytest.fixture
def workspace(tmp_path: Path):
    data = tmp_path / "workspace"
    shutil.copytree(EXAMPLE, data, ignore=shutil.ignore_patterns("attachments"))
    env = {**os.environ, "HUNT_DATA": str(data), "PYTHONPATH": str(ROOT)}
    return data, env


def _listing_without_pay(ws: Workspace, *, company="Roche", title="Staff SWE"):
    list_sources(ws)
    listing, _ = upsert_listing(
        ws,
        source_id="justjoin-sample",
        external_id="roche-1",
        title=title,
        company=company,
        payload={
            "location_country": "CH",
            "engagement": "fte",
            "modality": "hybrid",
        },
        commit=True,
    )
    return listing


def _set_why(ws: Workspace, item_id: str, *, why_keep, why_risk):
    ws.conn.execute(
        "UPDATE inbox_items SET why_keep = ?, why_risk = ? WHERE id = ?",
        (why_keep, why_risk, item_id),
    )
    ws.conn.commit()


def test_is_boilerplate_why():
    assert is_boilerplate_why(None)
    assert is_boilerplate_why("")
    assert is_boilerplate_why("  ")
    assert is_boilerplate_why(PASSED_WORKSPACE_KNOCKOUTS)
    assert is_boilerplate_why("pay_unknown")
    assert is_boilerplate_why("pay_unknown, title")
    assert is_boilerplate_why("title, pay_unknown", ["title", "pay_unknown"])
    assert not is_boilerplate_why(
        "Roche Staff SWE, Switzerland, FTE, pay_unknown"
    )
    assert not is_boilerplate_why("quoted pay missing; net unknown")


def test_first_screen_writes_boilerplate_and_pay_unknown(workspace):
    data, _env = workspace
    with Workspace.open(data) as ws:
        listing = _listing_without_pay(ws)
        result = screen_listing(ws, listing)
        ws.conn.commit()
        item = get_inbox_item(ws, result["inbox_id"])
        assert item.status == "pending"
        assert item.why_keep is None
        assert item.why_risk == "pay_unknown"
        assert item.knockouts == ["pay_unknown"]
        assert result["knockouts"] == ["pay_unknown"]


def test_refresh_preserves_human_why_and_updates_knockouts(workspace):
    data, _env = workspace
    keep = "Roche Staff SWE, Switzerland, FTE, pay_unknown"
    risk = "quoted pay missing; net unknown"
    with Workspace.open(data) as ws:
        listing = _listing_without_pay(ws)
        first = screen_listing(ws, listing)
        ws.conn.commit()
        item_id = first["inbox_id"]
        _set_why(ws, item_id, why_keep=keep, why_risk=risk)

        listing, _ = upsert_listing(
            ws,
            source_id="justjoin-sample",
            external_id="roche-1",
            title="Staff SWE",
            company="Roche",
            payload={
                "location_country": "CH",
                "engagement": "fte",
                "modality": "hybrid",
                "comp_quoted": {"amount": 14000, "currency": "CHF", "unit": "month"},
            },
            commit=True,
        )
        listing = get_listing(ws, listing.id)
        second = screen_listing(ws, listing)
        ws.conn.commit()
        item = get_inbox_item(ws, item_id)
        assert second["refreshed"] is True
        assert item.status == "pending"
        assert item.why_keep == keep
        assert item.why_risk == risk
        assert item.knockouts == []
        assert second["knockouts"] == []


def test_refresh_replaces_boilerplate_why_when_knockouts_change(workspace):
    data, _env = workspace
    with Workspace.open(data) as ws:
        listing = _listing_without_pay(ws)
        first = screen_listing(ws, listing)
        ws.conn.commit()
        item = get_inbox_item(ws, first["inbox_id"])
        assert item.why_risk == "pay_unknown"
        assert item.why_keep is None

        listing, _ = upsert_listing(
            ws,
            source_id="justjoin-sample",
            external_id="roche-1",
            title="Staff SWE",
            company="Roche",
            payload={
                "location_country": "CH",
                "engagement": "fte",
                "comp_quoted": {"amount": 14000, "currency": "CHF", "unit": "month"},
            },
            commit=True,
        )
        listing = get_listing(ws, listing.id)
        screen_listing(ws, listing)
        ws.conn.commit()
        item = get_inbox_item(ws, first["inbox_id"])
        assert item.why_keep == PASSED_WORKSPACE_KNOCKOUTS
        assert item.why_risk is None
        assert item.knockouts == []
        assert item.status == "pending"


def test_refresh_fills_empty_why_with_boilerplate(workspace):
    data, _env = workspace
    with Workspace.open(data) as ws:
        listing = _listing_without_pay(ws)
        first = screen_listing(ws, listing)
        ws.conn.commit()
        _set_why(ws, first["inbox_id"], why_keep="", why_risk="")
        listing = get_listing(ws, listing.id)
        screen_listing(ws, listing)
        ws.conn.commit()
        item = get_inbox_item(ws, first["inbox_id"])
        assert item.why_keep is None
        assert item.why_risk == "pay_unknown"
        assert item.knockouts == ["pay_unknown"]


def test_refresh_does_not_promote_or_dismiss(workspace):
    data, _env = workspace
    with Workspace.open(data) as ws:
        listing = _listing_without_pay(ws)
        first = screen_listing(ws, listing)
        ws.conn.commit()
        _set_why(
            ws,
            first["inbox_id"],
            why_keep="Roche Staff SWE, Switzerland",
            why_risk="pay_unknown",
        )
        out = screen_inbox(ws)
        item = get_inbox_item(ws, first["inbox_id"])
        assert out["refreshed"] == 1
        assert out["inbox_added"] == 0
        assert item.status == "pending"
        assert item.application_id is None
        assert item.why_keep == "Roche Staff SWE, Switzerland"
        # why_risk is codes-only boilerplate, so it stays in sync with knockouts
        assert item.why_risk == "pay_unknown"
        assert item.knockouts == ["pay_unknown"]


def test_screen_inbox_job_preserves_pending_why(workspace):
    data, env = workspace
    with Workspace.open(data) as ws:
        listing = _listing_without_pay(ws, company="Whatnot", title="Staff Engineer")
        first = screen_listing(ws, listing)
        ws.conn.commit()
        keep = "Whatnot Staff Engineer, US remote, FTE, pay_unknown"
        _set_why(ws, first["inbox_id"], why_keep=keep, why_risk="net unknown")
        item_id = first["inbox_id"]

    _, screened = _json(["jobs", "enqueue", "--type", "screen-inbox", "--run"], env)
    assert screened["result"]["refreshed"] == 1
    assert screened["result"]["inbox_added"] == 0

    _, inbox = _json(["inbox", "list"], env)
    row = next(item for item in inbox["inbox"] if item["id"] == item_id)
    assert row["status"] == "pending"
    assert row["why_keep"] == keep
    assert row["why_risk"] == "net unknown"
    assert "pay_unknown" in row["knockouts"]
