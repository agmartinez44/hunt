# Self-host Hunt

One person. One `$HUNT_DATA` directory. One process on `127.0.0.1`.
Each friend clones the repo and keeps their own directory. No accounts.
Mail stays off.

Setup steps: [onboarding.md](onboarding.md). Code changes:
[CONTRIBUTING.md](../CONTRIBUTING.md).

## Modules

Code is this repository. Your life is not.

| Module | Path | Role |
|---|---|---|
| `hunt.core` | `hunt/core/` | Workspace, SQLite, applications, inbox, jobs, pay, facts, screening |
| CLI | `hunt/cli.py` | `hunt <noun> <verb>`. Same actions as MCP |
| MCP | `hunt/mcp.py` | stdio tools. `hunt mcp` |
| `hunt.cv` | `hunt/cv/` | Render, finalize, verify. `hunt-cv` |
| `hunt.http` | `hunt/http/` | `hunt serve`. The UI calls the same core |
| `hunt.agent` | `hunt/agent/` | Copy skill packs into a harness you already run. Doctor. Exec that harness |
| Adapters | `hunt/adapters/` | Fetch listings. Never submit a form and never send mail |
| Packs | `skills/hunt-operator/`, `skills/hunt-screener/` | Instructions installed into the harness |

There is no plugin directory. A source you turn on is a block in
`config.yaml`. A new job-board profile is a code change in
`hunt/adapters/http_json.py` (`PROFILES`). A new source kind is a code
change in `hunt/adapters/__init__.py` (`SOURCE_KINDS`).

Source kinds that exist today:

| Kind | Module | Default in the example |
|---|---|---|
| `http_json` | `hunt/adapters/http_json.py` | On, reading offline fixtures |
| `imap_alerts` | `hunt/adapters/imap_alerts.py` | Off (`mail-alerts`, `enabled: false`) |

`hunt/adapters/linkedin_alert.py` is not a source. It parses mail that
`imap_alerts` already fetched. Leave that path off.

`http_json` profiles that exist today: `justjoin`, `remotive`,
`landing_jobs`. The example points them at `fixtures/*.json`. Delete the
`path` key to GET the live URL built into that profile. Do not invent a
profile name.

## Where configuration lives

All of it is under `$HUNT_DATA`. Hunt never writes it back into the git
clone.

```
$HUNT_DATA/
  config.yaml          # the only settings file
  secrets.env          # keys. gitignored. never commit
  knowledge/           # facts. see docs/kb-guide.md
  store.sqlite         # created on first open
  attachments/         # CV PDFs and application files
```

`config.yaml` blocks, in the order they appear in the example:

| Block | Day one | What it does |
|---|---|---|
| `bind`, `port` | Leave `127.0.0.1` / `8787` | Where `hunt serve` listens. `http.bind` / `http.port` are fallbacks |
| `hours_per_month`, `days_per_month` | Leave unless your estimate unit is different | Pay math |
| `display_currency` | Set yours | Currency the board shows |
| `comp_floor` | Set yours or remove it | Screening floor. Missing pay stays unknown. It is not invented |
| `fx` | Set the rates you use | Quote conversion. `as_of` is a stamp you choose |
| `tax_homes` | Keep only your countries | Net estimate. Not tax advice |
| `worker.backend` | Leave `none` | `none` (you run jobs), `cli` (a cron drains the queue), `hermes` (optional). See [workers.md](workers.md) |
| `inbox.pending_cap` | Leave `200` | Cap on pending inbox rows. `0` is unlimited |
| `cv.filename_pattern` | Leave | Neutral file name. Do not put an employer in it |
| `agent` | Set the model you use | `harness`, `model.base_url`, `model.api_key_env`, `model.model`. The key itself is `secrets.env` |
| `agent.triage` | Leave `enabled: false` | Local classifier. Off means poll will not start it |
| `knockouts` | Leave the lists empty | Phrase filters. A bare `intern` token also matches unrelated words. Prefer phrases |
| `sources` | Turn on the boards you want | `id`, `kind`, `name`, `enabled`, `config`. See the kinds table above |

`auth_token` (also `auth.token` or `http.token`) is optional and absent
from the example. You do not need it while `bind` is `127.0.0.1`.

Knowledge files are listed in [onboarding.md](onboarding.md). Integrity
rules are data in `knowledge/integrity.yaml`, not constants in the code.

## Run

```bash
export HUNT_DATA="$HOME/hunt-data"
.venv/bin/hunt serve
```

`--host` and `--port` override `config.yaml` for one process. The default
is still localhost.

Jobs do not run themselves when `worker.backend` is `none`:

```bash
hunt --json sources list
hunt --json sources run justjoin-sample --run
hunt --json jobs enqueue --type screen-inbox --run
```

Use a source id from `hunt sources list`. Do not assume a mail source
exists.

### Optional: start at login

A user service is enough. It stays on localhost.

```ini
# ~/.config/systemd/user/hunt.service
[Service]
Environment=HUNT_DATA=%h/hunt-data
ExecStart=%h/hunt/.venv/bin/hunt serve
WorkingDirectory=%h/hunt
Restart=on-failure

[Install]
WantedBy=default.target
```

```bash
systemctl --user enable --now hunt.service
```

Change the two paths if your clone or data directory is elsewhere.
Do not add a public DNS name here.

## Out of scope

Several people, a hosted database, a public DNS name, and sending mail or
applications. State is `store.sqlite` in that person's directory.
`imap_alerts` can stay in the tree and stay disabled.
