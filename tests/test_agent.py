"""Agent packs + ``hunt agent install/doctor/run``. Hunt does not vendor a loop."""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
import yaml

from hunt.agent.config import OPENCODE_INSTALL_COMMAND, models_url
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
        payload = json.loads(r.stdout) if r.stdout.strip().startswith("{") else None
        if payload is None and r.stderr.strip().startswith("{"):
            payload = json.loads(r.stderr)
        return r, payload
    return r, json.loads(r.stdout)


@pytest.fixture
def workspace(tmp_path: Path):
    data = tmp_path / "workspace"
    shutil.copytree(EXAMPLE, data, ignore=shutil.ignore_patterns("attachments"))
    env = {**os.environ, "HUNT_DATA": str(data), "PYTHONPATH": str(ROOT)}
    return data, env


def _set_model(data: Path, base_url: str, model: str = "grok-4.5", api_key_env: str = "") -> None:
    path = data / "config.yaml"
    cfg = yaml.safe_load(path.read_text(encoding="utf-8"))
    cfg["agent"] = {
        "harness": "auto",
        "model": {
            "base_url": base_url,
            "api_key_env": api_key_env,
            "model": model,
        },
    }
    path.write_text(yaml.safe_dump(cfg, sort_keys=False), encoding="utf-8")


