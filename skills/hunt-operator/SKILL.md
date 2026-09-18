---
name: hunt-operator
description: Operate Hunt (applications, inbox, jobs, honesty-gated CVs) via CLI or MCP. Use when tracking a job search or generating CVs in a Hunt workspace.
---

# Hunt Operator

The human owns facts. You own assembly and bookkeeping. Deterministic
tooling (`hunt.core`, `hunt.cv`) arbitrates. Workspace data is `$HUNT_DATA`
(or `--data`). Never write a real person's YAML, mail, or PDFs into the
Hunt source tree.

Surfaces call the same domain layer: `hunt <noun> <verb> --json`,
`hunt mcp` (stdio), later HTTP. Prefer `--json`. Exit non-zero is an error.

## Product loop

```
source-poll → listings → screen-inbox → inbox
    → promote | dismiss   (explicit; never inferred)
    → application + attachments
    → tailor-cv / cv render
    → human submits outside Hunt
```

Promote is the **only** listing → application path, including MCP.
`applications create` is for a manual row, not for converting a listing.

## Hard rules

- Never invent metrics, employers, dates, tools, or ownership.
- `verified: false` does not render. Do not flip the flag.
- Tailor emphasis, never facts. Unknown emphasis keys fail the build.
- Neutral filenames (`output_name`). Do not echo company/role/JD in the headline.
- Contact details stay in `knowledge/profile.yaml` only.
- **Never apply, never send mail.** Adapters are GET / IMAP PEEK only.
  A PDF in attachments is not a submission.
- Draft YAML ≠ verified YAML. New facts go in as `verified: false`.

## MCP / CLI nouns

| Tool | Notes |
|---|---|
| `applications_list/get/create/update` | Board CRUD |
| `inbox_list/promote/dismiss` | Promote is explicit |
| `jobs_enqueue/status/run` | Types: `source-poll`, `screen-inbox`, `tailor-cv` |
| `cv_render` | Writes `$HUNT_DATA/attachments`; then finalize + verify |
| `sources_list/run` | Run enqueues `source-poll`; does not promote |
| `pay_estimate` | Country + engagement → net / month EUR. Estimate, not tax advice. |
| `profile_get/update` | Knowledge profile. Contact lives only here; agents cannot change it. |
| `positions_list/get/create/update` | YAML SoT. Agent writes are `verified: false`. Do not weaken `scope_facts`. |
| `achievements_list/get/create/update` | YAML SoT. Never flip `verified`. Human confirms via UI or `hunt achievements confirm`. |
| `projects_list/get/create/update` | Personal projects; keep scope honest. |
| `skills_get/update` | Agents cannot remove `forbidden_claims`. |
| `integrity_get` | Read-only for agents. Do not weaken rules. |

`jobs_enqueue` with `run: true` (CLI `--run`) claims and executes immediately.
`worker.backend` `none` means you must run/drain jobs; `cli` is the in-process
worker; Hermes is optional (see `docs/workers.md`).

## CV gate (never skip)

1. Read `AGENTS.md` and any `emphasis.yaml`.
2. `hunt cv render --application ID` **or** enqueue `tailor-cv` with `--run`.
3. `hunt-cv finalize <pdf>` — strips producer strings/dates.
4. `hunt-cv verify <pdf> --json --expect "<keywords from JD>"`.
5. Rasterize (`pdftoppm -png -r 100`) and visually inspect every page.
6. Only then deliver. Text-pass alone is not verification.

Stderr lists excluded unverified achievements — report them; do not fix by
rewriting text to dodge the rule.

## Inbox screening

Knockouts are workspace data (`config.yaml` `knockouts`). Missing pay is
`pay_unknown`, never invented. You may promote or dismiss when the user
asked. You may not infer hire/reject from mail.
