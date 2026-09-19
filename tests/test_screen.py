"""screen-inbox refreshes knockouts without wiping agent/human why notes."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import yaml

from hunt.core.errors import HuntError
from hunt.core.inbox import dismiss, get_inbox_item, promote, restore
from hunt.core.listings import get_listing, upsert_listing
from hunt.core.screen import (
    PASSED_WORKSPACE_KNOCKOUTS,
    evaluate_knockouts,
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
    assert is_boilerplate_why("wrong_role, triaged")
    assert is_boilerplate_why("pay_below_floor, experience")
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


HIGH_PAY = {"comp_quoted": {"amount": 14000, "currency": "EUR", "unit": "month"}}
LOW_PLN = {"comp_quoted": {"amount": 22000, "currency": "PLN", "unit": "month"}}
EXACT_FLOOR = {"comp_quoted": {"amount": 7000, "currency": "EUR", "unit": "month"}}
STARTING_TITLE_EXCLUDE = [
    "junior",
    "trainee",
    "helpdesk",
    "service desk",
    "microsoft 365",
    "m365",
    "internship",
    "intern ",
    " intern ",
]


def _write_knockouts(data: Path, **updates) -> None:
    path = data / "config.yaml"
    cfg = yaml.safe_load(path.read_text()) or {}
    rules = cfg.setdefault("knockouts", {})
    rules.update(updates)
    path.write_text(yaml.safe_dump(cfg, sort_keys=False))


def _set_triage(ws: Workspace, item_id: str, triage: dict) -> None:
    ws.conn.execute(
        "UPDATE inbox_items SET triage_json = ? WHERE id = ?",
        (json.dumps(triage), item_id),
    )
    ws.conn.commit()


def _listing(
    ws: Workspace,
    *,
    external_id: str,
    title: str = "Staff SWE",
    company: str = "Acme",
    payload: dict | None = None,
):
    list_sources(ws)
    listing, _ = upsert_listing(
        ws,
        source_id="justjoin-sample",
        external_id=external_id,
        title=title,
        company=company,
        payload=payload or {},
        commit=True,
    )
    return listing


def test_example_workspace_empty_drop_on_does_not_dismiss(workspace):
    data, _env = workspace
    with Workspace.open(data) as ws:
        rules = ws.knockout_rules()
        assert list(rules.get("drop_on") or []) == []
        assert list(rules.get("title_exclude") or []) == []
        assert list(rules.get("experience_block") or []) == []
        listing = _listing(
            ws,
            external_id="junior-low",
            title="Junior Helpdesk",
            payload={
                "experience_level": "junior",
                **LOW_PLN,
            },
        )
        result = screen_listing(ws, listing)
        ws.conn.commit()
        assert result.get("dropped") is not True
        assert result.get("dismissed") is not True
        item = get_inbox_item(ws, result["inbox_id"])
        assert item.status == "pending"


def test_pay_below_floor_fires_when_location_country_none(workspace):
    data, _env = workspace
    with Workspace.open(data) as ws:
        codes = evaluate_knockouts(
            ws,
            title="Staff SWE",
            payload={**LOW_PLN, "location_country": None, "engagement": "fte"},
        )
        assert "pay_below_floor" in codes
        assert "pay_unknown" not in codes


def test_pay_below_floor_exact_floor_does_not_fire(workspace):
    data, _env = workspace
    with Workspace.open(data) as ws:
        codes = evaluate_knockouts(
            ws,
            title="Staff SWE",
            payload={**EXACT_FLOOR, "location_country": None},
        )
        assert "pay_below_floor" not in codes


def test_pay_below_floor_does_not_fire_when_quote_or_fx_missing(workspace):
    data, _env = workspace
    with Workspace.open(data) as ws:
        no_quote = evaluate_knockouts(ws, title="Staff SWE", payload={})
        assert "pay_below_floor" not in no_quote
        no_fx = evaluate_knockouts(
            ws,
            title="Staff SWE",
            payload={"comp_quoted": {"amount": 1000, "currency": "JPY", "unit": "month"}},
        )
        assert "pay_unknown" not in no_fx
        assert "pay_below_floor" not in no_fx


def test_pay_unknown_not_pay_below_floor(workspace):
    data, _env = workspace
    with Workspace.open(data) as ws:
        codes = evaluate_knockouts(ws, title="Staff SWE", payload={})
        assert "pay_unknown" in codes
        assert "pay_below_floor" not in codes


def test_title_exclude_substring(workspace):
    data, _env = workspace
    with Workspace.open(data) as ws:
        empty = evaluate_knockouts(
            ws, title="IT Helpdesk Specialist", payload=dict(HIGH_PAY)
        )
        assert "title_exclude" not in empty
    _write_knockouts(data, title_exclude=["helpdesk"])
    with Workspace.open(data) as ws:
        hit = evaluate_knockouts(
            ws, title="IT Helpdesk Specialist", payload=dict(HIGH_PAY)
        )
        miss = evaluate_knockouts(ws, title="Staff SRE", payload=dict(HIGH_PAY))
        assert "title_exclude" in hit
        assert "title_exclude" not in miss


def test_title_exclude_intern_does_not_match_internetowej(workspace):
    data, _env = workspace
    _write_knockouts(data, title_exclude=STARTING_TITLE_EXCLUDE)
    title = "Specjalista/Specjalistka ds. analityki internetowej"
    with Workspace.open(data) as ws:
        codes = evaluate_knockouts(ws, title=title, payload=dict(HIGH_PAY))
        assert "title_exclude" not in codes
        intern_prefix = evaluate_knockouts(
            ws, title="Intern Application Engineer", payload=dict(HIGH_PAY)
        )
        assert "title_exclude" in intern_prefix


def test_experience_block_junior_only(workspace):
    data, _env = workspace
    _write_knockouts(data, experience_block=["junior"])
    with Workspace.open(data) as ws:
        junior = evaluate_knockouts(
            ws,
            title="SWE",
            payload={**HIGH_PAY, "experience_level": "junior"},
        )
        mid = evaluate_knockouts(
            ws,
            title="SWE",
            payload={**HIGH_PAY, "experience_level": "mid"},
        )
        intern = evaluate_knockouts(
            ws,
            title="SWE",
            payload={**HIGH_PAY, "experience_level": "intern"},
        )
        assert "experience" in junior
        assert "experience" not in mid
        assert "experience" not in intern


def test_refresh_unions_non_layer1_codes(workspace):
    data, _env = workspace
    with Workspace.open(data) as ws:
        listing = _listing_without_pay(ws)
        first = screen_listing(ws, listing)
        ws.conn.commit()
        ws.conn.execute(
            "UPDATE inbox_items SET knockouts_json = ? WHERE id = ?",
            (
                json.dumps(["pay_unknown", "wrong_role", "triaged"]),
                first["inbox_id"],
            ),
        )
        ws.conn.commit()
        listing = get_listing(ws, listing.id)
        second = screen_listing(ws, listing)
        ws.conn.commit()
        item = get_inbox_item(ws, first["inbox_id"])
        assert second["refreshed"] is True
        assert item.knockouts == ["pay_unknown", "wrong_role", "triaged"]


def test_refresh_never_deletes_triage_json(workspace):
    data, _env = workspace
    triage = {
        "action": "keep",
        "reason": "sre observability fit",
        "codes": ["triaged"],
        "model": "gemma-test",
    }
    with Workspace.open(data) as ws:
        listing = _listing_without_pay(ws)
        first = screen_listing(ws, listing)
        ws.conn.commit()
        _set_triage(ws, first["inbox_id"], triage)
        listing = get_listing(ws, listing.id)
        screen_listing(ws, listing)
        ws.conn.commit()
        item = get_inbox_item(ws, first["inbox_id"])
        assert item.triage is not None
        assert item.triage["reason"] == "sre observability fit"
        assert item.triage["action"] == "keep"
        assert item.triage["codes"] == ["triaged"]
        assert item.why_risk == "pay_unknown"


def test_drop_on_dismisses_boilerplate_pending(workspace):
    data, _env = workspace
    with Workspace.open(data) as ws:
        listing = _listing(
            ws,
            external_id="junior-1",
            title="Staff SWE",
            payload={**HIGH_PAY, "experience_level": "junior"},
        )
        first = screen_listing(ws, listing)
        ws.conn.commit()
        item = get_inbox_item(ws, first["inbox_id"])
        assert item.status == "pending"
        assert "experience" not in item.knockouts
    _write_knockouts(data, experience_block=["junior"], drop_on=["experience"])
    with Workspace.open(data) as ws:
        out = screen_inbox(ws)
        item = get_inbox_item(ws, first["inbox_id"])
        assert out["dismissed"] == 1
        assert out["refreshed"] == 0
        assert item.status == "dismissed"
        assert "experience" in item.knockouts


def test_drop_on_does_not_dismiss_human_why(workspace):
    data, _env = workspace
    keep = "Roche Staff SWE, Switzerland, FTE, pay_unknown"
    with Workspace.open(data) as ws:
        listing = _listing(
            ws,
            external_id="junior-note",
            title="Staff SWE",
            payload={**HIGH_PAY, "experience_level": "junior"},
        )
        first = screen_listing(ws, listing)
        ws.conn.commit()
        _set_why(ws, first["inbox_id"], why_keep=keep, why_risk="net unknown")
    _write_knockouts(data, experience_block=["junior"], drop_on=["experience"])
    with Workspace.open(data) as ws:
        out = screen_inbox(ws)
        item = get_inbox_item(ws, first["inbox_id"])
        assert out["dismissed"] == 0
        assert item.status == "pending"
        assert item.why_keep == keep
        assert "experience" in item.knockouts


def test_drop_on_does_not_dismiss_keep_unsure(workspace):
    data, _env = workspace
    with Workspace.open(data) as ws:
        listing = _listing(
            ws,
            external_id="junior-keep",
            title="Staff SWE",
            payload={**HIGH_PAY, "experience_level": "junior"},
        )
        first = screen_listing(ws, listing)
        ws.conn.commit()
        _set_triage(ws, first["inbox_id"], {"action": "keep", "codes": ["triaged"]})
    _write_knockouts(data, experience_block=["junior"], drop_on=["experience"])
    with Workspace.open(data) as ws:
        out = screen_inbox(ws)
        item = get_inbox_item(ws, first["inbox_id"])
        assert out["dismissed"] == 0
        assert item.status == "pending"
        assert item.triage["action"] == "keep"


def test_drop_on_does_not_dismiss_restored(workspace):
    data, _env = workspace
    with Workspace.open(data) as ws:
        listing = _listing(
            ws,
            external_id="junior-restored",
            title="Staff SWE",
            payload={**HIGH_PAY, "experience_level": "junior"},
        )
        first = screen_listing(ws, listing)
        ws.conn.commit()
        dismiss(ws, first["inbox_id"])
        restored = restore(ws, first["inbox_id"])
        assert restored.status == "pending"
        assert restored.triage["restored"] is True
    _write_knockouts(data, experience_block=["junior"], drop_on=["experience"])
    with Workspace.open(data) as ws:
        out = screen_inbox(ws)
        item = get_inbox_item(ws, first["inbox_id"])
        assert out["dismissed"] == 0
        assert item.status == "pending"
        assert item.triage["restored"] is True
        assert item.triage["action"] == "keep"


def test_restore_sets_keep_restored(workspace):
    data, _env = workspace
    with Workspace.open(data) as ws:
        listing = _listing_without_pay(ws)
        first = screen_listing(ws, listing)
        ws.conn.commit()
        item_id = first["inbox_id"]
        dismissed = dismiss(ws, item_id)
        assert dismissed.status == "dismissed"
        item = restore(ws, item_id)
        assert item.status == "pending"
        assert item.triage is not None
        assert item.triage["action"] == "keep"
        assert item.triage["restored"] is True
        assert item.triage.get("restored_at")
        assert item.knockouts == ["pay_unknown"]
        assert item.triage["codes"] == ["pay_unknown"]
        with pytest.raises(HuntError, match="not dismissed"):
            restore(ws, item_id)
        promoted = _listing(
            ws,
            external_id="promo-1",
            title="Staff SWE",
            payload=dict(HIGH_PAY),
        )
        promo = screen_listing(ws, promoted)
        ws.conn.commit()
        promote(ws, promo["inbox_id"])
        with pytest.raises(HuntError, match="not dismissed"):
            restore(ws, promo["inbox_id"])


def test_dismiss_commit_false_does_not_commit(workspace):
    data, _env = workspace
    with Workspace.open(data) as ws:
        listing = _listing_without_pay(ws)
        first = screen_listing(ws, listing)
        ws.conn.commit()
        item_id = first["inbox_id"]
        dismiss(ws, item_id, commit=False)
        assert get_inbox_item(ws, item_id).status == "dismissed"
    with Workspace.open(data) as ws:
        assert get_inbox_item(ws, item_id).status == "pending"


def test_restore_commit_false_does_not_commit(workspace):
    data, _env = workspace
    with Workspace.open(data) as ws:
        listing = _listing_without_pay(ws)
        first = screen_listing(ws, listing)
        ws.conn.commit()
        item_id = first["inbox_id"]
        dismiss(ws, item_id)
        restore(ws, item_id, commit=False)
        assert get_inbox_item(ws, item_id).status == "pending"
        assert get_inbox_item(ws, item_id).triage["restored"] is True
    with Workspace.open(data) as ws:
        assert get_inbox_item(ws, item_id).status == "dismissed"


class _CommitSpy:
    def __init__(self, conn):
        object.__setattr__(self, "_conn", conn)
        object.__setattr__(self, "commits", 0)

    def commit(self):
        object.__setattr__(self, "commits", self.commits + 1)
        return self._conn.commit()

    def __getattr__(self, name):
        return getattr(self._conn, name)


def test_screen_inbox_layer1_dismiss_is_one_commit(workspace):
    data, _env = workspace
    ids = []
    with Workspace.open(data) as ws:
        for i in range(4):
            listing = _listing(
                ws,
                external_id=f"junior-batch-{i}",
                title="Staff SWE",
                payload={**HIGH_PAY, "experience_level": "junior"},
            )
            result = screen_listing(ws, listing)
            ids.append(result["inbox_id"])
        ws.conn.commit()
    _write_knockouts(data, experience_block=["junior"], drop_on=["experience"])
    with Workspace.open(data) as ws:
        spy = _CommitSpy(ws.conn)
        ws.conn = spy
        out = screen_inbox(ws)
        assert spy.commits == 1
        assert out["dismissed"] == 4
        for item_id in ids:
            assert get_inbox_item(ws, item_id).status == "dismissed"


def test_screen_inbox_exception_rolls_back_partial_dismisses(workspace, monkeypatch):
    data, _env = workspace
    ids = []
    with Workspace.open(data) as ws:
        for i in range(4):
            listing = _listing(
                ws,
                external_id=f"junior-boom-{i}",
                title="Staff SWE",
                payload={**HIGH_PAY, "experience_level": "junior"},
            )
            result = screen_listing(ws, listing)
            ids.append(result["inbox_id"])
        ws.conn.commit()
    _write_knockouts(data, experience_block=["junior"], drop_on=["experience"])
    import hunt.core.screen as screen_mod

    real = screen_mod.screen_listing
    calls = {"n": 0}

    def boom(ws, listing):
        calls["n"] += 1
        if calls["n"] == 3:
            raise RuntimeError("boom")
        return real(ws, listing)

    monkeypatch.setattr(screen_mod, "screen_listing", boom)
    with pytest.raises(RuntimeError, match="boom"):
        with Workspace.open(data) as ws:
            screen_inbox(ws)
    with Workspace.open(data) as ws:
        for item_id in ids:
            assert get_inbox_item(ws, item_id).status == "pending"


def test_upsert_preserves_experience_level(workspace):
    data, _env = workspace
    with Workspace.open(data) as ws:
        list_sources(ws)
        listing, created = upsert_listing(
            ws,
            source_id="justjoin-sample",
            external_id="exp-1",
            title="Staff SWE",
            company="Acme",
            payload={"experience_level": "mid", **HIGH_PAY},
            commit=True,
        )
        assert created is True
        updated, created = upsert_listing(
            ws,
            source_id="justjoin-sample",
            external_id="exp-1",
            title="Staff SWE",
            company="Acme",
            payload=dict(HIGH_PAY),
            commit=True,
        )
        assert created is False
        assert updated.id == listing.id
        assert updated.payload.get("experience_level") == "mid"
