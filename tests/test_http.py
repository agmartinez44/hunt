"""HTTP is a thin client of hunt.core — same mutations as CLI --json."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from hunt.core.inbox import add_item
from hunt.core.workspace import Workspace
from hunt.http.app import create_app

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


@pytest.fixture
def client(workspace):
    data, env = workspace
    app = create_app(data)
    return TestClient(app), data, env


def test_board_crud_http_and_cli_json(client):
    http, data, env = client
    created = http.post(
        "/api/applications",
        json={
            "company": "Acme Radar",
            "title_posted": "Staff SRE",
            "title_ours": "SRE",
            "source": "manual",
            "url": "https://example.test/jobs/acme",
            "location_country": "US",
            "location_city": "Austin",
            "modality": "remote",
            "engagement": "b2b",
            "comp_quoted": {"amount": 50, "currency": "USD", "unit": "hour"},
            "tax_home_for_net": "pl_jdg",
        },
    )
    assert created.status_code == 200, created.text
    app = created.json()["application"]
    assert app["status"] == "researching"
    assert app["company"] == "Acme Radar"
    assert app["comp_quoted"] == {"amount": 50.0, "currency": "USD", "unit": "hour"}
    derived = app["comp_derived"]
    assert derived["fx_as_of"] == "2026-09-01"
    assert derived["hour"] == 45.0
    assert derived["month"] == 7200.0
    assert derived["net_month"] == 5976.0
    assert derived["clears_floor"] is False
    app_id = app["id"]
    assert (data / "store.sqlite").is_file()
    assert not (ROOT / "store.sqlite").exists()

    listed = http.get("/api/applications")
    assert [row["id"] for row in listed.json()["applications"]] == [app_id]

    # Restart-safe: new app instance, same $HUNT_DATA.
    http2 = TestClient(create_app(data))
    got = http2.get(f"/api/applications/{app_id}")
    assert got.json()["application"]["title_posted"] == "Staff SRE"

    patched = http.patch(
        f"/api/applications/{app_id}",
        json={"status": "prepared", "title_ours": "Platform"},
    )
    assert patched.json()["application"]["status"] == "prepared"
    assert patched.json()["application"]["title_ours"] == "Platform"

    events = http.get(f"/api/applications/{app_id}/events")
    kinds = [e["kind"] for e in events.json()["events"]]
    actors = {e["actor"] for e in events.json()["events"]}
    assert "created" in kinds
    assert "updated" in kinds
    assert actors == {"ui"}

    _, cli = _json(["applications", "get", app_id], env)
    assert cli["application"]["status"] == "prepared"
    assert cli["application"]["comp_derived"]["month"] == 7200.0


def test_http_rejects_listing_id_create(client):
    http, _, _ = client
    res = http.post(
        "/api/applications",
        json={"company": "Nope", "listing_id": "lst_secret"},
    )
    assert res.status_code == 400
    assert "listing" in res.json()["error"].lower()
    listed = http.get("/api/applications")
    assert listed.json()["applications"] == []


def test_inbox_promote_http_is_only_listing_path(client):
    http, data, env = client
    with Workspace.open(data) as ws:
        pending = add_item(
            ws,
            company="Northwind Platform",
            title="Staff SRE",
            url="https://example.test/jobs/northwind",
            why_keep="B2B remote EU",
            why_risk="On-call not specified",
            payload={
                "comp_quoted": {"amount": 85, "currency": "EUR", "unit": "hour"},
                "tax_home_for_net": "pl_jdg",
                "modality": "remote",
                "source": "http_json",
            },
        )
        item_id = pending.id

    inbox = http.get("/api/inbox")
    assert inbox.json()["inbox"][0]["id"] == item_id
    assert "comp_derived" not in inbox.json()["inbox"][0]
    pay = inbox.json()["inbox"][0]["payload"]["comp_quoted"]
    assert pay["amount"] == 85

    promoted = http.post(f"/api/inbox/{item_id}/promote", json={})
    assert promoted.status_code == 200, promoted.text
    app = promoted.json()["application"]
    assert app["company"] == "Northwind Platform"
    assert app["status"] == "researching"
    assert app["source"] == "http_json"

    events = http.get(f"/api/applications/{app['id']}/events")
    kinds = [e["kind"] for e in events.json()["events"]]
    assert "promoted_from_inbox" in kinds
    assert events.json()["events"][0]["actor"] == "ui"

    pending_after = http.get("/api/inbox")
    assert pending_after.json()["inbox"] == []

    again = http.post(f"/api/inbox/{item_id}/promote", json={})
    assert again.status_code == 400

    _, cli_apps = _json(["applications", "list"], env)
    assert cli_apps["applications"][0]["id"] == app["id"]


def test_sources_and_jobs_http_and_cli(client):
    http, _, env = client
    sources = http.get("/api/sources")
    ids = {s["id"] for s in sources.json()["sources"]}
    assert "justjoin-sample" in ids
    assert "mail-alerts" in ids
    by_id = {s["id"]: s for s in sources.json()["sources"]}
    assert by_id["justjoin-sample"]["kind"] == "http_json"
    assert by_id["mail-alerts"]["kind"] == "imap_alerts"
    assert by_id["mail-alerts"]["enabled"] is False

    run = http.post("/api/sources/justjoin-sample/run")
    assert run.status_code == 200, run.text
    job = run.json()["job"]
    assert job["type"] == "source-poll"
    assert job["state"] == "queued"
    assert job["target_id"] == "justjoin-sample"

    disabled = http.post("/api/sources/mail-alerts/run")
    assert disabled.status_code == 400

    jobs = http.get("/api/jobs")
    assert jobs.json()["jobs"][0]["id"] == job["id"]

    tailor_app = http.post("/api/applications", json={"company": "Initech Cloud"})
    app_id = tailor_app.json()["application"]["id"]
    queued = http.post("/api/jobs", json={"type": "tailor-cv", "target_id": app_id})
    assert queued.json()["job"]["type"] == "tailor-cv"

    _, cli_jobs = _json(["jobs", "list"], env)
    types = {j["type"] for j in cli_jobs["jobs"]}
    assert "source-poll" in types
    assert "tailor-cv" in types

    _, cli_src = _json(["sources", "list"], env)
    assert {s["id"] for s in cli_src["sources"]} == ids


def test_optional_auth_token(workspace):
    data, _env = workspace
    cfg = (data / "config.yaml").read_text()
    (data / "config.yaml").write_text(cfg + "\nauth_token: secret-token\n")
    http = TestClient(create_app(data))
    meta = http.get("/api/meta")
    assert meta.status_code == 200
    assert meta.json()["auth_required"] is True
    denied = http.get("/api/applications")
    assert denied.status_code == 401
    ok = http.get("/api/applications", headers={"Authorization": "Bearer secret-token"})
    assert ok.status_code == 200
    assert ok.json()["applications"] == []


def test_ui_has_no_apply_controls():
    js = (ROOT / "hunt" / "http" / "static" / "app.js").read_text()
    css = (ROOT / "hunt" / "http" / "static" / "hunt.css").read_text()
    html = (ROOT / "hunt" / "http" / "static" / "index.html").read_text()
    blob = js + css + html
    for forbidden in (
        "Easy Apply",
        "Auto-apply",
        "Apply for me",
        "Send email",
        "Submit to employer",
        "Generate and send",
    ):
        assert forbidden not in blob
    assert "I already sent it" in js
    assert "--bg: #f3f0ea" in css
    assert 'data-primitive="PayQuoted"' in js
    assert 'data-primitive="PromoteDialog"' in js
    assert 'data-primitive="AppTabBar"' in js


def test_ui_visual_followup_mira_agu7():
    """Mira AGU-7 changes-requested: mobile cards, density, sticky-save, jobs filters."""
    js = (ROOT / "hunt" / "http" / "static" / "app.js").read_text()
    css = (ROOT / "hunt" / "http" / "static" / "hunt.css").read_text()
    assert "sources-cards" in js
    assert "jobs-cards" in js
    assert "source-card" in js
    assert "job-card" in js
    assert ".sources-cards" in css
    assert ".jobs-cards" in css
    assert '[data-primitive="DataTable"] td [data-primitive="PayQuoted"] > .caption' in css
    assert "display: none" in css.split('[data-primitive="DataTable"] td [data-primitive="PayQuoted"] > .caption')[1][:400]
    assert '<span data-primitive="PayUnknown">Pay unknown</span>' in js
    assert '<span class="caption">Pay</span>Pay unknown' not in js
    assert ".sticky-save.is-dirty" in css
    assert "position: fixed" in css.split(".sticky-save.is-dirty")[1][:500]
    assert "quotedDirty" in js
    assert ".enqueue-form" in css
    assert "flex-direction: column" in css
    assert "[data-primitive=\"PageHeader\"]:has(.enqueue-form) .header-actions" in css
    assert "detail-header-actions" in css
    assert 'data-primitive="StatusSelect"' in js
    assert "file-btn" in css
    assert "Choose file" in js
    assert 'data-job-state=' in js
    assert 'data-job-type=' in js
    assert "overflow-wrap: anywhere" in css
    assert "font-size: var(--text-xs)" in css.split('[data-primitive="CountBadge"]')[1][:400]
    assert "skel-chrome" in js
    assert "BOARD_COLS" in js


def test_spa_and_meta(client):
    http, _, _ = client
    page = http.get("/")
    assert page.status_code == 200
    assert "Hunt" in page.text
    inbox = http.get("/inbox")
    assert inbox.status_code == 200
    meta = http.get("/api/meta")
    assert meta.json()["profile_name"] == "Jane Doe"
    assert meta.json()["auth_required"] is False
    missing = http.get("/api/applications/no-such-id")
    assert missing.status_code == 404
    assert "not found" in missing.json()["error"]
