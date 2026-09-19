# Hunt

Self-hosted, **agent-native** job-hunt platform. Track applications, store
artifacts, screen sources into an inbox, and build honesty-gated CVs. Humans
still submit applications to employers.

This repository is **code**. Your biography, mail, PDFs, and SQLite store
belong in a private workspace (`$HUNT_DATA`), never in git.

v1 is being built in layers. This tree ships **`hunt.core`** (SQLite board,
inbox, artifacts, events, jobs, quoted→derived pay), **`hunt <noun> <verb>`**,
**`hunt mcp`** (stdio tools mapped 1:1 onto those nouns), source adapters
(`http_json`, `imap_alerts`), and **`hunt.cv`**. HTTP/UI is a thin client of
the same domain layer.

## Clone + example workspace

```bash
git clone <this-repo> hunt
cd hunt
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"

# Copy the fictional example to a private data dir (do not edit the copy in git
# as if it were your life — start from a copy outside the source tree).
cp -a example-workspace /tmp/hunt-data-jane
export HUNT_DATA=/tmp/hunt-data-jane

.venv/bin/hunt --json applications create --company "Acme Radar" \
  --title-posted "Staff SRE" --comp-amount 50 --comp-currency USD --comp-unit hour
.venv/bin/hunt applications list
.venv/bin/hunt --json positions list
.venv/bin/hunt --json achievements get wc-gitops
.venv/bin/hunt --json sources run justjoin-sample --run
.venv/bin/hunt --json jobs enqueue --type screen-inbox --run
.venv/bin/hunt inbox list
.venv/bin/hunt serve            # FastAPI + UI on 127.0.0.1:8787
.venv/bin/hunt-cv render
.venv/bin/hunt-cv finalize "$HUNT_DATA/attachments/cv/Jane_Doe_CV.pdf"
.venv/bin/hunt-cv verify "$HUNT_DATA/attachments/cv/Jane_Doe_CV.pdf" --json --expect Kubernetes
```

`hunt mcp` is the stdio MCP server (same nouns as the CLI). Writes stay in
`$HUNT_DATA`. Adapters never submit employer forms.

Equivalent: `python -m hunt` and `python -m hunt.cv`. `--json` is the agent
contract; default human output is tables. Exit status is non-zero on error.

Expect a tagged PDF, `verify` exit code 0, and a stderr note that the
deliberately unverified achievement `ec-monitoring` was excluded.

## `$HUNT_DATA` vs this source tree

| Layer | What | Where |
|---|---|---|
| **Hunt (code)** | `hunt.core`, CLI, HTTP/UI, MCP, adapters, jobs, `hunt.cv` | This repo |
| **Workspace (data)** | `config.yaml`, `knowledge/*.yaml` (facts + integrity *values*), `store.sqlite`, attachments | **`$HUNT_DATA`** |

```
$HUNT_DATA/
  config.yaml
  secrets.env          # gitignored; never commit
  knowledge/           # facts + this user's integrity.yaml
  store.sqlite         # applications, inbox, events, artifacts
  attachments/
    cv/                # hunt.cv output
    applications/<id>/
```

`example-workspace/` in this repo is a **fictional** persona (Jane Doe at
WidgetCorp / ExampleCorp). Copy it. Do not replace it with a real person
and push.

Writes from Hunt go to `$HUNT_DATA` (or `--data`). They never land in the
source tree.

## Honesty gate (`hunt.cv`)

```
$HUNT_DATA/knowledge/*.yaml  →  render  →  finalize  →  verify  →  PDF
     facts + integrity            Jinja      strip         JSON verdict
     rules as data                WeasyPrint metadata
```

- **Facts live only in YAML.** Never retype career content into prompts.
- Every achievement has `verified: true/false` and an `evidence:` pointer.
  Unverified claims **do not render**.
- Integrity rules are workspace data (`knowledge/integrity.yaml`), not
  hardcoded employers in Hunt.
- The verifier is adversarial toward the agent: forbidden phrases, line
  traps, quantifiers without evidence, production skills with no supporting
  achievement.

Golden test (proves the unverified bullet stays out of the PDF):

```bash
.venv/bin/python -m pytest tests/test_golden.py
```

## Agents (packs, not a Hunt harness)

Hunt does **not** run a ReAct loop, session manager, or tool dispatcher.
You bring a harness you already use, or paste a token / local OpenAI-compat
URL and exec the documented default runner (OpenCode). The HTTP UI exposes
the same install / key / local-URL path at **Agent** → `/settings`.

```bash
# 1. Point $HUNT_DATA at a workspace copy, then install MCP + skills:
.venv/bin/hunt agent install --harness claude     # also: cursor, opencode, codex, openclaw, paperclip
.venv/bin/hunt agent install --harness cursor
.venv/bin/hunt agent install --harness opencode

# 2. Hosted default is SpaceXAI / xAI. Key in $HUNT_DATA/secrets.env, never in git:
#    XAI_API_KEY=...
#    agent.model.base_url: https://api.x.ai/v1
#    agent.model.model: grok-4.5
#
#    Or BYO local llama.cpp (api_key_env can be empty):
#    agent.model.base_url: http://127.0.0.1:8080/v1

.venv/bin/hunt agent doctor --json    # workspace + MCP + skill + GET /v1/models
.venv/bin/hunt agent run operator --dry-run
```

`hunt agent run <operator|screener>` detects a runner in this order:
**OpenCode → Claude Code / Codex**. If none are on `PATH`, it prints **one**
OpenCode install command (`curl -fsSL https://opencode.ai/install | bash`)
and exits. Paperclip is optional company OS, not a Hunt dependency.
After `source-poll`, knockouts run in-process; `new > 0` starts the
screener harness immediately, and `new == 0` skips the LLM.

Packs:

- `skills/hunt-operator` — board, facts, CV gate, never apply/send
- `skills/hunt-screener` — standardize, prefilter, **propose**; never promote unless asked

Read `AGENTS.md` and the pack `SKILL.md` before driving a workspace. Draft
facts as `verified: false`; only the human flips them. Never invent metrics.
Never submit an application or send mail. Worker backends: `docs/workers.md`.

## License

MIT. No real person's career data ships in this repo or its history.
