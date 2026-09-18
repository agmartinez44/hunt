"""Knowledge YAML is the biography SoT. CLI / HTTP / MCP share hunt.core.facts."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
import yaml

from hunt.core.context import current_actor
from hunt.core.errors import ValidationError
from hunt.core.facts import (
    confirm_achievement,
    create_achievement,
    get_integrity,
    get_position,
    get_profile,
    update_achievement,
    update_integrity,
    update_position,
    update_profile,
    update_skills,
)
from hunt.core.workspace import Workspace
from hunt.http.app import create_app
from hunt.mcp import TOOLS, handle_rpc

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


def _mcp(name: str, arguments: dict | None, data_dir: Path):
    reply = handle_rpc(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments or {}},
        },
        data_dir=str(data_dir),
    )
    assert reply is not None
    result = reply["result"]
    payload = json.loads(result["content"][0]["text"])
    return result, payload


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


@pytest.fixture
def workspace(tmp_path: Path):
    data = tmp_path / "workspace"
    shutil.copytree(EXAMPLE, data, ignore=shutil.ignore_patterns("attachments"))
    env = {**os.environ, "HUNT_DATA": str(data), "PYTHONPATH": str(ROOT)}
    return data, env


def test_cli_http_mcp_list_and_update_jane_doe(workspace):
    data, env = workspace

    _, listed = _json(["positions", "list"], env)
    ids = [row["id"] for row in listed["positions"]]
    assert ids == ["widgetcorp", "examplecorp"]
    _, pos = _json(["positions", "get", "widgetcorp"], env)
    assert pos["position"]["employer"] == "WidgetCorp"
    assert pos["position"]["verified"] is True

    _, updated = _json(
        ["positions", "update", "widgetcorp", "--location", "Galway, Ireland"],
        env,
    )
    assert updated["position"]["location"] == "Galway, Ireland"
    assert updated["position"]["verified"] is False
    assert updated["position"]["id"] == "widgetcorp"
    yaml_pos = yaml.safe_load((data / "knowledge" / "positions.yaml").read_text())
    match = next(p for p in yaml_pos["positions"] if p["id"] == "widgetcorp")
    assert match["location"] == "Galway, Ireland"
    assert match["verified"] is False

    _, restored = _json(
        [
            "positions",
            "update",
            "widgetcorp",
            "--location",
            "Dublin, Ireland",
            "--verified",
            "--confirm",
        ],
        env,
    )
    assert restored["position"]["location"] == "Dublin, Ireland"
    assert restored["position"]["verified"] is True

    http = TestClient(create_app(data))
    achs = http.get("/api/achievements")
    assert achs.status_code == 200
    ach_ids = [row["id"] for row in achs.json()["achievements"]]
    assert "wc-gitops" in ach_ids
    assert "ec-monitoring" in ach_ids
    patched = http.patch(
        "/api/achievements/wc-slo",
        json={"evidence": "SLO docs in internal wiki; launch-deferral decision recorded in ADR-041."},
    )
    assert patched.status_code == 200, patched.text
    # HTTP actor is the human UI — verified stays true.
    assert patched.json()["achievement"]["verified"] is True
    assert patched.json()["achievement"]["id"] == "wc-slo"

    mcp_list, payload = _mcp("positions_list", {}, data)
    assert mcp_list["isError"] is False
    assert [row["id"] for row in payload["positions"]] == ["widgetcorp", "examplecorp"]
    _, ach_payload = _mcp("achievements_get", {"id": "wc-gitops"}, data)
    assert ach_payload["achievement"]["verified"] is True
    _, after_mcp = _mcp(
        "achievements_update",
        {"id": "wc-gitops", "evidence": "Delivery metrics dashboard, reviewed in Q3 2024 team retro."},
        data,
    )
    assert after_mcp["achievement"]["verified"] is False
    assert after_mcp["achievement"]["id"] == "wc-gitops"

    names = {t["name"] for t in TOOLS}
    assert "positions_list" in names
    assert "achievements_update" in names
    assert "achievements_confirm" not in names
    assert "integrity_get" in names
    assert "integrity_update" not in names


def test_agent_writes_are_drafts_and_cannot_weaken_integrity(workspace):
    data, env = workspace
    with Workspace.open(data) as ws:
        token = current_actor.set("mcp")
        try:
            created = create_achievement(
                ws,
                {
                    "id": "draft-sre",
                    "text": "Wrote a runbook for Friday deploys.",
                    "evidence": "Own work.",
                    "tags": ["incident"],
                    "verified": True,
                },
            )
            raise AssertionError("agent must not set verified true")
        except ValidationError as exc:
            assert "verified" in str(exc)
        created = create_achievement(
            ws,
            {
                "id": "draft-sre",
                "text": "Wrote a runbook for Friday deploys.",
                "evidence": "Own work.",
                "tags": ["incident"],
            },
        )
        assert created["verified"] is False
        try:
            confirm_achievement(ws, "draft-sre")
            raise AssertionError("agent must not confirm")
        except ValidationError as exc:
            assert "human" in str(exc)
        try:
            update_position(
                ws,
                "widgetcorp",
                {"scope_facts": ["CI/CD is GitHub Actions only; no Jenkins at WidgetCorp."]},
            )
            raise AssertionError("agent must not drop scope_facts")
        except ValidationError as exc:
            assert "scope_facts" in str(exc)
        try:
            update_integrity(ws, {"forbidden_phrases": []})
            raise AssertionError("agent must not write integrity")
        except ValidationError as exc:
            assert "integrity" in str(exc)
        try:
            update_skills(ws, {"forbidden_claims": []})
            raise AssertionError("agent must not drop forbidden_claims")
        except ValidationError as exc:
            assert "forbidden_claims" in str(exc)
        try:
            update_profile(ws, {"contact": {"email": "agent@example.org"}})
            raise AssertionError("agent must not change contact")
        except ValidationError as exc:
            assert "contact" in str(exc)
        try:
            update_achievement(ws, "wc-gitops", {"email": "leak@example.org"})
            raise AssertionError("contact must not land on achievements")
        except ValidationError as exc:
            assert "profile.contact" in str(exc)
        update_position(ws, "widgetcorp", {"location": "Cork, Ireland"})
        assert get_position(ws, "widgetcorp")["verified"] is False
        current_actor.reset(token)

        token = current_actor.set("cli")
        try:
            confirmed = confirm_achievement(ws, "draft-sre", confirm=True)
            assert confirmed["verified"] is True
            integ = get_integrity(ws)
            phrases = list(integ["forbidden_phrases"]) + ["never claim DBA title"]
            updated = update_integrity(
                ws, {"forbidden_phrases": phrases}, confirm=True
            )
            assert "never claim DBA title" in updated["forbidden_phrases"]
        finally:
            current_actor.reset(token)

    listed = _run(["--json", "achievements", "get", "draft-sre"], env)
    body = json.loads(listed.stdout)
    assert body["achievement"]["verified"] is True


def test_round_trip_render_verify_jane_doe(workspace):
    data, env = workspace
    _, before = _json(["achievements", "get", "wc-gitops"], env)
    original_text = before["achievement"]["text"]

    _, updated = _json(
        [
            "achievements",
            "update",
            "wc-gitops",
            "--set",
            json.dumps({"text": original_text}),
            "--verified",
            "--confirm",
        ],
        env,
    )
    assert updated["achievement"]["verified"] is True
    assert updated["achievement"]["id"] == "wc-gitops"

    pdf = data / "attachments" / "cv" / "Jane_Doe_CV.pdf"
    rendered = _cv(["render"], env)
    assert "ec-monitoring" in rendered.stderr
    text = _pdf_text(pdf)
    assert "GitOps delivery" in text
    assert "cut alert noise by roughly half" not in text

    _cv(["finalize", str(pdf)], env)
    verdict_run = _cv(["verify", str(pdf), "--json", "--expect", "Kubernetes"], env)
    verdict = json.loads(verdict_run.stdout)
    assert verdict["ok"], verdict.get("findings")

    # Agent edit of a verified bullet must drop it from the PDF until confirm.
    _, drafted = _mcp(
        "achievements_update",
        {"id": "wc-gitops", "text": original_text},
        data,
    )
    assert drafted["achievement"]["verified"] is False
    rendered_draft = _cv(["render"], env)
    assert "wc-gitops" in rendered_draft.stderr
    draft_text = _pdf_text(pdf)
    assert "GitOps delivery" not in draft_text

    _, confirmed = _json(["achievements", "confirm", "wc-gitops"], env)
    assert confirmed["achievement"]["verified"] is True
    _cv(["render"], env)
    assert "GitOps delivery" in _pdf_text(pdf)

    with Workspace.open(data) as ws:
        profile = get_profile(ws)
    assert "email" in (profile.get("contact") or {})
    assert profile["name"] == "Jane Doe"


def test_cli_create_draft_then_human_confirm(workspace):
    data, env = workspace
    _, created = _json(
        [
            "achievements",
            "create",
            "--id",
            "wc-docs",
            "--text",
            "Documented namespace upgrade runbooks used by the team.",
            "--tags",
            "kubernetes,platform",
            "--evidence",
            "Own work.",
        ],
        env,
    )
    assert created["achievement"]["verified"] is False
    denied = _run(
        ["--json", "achievements", "update", "wc-docs", "--verified"],
        env,
        check=False,
    )
    assert denied.returncode != 0
    err = json.loads(denied.stderr)
    assert "verified" in err["error"]

    _, confirmed = _json(["achievements", "confirm", "wc-docs"], env)
    assert confirmed["achievement"]["verified"] is True
    assert confirmed["achievement"]["tags"] == ["kubernetes", "platform"]

    http = TestClient(create_app(data))
    skills = http.get("/api/skills")
    assert skills.status_code == 200
    groups = skills.json()["skills"]["skill_groups"]
    assert groups[0]["name"] == "Platforms & delivery"
    projects = http.get("/api/projects")
    assert {p["id"] for p in projects.json()["projects"]} == {"home-lab"}
    integrity = http.get("/api/integrity")
    assert "forbidden_phrases" in integrity.json()["integrity"]
