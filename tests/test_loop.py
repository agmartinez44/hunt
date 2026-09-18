"""Source → inbox → promote is explicit; jobs and adapters never apply."""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from hunt.adapters.http_json import poll_http_json
from hunt.adapters.imap_alerts import poll_imap_alerts
from hunt.core.workspace import Workspace

ROOT = Path(__file__).resolve().parent.parent
EXAMPLE = ROOT / "example-workspace"
FIXTURES = Path(__file__).resolve().parent / "fixtures"


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


class FakeIMAP:
    def __init__(self, messages):
        self.messages = messages
        self.readonly = None
        self.fetched_spec = []

    def select(self, folder, readonly=False):
        self.readonly = readonly
        return "OK", [b"1"]

    def search(self, charset, *criteria):
        ids = b" ".join(k.encode() for k in self.messages)
        return "OK", [ids]

    def fetch(self, msg_id, spec):
        self.fetched_spec.append(spec)
        key = msg_id.decode() if isinstance(msg_id, bytes) else str(msg_id)
        headers = self.messages[key]
        if headers.get("raw") is not None:
            raw = headers["raw"]
            if isinstance(raw, str):
                raw = raw.encode("utf-8")
        else:
            body = headers.get("body") or ""
            raw = (
                f"From: {headers['from']}\r\n"
                f"Subject: {headers['subject']}\r\n"
                f"Date: {headers['date']}\r\n"
                f"Message-ID: {headers['message_id']}\r\n"
                f"MIME-Version: 1.0\r\n"
                f"Content-Type: text/plain; charset=UTF-8\r\n"
                f"\r\n"
                f"{body}"
            ).encode("utf-8")
        return "OK", [(b"1 (BODY[] {%d}" % len(raw), raw), b")"]

    def logout(self):
        return "OK", []


def _eml_message(name: str) -> dict:
    raw = (FIXTURES / name).read_bytes()
    import email

    msg = email.message_from_bytes(raw)
    return {
        "from": msg.get("From"),
        "subject": msg.get("Subject"),
        "date": msg.get("Date"),
        "message_id": msg.get("Message-ID"),
        "raw": raw,
    }


def test_source_poll_screen_promote_only_path(workspace):
    data, env = workspace
    _, sources = _json(["sources", "list"], env)
    ids = {row["id"] for row in sources["sources"]}
    assert "justjoin-sample" in ids

    _, poll = _json(["sources", "run", "justjoin-sample", "--run"], env)
    assert poll["job"]["state"] == "done"
    assert poll["result"]["listings"] == 2
    assert poll["result"]["new"] == 2

    _, apps_before = _json(["applications", "list"], env)
    assert apps_before["applications"] == []
    _, inbox_before = _json(["inbox", "list"], env)
    assert inbox_before["inbox"] == []

    _, screened = _json(["jobs", "enqueue", "--type", "screen-inbox", "--run"], env)
    assert screened["result"]["inbox_added"] == 2

    _, inbox = _json(["inbox", "list"], env)
    by_company = {row["company"]: row for row in inbox["inbox"]}
    assert "Acme Radar" in by_company
    assert "No Hire Inc" in by_company
    assert "pay_unknown" in by_company["No Hire Inc"]["knockouts"]
    acme = by_company["Acme Radar"]
    assert acme["status"] == "pending"

    _, apps_mid = _json(["applications", "list"], env)
    assert apps_mid["applications"] == []

    _, promoted = _json(["inbox", "promote", acme["id"]], env)
    app = promoted["application"]
    assert app["company"] == "Acme Radar"
    assert app["source"] == "justjoin-sample"
    assert app["comp_quoted"]["amount"] == 50.0
    assert app["comp_derived"]["hour"] == 45.0

    _, apps = _json(["applications", "list"], env)
    assert [row["id"] for row in apps["applications"]] == [app["id"]]

    again = _run(["--json", "inbox", "promote", acme["id"]], env, check=False)
    assert again.returncode != 0

    # Dedup: second poll creates no extra listings / inbox rows.
    _, poll2 = _json(["sources", "run", "justjoin-sample", "--run"], env)
    assert poll2["result"]["new"] == 0
    _, inbox_all = _json(["inbox", "list", "--status", "all"], env)
    assert len(inbox_all["inbox"]) == 2

    assert not (ROOT / "store.sqlite").exists()
    assert (data / "store.sqlite").is_file()


def test_tailor_cv_job_writes_attachment(workspace):
    data, env = workspace
    _, created = _json(
        ["applications", "create", "--company", "ExampleCorp", "--title-posted", "SRE"],
        env,
    )
    app_id = created["application"]["id"]
    _, out = _json(
        ["jobs", "enqueue", "--type", "tailor-cv", "--target", app_id, "--run"],
        env,
    )
    assert out["job"]["state"] == "done"
    pdf = Path(out["result"]["path"])
    assert pdf.is_file()
    assert pdf.parent == data / "attachments" / "applications" / app_id
    assert out["result"]["artifact"]["kind"] == "cv"
    _, status = _json(["jobs", "status", out["job"]["id"]], env)
    assert status["job"]["state"] == "done"


