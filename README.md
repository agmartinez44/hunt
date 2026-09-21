# Hunt

Self-hosted job search for one person. This repository is code. Biography,
mail, PDFs, and SQLite stay in `$HUNT_DATA`, never in git.

Humans submit applications. Hunt does not.

## Read this

| File | When |
|---|---|
| [AGENTS.md](AGENTS.md) | Rules. Read before you edit code or touch a CV. |
| [docs/onboarding.md](docs/onboarding.md) | Set up a private workspace. |
| [docs/self-host.md](docs/self-host.md) | Modules, and every `config.yaml` block. |
| [CONTRIBUTING.md](CONTRIBUTING.md) | Lean code, new modules, suggestions. |
| [docs/README.md](docs/README.md) | The other docs. |

Facts live in `$HUNT_DATA/knowledge/*.yaml`. A row with `verified: false`
does not render.

## Install check

Jane Doe is fictional. This is a smoke test, not your workspace. Your setup
is [docs/onboarding.md](docs/onboarding.md).

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
cp -a example-workspace /tmp/hunt-data-jane
export HUNT_DATA=/tmp/hunt-data-jane

.venv/bin/hunt --json sources run justjoin-sample --run
.venv/bin/hunt serve
# http://127.0.0.1:8787

.venv/bin/hunt-cv render
.venv/bin/hunt-cv finalize "$HUNT_DATA/attachments/cv/Jane_Doe_CV.pdf"
.venv/bin/hunt-cv verify "$HUNT_DATA/attachments/cv/Jane_Doe_CV.pdf" --json --expect Kubernetes
```

`hunt-cv` needs the OS Pango libraries. The board and CLI do not.
Leave `bind` on `127.0.0.1`. Leave mail off.

`hunt <noun> <verb> --json` and `hunt mcp` call the same core.

## License

MIT. Changes: [CONTRIBUTING.md](CONTRIBUTING.md).
