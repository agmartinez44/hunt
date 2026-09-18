# Example Hunt workspace (fictional)

This directory is a **complete fictional persona**: Jane Doe, WidgetCorp,
ExampleCorp, `jane.doe@example.org`. It exists so clone + render works
without anyone's real biography.

Copy it to a private `$HUNT_DATA` and replace the YAML with your own
facts. Do not commit a real person's knowledge base, mail, or PDFs to
the Hunt repo.

```
example-workspace/
  config.yaml          # bind, worker, display currency (no secrets)
  knowledge/           # honesty-gated YAML + integrity *values*
  attachments/         # hunt.cv writes PDFs here at runtime (gitignored)
```
