"""HTTP is a thin client of hunt.core — same mutations as CLI --json."""

from __future__ import annotations

import json
import os
import re
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
    assert derived["net_month"] == 5936.0
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
    row = inbox.json()["inbox"][0]
    assert row["id"] == item_id
    pay = row["payload"]["comp_quoted"]
    assert pay["amount"] == 85
    assert row["comp_quoted"]["amount"] == 85
    assert row["role"] == "Staff SRE"
    assert "net_month" in row
    assert "display_currency" in row
    if row.get("comp_derived"):
        assert "net_month" in row["comp_derived"]

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
    assert "FloorBadge" not in js
    assert "<th>Floor</th>" not in js
    assert 'BOARD_COLS = ["Company", "Title", "Status", "Modality", "Location", "Pay", "Updated", "Id"]' in js
    assert 'INBOX_COLS = ["Company", "Role", "Location", "Engagement", "Net /mo", "Why keep", "Why risk", "Age", "Actions", "Id"]' in js
    assert "<th>Net /mo</th>" in js
    assert "<th>Floor</th>" not in js
    assert 'data-primitive="NetEstimate"' in js
    assert "${esc(money(derived.net_month))} ${esc(derived.display_currency)} /mo" in js


def test_ui_polish_net_estimate_and_plex():
    """AGU-15 / AGU-11 §8: Plex, tokens, NetEstimate; floor leaves the human UI."""
    static = ROOT / "hunt" / "http" / "static"
    js = (static / "app.js").read_text()
    css = (static / "hunt.css").read_text()
    html = (static / "index.html").read_text()
    blob = js + css + html
    fonts = static / "fonts"
    assert (fonts / "ibm-plex-sans-regular.woff2").is_file()
    assert (fonts / "ibm-plex-sans-semibold.woff2").is_file()
    assert (fonts / "ibm-plex-mono-regular.woff2").is_file()
    for name in (
        "ibm-plex-sans-regular.woff2",
        "ibm-plex-sans-semibold.woff2",
        "ibm-plex-mono-regular.woff2",
    ):
        assert (fonts / name).read_bytes()[:4] == b"wOF2"
    assert "@font-face" in css
    assert 'url("/static/fonts/ibm-plex-sans-regular.woff2")' in css
    assert 'url("/static/fonts/ibm-plex-sans-semibold.woff2")' in css
    assert 'url("/static/fonts/ibm-plex-mono-regular.woff2")' in css
    assert "font-size: 100%" in css
    assert "--text-lg: 18px" in css
    assert "--radius-1: 4px" in css
    assert "--radius-2: 8px" in css
    assert "--radius-pill: 999px" in css
    assert "--shadow-1:" in css
    assert "--shadow-2:" in css
    assert 'data-primitive="NetEstimate"' in js
    assert "function NetEstimate" in js
    assert 'id="create-form" class="form-grid"' in js
    assert '<div class="section">' in js
    assert "inbox-actions" in js
    assert "Add a tax home to estimate net." in js
    assert 'data-primitive="FloorBadge"' not in blob
    assert "below floor" not in blob.lower()
    assert "clears_floor" not in blob
    assert "<th>Floor</th>" not in js
    assert ".floor-clears" not in css
    assert ".floor-below" not in css
    assert ".floor-unknown" not in css
    assert "FloorBadge" not in blob
    assert "net ${esc(money(d.net_month))}" not in js


def test_spa_and_meta(client):
    http, _, _ = client
    page = http.get("/")
    assert page.status_code == 200
    assert "Hunt" in page.text
    inbox = http.get("/inbox")
    assert inbox.status_code == 200
    profile = http.get("/profile")
    assert profile.status_code == 200
    assert "Hunt" in profile.text
    meta = http.get("/api/meta")
    assert meta.json()["profile_name"] == "Jane Doe"
    assert meta.json()["auth_required"] is False
    missing = http.get("/api/applications/no-such-id")
    assert missing.status_code == 404
    assert "not found" in missing.json()["error"]


