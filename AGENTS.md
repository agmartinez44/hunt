# AGENTS.md

Rules for any agent working in this repo. A local `Agents.md` that is not
in git is not this file. On a case-insensitive disk the two names are the
same path. Read this file.

Hunt generates CVs and tracks a job search. It is **honesty-gated
by design**: the human owns facts, the agent owns assembly, and
deterministic tooling arbitrates.

This repository is **code**. Workspace data lives in `$HUNT_DATA`.

## Read first

| File | When |
|---|---|
| This file | Before any edit, CV, or fact change |
| [docs/onboarding.md](docs/onboarding.md) | New workspace |
| [docs/self-host.md](docs/self-host.md) | Where a setting lives |
| [CONTRIBUTING.md](CONTRIBUTING.md) | Style, a new module, or a suggestion |
| [docs/README.md](docs/README.md) | The rest |

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

## Code

The short version of [CONTRIBUTING.md](CONTRIBUTING.md):

- KISS. The smallest change that does the job.
- Lean. No wrapper, config key, or dependency for one caller.
- Comments are brief or absent. Explain a constraint the code cannot show. Do not narrate.
- Docs are brief. One place for each fact. Link to it. Do not restate it.
- No filler: no status essays, and no docs that paraphrase the source.

## Set up a workspace for a new user

Follow [docs/onboarding.md](docs/onboarding.md). The module and config map
is [docs/self-host.md](docs/self-host.md).

- Copy `example-workspace/` to a private directory. Export `HUNT_DATA`.
- Replace `knowledge/*.yaml` with facts the human gave you. Drafts stay
  `verified: false`. Do not reuse Jane Doe's employers, dates, or metrics.
- Leave `mail-alerts` disabled. Do not add an `imap_alerts` source.
  LinkedIn-on-the-CV (`profile.yaml` `contact.linkedin`) is a URL, not mail.
- Leave `bind` at `127.0.0.1`. Do not publish a hostname.
- Install a harness only if the human named one (`hunt agent install`).
- Run `hunt agent doctor --json`. Report failed checks. Never print secrets.
- Stop for human confirmation. Do not apply and do not send mail.

## Harness

Hunt ships operator and screener packs under `skills/`. Install them into
a tool you already run; Hunt does not implement a tool loop.

```
hunt agent install --harness opencode   # also claude, cursor, codex, openclaw, paperclip
hunt agent doctor --json
hunt agent run operator
```

`run` execs OpenCode, then Claude Code / Codex. Paperclip is optional.

## Machine-readable workflow contract

- Emphasis schema: `ALLOWED_EMPHASIS_KEYS` in `hunt/cv/render.py`.
- Verify verdict JSON: `hunt-cv verify <pdf> --json`; on failure, fix the
  listed findings and re-run until `"ok": true`.
