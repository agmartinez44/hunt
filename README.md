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

## Agents

Read `AGENTS.md`, then `skills/hunt-operator/SKILL.md`. Draft facts as
`verified: false`; only the human flips them. Never invent metrics. Never
submit an application or send mail. Worker backends: `docs/workers.md`.

## License

MIT. No real person's career data ships in this repo or its history.
