"""Board CRUD via ``hunt --json`` against a copy of the example workspace."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from hunt.core.inbox import add_item
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
    shutil.copytree(
        EXAMPLE, data, ignore=shutil.ignore_patterns("attachments", "store.sqlite")
    )
    env = {**os.environ, "HUNT_DATA": str(data), "PYTHONPATH": str(ROOT)}
    return data, env


def test_board_crud_json_and_restart(workspace):
    data, env = workspace
    source_store = EXAMPLE / "store.sqlite"
    assert not source_store.exists(), "example-workspace must not ship a sqlite file"

    created_run, created = _json(
        [
            "applications",
            "create",
            "--company",
            "Acme Radar",
            "--title-posted",
            "Staff SRE",
            "--title-ours",
            "SRE",
            "--source",
            "manual",
            "--url",
            "https://example.test/jobs/acme",
            "--location-country",
            "US",
            "--location-city",
            "Austin",
            "--modality",
            "remote",
            "--engagement",
            "b2b",
            "--comp-amount",
            "50",
            "--comp-currency",
            "USD",
            "--comp-unit",
            "hour",
            "--tax-home",
            "pl_jdg",
            "--status",
            "researching",
        ],
        env,
    )
    app = created["application"]
    app_id = app["id"]
    assert app["company"] == "Acme Radar"
    assert app["comp_quoted"] == {"amount": 50.0, "currency": "USD", "unit": "hour"}
    derived = app["comp_derived"]
    assert derived["fx_as_of"] == "2026-09-01"
    assert derived["hour"] == 45.0
    assert derived["day"] == 360.0
    assert derived["month"] == 7200.0
    assert derived["year"] == 86400.0
    assert derived["net_month"] == 5936.0
    assert derived["clears_floor"] is False
    assert (data / "store.sqlite").is_file()
    assert not (ROOT / "store.sqlite").exists()

    listed_run, listed = _json(["applications", "list"], env)
    assert [row["id"] for row in listed["applications"]] == [app_id]

    # New process, same workspace — restart-safe.
    got_run, got = _json(["applications", "get", app_id], env)
    assert got["application"]["title_posted"] == "Staff SRE"
    assert got["application"]["comp_derived"]["month"] == 7200.0

    _, updated = _json(
        ["applications", "update", app_id, "--status", "prepared", "--title-ours", "Platform"],
        env,
    )
    assert updated["application"]["status"] == "prepared"
    assert updated["application"]["title_ours"] == "Platform"

    _, after = _json(["applications", "get", app_id], env)
    assert after["application"]["status"] == "prepared"

    jd = data / "jd.txt"
    jd.write_text("Staff SRE at Acme Radar\n", encoding="utf-8")
    _, artifact = _json(
        ["artifacts", "add", "--application", app_id, "--file", str(jd), "--kind", "jd"],
        env,
    )
    stored = Path(artifact["artifact"]["path"])
    assert stored.is_file()
    assert stored.parent == data / "attachments" / "applications" / app_id
    assert artifact["artifact"]["sha256"]
    assert stored.read_text(encoding="utf-8") == "Staff SRE at Acme Radar\n"

    _, events = _json(["events", "list", "--application", app_id], env)
    kinds = [e["kind"] for e in events["events"]]
    assert kinds == ["created", "updated", "artifact_added"]

    missing = _run(["--json", "applications", "get", "no-such-id"], env, check=False)
    assert missing.returncode != 0
    err = json.loads(missing.stderr)
    assert "not found" in err["error"]


def test_inbox_promote_is_explicit(workspace):
    data, env = workspace
    with Workspace.open(data) as ws:
        pending = add_item(
            ws,
            company="Widget Labs",
            title="Reliability Engineer",
            url="https://example.test/jobs/widget",
            why_keep="English + B2B",
            why_risk="pay_unknown",
            knockouts=["pay_unknown"],
            payload={
                "comp_quoted": {"amount": 50, "currency": "USD", "unit": "hour"},
                "tax_home_for_net": "pl_jdg",
                "modality": "remote",
                "engagement": "b2b",
            },
        )
        dismissed = add_item(
            ws,
            company="No Hire Inc",
            title="Onsite only",
            knockouts=["onsite"],
        )
        item_id = pending.id
        dismiss_id = dismissed.id

    _, inbox = _json(["inbox", "list"], env)
    ids = {row["id"] for row in inbox["inbox"]}
    assert item_id in ids
    assert dismiss_id in ids
    widget = next(row for row in inbox["inbox"] if row["id"] == item_id)
    assert widget["role"] == "Reliability Engineer"
    assert widget["engagement"] == "b2b"
    assert widget["engagement_label"] == "Freelance"
    assert widget["net_month"] is not None
    assert widget["display_currency"] == "EUR"
    assert widget["comp_derived"]["net_month"] == widget["net_month"]
    assert "clears_floor" in widget["comp_derived"]
    table = _run(["inbox", "list"], env)
    assert "ROLE" in table.stdout
    assert "SOURCE" in table.stdout
    assert "NET/MO" in table.stdout
    assert "WHY KEEP" in table.stdout
    assert "FLOOR" not in table.stdout
    _, apps_before = _json(["applications", "list"], env)
    assert apps_before["applications"] == []

    dismissed_run, dismissed_item = _json(["inbox", "dismiss", dismiss_id], env)
    assert dismissed_item["inbox_item"]["status"] == "dismissed"

    promote_dismissed = _run(
        ["--json", "inbox", "promote", dismiss_id], env, check=False
    )
    assert promote_dismissed.returncode != 0

    _, promoted = _json(["inbox", "promote", item_id], env)
    app = promoted["application"]
    assert app["company"] == "Widget Labs"
    assert app["source"] == "inbox"
    assert app["comp_quoted"]["amount"] == 50
    assert app["comp_derived"]["hour"] == 45.0

    _, inbox_pending = _json(["inbox", "list"], env)
    assert inbox_pending["inbox"] == []
    _, inbox_all = _json(["inbox", "list", "--status", "all"], env)
    by_id = {row["id"]: row for row in inbox_all["inbox"]}
    assert by_id[item_id]["status"] == "promoted"
    assert by_id[item_id]["application_id"] == app["id"]
    assert by_id[dismiss_id]["status"] == "dismissed"

    again = _run(["--json", "inbox", "promote", item_id], env, check=False)
    assert again.returncode != 0


def test_cv_render_records_artifact(workspace):
    data, env = workspace
    _, created = _json(
        ["applications", "create", "--company", "ExampleCorp", "--title-posted", "SRE"],
        env,
    )
    app_id = created["application"]["id"]
    _, rendered = _json(["cv", "render", "--application", app_id], env)
    pdf = Path(rendered["path"])
    assert pdf.is_file()
    assert pdf.parent == data / "attachments" / "applications" / app_id
    assert rendered["artifact"]["kind"] == "cv"
    assert not (EXAMPLE / "store.sqlite").exists()
    assert not (ROOT / "store.sqlite").exists()
    assert not (ROOT / "attachments").exists()


def test_human_table_and_error_without_workspace(tmp_path: Path):
    env = {**os.environ, "PYTHONPATH": str(ROOT)}
    env.pop("HUNT_DATA", None)
    missing = subprocess.run(
        [sys.executable, "-m", "hunt", "--json", "applications", "list"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    assert missing.returncode != 0
    assert "workspace" in json.loads(missing.stderr)["error"].lower()

    data = tmp_path / "workspace"
    shutil.copytree(
        EXAMPLE, data, ignore=shutil.ignore_patterns("attachments", "store.sqlite")
    )
    env["HUNT_DATA"] = str(data)
    empty = _run(["applications", "list"], env)
    assert empty.returncode == 0
    assert "(none)" in empty.stdout


def test_pay_estimate_json_six_cells_and_usd_hour(workspace):
    _, env = workspace
    cells = [
        ("CH", "fte", 7760.0, False),
        ("Switzerland", "freelance", 7400.0, True),
        ("ES", "fte", 6950.0, False),
        ("Spain", "autonomo", 7700.0, True),
        ("PL", "fte", 7429.0, False),
        ("Poland", "jdg", 8400.0, True),
    ]
    for country, engagement, net, vat_out in cells:
        _, payload = _json(
            [
                "pay",
                "estimate",
                "--country",
                country,
                "--engagement",
                engagement,
                "--amount",
                "10000",
                "--currency",
                "EUR",
                "--unit",
                "month",
            ],
            env,
        )
        assert payload["net_month"] == net, (country, engagement, payload)
        assert payload["gross"]["month"] == 10000.0
        assert payload["assumptions"]["vat_out"] is vat_out
        assert payload["assumptions"]["disclaimer"].startswith("Estimate")
        assert payload["clears_floor"] is (net >= 7000)

    _, usd = _json(
        [
            "pay",
            "estimate",
            "--country",
            "PL",
            "--engagement",
            "b2b",
            "--amount",
            "50",
            "--currency",
            "USD",
            "--unit",
            "hour",
        ],
        env,
    )
    assert usd["gross"]["hour"] == 45.0
    assert usd["gross"]["day"] == 360.0
    assert usd["gross"]["month"] == 7200.0
    assert usd["gross"]["year"] == 86400.0
    assert usd["net_month"] == 5936.0
    assert usd["clears_floor"] is False


def test_pay_estimate_pl_fte_without_tax_home(workspace):
    data, env = workspace
    _, created = _json(
        [
            "applications",
            "create",
            "--company",
            "Example Search",
            "--location-country",
            "PL",
            "--engagement",
            "fte",
            "--comp-amount",
            "364000",
            "--comp-currency",
            "PLN",
            "--comp-unit",
            "year",
        ],
        env,
    )
    derived = created["application"]["comp_derived"]
    assert created["application"]["tax_home_for_net"] is None
    assert derived["month"] == 6976.0
    assert derived["net_month"] == 5182.47
    assert derived["clears_floor"] is False

    _, restamp = _json(["pay", "restamp"], env)
    assert restamp["total"] >= 1


def test_restore_sets_keep_restored(workspace):
    data, env = workspace
    with Workspace.open(data) as ws:
        item = add_item(
            ws,
            company="Acme Radar",
            title="Staff SWE",
            why_risk="experience",
            knockouts=["experience"],
        )
        item_id = item.id
    pending = _run(["--json", "inbox", "restore", item_id], env, check=False)
    assert pending.returncode != 0
    _, dismissed = _json(["inbox", "dismiss", item_id], env)
    assert dismissed["inbox_item"]["status"] == "dismissed"
    _, restored = _json(["inbox", "restore", item_id], env)
    row = restored["inbox_item"]
    assert row["status"] == "pending"
    assert row["triage"]["action"] == "keep"
    assert row["triage"]["restored"] is True
    assert row["triage"]["codes"] == ["experience"]
    again = _run(["--json", "inbox", "restore", item_id], env, check=False)
    assert again.returncode != 0


def test_inbox_list_source_knockout_triage_flags(workspace):
    data, env = workspace
    with Workspace.open(data) as ws:
        list_sources(ws)
        add_item(
            ws,
            company="Floor Co",
            title="Role A",
            source_id="justjoin-sample",
            knockouts=["pay_below_floor"],
            external_id="floor-1",
        )
        add_item(
            ws,
            company="Other Co",
            title="Role B",
            source_id="remotive-eu",
            knockouts=["title"],
            external_id="other-1",
        )
    _, by_source = _json(["inbox", "list", "--source", "justjoin-sample"], env)
    assert [row["company"] for row in by_source["inbox"]] == ["Floor Co"]
    _, floor = _json(
        ["inbox", "list", "--knockout", "pay_below_floor|below_floor"], env
    )
    assert [row["company"] for row in floor["inbox"]] == ["Floor Co"]


def test_inbox_list_sort_flags(workspace):
    data, env = workspace
    with Workspace.open(data) as ws:
        list_sources(ws)
        ws.conn.execute("DELETE FROM inbox_items")
        ws.conn.execute("DELETE FROM listings")
        ws.conn.commit()
        add_item(
            ws,
            company="Zulu Co",
            title="Role Z",
            source_id="justjoin-sample",
            external_id="cli-z",
        )
        add_item(
            ws,
            company="Alpha Co",
            title="Role A",
            source_id="landing-jobs-eu",
            external_id="cli-a",
        )
        add_item(
            ws,
            company="Mid Co",
            title="Role M",
            source_id="remotive-eu",
            external_id="cli-m",
        )
    _, by_company = _json(["inbox", "list", "--sort", "company"], env)
    assert [row["company"] for row in by_company["inbox"]] == [
        "Alpha Co",
        "Mid Co",
        "Zulu Co",
    ]
    _, by_source = _json(
        ["inbox", "list", "--sort", "source_id", "--order", "desc"], env
    )
    assert [row["source_id"] for row in by_source["inbox"]] == [
        "remotive-eu",
        "landing-jobs-eu",
        "justjoin-sample",
    ]
    bad = _run(["--json", "inbox", "list", "--sort", "nope"], env, check=False)
    assert bad.returncode != 0