def _serve_models(ids: list[str]):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            if self.path.rstrip("/") not in {"/v1/models", "/models"}:
                self.send_response(404)
                self.end_headers()
                return
            body = json.dumps(
                {"object": "list", "data": [{"id": name, "object": "model"} for name in ids]}
            ).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format, *args):  # noqa: A003
            return

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    host, port = sock.getsockname()
    sock.close()
    server = ThreadingHTTPServer((host, port), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, f"http://{host}:{port}/v1"


def test_example_workspace_ships_xai_default():
    cfg = yaml.safe_load((EXAMPLE / "config.yaml").read_text(encoding="utf-8"))
    agent = cfg["agent"]
    assert agent["harness"] == "auto"
    assert agent["model"]["base_url"] == "https://api.x.ai/v1"
    assert agent["model"]["api_key_env"] == "XAI_API_KEY"
    assert agent["model"]["model"] == "grok-4.5"
    raw = (EXAMPLE / "config.yaml").read_text(encoding="utf-8")
    assert "XAI_API_KEY=" not in raw
    assert "xai-" not in raw


def test_models_url_appends_v1_models():
    assert models_url("https://api.x.ai/v1") == "https://api.x.ai/v1/models"
    assert models_url("http://127.0.0.1:8080/v1") == "http://127.0.0.1:8080/v1/models"
    assert models_url("http://127.0.0.1:8080") == "http://127.0.0.1:8080/v1/models"


def test_install_claude_cursor_opencode_write_mcp_and_skills(workspace, tmp_path: Path):
    data, env = workspace
    root = tmp_path / "project"
    root.mkdir()
    home = tmp_path / "home"
    home.mkdir()

    for harness in ("claude", "cursor", "opencode"):
        _, payload = _json(
            [
                "agent",
                "install",
                "--harness",
                harness,
                "--root",
                str(root),
                "--home",
                str(home),
            ],
            env,
        )
        assert payload["harness"] == harness
        assert payload["roles"] == ["operator", "screener"]
        assert payload["never_apply"] is True
        assert payload["mcp"]["args"][-1] == "mcp"
        assert payload["mcp"]["env"]["HUNT_DATA"] == str(data.resolve())

    claude_mcp = json.loads((root / ".mcp.json").read_text(encoding="utf-8"))
    assert claude_mcp["mcpServers"]["hunt"]["env"]["HUNT_DATA"] == str(data.resolve())
    assert (root / ".claude" / "skills" / "hunt-operator" / "SKILL.md").is_file()
    assert (root / ".claude" / "skills" / "hunt-screener" / "SKILL.md").is_file()
    screener = (root / ".claude" / "skills" / "hunt-screener" / "SKILL.md").read_text(
        encoding="utf-8"
    )
    assert "never promote unless the user asked" in screener.lower()
    assert "never apply" in screener.lower()

    cursor_mcp = json.loads((root / ".cursor" / "mcp.json").read_text(encoding="utf-8"))
    assert "hunt" in cursor_mcp["mcpServers"]
    assert (root / ".cursor" / "skills" / "hunt-operator" / "SKILL.md").is_file()

    opencode = json.loads((root / "opencode.json").read_text(encoding="utf-8"))
    assert opencode["mcp"]["hunt"]["type"] == "local"
    assert opencode["mcp"]["hunt"]["command"][-1] == "mcp"
    assert (root / ".opencode" / "skills" / "hunt-screener" / "SKILL.md").is_file()


def test_install_codex_openclaw_paperclip(workspace, tmp_path: Path):
    data, env = workspace
    root = tmp_path / "project"
    root.mkdir()
    home = tmp_path / "home"
    home.mkdir()

    _, codex = _json(
        ["agent", "install", "--harness", "codex", "--root", str(root), "--home", str(home)],
        env,
    )
    config = (home / ".codex" / "config.toml").read_text(encoding="utf-8")
    assert "[mcp_servers.hunt]" in config
    assert str(data.resolve()) in config
    assert (home / ".codex" / "skills" / "hunt-operator" / "SKILL.md").is_file()
    assert "never_apply" in json.dumps(codex)

    _, claw = _json(
        [
            "agent",
            "install",
            "--harness",
            "openclaw",
            "--root",
            str(root),
            "--home",
            str(home),
        ],
        env,
    )
    claw_cfg = json.loads((root / ".openclaw" / "openclaw.json").read_text(encoding="utf-8"))
    assert claw_cfg["mcp"]["hunt"]["env"]["HUNT_DATA"] == str(data.resolve())
    assert (root / ".openclaw" / "skills" / "hunt-screener" / "SKILL.md").is_file()
    assert claw["never_apply"] is True

    _, paper = _json(
        [
            "agent",
            "install",
            "--harness",
            "paperclip",
            "--root",
            str(root),
            "--home",
            str(home),
        ],
        env,
    )
    manifest = json.loads(
        (root / ".paperclip" / "hunt-import.json").read_text(encoding="utf-8")
    )
    assert manifest["paperclip_required"] is False
    assert (root / ".paperclip" / "skills" / "hunt-operator" / "SKILL.md").is_file()
    assert paper["note"].lower().startswith("wrote importable")


def test_doctor_green_against_example_workspace_copy(workspace, tmp_path: Path):
    data, env = workspace
    root = tmp_path / "project"
    root.mkdir()
    home = tmp_path / "home"
    home.mkdir()
    server, base_url = _serve_models(["grok-4.5"])
    try:
        _set_model(data, base_url, model="grok-4.5", api_key_env="")
        _json(
            [
                "agent",
                "install",
                "--harness",
                "claude",
                "--root",
                str(root),
                "--home",
                str(home),
            ],
            env,
        )
        run, report = _json(
            ["agent", "doctor", "--root", str(root), "--home", str(home)],
            env,
        )
        assert run.returncode == 0
        assert report["ok"] is True
        by_id = {row["id"]: row for row in report["checks"]}
        assert by_id["workspace"]["ok"] is True
        assert by_id["mcp"]["ok"] is True
        assert by_id["skill"]["ok"] is True
        assert by_id["models"]["ok"] is True
        assert "grok-4.5" in by_id["models"]["models"]
        assert report["warnings"] == []
    finally:
        server.shutdown()


def test_doctor_warns_on_small_local_gemma(workspace, tmp_path: Path):
    data, env = workspace
    root = tmp_path / "project"
    root.mkdir()
    home = tmp_path / "home"
    home.mkdir()
    server, base_url = _serve_models(["gemma-3-e4b-it"])
    try:
        _set_model(data, base_url, model="gemma-3-e4b-it", api_key_env="")
        _json(
            [
                "agent",
                "install",
                "--harness",
                "opencode",
                "--root",
                str(root),
                "--home",
                str(home),
            ],
            env,
        )
        _, report = _json(
            ["agent", "doctor", "--root", str(root), "--home", str(home)],
            env,
        )
        assert report["ok"] is True
        assert any("Grok or Claude" in w for w in report["warnings"])
        assert any("Gemma E4B" in w for w in report["warnings"])
    finally:
        server.shutdown()


def test_run_dry_run_detects_opencode(workspace, tmp_path: Path):
    data, env = workspace
    bindir = tmp_path / "bin"
    bindir.mkdir()
    fake = bindir / "opencode"
    fake.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    fake.chmod(0o755)
    env = {**env, "PATH": str(bindir)}
    _, plan = _json(["agent", "run", "operator", "--dry-run"], env)
    assert plan["ok"] is True
    assert plan["harness"] == "opencode"
    assert plan["command"][0].endswith("opencode")
    assert plan["never_apply"] is True
    assert "HUNT_DATA" in plan["env_keys"]


def test_run_prints_one_opencode_install_when_missing(workspace, tmp_path: Path):
    data, env = workspace
    empty = tmp_path / "empty-bin"
    empty.mkdir()
    env = {**env, "PATH": str(empty)}
    run, payload = _json(["agent", "run", "screener", "--dry-run"], env, check=False)
    assert run.returncode != 0
    text = (run.stdout or "") + (run.stderr or "")
    assert OPENCODE_INSTALL_COMMAND in text
    assert "claude.ai" not in text.lower()
    assert text.count("curl -fsSL") == 1
    if payload:
        assert payload.get("install") == OPENCODE_INSTALL_COMMAND or "opencode.ai/install" in json.dumps(
            payload
        )


def test_no_hunt_agent_loop_or_tool_dispatcher_module():
    hits: list[str] = []
    forbidden_names = ("agent_loop", "tool_dispatcher", "react_loop", "session_manager")
    forbidden_text = (
        "react loop",
        "tool dispatcher",
        "session manager",
        "custom context window",
    )
    for path in (ROOT / "hunt").rglob("*.py"):
        rel = str(path.relative_to(ROOT))
        stem = path.stem.lower()
        if any(token in stem for token in forbidden_names) or any(
            token in path.name.lower() for token in ("dispatcher", "react")
        ):
            hits.append(rel)
            continue
        body = path.read_text(encoding="utf-8").lower()
        if any(token in body for token in forbidden_text):
            hits.append(rel)
    assert hits == []

    grep = subprocess.run(
        [
            "git",
            "grep",
            "-n",
            "-i",
            "-E",
            r"agent[-_]loop|tool[-_]dispatcher|session manager|ReAct loop",
            "--",
            "hunt",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert grep.returncode in (0, 1)
    assert grep.stdout.strip() == ""


def test_mcp_agent_tools_install_without_exec(workspace, tmp_path: Path):
    data, _env = workspace
    names = {t["name"] for t in TOOLS}
    assert {"agent_install", "agent_doctor", "agent_run"} <= names
    root = tmp_path / "project"
    root.mkdir()
    home = tmp_path / "home"
    home.mkdir()
    reply = handle_rpc(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "agent_install",
                "arguments": {
                    "harness": "claude",
                    "root": str(root),
                    "home": str(home),
                },
            },
        },
        data_dir=str(data),
    )
    payload = json.loads(reply["result"]["content"][0]["text"])
    assert payload["harness"] == "claude"
    assert (root / ".mcp.json").is_file()

    run_reply = handle_rpc(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": "agent_run",
                "arguments": {"role": "operator", "root": str(root)},
            },
        },
        data_dir=str(data),
    )
    run_payload = json.loads(run_reply["result"]["content"][0]["text"])
    assert run_payload["never_apply"] is True
    assert "command" in run_payload or "install" in run_payload