def test_profile_editor_ui_contract():
    """AGU-19 / AGU-16 §3: profile editor primitives; no sixth tab; no verified checkbox."""
    static = ROOT / "hunt" / "http" / "static"
    js = (static / "app.js").read_text()
    css = (static / "hunt.css").read_text()
    blob = js + css
    assert 'href="/profile"' in js
    assert 'if (path === "/profile")' in js
    assert 'if (ev.key === "p") go("/profile")' in js
    assert 'data-primitive="WorkspaceChip" href="/profile"' in js
    assert 'data-primitive="DraftBadge"' in js
    assert 'data-primitive="VerifiedBadge"' in js
    assert 'data-primitive="ConfirmVerifyDialog"' in js
    assert 'data-primitive="ScopeFactsEditor"' in js
    assert 'data-primitive="ProfileNav"' in js
    assert 'data-primitive="IntegrityPanel"' in js
    assert 'data-primitive="KnowledgeConflict"' in js
    assert "Confirm this fact?" in js
    assert "Unverified claims never appear on a CV" in js
    assert "scope_facts" in js
    assert "forbidden_claims" in js
    assert "If-Match" in js
    assert '["/ ", "Board", "board"]' in js
    assert '["/inbox", "Inbox", "inbox"]' in js
    assert '["/sources", "Sources", "sources"]' in js
    assert '["/jobs", "Jobs", "jobs"]' in js
    assert '["/profile", "Profile", "profile"]' not in js
    assert 'name="verified"' not in js
    assert "type=checkbox" not in js.lower() or "data-present-index" in js
    assert 'data-path="achievements' in js
    assert "Apply for Jane Doe" not in js
    assert "Submit to employer" not in js
    assert "Send this CV" not in js
    assert 'data-primitive="FloorBadge"' not in blob
    assert "clears_floor" not in blob
    assert "--draft-fg" in css
    assert "[data-primitive=\"DraftBadge\"]" in css
    assert "[data-primitive=\"ProfileNav\"]" in css
    assert "[data-primitive=\"KnowledgeConflict\"]" in css