def test_disabled_imap_source_does_not_run(workspace):
    _data, env = workspace
    failed = _run(
        ["--json", "sources", "run", "mail-alerts", "--run"], env, check=False
    )
    assert failed.returncode != 0
    assert "disabled" in json.loads(failed.stderr)["error"]


def test_jobs_worker_drains_oldest_first(workspace):
    data, env = workspace
    _, first = _json(["jobs", "enqueue", "--type", "screen-inbox"], env)
    _, second = _json(["jobs", "enqueue", "--type", "screen-inbox"], env)
    first_id = first["job"]["id"]
    second_id = second["job"]["id"]
    conn = sqlite3.connect(data / "store.sqlite")
    conn.execute(
        "UPDATE jobs SET created_at = ? WHERE id = ?",
        ("2026-01-01T00:00:00Z", first_id),
    )
    conn.execute(
        "UPDATE jobs SET created_at = ? WHERE id = ?",
        ("2026-01-02T00:00:00Z", second_id),
    )
    conn.commit()
    conn.close()
    _, out = _json(["jobs", "worker"], env)
    assert [row["job"]["id"] for row in out["runs"]] == [first_id, second_id]


def test_http_json_is_get_only():
    with pytest.raises(Exception, match="GET-only"):
        poll_http_json({"url": "https://example.test/apply", "method": "POST"})


def test_imap_alerts_peek_readonly():
    fake = FakeIMAP(
        {
            "1": {
                "from": "alerts@jobs.example.org",
                "subject": "Job alert: Staff SRE at Acme",
                "date": "Thu, 1 Jan 2026 00:00:00 +0000",
                "message_id": "<alert-1@example.org>",
            },
            "2": {
                "from": "hr@other.test",
                "subject": "Lunch menu",
                "date": "Thu, 1 Jan 2026 00:00:00 +0000",
                "message_id": "<other@example.org>",
            },
        }
    )
    listings = poll_imap_alerts(
        {
            "from_contains": ["jobs.example.org"],
            "subject_contains": ["job alert"],
            "company_default": "Job alert",
        },
        {},
        connect=lambda: fake,
    )
    assert fake.readonly is True
    assert all("PEEK" in spec for spec in fake.fetched_spec)
    assert all("STORE" not in spec and "EXPUNGE" not in spec for spec in fake.fetched_spec)
    assert len(listings) == 1
    assert listings[0].title == "Staff SRE"
    assert listings[0].company == "Acme"


def test_imap_alerts_filters_before_limit():
    messages = {}
    messages["1"] = {
        "from": "jobalerts-noreply@linkedin.com",
        "subject": "Acme is hiring a Staff SRE",
        "date": "Thu, 1 Jan 2026 00:00:00 +0000",
        "message_id": "<old-alert@linkedin.com>",
    }
    for i in range(2, 62):
        messages[str(i)] = {
            "from": "hit-reply@linkedin.com",
            "subject": f"Message replied: chat {i}",
            "date": "Thu, 1 Jan 2026 00:00:00 +0000",
            "message_id": f"<inmail-{i}@linkedin.com>",
        }
    fake = FakeIMAP(messages)
    listings = poll_imap_alerts(
        {
            "from_contains": ["jobalerts-noreply"],
            "subject_contains": ["is hiring", " at ", "jobs for you"],
            "company_default": "LinkedIn",
            "limit": 50,
        },
        {},
        connect=lambda: fake,
    )
    assert len(listings) == 1
    assert listings[0].company == "Acme"
    assert listings[0].title == "Staff SRE"


def test_imap_alerts_skips_inmail_from_same_domain():
    fake = FakeIMAP(
        {
            "1": {
                "from": "jobalerts-noreply@linkedin.com",
                "subject": "Reap is hiring a Senior Site Reliability Engineer",
                "date": "Thu, 1 Jan 2026 00:00:00 +0000",
                "message_id": "<alert@linkedin.com>",
            },
            "2": {
                "from": "inmail-hit-reply@linkedin.com",
                "subject": "Senior Site Reliability Engineer",
                "date": "Thu, 1 Jan 2026 00:00:00 +0000",
                "message_id": "<inmail@linkedin.com>",
            },
            "3": {
                "from": "jobalerts-noreply@linkedin.com",
                "subject": "SRE at ExampleCorp: up to EUR 10K/month",
                "date": "Thu, 1 Jan 2026 00:00:00 +0000",
                "message_id": "<salary-alert@linkedin.com>",
            },
        }
    )
    listings = poll_imap_alerts(
        {
            "from_contains": ["jobalerts-noreply"],
            "subject_contains": ["is hiring", " at "],
            "company_default": "LinkedIn",
        },
        {},
        connect=lambda: fake,
    )
    by_company = {row.company: row for row in listings}
    assert set(by_company) == {"Reap", "ExampleCorp"}
    assert by_company["Reap"].title == "Senior Site Reliability Engineer"
    assert by_company["ExampleCorp"].title == "SRE"
    assert by_company["ExampleCorp"].payload["comp_quoted"] == {
        "amount": 10000.0,
        "currency": "EUR",
        "unit": "month",
    }


