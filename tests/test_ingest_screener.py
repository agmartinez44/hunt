"""source-poll never wakes Layer 3. Wake is triage-inbox keep+unsure only."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest
import yaml

from hunt.adapters import RawListing
from hunt.agent.wake import maybe_wake_screener
from hunt.core.jobs import enqueue, list_jobs
from hunt.core.sources import list_sources
from hunt.core.worker import drain, run_one
from hunt.core.workspace import Workspace

ROOT = Path(__file__).resolve().parent.parent
EXAMPLE = ROOT / "example-workspace"


@pytest.fixture
def workspace(tmp_path: Path):
    data = tmp_path / "workspace"
    shutil.copytree(EXAMPLE, data, ignore=shutil.ignore_patterns("attachments"))
    return data


def _enable_mail_alerts(data: Path) -> None:
    path = data / "config.yaml"
    cfg = yaml.safe_load(path.read_text(encoding="utf-8"))
    for spec in cfg.get("sources") or []:
        if spec.get("id") == "mail-alerts":
            spec["enabled"] = True
    path.write_text(yaml.safe_dump(cfg, sort_keys=False), encoding="utf-8")


def _enable_poll_triage(data: Path) -> None:
    path = data / "config.yaml"
    cfg = yaml.safe_load(path.read_text(encoding="utf-8"))
    cfg.setdefault("agent", {}).setdefault("triage", {})["enabled"] = True
    cfg.setdefault("knockouts", {})["drop_on"] = ["title_exclude"]
    path.write_text(yaml.safe_dump(cfg, sort_keys=False), encoding="utf-8")


def _fake_listing(source_id: str) -> RawListing:
    return RawListing(
        external_id=f"{source_id}-ext-1",
        title="Staff SRE",
        company="Acme Radar",
        url="https://example.test/jobs/1",
        payload={
            "engagement": "b2b",
            "modality": "remote",
            "comp_quoted": {"amount": 50.0, "currency": "USD", "unit": "hour"},
        },
    )


class _Proc:
    def __init__(self, pid: int = 4242):
        self.pid = pid


def test_wake_skips_llm_when_survivors_is_zero(workspace):
    spawned: list[dict] = []

    def spawn(plan, env):
        spawned.append({"plan": plan, "env": env})
        return _Proc()

    with Workspace.open(workspace) as ws:
        out = maybe_wake_screener(ws, survivors=0, spawn=spawn)
    assert out["triggered"] is False
    assert out["started"] is False
    assert out["reason"] == "no_survivors"
    assert out["never_apply"] is True
    assert out["never_send_mail"] is True
    assert spawned == []


def test_wake_spawns_screener_when_survivors_and_harness(workspace, tmp_path, monkeypatch):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    fake = bindir / "opencode"
    fake.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    fake.chmod(0o755)
    spawned: list[dict] = []

    def spawn(plan, env):
        spawned.append({"plan": plan, "env": env})
        return _Proc(99)

    with Workspace.open(workspace) as ws:
        out = maybe_wake_screener(
            ws, survivors=2, spawn=spawn, path_env=str(bindir)
        )
    assert out["triggered"] is True
    assert out["started"] is True
    assert out["reason"] == "survivors"
    assert out["harness"] == "opencode"
    assert out["pid"] == 99
    assert out["never_apply"] is True
    assert len(spawned) == 1
    plan = spawned[0]["plan"]
    assert plan["role"] == "screener"
    assert plan["never_apply"] is True
    assert plan["command"][0].endswith("opencode")
    assert spawned[0]["env"]["HUNT_AGENT_ROLE"] == "screener"
    assert spawned[0]["env"]["HUNT_DATA"] == str(workspace)


def test_wake_without_harness_does_not_fail_ingest(workspace, tmp_path):
    empty = tmp_path / "empty-bin"
    empty.mkdir()
    with Workspace.open(workspace) as ws:
        out = maybe_wake_screener(ws, survivors=1, path_env=str(empty))
    assert out["triggered"] is True
    assert out["started"] is False
    assert out["reason"] == "no_harness"
    assert out["never_apply"] is True


@pytest.mark.parametrize("source_id,kind", [("justjoin-sample", "http_json"), ("mail-alerts", "imap_alerts")])
def test_source_poll_never_calls_maybe_wake_screener(workspace, monkeypatch, source_id, kind):
    if source_id == "mail-alerts":
        _enable_mail_alerts(workspace)
    wakes: list = []

    def fake_poll(ws, source, **kwargs):
        assert source.kind == kind
        return [_fake_listing(source.id)]

    def fake_wake(ws, *, survivors, **kwargs):
        wakes.append(survivors)
        return {
            "triggered": True,
            "started": True,
            "reason": "survivors",
            "never_apply": True,
            "never_send_mail": True,
        }

    monkeypatch.setattr("hunt.adapters.poll_source", fake_poll)
    monkeypatch.setattr("hunt.agent.wake.maybe_wake_screener", fake_wake)
    monkeypatch.setattr("hunt.core.triage.maybe_wake_screener", fake_wake)

    with Workspace.open(workspace) as ws:
        list_sources(ws)
        job = enqueue(ws, job_type="source-poll", target_id=source_id)
        out = run_one(ws, job.id)
        inbox = ws.conn.execute(
            "SELECT COUNT(*) AS n FROM inbox_items WHERE status = 'pending'"
        ).fetchone()["n"]
        apps = ws.conn.execute("SELECT COUNT(*) AS n FROM applications").fetchone()["n"]
        triage_jobs = [
            j for j in list_jobs(ws, job_type="triage-inbox") if j.state in {"queued", "running"}
        ]

    assert out["job"]["state"] == "done"
    assert out["result"]["kind"] == kind
    assert out["result"]["new"] == 1
    assert out["result"]["knockouts"]["inbox_added"] == 1
    assert out["result"]["screener"]["triggered"] is False
    assert out["result"]["screener"]["reason"] == "triage_disabled"
    assert out["result"]["screener"]["never_apply"] is True
    assert out["result"]["screener"]["never_send_mail"] is True
    assert wakes == []
    assert triage_jobs == []
    assert inbox == 1
    assert apps == 0


def test_source_poll_skips_llm_when_no_new_still_runs_knockouts(workspace, monkeypatch):
    wakes: list = []

    def fake_wake(ws, *, survivors, **kwargs):
        wakes.append(survivors)
        return {
            "triggered": False,
            "started": False,
            "reason": "no_survivors",
            "never_apply": True,
            "never_send_mail": True,
        }

    monkeypatch.setattr("hunt.agent.wake.maybe_wake_screener", fake_wake)

    with Workspace.open(workspace) as ws:
        list_sources(ws)
        first = enqueue(ws, job_type="source-poll", target_id="justjoin-sample")
        first_out = run_one(ws, first.id)
        second = enqueue(ws, job_type="source-poll", target_id="justjoin-sample")
        second_out = run_one(ws, second.id)
        inbox = ws.conn.execute("SELECT COUNT(*) AS n FROM inbox_items").fetchone()["n"]
        apps = ws.conn.execute("SELECT COUNT(*) AS n FROM applications").fetchone()["n"]

    assert first_out["result"]["new"] == 2
    assert first_out["result"]["knockouts"]["inbox_added"] == 2
    assert first_out["result"]["screener"]["triggered"] is False
    assert second_out["result"]["new"] == 0
    assert second_out["result"]["screener"]["triggered"] is False
    assert second_out["result"]["screener"]["reason"] == "triage_disabled"
    assert second_out["result"]["knockouts"]["inbox_added"] == 0
    assert wakes == []
    assert inbox == 2
    assert apps == 0


def test_drain_enqueues_at_most_one_triage_across_polls(workspace, monkeypatch):
    import json as json_mod

    _enable_poll_triage(workspace)
    wakes: list = []

    def fake_wake(ws, *, survivors, **kwargs):
        wakes.append(survivors)
        return {
            "triggered": False,
            "started": False,
            "reason": "wake_disabled",
            "never_apply": True,
            "never_send_mail": True,
        }

    def fake_chat(url, payload, *, headers, timeout):
        user = json_mod.loads(payload["messages"][1]["content"])
        rows = [
            {
                "id": card["id"],
                "action": "keep",
                "codes": [],
                "reason": "ok",
            }
            for card in user["cards"]
        ]
        body = json_mod.dumps(
            {"choices": [{"message": {"content": json_mod.dumps({"results": rows})}}]}
        )
        return 200, body

    monkeypatch.setattr("hunt.agent.wake.maybe_wake_screener", fake_wake)
    monkeypatch.setattr("hunt.core.triage.maybe_wake_screener", fake_wake)
    monkeypatch.setattr("hunt.core.triage.chat_completions", fake_chat)

    with Workspace.open(workspace) as ws:
        list_sources(ws)
        enqueue(ws, job_type="source-poll", target_id="justjoin-sample")
        enqueue(ws, job_type="source-poll", target_id="remotive-eu")
        runs = drain(ws)

    polls = [row for row in runs if row["job"]["type"] == "source-poll"]
    triages = [row for row in runs if row["job"]["type"] == "triage-inbox"]
    assert len(polls) == 2
    assert len(triages) <= 1
    for row in polls:
        assert row["result"]["screener"]["triggered"] is False


def test_drain_does_not_wake_on_new(workspace, monkeypatch):
    wakes: list = []

    def fake_wake(ws, *, survivors, **kwargs):
        wakes.append(survivors)
        return {
            "triggered": True,
            "started": True,
            "reason": "survivors",
            "never_apply": True,
            "never_send_mail": True,
        }

    monkeypatch.setattr("hunt.agent.wake.maybe_wake_screener", fake_wake)

    with Workspace.open(workspace) as ws:
        list_sources(ws)
        enqueue(ws, job_type="source-poll", target_id="justjoin-sample")
        enqueue(ws, job_type="source-poll", target_id="remotive-eu")
        runs = drain(ws)

    polls = [row for row in runs if row["job"]["type"] == "source-poll"]
    assert len(polls) == 2
    total_new = sum(int(row["result"]["new"]) for row in polls)
    assert total_new == 3
    assert wakes == []
    for row in polls:
        assert row["result"]["screener"]["triggered"] is False
        assert row["result"]["screener"]["never_apply"] is True


def test_drain_with_no_new_does_not_start_llm(workspace, monkeypatch):
    wakes: list = []

    def fake_wake(ws, *, survivors, **kwargs):
        wakes.append(survivors)
        return {
            "triggered": False,
            "started": False,
            "reason": "no_survivors",
            "never_apply": True,
            "never_send_mail": True,
        }

    monkeypatch.setattr("hunt.agent.wake.maybe_wake_screener", fake_wake)

    with Workspace.open(workspace) as ws:
        list_sources(ws)
        first = enqueue(ws, job_type="source-poll", target_id="justjoin-sample")
        run_one(ws, first.id)
        wakes.clear()
        enqueue(ws, job_type="source-poll", target_id="justjoin-sample")
        runs = drain(ws)

    assert [row["result"]["new"] for row in runs] == [0]
    assert wakes == []
    assert runs[0]["result"]["screener"]["triggered"] is False


def test_wake_module_never_applies_or_sends():
    text = (ROOT / "hunt" / "agent" / "wake.py").read_text(encoding="utf-8").lower()
    worker = (ROOT / "hunt" / "core" / "worker.py").read_text(encoding="utf-8").lower()
    blob = text + worker
    assert "smtp" not in blob
    assert "easy apply" not in blob
    assert "send mail" in blob
    assert "never apply" in blob


def test_cli_poll_records_screener_trigger(workspace, monkeypatch):
    import json
    import subprocess
    import sys

    env = {
        **os.environ,
        "HUNT_DATA": str(workspace),
        "PYTHONPATH": str(ROOT),
        "HUNT_SCREENER_WAKE": "0",
    }
    r = subprocess.run(
        [sys.executable, "-m", "hunt", "--json", "sources", "run", "justjoin-sample", "--run"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    assert r.returncode == 0, r.stderr
    payload = json.loads(r.stdout)
    assert payload["result"]["new"] == 2
    assert payload["result"]["knockouts"]["inbox_added"] == 2
    assert payload["result"]["screener"]["triggered"] is False
    assert payload["result"]["screener"]["never_apply"] is True
    assert payload["result"]["screener"]["reason"] == "triage_disabled"

    r2 = subprocess.run(
        [sys.executable, "-m", "hunt", "--json", "sources", "run", "justjoin-sample", "--run"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    assert r2.returncode == 0, r2.stderr
    again = json.loads(r2.stdout)
    assert again["result"]["new"] == 0
    assert again["result"]["screener"]["triggered"] is False
    assert again["result"]["screener"]["reason"] == "triage_disabled"


def test_source_poll_enabled_still_does_not_wake(workspace, monkeypatch):
    _enable_poll_triage(workspace)
    wakes: list = []

    def fake_wake(ws, *, survivors, **kwargs):
        wakes.append(survivors)
        return {
            "triggered": True,
            "started": False,
            "reason": "survivors",
            "never_apply": True,
            "never_send_mail": True,
        }

    monkeypatch.setattr("hunt.agent.wake.maybe_wake_screener", fake_wake)
    monkeypatch.setattr("hunt.adapters.poll_source", lambda ws, source, **k: [_fake_listing(source.id)])

    with Workspace.open(workspace) as ws:
        list_sources(ws)
        job = enqueue(ws, job_type="source-poll", target_id="justjoin-sample")
        out = run_one(ws, job.id)

    assert out["result"]["new"] == 1
    assert out["result"]["screener"]["triggered"] is False
    assert out["result"]["screener"]["reason"] == "deferred_to_triage"
    assert out["result"]["triage_job_id"]
    assert wakes == []
