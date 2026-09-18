---
name: hunt-operator
description: Operate Hunt's honesty-gated CV pipeline (render/finalize/verify, knowledge integrity). Use when generating or tailoring CVs in a Hunt workspace.
---

# Hunt Operator

Deterministic, honesty-gated CV generation. The human owns facts; you own
assembly; `hunt-cv verify` arbitrates. Full context: repo `AGENTS.md`,
`docs/kb-guide.md`, `docs/tailoring.md`.

Workspace data is `$HUNT_DATA` (or `--data`). Never write a real person's
YAML or PDFs into the Hunt source tree.

## The loop (never skip a step)

1. Read `AGENTS.md` and any target `emphasis.yaml`.
2. Render: `HUNT_DATA=<workspace> hunt-cv render [--emphasis <file>]`
   - stderr lists excluded unverified achievements — report them to the
     human; do not flip flags yourself.
3. Finalize: `hunt-cv finalize <pdf>` — strips producer strings/dates.
4. Verify: `hunt-cv verify <pdf> --json --expect "<keywords from JD>"`.
5. Rasterize (`pdftoppm -png -r 100`) and VISUALLY inspect every page:
   stub final pages (<40% full), overflow, overlap, cut-offs.
6. Only then deliver. Text-pass alone is not verification.

## KB changes

- New fact → draft YAML under `$HUNT_DATA/knowledge/` with
  `verified: false` → human confirms.
- Suspect claims → run `hunt-cv verify --lint --json`; findings go back
  to the human, never silently fixed by rewriting text to dodge the rule.

## Hard rules

- Never invent metrics, tools, ownership, or dates.
- Tailor emphasis, never facts.
- Neutral filenames; no JD phrasing echoes in headline/profile.
- Contact details stay in `profile.yaml` only.
- Never submit employer forms or send mail. Hunt does not apply.
