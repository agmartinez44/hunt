# AGENTS.md — operating rules for any AI agent working in this repo

Hunt generates CVs and (later) tracks a job search. It is **honesty-gated
by design**: the human owns facts, the agent owns assembly, and
deterministic tooling arbitrates.

This repository is **code**. Workspace data lives in `$HUNT_DATA`.

## Non-negotiable rules

1. **Never invent career content.** No metrics, employers, dates, tools,
   ownership, or years beyond what `$HUNT_DATA/knowledge/*.yaml` states.
   If a job description seems to need something the knowledge base lacks,
   surface the gap — do not fill it.
2. **Truth precedence.** `knowledge/integrity.yaml` + human-confirmed YAML
   override everything: older drafts, chat history, JD phrasing, your own
   prior outputs.
3. **`verified: false` blocks rendering.** Achievements flagged unverified
   are excluded by `hunt.cv` render and reported by verify. The fix is
   human confirmation, never flipping the flag yourself.
4. **Integrity rules are data.** Extend `knowledge/integrity.yaml` only when
   the human states a new boundary. Never weaken existing entries. Never
   hardcode employers or floors into Hunt source.
5. **Tailor emphasis, never facts.** Optional `emphasis.yaml` selects and
   orders verified bullets. Unknown keys fail the build.
6. **Run the full gate before delivering any PDF**:
   `hunt-cv render → hunt-cv finalize → hunt-cv verify --json`. Rasterize
   and visually inspect every page if you have vision. Text-pass alone is
   not verification.
7. **Anti-fingerprint conventions**: neutral output filename via
   `output_name` (never echo company/role/JD), no creation dates in PDF
   metadata (`finalize` handles it), headline must not parrot JD wording.
8. **Contact details live only in `knowledge/profile.yaml`.** Never copy
   them into notes, logs, commit messages, or chat summaries.
9. **Never apply, never send mail.** Presence of a PDF is not a submitted
   application. Hunt adapters must not submit employer forms.

## Workspace vs source tree

- Code changes: this git repo.
- Knowledge YAML, integrity *values*, rendered PDFs, SQLite: `$HUNT_DATA`
  (or `--data`). Never commit a real person's YAML, mail, or PDFs.
- `example-workspace/` is a fictional persona for tests and docs.

## Machine-readable workflow contract

- Emphasis schema: `ALLOWED_EMPHASIS_KEYS` in `hunt/cv/render.py`.
- Verify verdict JSON: `hunt-cv verify <pdf> --json`; on failure, fix the
  listed findings and re-run until `"ok": true`.
