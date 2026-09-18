"""``python -m hunt.cv`` / ``hunt-cv`` — render, finalize, verify, intake."""

from __future__ import annotations

import argparse
import sys

from hunt.cv.finalize import finalize_pdf
from hunt.cv.intake import print_intake
from hunt.cv.render import render_cv
from hunt.cv.verify import emit_verdict, lint_knowledge, verify_pdf


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = argparse.ArgumentParser(
        prog="hunt-cv",
        description="Honesty-gated CV pipeline (render → finalize → verify).",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_render = sub.add_parser("render", help="Render a tagged PDF into $HUNT_DATA")
    p_render.add_argument("--data", help="Workspace directory (else $HUNT_DATA)")
    p_render.add_argument(
        "--emphasis",
        help="Optional emphasis.yaml (tailor selection, never facts)",
    )
    p_render.add_argument(
        "--out",
        help="Output directory (default: $HUNT_DATA/attachments/cv)",
    )

    p_fin = sub.add_parser("finalize", help="Strip producer/dates from a PDF")
    p_fin.add_argument("pdf")
    p_fin.add_argument("author", nargs="?")
    p_fin.add_argument("--data", help="Workspace directory (else $HUNT_DATA)")

    p_ver = sub.add_parser("verify", help="Deterministic PDF / KB gate")
    p_ver.add_argument("pdf", nargs="?")
    p_ver.add_argument("--lint", action="store_true")
    p_ver.add_argument("--json", action="store_true")
    p_ver.add_argument("--expect", nargs="*", default=[])
    p_ver.add_argument("--data", help="Workspace directory (else $HUNT_DATA)")

    sub.add_parser("intake", help="Print the human+agent KB intake questions")

    args = parser.parse_args(argv)

    if args.cmd == "render":
        render_cv(
            data_dir=args.data,
            emphasis_path=args.emphasis,
            output_dir=args.out,
        )
        return 0
    if args.cmd == "finalize":
        finalize_pdf(args.pdf, args.author, data_dir=args.data)
        return 0
    if args.cmd == "verify":
        if args.lint:
            return emit_verdict(lint_knowledge(data_dir=args.data), args.json)
        if not args.pdf:
            print("verify: pass a PDF path, or use --lint", file=sys.stderr)
            return 2
        return emit_verdict(
            verify_pdf(args.pdf, args.expect, data_dir=args.data),
            args.json,
        )
    if args.cmd == "intake":
        print_intake()
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