def test_imap_alerts_header_only_vs_body_fixture():
    header_only = FakeIMAP({"1": _eml_message("linkedin_alert_headers_only.eml")})
    cfg = {
        "from_contains": ["jobalerts-noreply"],
        "subject_contains": ["is hiring", " at "],
        "company_default": "LinkedIn",
    }
    headers = poll_imap_alerts(cfg, {}, connect=lambda: header_only)
    assert header_only.readonly is True
    assert header_only.fetched_spec == ["(BODY.PEEK[])"]
    assert len(headers) == 1
    assert headers[0].company == "Acme Radar"
    assert headers[0].title == "Staff SRE"
    assert headers[0].url is None
    assert "comp_quoted" not in headers[0].payload

    with_body = FakeIMAP({"1": _eml_message("linkedin_alert_body.eml")})
    listings = poll_imap_alerts(cfg, {}, connect=lambda: with_body)
    assert listings[0].company == "Acme Radar"
    assert listings[0].title == "Staff SRE"
    assert listings[0].url == "https://www.linkedin.com/jobs/view/4290000001"
    assert listings[0].payload["location_city"] == "Warsaw"
    assert listings[0].payload["location_country"] == "Poland"
    assert listings[0].payload["modality"] == "remote"
    assert listings[0].payload["comp_quoted"]["amount"] == 90000.0
    assert listings[0].external_id == "<body-alert@linkedin.com>"


def test_imap_rescreen_updates_listing_without_duplicate(workspace):
    data, env = workspace
    from hunt.core.listings import upsert_listing
    from hunt.core.screen import screen_inbox
    from hunt.core.workspace import Workspace

    with Workspace.open(data) as ws:
        from hunt.core.sources import list_sources

        list_sources(ws)
        listing, created = upsert_listing(
            ws,
            source_id="mail-alerts",
            external_id="<body-alert@linkedin.com>",
            title="Acme Radar is hiring a Staff SRE",
            company="LinkedIn",
            url=None,
            payload={
                "company": "LinkedIn",
                "title_posted": "Acme Radar is hiring a Staff SRE",
                "source": "mail-alerts",
            },
            commit=True,
        )
        assert created is True
        listing_id = listing.id
        first = screen_inbox(ws)
        assert first["inbox_added"] == 1
        enriched = ws.conn.execute(
            "SELECT company, title FROM listings WHERE id = ?", (listing_id,)
        ).fetchone()
        assert enriched["company"] == "Acme Radar"
        assert enriched["title"] == "Staff SRE"
        inbox_id = ws.conn.execute(
            "SELECT id FROM inbox_items WHERE listing_id = ?", (listing_id,)
        ).fetchone()["id"]

        fake = FakeIMAP({"1": _eml_message("linkedin_alert_body.eml")})
        fetched = poll_imap_alerts(
            {
                "from_contains": ["jobalerts-noreply"],
                "subject_contains": ["is hiring"],
                "company_default": "LinkedIn",
            },
            {},
            connect=lambda: fake,
        )
        raw = fetched[0]
        updated, is_new = upsert_listing(
            ws,
            source_id="mail-alerts",
            external_id=raw.external_id,
            title=raw.title,
            company=raw.company,
            url=raw.url,
            payload=raw.payload,
            commit=True,
        )
        assert is_new is False
        assert updated.id == listing_id
        assert updated.company == "Acme Radar"
        assert updated.title == "Staff SRE"
        assert updated.url == "https://www.linkedin.com/jobs/view/4290000001"
        second = screen_inbox(ws)
        assert second["inbox_added"] == 0
        assert second["refreshed"] == 1
        row = ws.conn.execute(
            "SELECT id FROM inbox_items WHERE listing_id = ?", (listing_id,)
        ).fetchone()
        assert row["id"] == inbox_id
        count = ws.conn.execute("SELECT COUNT(*) AS n FROM inbox_items").fetchone()["n"]
        assert count == 1


def test_adapter_sources_have_no_submit():
    text = ""
    for path in (ROOT / "hunt" / "adapters").glob("*.py"):
        text += path.read_text(encoding="utf-8")
    lowered = text.lower()
    assert "smtp" not in lowered
    assert "easy apply" not in lowered
    assert "imap4.append" not in lowered
    assert "method=\"POST\"" not in text