def _cv(args, env, check=True):
    r = subprocess.run(
        [sys.executable, "-m", "hunt.cv", *args],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    if check and r.returncode != 0:
        print(r.stdout, r.stderr, sep="\n")
        raise AssertionError(f"FAILED ({r.returncode}): hunt.cv {' '.join(args)}")
    return r


def _pdf_text(pdf: Path) -> str:
    return subprocess.run(
        ["pdftotext", str(pdf), "-"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout


def test_profile_editor_round_trip_conflict_and_honesty(client):
    """Edit Jane Doe via HTTP UI, reload, conflict on stale YAML, CV stays honest."""
    http, data, env = client
    listed = http.get("/api/achievements")
    assert listed.status_code == 200
    payload = listed.json()
    ids = [row["id"] for row in payload["achievements"]]
    assert "ec-monitoring" in ids
    assert "wc-gitops" in ids
    rev = payload["revision"]
    assert rev

    monitoring = next(row for row in payload["achievements"] if row["id"] == "ec-monitoring")
    assert monitoring["verified"] is False
    patched = http.patch(
        "/api/achievements/ec-monitoring",
        json={
            "text": monitoring["text"],
            "evidence": "Grafana dashboards still in place at exit; figure still an estimate.",
        },
        headers={"If-Match": rev},
    )
    assert patched.status_code == 200, patched.text
    body = patched.json()
    assert body["achievement"]["verified"] is False
    assert body["achievement"]["id"] == "ec-monitoring"
    new_rev = body["revision"]
    assert new_rev and new_rev != rev

    reloaded = http.get("/api/achievements/ec-monitoring")
    assert reloaded.status_code == 200
    assert reloaded.json()["achievement"]["evidence"].startswith("Grafana dashboards")
    assert reloaded.json()["achievement"]["verified"] is False

    stale = http.patch(
        "/api/achievements/wc-gitops",
        json={"evidence": "stale write must not land"},
        headers={"If-Match": rev},
    )
    assert stale.status_code == 409
    assert "not saved" in stale.json()["error"].lower()
    gitops = http.get("/api/achievements/wc-gitops").json()["achievement"]
    assert gitops["evidence"] != "stale write must not land"

    pdf = data / "attachments" / "cv" / "Jane_Doe_CV.pdf"
    rendered = _cv(["render"], env)
    assert "ec-monitoring" in rendered.stderr
    text = _pdf_text(pdf)
    assert "GitOps delivery" in text
    assert "cut alert noise by roughly half" not in text
    _cv(["finalize", str(pdf)], env)
    verdict = json.loads(_cv(["verify", str(pdf), "--json"], env).stdout)
    assert verdict["ok"], verdict.get("findings")

    confirmed = http.post(
        "/api/positions/widgetcorp/confirm",
        headers={"If-Match": http.get("/api/positions").json()["revision"]},
    )
    assert confirmed.status_code == 200
    assert confirmed.json()["position"]["verified"] is True
    assert confirmed.json()["position"]["employer"] == "WidgetCorp"


def test_connect_agent_settings_ui_contract():
    """AGU-23 / AGU-22: Agent is AppBar-only; no chat panel; no fifth tab."""
    static = ROOT / "hunt" / "http" / "static"
    js = (static / "app.js").read_text()
    css = (static / "hunt.css").read_text()
    blob = js + css
    assert 'if (path === "/settings")' in js
    assert 'NavItem("/settings", "Agent"' in js
    assert '["/settings", "Agent"' not in js
    assert 'data-primitive="AppTabBar"' in js
    assert 'data-primitive="AgentStatus"' in js
    assert 'data-primitive="HarnessPicker"' in js
    assert 'data-primitive="InstallPreview"' in js
    assert 'data-primitive="DoctorList"' in js
    assert 'data-primitive="SecretField"' in js
    assert 'data-primitive="AgentSection"' in js
    assert "Hunt does not run a chat" in js
    assert "Hunt does not sell a model subscription" in js
    assert "ChatPanel" not in blob
    assert "Ask Hunt" not in blob
    assert "state.apiKey" not in js
    assert "clears_floor" not in blob
    assert "Easy Apply" not in blob
    assert 'type="password" autocomplete="off" placeholder="xai-…"' in js
    assert "hunt agent doctor --json" in js
    assert "hunt agent run operator" in js
    assert "agent-dirty" in js
    assert "[data-primitive=\"AgentStatus\"]" in css
    assert "[data-primitive=\"DoctorList\"]" in css
    assert "[data-primitive=\"SecretField\"]" in css
    # AGU-25: ErrorBanner is for network/exception only, not first doctor fail.
    assert "ErrorBanner(state.agentError" in js
    assert "ErrorBanner(firstFail" not in js
    # AGU-25: last DoctorList row rule must not pin 36px (mobile wrap collision).
    row_blocks = list(re.finditer(
        r'\[data-primitive="DoctorList"\] \.doctor-row \{([^}]+)\}', css
    ))
    assert row_blocks, "DoctorList .doctor-row rules missing"
    last_row = row_blocks[-1].group(1)
    assert re.search(r"(?<!min-)height:\s*36px", last_row) is None
    assert "height: auto" in last_row
    assert "flex-wrap: wrap" in last_row
    assert "[data-primitive=\"AgentStatus\"][data-state=\"connecting\"]" in css
    assert "AppBar\"] > [data-primitive=\"NavItem\"]" in css
    assert "PageHeader\"].agent-header [data-primitive=\"Btn\"].primary" in css


def test_agent_http_secret_never_returned_and_local_round_trip(client):
    """Token stays in secrets.env; GET/CLI doctor agree on the local URL."""
    http, data, env = client
    page = http.get("/settings")
    assert page.status_code == 200
    assert "Hunt" in page.text
    unknown = http.get("/settings/nope")
    assert unknown.status_code == 200

    got = http.get("/api/agent")
    assert got.status_code == 200, got.text
    payload = got.json()
    dumped = json.dumps(payload)
    assert "api_key" not in dumped or "api_key_env" in dumped
    assert "api_key_set" in payload
    assert payload["api_key_set"] is False
    assert payload["model"]["base_url"] == "https://api.x.ai/v1"
    assert payload["model"]["api_key_env"] == "XAI_API_KEY"
    assert payload["model"]["model"] == "grok-4.5"
    assert payload["never_apply"] is True
    assert "xai-" not in dumped.lower()
    blob = json.dumps(payload).lower()
    assert "sk-" not in blob

    secret_value = "xai-jane-doe-test-key-not-real"
    saved = http.put(
        "/api/agent/secret",
        json={"env": "XAI_API_KEY", "value": secret_value},
    )
    assert saved.status_code == 200, saved.text
    saved_body = saved.json()
    assert saved_body["api_key_set"] is True
    assert secret_value not in json.dumps(saved_body)
    secrets_text = (data / "secrets.env").read_text(encoding="utf-8")
    assert "XAI_API_KEY=" in secrets_text
    assert secret_value in secrets_text
    cfg = (data / "config.yaml").read_text(encoding="utf-8")
    assert secret_value not in cfg

    again = http.get("/api/agent")
    assert again.json()["api_key_set"] is True
    assert secret_value not in json.dumps(again.json())
    assert again.json()["model"]["api_key_env"] == "XAI_API_KEY"

    local = http.patch(
        "/api/agent",
        json={
            "harness": "claude",
            "model": {
                "base_url": "http://127.0.0.1:9/v1",
                "api_key_env": "",
                "model": "gemma-e4b",
            },
        },
    )
    assert local.status_code == 200, local.text
    local_body = local.json()
    assert local_body["model"]["base_url"] == "http://127.0.0.1:9/v1"
    assert local_body["model"]["api_key_env"] in ("", None)
    assert local_body["doctor"]["ok"] is False
    assert local_body["state"] == "fail"
    models = next(c for c in local_body["doctor"]["checks"] if c["id"] == "models")
    assert models["ok"] is False
    assert "127.0.0.1:9" in (models["label"] or models["detail"])
    assert secret_value not in json.dumps(local_body)

    reloaded = http.get("/api/agent")
    assert reloaded.json()["model"]["base_url"] == "http://127.0.0.1:9/v1"

    _, cli = _json(["agent", "status"], env)
    assert cli["model"]["base_url"] == "http://127.0.0.1:9/v1"
    run, _err = _json(
        ["agent", "doctor", "--root", str(data), "--timeout", "1"],
        env,
        check=False,
    )
    doctor = json.loads(run.stdout)
    assert doctor["ok"] is False
    assert doctor["model"]["base_url"] == "http://127.0.0.1:9/v1"
    assert doctor["model"].get("local") is True
    assert secret_value not in json.dumps(doctor)

    preview = http.get("/api/agent?harness=claude")
    writes = preview.json()["preview"]["writes"]
    assert writes
    assert any(row["kind"] == "mcp" for row in writes)
    dry = _json(
        ["agent", "install", "--harness", "claude", "--dry-run", "--root", str(data)],
        env,
    )[1]
    assert dry["writes"]
    assert not (data / ".mcp.json").exists()

    installed = http.post("/api/agent/install", json={"harness": "claude"})
    assert installed.status_code == 200, installed.text
    assert (data / ".mcp.json").is_file()
    assert (data / ".claude" / "skills" / "hunt-operator" / "SKILL.md").is_file()
    mcp = json.loads((data / ".mcp.json").read_text(encoding="utf-8"))
    assert mcp["mcpServers"]["hunt"]["env"]["HUNT_DATA"] == str(data.resolve())
    assert secret_value not in json.dumps(mcp)
