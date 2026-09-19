"""source-poll wakes the LLM screener only when ingest created listings."""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from hunt.adapters import RawListing
from hunt.agent.wake import maybe_wake_screener
from hunt.core.jobs import enqueue
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


def test_wake_skips_llm_when_new_is_zero(workspace):
    spawned: list[dict] = []

    def spawn(plan, env):
        spawned.append({"plan": plan, "env": env})
        return _Proc()

    with Workspace.open(workspace) as ws:
        out = maybe_wake_screener(ws, new=0, spawn=spawn)
    assert out["triggered"] is False
    assert out["started"] is False
    assert out["reason"] == "no_new_listings"
    assert out["never_apply"] is True
    assert out["never_send_mail"] is True
    assert spawned == []


def test_wake_spawns_screener_when_new_and_harness(workspace, tmp_path, monkeypatch):
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
            ws, new=2, spawn=spawn, path_env=str(bindir)
        )
    assert out["triggered"] is True
    assert out["started"] is True
    assert out["reason"] == "new_listings"
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
        out = maybe_wake_screener(ws, new=1, path_env=str(empty))
    assert out["triggered"] is True
    assert out["started"] is False
    assert out["reason"] == "no_harness"
    assert out["never_apply"] is True


@pytest.mark.parametrize("source_id,kind", [("justjoin-sample", "http_json"), ("mail-alerts", "imap_alerts")])
def test_source_poll_triggers_screener_per_kind(workspace, monkeypatch, source_id, kind):
    if source_id == "mail-alerts":
        _enable_mail_alerts(workspace)
    calls: list[int] = []

    def fake_poll(ws, source, **kwargs):
        assert source.kind == kind
        return [_fake_listing(source.id)]

    def fake_wake(ws, *, new, **kwargs):
        calls.append(new)
        return {
            "triggered": new > 0,
            "started": new > 0,
            "reason": "new_listings" if new > 0 else "no_new_listings",
            "never_apply": True,
            "never_send_mail": True,
        }

    monkeypatch.setattr("hunt.adapters.poll_source", fake_poll)
    monkeypatch.setattr("hunt.agent.wake.maybe_wake_screener", fake_wake)

    with Workspace.open(workspace) as ws:
        list_sources(ws)
        job = enqueue(ws, job_type="source-poll", target_id=source_id)
        out = run_one(ws, job.id)
        inbox = ws.conn.execute(
            "SELECT COUNT(*) AS n FROM inbox_items WHERE status = 'pending'"
        ).fetchone()["n"]
        apps = ws.conn.execute("SELECT COUNT(*) AS n FROM applications").fetchone()["n"]

    assert out["job"]["state"] == "done"
    assert out["result"]["kind"] == kind
    assert out["result"]["new"] == 1
    assert out["result"]["knockouts"]["inbox_added"] == 1
    assert out["result"]["screener"]["triggered"] is True
    assert out["result"]["screener"]["never_apply"] is True
    assert out["result"]["screener"]["never_send_mail"] is True
    assert calls == [1]
    assert inbox == 1
    assert apps == 0


def test_source_poll_skips_llm_when_no_new_still_runs_knockouts(workspace, monkeypatch):
    wakes: list[int] = []

    def fake_wake(ws, *, new, **kwargs):
        wakes.append(new)
        return {
            "triggered": False,
            "started": False,
            "reason": "no_new_listings",
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
    assert second_out["result"]["new"] == 0
    assert second_out["result"]["screener"]["triggered"] is False
    assert second_out["result"]["screener"]["reason"] == "no_new_listings"
    assert second_out["result"]["knockouts"]["inbox_added"] == 0
    assert wakes[-1] == 0
    assert inbox == 2
    assert apps == 0


def test_drain_coalesces_screener_wake_after_all_polls(workspace, monkeypatch):
    wakes: list[int] = []

    def fake_wake(ws, *, new, **kwargs):
        wakes.append(new)
        return {
            "triggered": new > 0,
            "started": new > 0,
            "reason": "new_listings" if new > 0 else "no_new_listings",
            "never_apply": True,
            "never_send_mail": True,
            "pid": 7 if new > 0 else None,
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
    assert wakes == [total_new]
    for row in polls:
        if row["result"]["new"] > 0:
            assert row["result"]["screener"]["triggered"] is True
            assert row["result"]["screener"]["started"] is True
        assert row["result"]["knockouts"]["inbox_added"] >= 0
        assert row["result"]["screener"]["never_apply"] is True


def test_drain_with_no_new_does_not_start_llm(workspace, monkeypatch):
    wakes: list[int] = []

    def fake_wake(ws, *, new, **kwargs):
        wakes.append(new)
        return {
            "triggered": False,
            "started": False,
            "reason": "no_new_listings",
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
    assert wakes == [0]
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
    assert payload["result"]["screener"]["triggered"] is True
    assert payload["result"]["screener"]["started"] is False
    assert payload["result"]["screener"]["never_apply"] is True

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
    assert again["result"]["screener"]["reason"] == "no_new_listings"
