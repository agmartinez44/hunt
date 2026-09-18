# Workers

Hunt stores jobs in SQLite (`source-poll`, `screen-inbox`, `tailor-cv`).
HTTP, CLI, and MCP only enqueue. Something has to **claim** `queued` rows.

`config.yaml`:

```yaml
worker:
  backend: none   # none | cli | hermes
```

## `none` (default)

Jobs stay queued until you run them:

```bash
hunt --json jobs enqueue --type screen-inbox --run
hunt --json jobs run              # next queued
hunt --json jobs run "$JOB_ID"
hunt --json jobs worker           # drain the queue once
```

`--run` on `jobs enqueue` / `sources run` claims that job immediately.

## `cli`

Same in-process executor. Point a cron or supervisor at a drain, after
enqueueing the work you want:

```bash
hunt --data "$HUNT_DATA" --json sources run linkedin-alerts
hunt --data "$HUNT_DATA" --json jobs enqueue --type screen-inbox
hunt --data "$HUNT_DATA" jobs worker
```

The process drains `queued` jobs and exits. It does not daemonize.

## `hermes` (optional)

Hermes is **not required**. If you already run a Hermes cron, treat it as a
dispatcher, not the tailor:

1. Cron (short, `no_agent` if you have it) runs
   `hunt --data "$HUNT_DATA" jobs worker`.
2. Long `tailor-cv` work happens inside that Hunt process (`hunt.cv`), not
   inside the cron agent's chat body.
3. Do not spawn a Hermes session that emails or submits forms.

Hunt does not ship a Hermes client. There is no apply/send job type.

## Safety

Adapters (`imap_alerts`, `http_json`) never POST an application and never
send mail. `imap_alerts` selects the folder `readonly=True` and uses
`BODY.PEEK[]` (headers + body; never STORE/EXPUNGE). LinkedIn alerts are
parsed into employer, role, location, URL, engagement, and quoted pay
when present. It searches FROM (when configured), then applies header
filters, then the `limit` — so matches older than the last N messages in
a large INBOX still land. Credentials live in `$HUNT_DATA/secrets.env`,
not in git.
