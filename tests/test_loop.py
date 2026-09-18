"""Source → inbox → promote is explicit; jobs and adapters never apply."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from hunt.adapters.http_json import poll_http_json
from hunt.adapters.imap_alerts import poll_imap_alerts
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
        raw = (
            f"From: {headers['from']}\r\n"
            f"Subject: {headers['subject']}\r\n"
            f"Date: {headers['date']}\r\n"
            f"Message-ID: {headers['message_id']}\r\n\r\n"
        )
        return "OK", [(b"1", raw.encode())]

    def logout(self):
        return "OK", []


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
    assert len(listings) == 1
    assert listings[0].title.startswith("Job alert")
    assert listings[0].company == "Job alert"


def test_adapter_sources_have_no_submit():
    text = ""
    for path in (ROOT / "hunt" / "adapters").glob("*.py"):
        text += path.read_text(encoding="utf-8")
    lowered = text.lower()
    assert "smtp" not in lowered
    assert "easy apply" not in lowered
    assert "imap4.append" not in lowered
    assert "method=\"POST\"" not in text
