# Example Hunt workspace (fictional)

This directory is a **complete fictional persona**: Jane Doe, WidgetCorp,
ExampleCorp, `jane.doe@example.org`. It exists so clone + render works
without anyone's real biography.

Copy it to a private `$HUNT_DATA` and replace the YAML with your own
facts. Do not commit a real person's knowledge base, mail, or PDFs to
the Hunt repo.

```
example-workspace/
  config.yaml          # bind, FX stamp, tax_homes, floor, sample sources, agent.model (no secrets)
  fixtures/            # fictional http_json payload for offline source-poll
  knowledge/           # honesty-gated YAML + integrity *values*
  store.sqlite         # created at runtime by hunt.core (gitignored)
  attachments/         # PDFs and application files (gitignored)
  secrets.env          # gitignored; IMAP_* for imap_alerts if you enable it
```
