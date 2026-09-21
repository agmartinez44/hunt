# Set up a Hunt workspace

One person. One private directory. Localhost only.

The human owns the facts. You may copy files and draft `verified: false`.
You may not invent employers, dates, tools, metrics, or years, and you may
not flip `verified` to true.

Mailbox alerts stay off. `contact.linkedin` in the profile is a CV URL,
not mail.

## 1. Install

From a clone of this repository:

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
```

`hunt-cv` needs the OS Pango libraries (`import weasyprint`). The board
and CLI do not.

## 2. Copy the example out of git

`example-workspace/` is Jane Doe. Copy it. Do not edit it in the clone.

```bash
cp -a example-workspace "$HOME/hunt-data"
export HUNT_DATA="$HOME/hunt-data"
```

Put that export in the shell profile you use. Commands read `$HUNT_DATA`,
or `--data`.

## 3. Replace the fictional files

Edit only `$HUNT_DATA/knowledge/`:

| File | Replace with |
|---|---|
| `profile.yaml` | Name, headline, location, languages, education. Contact lives only here |
| `positions.yaml` | Real jobs and `scope_facts`. New rows stay `verified: false` |
| `achievements.yaml` | One claim per bullet, plus `evidence`. New rows stay `verified: false` |
| `skills.yaml` | Levels you can defend. `production` needs a verified achievement |
| `projects.yaml` | Real projects, or an empty list |
| `integrity.yaml` | This person's boundaries. Do not keep the example rules, and do not weaken a rule they already stated |

Field order: [kb-guide.md](kb-guide.md). The human confirms a row in the UI
or with `hunt achievements confirm` / `hunt positions confirm`.

## 4. Settings

`$HUNT_DATA/config.yaml` is the only settings file. Each block is in
[self-host.md](self-host.md).

On day one, change `comp_floor`, `display_currency`, `fx`, `tax_homes`,
`agent.model`, and `sources` when the example is wrong. Delete `path` on a
sample source to use that profile's live GET. Do not invent a profile name.

Leave `bind` at `127.0.0.1`, `worker.backend: none`,
`agent.triage.enabled: false`, `knockouts` empty, and `mail-alerts` with
`enabled: false`.

## 5. Key, then check

```bash
.venv/bin/hunt agent secret set --env XAI_API_KEY
.venv/bin/hunt agent install --harness opencode
.venv/bin/hunt agent doctor --json
```

The key is written to `$HUNT_DATA/secrets.env`. It is not printed.
`--harness` is `opencode`, `claude`, `cursor`, `codex`, `openclaw`, or
`paperclip`. Install a tool they already run. If they named none, install
nothing and give them the command.

`doctor` must report the workspace as readable. A failed model check is the
key or `agent.model.base_url`. It is not a reason to turn mail on.

## 6. Run

```bash
.venv/bin/hunt serve
```

The UI is `http://127.0.0.1:8787`. No public name, no tunnel, no extra users.

Smoke-test Jane Doe in a separate directory. Do not use `$HUNT_DATA` for
that after you replace the YAML.

## Agent checklist

Read [AGENTS.md](../AGENTS.md) before you edit. Code changes follow
[CONTRIBUTING.md](../CONTRIBUTING.md).

1. Copy `example-workspace/` outside the repo. Export `HUNT_DATA`.
2. Write only facts the human gave you, as `verified: false`. Leave a missing field missing.
3. Do not reuse Jane Doe's employers, dates, or metrics.
4. Do not copy contact details into chat, issues, commits, or logs.
5. Do not enable mail. Do not change `bind`.
6. Install a harness only if they named one.
7. Run `hunt agent doctor --json`. Report failed check ids. Do not print secrets.
8. Stop. The human confirms facts and submits applications.
