"""Golden test: render the example workspace, finalize, verify.

Proves unverified claims do not render. Writes only into a temp copy of
example-workspace — never into the source tree.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EXAMPLE = ROOT / "example-workspace"
UNVERIFIED_ID = "ec-monitoring"
UNVERIFIED_PHRASE = "cut alert noise by roughly half"
VERIFIED_PHRASE = "GitOps delivery"


def _run(args, env, check=True):
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


def test_unverified_claims_do_not_render(tmp_path: Path):
    data = tmp_path / "workspace"
    shutil.copytree(EXAMPLE, data, ignore=shutil.ignore_patterns("attachments"))
    env = {**os.environ, "HUNT_DATA": str(data), "PYTHONPATH": str(ROOT)}
    pdf = data / "attachments" / "cv" / "Jane_Doe_CV.pdf"

    rendered = _run(["render"], env)
    assert UNVERIFIED_ID in rendered.stderr, (
        "expected unverified-achievement exclusion notice on stderr"
    )
    assert pdf.exists(), "PDF not produced under $HUNT_DATA/attachments/cv"

    text = _pdf_text(pdf)
    assert UNVERIFIED_PHRASE not in text.lower() and UNVERIFIED_PHRASE not in text, (
        "unverified claim leaked into the rendered PDF"
    )
    assert VERIFIED_PHRASE in text, "verified claim missing from PDF"
    assert "ec-monitoring" not in text

    _run(["finalize", str(pdf)], env)

    verdict_run = _run(
        ["verify", str(pdf), "--json", "--expect", "Kubernetes"], env
    )
    verdict = json.loads(verdict_run.stdout)
    assert verdict["ok"], f"verify findings: {verdict['findings']}"
    assert verdict["pages"] >= 1

    lint_run = _run(["verify", "--lint", "--json"], env, check=False)
    lint_v = json.loads(lint_run.stdout)
    rules = sorted(f["rule"] for f in lint_v["findings"])
    assert rules == ["unverified_achievement"], f"lint drift: {rules}"
    assert lint_v["findings"][0]["id"] == UNVERIFIED_ID

    info = subprocess.run(
        ["pdfinfo", str(pdf)], capture_output=True, text=True, check=True
    ).stdout
    assert "Tagged:          yes" in info, "PDF not tagged"
    assert "CreationDate" not in info, "metadata date leaked"

    # Source tree must stay clean of rendered output.
    assert not (ROOT / "example-workspace" / "attachments" / "cv").exists() or not any(
        (ROOT / "example-workspace" / "attachments" / "cv").glob("*.pdf")
    )


def test_workspace_required(tmp_path: Path):
    env = {**os.environ, "PYTHONPATH": str(ROOT)}
    env.pop("HUNT_DATA", None)
    r = _run(["render"], env, check=False)
    assert r.returncode != 0
    assert "HUNT_DATA" in r.stderr or "workspace" in r.stderr.lower()


if __name__ == "__main__":
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        test_unverified_claims_do_not_render(Path(td))
        test_workspace_required(Path(td) / "empty")
    print(
        "GOLDEN TEST PASS — unverified claims excluded, verify gate, lint, "
        "tagging, metadata all behave as specified."
    )
