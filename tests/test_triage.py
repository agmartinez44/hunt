"""Layer 2 triage-inbox. Pytest mocks HTTP. Never opens 8080 or api.x.ai."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from hunt.agent.config import triage_auto_dismiss, triage_max_cards
from hunt.core.errors import HuntError, NotFoundError
from hunt.core.inbox import add_item, dismiss, get_inbox_item, list_inbox, restore
from hunt.core.jobs import enqueue, enqueue_triage_if_needed, get_job
from hunt.core.sources import list_sources
from hunt.core.triage import (
    TRIAGE_SYSTEM_PROMPT,
    count_untriaged_pending,
    input_hash,
    listing_card,
    run_triage_inbox,
    validate_outcome,
)
from hunt.core.worker import drain, run_one
from hunt.core.workspace import Workspace

ROOT = Path(__file__).resolve().parent.parent
EXAMPLE = ROOT / "example-workspace"


@pytest.fixture
def workspace(tmp_path: Path):
    import shutil

    data = tmp_path / "workspace"
    shutil.copytree(EXAMPLE, data, ignore=shutil.ignore_patterns("attachments"))
    return data


def _cfg(data: Path) -> dict:
    path = data / "config.yaml"
    cfg = yaml.safe_load(path.read_text(encoding="utf-8"))
    return cfg


def _save_cfg(data: Path, cfg: dict) -> None:
    (data / "config.yaml").write_text(
        yaml.safe_dump(cfg, sort_keys=False), encoding="utf-8"
    )


def _set_triage(data: Path, **updates) -> None:
    cfg = _cfg(data)
    triage = dict((cfg.get("agent") or {}).get("triage") or {})
    triage.update(updates)
    cfg.setdefault("agent", {})["triage"] = triage
    _save_cfg(data, cfg)


def _set_drop_on(data: Path, codes: list[str]) -> None:
    cfg = _cfg(data)
    cfg.setdefault("knockouts", {})["drop_on"] = list(codes)
    _save_cfg(data, cfg)


def _results_for_cards(payload, action="dismiss", codes=None, extra_ids=None):
    user = json.loads(payload["messages"][1]["content"])
    rows = [
        {
            "id": card["id"],
            "action": action,
            "codes": list(codes or ["wrong_role"]),
            "reason": "title mismatch",
        }
        for card in user["cards"]
    ]
    for ident in extra_ids or []:
        rows.append(
            {
                "id": ident,
                "action": action,
                "codes": ["wrong_role"],
                "reason": "extra",
            }
        )
    body = json.dumps(
        {"choices": [{"message": {"content": json.dumps({"results": rows})}}]}
    )
    return 200, body


def _mock_ok(monkeypatch, calls, *, action="dismiss", codes=None):
    def fake(url, payload, *, headers, timeout):
        calls.append({"url": url, "payload": payload, "headers": headers})
        return _results_for_cards(payload, action=action, codes=codes)

    monkeypatch.setattr("hunt.core.triage.chat_completions", fake)


def _add_pending(ws: Workspace, **kwargs):
    kwargs.setdefault("company", "Acme Radar")
    kwargs.setdefault("title", "Staff platform engineer")
    kwargs.setdefault("payload", {"engagement": "b2b", "modality": "remote"})
    kwargs.setdefault("source_id", "justjoin-sample")
    list_sources(ws)
    return add_item(ws, **kwargs)


def test_prompt_has_no_sre_or_country_allowlist():
    text = TRIAGE_SYSTEM_PROMPT
    assert "SRE" not in text
    assert "CH" not in text
    assert "ES" not in text
    assert "PL" not in text
    src = (ROOT / "hunt" / "core" / "triage.py").read_text(encoding="utf-8")
    assert "TRIAGE_SYSTEM_PROMPT" in src


def test_input_hash_stable_when_fx_gross_changes(workspace):
    with Workspace.open(workspace) as ws:
        item = _add_pending(
            ws,
            payload={
                "engagement": "b2b",
                "comp_quoted": {"amount": 50, "currency": "USD", "unit": "hour"},
            },
        )
        card = listing_card(item, source_id=item.source_id, ws=ws)
        first = input_hash(card)
        cfg = _cfg(workspace)
        cfg["fx"]["rates"]["USD"] = 0.5
        _save_cfg(workspace, cfg)
        ws.reload_config()
        item = get_inbox_item(ws, item.id)
        card2 = listing_card(item, source_id=item.source_id, ws=ws)
        assert input_hash(card2) == first
        assert card2["gross_month"] != card["gross_month"]


def test_input_hash_changes_on_title(workspace):
    with Workspace.open(workspace) as ws:
        item = _add_pending(ws, title="Staff platform")
        card = listing_card(item, source_id=item.source_id, ws=ws)
        other = dict(card)
        other["title"] = "Helpdesk technician"
        assert input_hash(other) != input_hash(card)


def test_cards_use_listings_source_id_not_payload_source(workspace):
    with Workspace.open(workspace) as ws:
        item = _add_pending(
            ws,
            source_id="justjoin-sample",
            payload={"source": "imap_alerts", "engagement": "b2b"},
        )
        card = listing_card(item, source_id=item.source_id, ws=ws)
        assert card["source_id"] == "justjoin-sample"
        assert card["source_id"] != "imap_alerts"


def test_cards_have_no_url(workspace):
    with Workspace.open(workspace) as ws:
        item = _add_pending(ws, url="https://example.test/jobs/1")
        card = listing_card(item, source_id=item.source_id, ws=ws)
        assert "url" not in card
        blob = json.dumps(card)
        assert "https://" not in blob


def test_validator_strips_unknown_codes_before_empty_dismiss():
    card = {"below_floor": False, "pay_unknown": False}
    keep = validate_outcome(
        {
            "id": "abc",
            "action": "dismiss",
            "codes": ["wrong_role", "triaged"],
            "reason": "helpdesk",
        },
        card,
        reason_max=80,
    )
    assert keep["action"] == "dismiss"
    assert keep["codes"] == ["wrong_role"]
    unsure = validate_outcome(
        {
            "id": "abc",
            "action": "dismiss",
            "codes": ["below_floor"],
            "reason": "pay",
        },
        card,
        reason_max=80,
    )
    assert unsure["action"] == "unsure"
    assert unsure["codes"] == []


def test_validator_strips_pay_below_floor_unless_hunt_below_floor():
    card = {"below_floor": False, "pay_unknown": False, "clears_floor": None}
    out = validate_outcome(
        {
            "id": "x",
            "action": "dismiss",
            "codes": ["pay_below_floor"],
            "reason": "below floor",
        },
        card,
        reason_max=80,
    )
    assert "pay_below_floor" not in out["codes"]
    assert out["action"] == "unsure"
    card2 = {"below_floor": True, "pay_unknown": False}
    ok = validate_outcome(
        {
            "id": "x",
            "action": "dismiss",
            "codes": ["pay_below_floor"],
            "reason": "below floor",
        },
        card2,
        reason_max=80,
    )
    assert ok["action"] == "dismiss"
    assert ok["codes"] == ["pay_below_floor"]


def test_validator_pay_unknown_cannot_dismiss_for_pay():
    card = {"below_floor": False, "pay_unknown": True}
    out = validate_outcome(
        {
            "id": "x",
            "action": "dismiss",
            "codes": ["pay_below_floor"],
            "reason": "salary too low",
        },
        card,
        reason_max=80,
    )
    assert out["action"] == "unsure"
    role = validate_outcome(
        {
            "id": "x",
            "action": "dismiss",
            "codes": ["wrong_role"],
            "reason": "helpdesk title",
        },
        card,
        reason_max=80,
    )
    assert role["action"] == "dismiss"


def test_validator_rejects_extra_ids(workspace, monkeypatch):
    calls = []

    def fake(url, payload, *, headers, timeout):
        calls.append(url)
        return _results_for_cards(payload, extra_ids=["deadbeefdead"])

    monkeypatch.setattr("hunt.core.triage.chat_completions", fake)
    with Workspace.open(workspace) as ws:
        item = _add_pending(ws)
        job = enqueue(ws, job_type="triage-inbox")
        out = run_triage_inbox(ws, job)
        assert item.id in {
            row.id for row in list_inbox(ws, status="all")
        }
        with pytest.raises(NotFoundError):
            get_inbox_item(ws, "deadbeefdead")
        assert out["selected"] == 1


def test_shrink_on_invalid_then_per_id_commit(workspace, monkeypatch):
    n = {"calls": 0}

    def fake(url, payload, *, headers, timeout):
        n["calls"] += 1
        user = json.loads(payload["messages"][1]["content"])
        if n["calls"] == 1:
            return 200, "not-json"
        return _results_for_cards(payload, action="keep", codes=[])

    monkeypatch.setattr("hunt.core.triage.chat_completions", fake)
    with Workspace.open(workspace) as ws:
        for i in range(2):
            _add_pending(ws, title=f"Role {i}", external_id=f"e{i}")
        job = enqueue(ws, job_type="triage-inbox")
        out = run_triage_inbox(ws, job)
        assert out["kept"] + out["unsure"] + out["dismissed"] >= 1
        assert n["calls"] >= 2


def test_response_format_http_400_retries_without(workspace, monkeypatch):
    seen = []

    def fake(url, payload, *, headers, timeout):
        seen.append("response_format" in payload)
        if payload.get("response_format"):
            return 400, '{"error":"unknown field"}'
        return _results_for_cards(payload, action="keep", codes=[])

    monkeypatch.setattr("hunt.core.triage.chat_completions", fake)
    with Workspace.open(workspace) as ws:
        _add_pending(ws)
        job = enqueue(ws, job_type="triage-inbox")
        out = run_triage_inbox(ws, job)
        assert True in seen and False in seen
        assert out["kept"] == 1


def test_model_down_fails_job_does_not_wake_or_call_hosted(workspace, monkeypatch):
    calls = []
    wakes = []

    def fake(url, payload, *, headers, timeout):
        calls.append(url)
        raise TimeoutError("down")

    def fake_wake(ws, *, survivors, **kwargs):
        wakes.append(survivors)
        return {"triggered": False, "started": False, "reason": "no_survivors"}

    monkeypatch.setattr("hunt.core.triage.chat_completions", fake)
    monkeypatch.setattr("hunt.core.triage.maybe_wake_screener", fake_wake)
    with Workspace.open(workspace) as ws:
        _add_pending(ws)
        job = enqueue(ws, job_type="triage-inbox")
        with pytest.raises(HuntError):
            run_one(ws, job.id)
        finished = get_job(ws, job.id)
        assert finished.state == "failed"
    assert calls
    assert all("api.x.ai" not in url for url in calls)
    assert wakes == []


def test_hosted_triage_url_fails_before_post(workspace, monkeypatch):
    calls = []

    def fake(url, payload, *, headers, timeout):
        calls.append(url)
        return 200, "{}"

    monkeypatch.setattr("hunt.core.triage.chat_completions", fake)
    _set_triage(
        workspace,
        model={
            "base_url": "https://api.x.ai/v1",
            "api_key_env": "XAI_API_KEY",
            "model": "grok-4.5",
        },
    )
    with Workspace.open(workspace) as ws:
        _add_pending(ws)
        job = enqueue(ws, job_type="triage-inbox")
        with pytest.raises(HuntError) as exc:
            run_triage_inbox(ws, job)
        assert "api.x.ai" in str(exc.value)
    assert calls == []


def test_http_5xx_shrink_then_done_if_any_committed(workspace, monkeypatch):
    n = {"calls": 0}

    def fake(url, payload, *, headers, timeout):
        n["calls"] += 1
        user = json.loads(payload["messages"][1]["content"])
        if n["calls"] == 1:
            return _results_for_cards(payload, action="keep", codes=[])
        return 502, "bad gateway"

    monkeypatch.setattr("hunt.core.triage.chat_completions", fake)
    _set_triage(workspace, batch_size=1)
    with Workspace.open(workspace) as ws:
        _add_pending(ws, title="One", external_id="a")
        _add_pending(ws, title="Two", external_id="b")
        job = enqueue(ws, job_type="triage-inbox")
        out = run_triage_inbox(ws, job)
        assert out["kept"] >= 1
        assert out.get("failed_reason")


def test_auto_dismiss_default_true_when_triage_block_missing(workspace):
    cfg = _cfg(workspace)
    cfg["agent"].pop("triage", None)
    _save_cfg(workspace, cfg)
    with Workspace.open(workspace) as ws:
        assert triage_auto_dismiss(ws) is True


def test_auto_dismiss_writes_triage_json_and_dismisses(workspace, monkeypatch):
    calls = []
    _mock_ok(monkeypatch, calls, action="dismiss", codes=["wrong_role"])
    with Workspace.open(workspace) as ws:
        item = _add_pending(ws, title="Microsoft 365 admin")
        job = enqueue(ws, job_type="triage-inbox")
        out = run_one(ws, job.id)
        row = get_inbox_item(ws, item.id)
        assert row.status == "dismissed"
        assert row.triage["action"] == "dismiss"
        assert "triaged" in row.knockouts
        assert out["result"]["dismissed"] == 1


def test_human_why_keep_junior_skipped_not_dismissed(workspace, monkeypatch):
    calls = []
    _mock_ok(monkeypatch, calls, action="dismiss", codes=["junior"])
    _set_drop_on(workspace, ["experience"])
    with Workspace.open(workspace) as ws:
        item = _add_pending(
            ws,
            title="Junior intern",
            why_keep="human wants this junior role",
            payload={"experience_level": "junior", "engagement": "fte"},
        )
        job = enqueue(ws, job_type="triage-inbox")
        out = run_triage_inbox(ws, job)
        row = get_inbox_item(ws, item.id)
        assert row.status == "pending"
        assert row.triage["skipped_human_note"] is True
        assert out["selected"] == 0
        posted = []
        for call in calls:
            user = json.loads(call["payload"]["messages"][1]["content"])
            posted.extend(c["id"] for c in user["cards"])
        assert item.id not in posted


def test_human_notes_do_not_consume_max_cards(workspace, monkeypatch):
    calls = []
    _mock_ok(monkeypatch, calls, action="keep", codes=[])
    _set_triage(workspace, max_cards=8)
    with Workspace.open(workspace) as ws:
        for i in range(8):
            _add_pending(
                ws,
                title=f"Noted {i}",
                why_keep="human keep",
                external_id=f"note-{i}",
            )
        for i in range(8):
            _add_pending(ws, title=f"Open {i}", external_id=f"open-{i}")
        job = enqueue(ws, job_type="triage-inbox")
        out = run_triage_inbox(ws, job)
        assert out["selected"] == 8
        assert out["skipped_human_note"] == 8
        assert out["remaining_untriaged"] == 0


def test_poll_job_does_not_stamp_human_notes(workspace, monkeypatch):
    calls = []
    _mock_ok(monkeypatch, calls, action="keep", codes=[])
    _set_triage(workspace, enabled=True)
    _set_drop_on(workspace, ["title_exclude"])
    with Workspace.open(workspace) as ws:
        _add_pending(ws, why_keep="human keep", external_id="note-1")
        _add_pending(ws, title="Open role", external_id="open-1")
        job = enqueue_triage_if_needed(ws, skip_if_idle=True)
        assert job is not None
        assert job.payload.get("stamp_human_notes") is not True
        out = run_triage_inbox(ws, job)
        assert out["skipped_human_note"] == 0


def test_force_does_not_auto_dismiss_human_why(workspace, monkeypatch):
    calls = []
    _mock_ok(monkeypatch, calls, action="dismiss", codes=["wrong_role"])
    with Workspace.open(workspace) as ws:
        item = _add_pending(ws, why_keep="human keep this")
        job = enqueue(ws, job_type="triage-inbox", payload={"force": True})
        out = run_triage_inbox(ws, job)
        row = get_inbox_item(ws, item.id)
        assert row.status == "pending"
        assert row.triage["action"] == "keep"
        assert row.why_keep == "human keep this"
        assert "wrong_role" in row.knockouts
        assert out["kept"] == 1
        assert out["dismissed"] == 0


def test_force_does_not_auto_dismiss_restored(workspace, monkeypatch):
    calls = []
    _mock_ok(monkeypatch, calls, action="dismiss", codes=["wrong_role"])
    with Workspace.open(workspace) as ws:
        item = _add_pending(ws)
        dismiss(ws, item.id)
        restored = restore(ws, item.id)
        assert restored.triage["restored"] is True
        job = enqueue(ws, job_type="triage-inbox", payload={"force": True})
        run_triage_inbox(ws, job)
        row = get_inbox_item(ws, item.id)
        assert row.status == "pending"
        assert row.triage["restored"] is True
        assert row.triage["action"] == "keep"


def test_result_json_selected_equals_outcomes(workspace, monkeypatch):
    calls = []
    _mock_ok(monkeypatch, calls, action="keep", codes=[])
    with Workspace.open(workspace) as ws:
        _add_pending(ws)
        job = enqueue(ws, job_type="triage-inbox")
        out = run_triage_inbox(ws, job)
        assert out["selected"] == out["dismissed"] + out["kept"] + out["unsure"] + out[
            "invalid"
        ]


def test_enqueue_triage_coalesces_under_immediate(workspace):
    with Workspace.open(workspace) as ws:
        first = enqueue(ws, job_type="triage-inbox")
        second = enqueue(ws, job_type="triage-inbox")
        assert first.id == second.id


def test_force_attaches_to_queued_refuses_if_running(workspace):
    with Workspace.open(workspace) as ws:
        first = enqueue(ws, job_type="triage-inbox")
        attached = enqueue(ws, job_type="triage-inbox", payload={"force": True})
        assert attached.id == first.id
        assert attached.payload.get("force") is True
        ws.conn.execute(
            "UPDATE jobs SET state = 'running' WHERE id = ?", (first.id,)
        )
        ws.conn.commit()
        with pytest.raises(HuntError, match="already running"):
            enqueue(ws, job_type="triage-inbox", payload={"force": True})


def test_enqueue_triage_integrityerror_returns_existing(workspace, monkeypatch):
    from hunt.core import jobs as jobs_mod

    with Workspace.open(workspace) as ws:
        first = enqueue(ws, job_type="triage-inbox")
        real_active = jobs_mod.active_for
        calls = {"n": 0}

        def fake_active(ws_inner, job_type, target_id=None):
            calls["n"] += 1
            if calls["n"] == 1:
                return []
            return real_active(ws_inner, job_type, target_id)

        monkeypatch.setattr(jobs_mod, "active_for", fake_active)
        again = jobs_mod.enqueue_triage_if_needed(ws, skip_if_idle=False)
        assert again.id == first.id


def test_enqueue_triage_rolls_back_open_txn_then_begins(workspace):
    with Workspace.open(workspace) as ws:
        ws.conn.execute("BEGIN")
        assert ws.conn.in_transaction
        job = enqueue_triage_if_needed(ws, skip_if_idle=False)
        assert job is not None
        assert job.type == "triage-inbox"


def test_manual_enqueue_triage_when_idle_inserts_job(workspace):
    with Workspace.open(workspace) as ws:
        assert count_untriaged_pending(ws) == 0
        job = enqueue(ws, job_type="triage-inbox")
        assert job.state == "queued"
        assert job.payload.get("stamp_human_notes") is True


def test_manual_enqueue_triage_when_disabled_inserts_job(workspace):
    _set_triage(workspace, enabled=False)
    _set_drop_on(workspace, [])
    with Workspace.open(workspace) as ws:
        job = enqueue(ws, job_type="triage-inbox")
        assert job is not None
        assert job.type == "triage-inbox"


def test_poll_does_not_enqueue_when_triage_disabled(workspace):
    _set_triage(workspace, enabled=False)
    _set_drop_on(workspace, ["title_exclude"])
    with Workspace.open(workspace) as ws:
        _add_pending(ws)
        job = enqueue_triage_if_needed(ws, skip_if_idle=True)
        assert job is None


def test_poll_does_not_enqueue_when_drop_on_empty(workspace):
    _set_triage(workspace, enabled=True)
    _set_drop_on(workspace, [])
    with Workspace.open(workspace) as ws:
        _add_pending(ws)
        job = enqueue_triage_if_needed(ws, skip_if_idle=True)
        assert job is None


def test_poll_enqueues_when_enabled_and_drop_on_and_untriaged(workspace):
    _set_triage(workspace, enabled=True)
    _set_drop_on(workspace, ["title_exclude"])
    with Workspace.open(workspace) as ws:
        _add_pending(ws)
        job = enqueue_triage_if_needed(ws, skip_if_idle=True)
        assert job is not None
        assert job.payload.get("stamp_human_notes") is not True


def test_poll_does_not_enqueue_when_only_human_notes_remain(workspace):
    _set_triage(workspace, enabled=True)
    _set_drop_on(workspace, ["title_exclude"])
    with Workspace.open(workspace) as ws:
        _add_pending(ws, why_keep="human keep")
        job = enqueue_triage_if_needed(ws, skip_if_idle=True)
        assert job is None


def test_empty_keep_hint_is_in_user_json(workspace, monkeypatch):
    seen = []

    def fake(url, payload, *, headers, timeout):
        user = json.loads(payload["messages"][1]["content"])
        seen.append(user["keep_hint"])
        return _results_for_cards(payload, action="dismiss", codes=["wrong_role"])

    monkeypatch.setattr("hunt.core.triage.chat_completions", fake)
    with Workspace.open(workspace) as ws:
        _add_pending(ws, title="Microsoft 365 admin")
        job = enqueue(ws, job_type="triage-inbox")
        run_triage_inbox(ws, job)
    assert seen == [""]


def test_triage_max_cards_exits_done_leaves_remaining(workspace, monkeypatch):
    calls = []
    _mock_ok(monkeypatch, calls, action="keep", codes=[])
    _set_triage(workspace, max_cards=8)
    with Workspace.open(workspace) as ws:
        for i in range(16):
            _add_pending(ws, title=f"Role {i}", external_id=f"r{i}")
        job = enqueue(ws, job_type="triage-inbox")
        out = run_one(ws, job.id)
        assert out["job"]["state"] == "done"
        assert out["result"]["selected"] == 8
        assert out["result"]["remaining_untriaged"] == 8
        queued = [
            row
            for row in ws.conn.execute(
                "SELECT id FROM jobs WHERE type = 'triage-inbox' AND state = 'queued'"
            ).fetchall()
        ]
        assert queued == []


def test_pytest_triage_uses_mock_http_max_cards_8(workspace, monkeypatch):
    calls = []
    _mock_ok(monkeypatch, calls, action="keep", codes=[])
    cfg = _cfg(workspace)
    assert cfg["agent"]["triage"]["max_cards"] == 8
    with Workspace.open(workspace) as ws:
        assert triage_max_cards(ws) == 8
        _add_pending(ws)
        job = enqueue(ws, job_type="triage-inbox")
        run_triage_inbox(ws, job)
    assert calls
    assert all("api.x.ai" not in c["url"] for c in calls)


def test_triage_does_not_self_enqueue_when_capped(workspace, monkeypatch):
    calls = []
    _mock_ok(monkeypatch, calls, action="keep", codes=[])
    _set_triage(workspace, max_cards=1)
    with Workspace.open(workspace) as ws:
        _add_pending(ws, title="A", external_id="a")
        _add_pending(ws, title="B", external_id="b")
        job = enqueue(ws, job_type="triage-inbox")
        run_triage_inbox(ws, job)
        n = ws.conn.execute(
            "SELECT COUNT(*) AS n FROM jobs WHERE type = 'triage-inbox'"
        ).fetchone()["n"]
        assert n == 1


def test_layer3_not_invoked_from_pytest_triage(workspace, monkeypatch):
    import os

    assert os.environ.get("HUNT_SCREENER_WAKE") == "0"
    spawned = []

    def spawn(plan, env):
        spawned.append(plan)
        return type("P", (), {"pid": 1})()

    monkeypatch.setattr("hunt.agent.wake.default_spawn", spawn)
    calls = []
    _mock_ok(monkeypatch, calls, action="keep", codes=[])
    with Workspace.open(workspace) as ws:
        _add_pending(ws)
        job = enqueue(ws, job_type="triage-inbox")
        out = run_triage_inbox(ws, job)
        assert out["screener"]["started"] is False
        assert spawned == []


def test_triage_wakes_on_this_job_survivors(workspace, monkeypatch):
    calls = []
    _mock_ok(monkeypatch, calls, action="keep", codes=[])
    wakes = []

    def fake_wake(ws, *, survivors, **kwargs):
        wakes.append(survivors)
        return {
            "triggered": survivors > 0,
            "started": False,
            "reason": "wake_disabled",
            "survivors": survivors,
        }

    monkeypatch.setattr("hunt.core.triage.maybe_wake_screener", fake_wake)
    with Workspace.open(workspace) as ws:
        _add_pending(ws)
        job = enqueue(ws, job_type="triage-inbox")
        out = run_triage_inbox(ws, job)
        assert wakes == [out["kept"] + out["unsure"]]
        assert wakes == [1]


def test_second_triage_all_dismiss_no_harness(workspace, monkeypatch):
    calls = []
    _mock_ok(monkeypatch, calls, action="dismiss", codes=["wrong_role"])
    wakes = []

    def fake_wake(ws, *, survivors, **kwargs):
        wakes.append(survivors)
        return {
            "triggered": False,
            "started": False,
            "reason": "no_survivors",
            "survivors": survivors,
        }

    monkeypatch.setattr("hunt.core.triage.maybe_wake_screener", fake_wake)
    with Workspace.open(workspace) as ws:
        _add_pending(ws)
        job = enqueue(ws, job_type="triage-inbox")
        run_triage_inbox(ws, job)
        assert wakes[-1] == 0


def test_untriaged_pending_not_ingest_new(workspace):
    with Workspace.open(workspace) as ws:
        _add_pending(ws)
        assert count_untriaged_pending(ws) == 1


def test_inbox_list_filters_source_knockout_triage(workspace, monkeypatch):
    calls = []
    _mock_ok(monkeypatch, calls, action="keep", codes=[])
    with Workspace.open(workspace) as ws:
        a = _add_pending(
            ws,
            company="A",
            source_id="justjoin-sample",
            knockouts=["pay_below_floor"],
            external_id="a",
        )
        _add_pending(
            ws,
            company="B",
            source_id="remotive-eu",
            knockouts=["title"],
            external_id="b",
        )
        by_source = list_inbox(ws, source_id="justjoin-sample")
        assert [i.id for i in by_source] == [a.id]
        floor = list_inbox(ws, knockout="pay_below_floor|below_floor")
        assert [i.id for i in floor] == [a.id]
        job = enqueue(ws, job_type="triage-inbox")
        run_triage_inbox(ws, job)
        kept = list_inbox(ws, triage_action="keep")
        assert kept


def test_schema_v4_unique_index(workspace):
    with Workspace.open(workspace) as ws:
        row = ws.conn.execute(
            "SELECT MAX(version) AS v FROM schema_migrations"
        ).fetchone()
        assert int(row["v"]) >= 4
        idx = ws.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name='idx_jobs_triage_inbox_active'"
        ).fetchone()
        assert idx is not None
