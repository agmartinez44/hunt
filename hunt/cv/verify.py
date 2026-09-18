"""Deterministic verification gate for generated CVs.

Exit codes: 0 = pass, 1 = findings, 2 = usage error.
With JSON output, emits a machine-readable verdict so an agent can
self-correct in a loop.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import yaml

from hunt.cv.workspace import knowledge_dir, resolve_data_dir


def pdf_text(pdf: Path) -> str:
    return subprocess.run(
        ["pdftotext", str(pdf), "-"],
        capture_output=True,
        text=True,
        check=False,
    ).stdout


def _load_integrity(kb: Path) -> dict:
    p = kb / "integrity.yaml"
    return yaml.safe_load(p.read_text()) if p.exists() else {}


def verify_pdf(
    pdf: str | Path,
    expects: list[str] | None = None,
    *,
    data_dir: str | Path | None = None,
) -> dict:
    findings = []
    pdf = Path(pdf)
    kb = knowledge_dir(resolve_data_dir(data_dir))
    integ = _load_integrity(kb)
    info = subprocess.run(
        ["pdfinfo", str(pdf)], capture_output=True, text=True, check=False
    ).stdout
    pages = int(
        next(l.split()[-1] for l in info.splitlines() if l.startswith("Pages:"))
    )
    text = pdf_text(pdf)
    low = text.lower()

    for phrase in expects or []:
        if phrase.lower() not in low:
            findings.append(
                {"rule": "expected_phrase", "detail": f"missing: {phrase}"}
            )
    for claim in integ.get("forbidden_phrases", []):
        if claim.lower() in low:
            findings.append({"rule": "forbidden_phrase", "detail": claim})
    for line in text.splitlines():
        ll = line.lower()
        for trap in integ.get("line_traps", []):
            if (
                trap["term"].lower() in ll
                and trap["never_with"].lower() in ll
            ):
                findings.append(
                    {
                        "rule": "line_trap",
                        "detail": (
                            f"'{trap['term']}' co-occurs with "
                            f"'{trap['never_with']}': {line.strip()[:90]}"
                        ),
                    }
                )

    return {
        "ok": not findings,
        "pdf": str(pdf),
        "pages": pages,
        "findings": findings,
    }


def lint_knowledge(*, data_dir: str | Path | None = None) -> dict:
    findings = []
    kb = knowledge_dir(resolve_data_dir(data_dir))
    integ = _load_integrity(kb)
    quant = integ.get("quantifier_terms", ["%"])
    ach = yaml.safe_load((kb / "achievements.yaml").read_text())["achievements"]
    skills = yaml.safe_load((kb / "skills.yaml").read_text())

    for a in ach:
        ev = (a.get("evidence") or "").strip()
        weak_evidence = ev.lower() in ("", "own work.")
        if (
            any(q in a["text"] for q in quant)
            and weak_evidence
            and a.get("verified")
        ):
            findings.append(
                {
                    "rule": "quantifier_needs_evidence",
                    "id": a["id"],
                    "detail": f"quantified but evidence='{ev}'",
                }
            )
        if not a.get("verified", False):
            findings.append(
                {
                    "rule": "unverified_achievement",
                    "id": a["id"],
                    "detail": "human confirmation required before render",
                }
            )
        if not ev:
            findings.append({"rule": "missing_evidence", "id": a["id"]})

    verified_blob = " ".join(
        a["text"] for a in ach if a.get("verified")
    ).lower()
    for grp in skills["skill_groups"]:
        for s in grp["skills"]:
            if s.get("level") != "production":
                continue
            nm = s["name"].lower().split(" (")[0]
            tokens = [t for t in re.split(r"[\s/]+", nm) if len(t) >= 3]
            matched = all(
                t in verified_blob
                or (t.endswith("s") and t[:-1] in verified_blob)
                for t in tokens
            )
            if tokens and not matched:
                findings.append(
                    {
                        "rule": "production_skill_unbacked",
                        "skill": s["name"],
                        "detail": "no verified achievement mentions it",
                    }
                )

    return {"ok": not findings, "kb": str(kb), "findings": findings}


def emit_verdict(result: dict, as_json: bool) -> int:
    if as_json:
        print(json.dumps(result, indent=2))
    else:
        print(f"pages={result.get('pages', 'n/a')}")
        for f in result["findings"]:
            print(
                " -",
                f.get("rule"),
                "::",
                f.get("detail") or f.get("skill") or f.get("id"),
            )
        print("PASS" if result["ok"] else "FAIL")
    return 0 if result["ok"] else 1


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    as_json = "--json" in args
    args = [a for a in args if a != "--json"]
    data = None
    if "--data" in args:
        i = args.index("--data")
        if i + 1 >= len(args):
            print("verify: --data requires a path", file=sys.stderr)
            return 2
        data = args[i + 1]
        del args[i : i + 2]
    if "--lint" in args:
        return emit_verdict(lint_knowledge(data_dir=data), as_json)
    if not args:
        print(__doc__, file=sys.stderr)
        return 2
    pdf = Path(args[0])
    expects = (
        args[args.index("--expect") + 1 :] if "--expect" in args else []
    )
    return emit_verdict(
        verify_pdf(pdf, expects, data_dir=data), as_json
    )


if __name__ == "__main__":
    sys.exit(main())
