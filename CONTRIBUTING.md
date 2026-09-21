# Contributing

Read [AGENTS.md](AGENTS.md) first. Hunt stays small on purpose.

## Style

- KISS. Ship the smallest change that does the job.
- Lean code. Do not add a wrapper, config key, or dependency for one caller.
- Comments are brief or absent. Explain a constraint the code cannot show.
  Do not narrate the change.
- Docs are brief. One place for each fact. Link to it. Do not paste it again.
- No filler. No status essays, no docs that paraphrase the source, no
  drive-by refactors.

## Add a module

Fetch listings only. Never submit a form and never send mail.

- A normal JSON list needs no code. Add a `sources` entry: `kind: http_json`,
  `url` or `path`, `items_path`, and `map` (`title`, `company`, `external_id`,
  `url`). The generic mapper is `_generic_listing` in
  `hunt/adapters/http_json.py`.
- A shared board (fixed URL, pagination, or its own fields) is a `PROFILES`
  entry in that file, plus a fixture and a test.
- A new source kind is a name in `SOURCE_KINDS` and a branch in `poll_source`
  (`hunt/adapters/__init__.py`).

Update the module table in [docs/self-host.md](docs/self-host.md) with one
row. Do not add a second guide.

## Suggest a change

Open an issue. State the problem, the smallest fix, and what you already
tried. Feature ideas are welcome, including a module you want but will not
write yourself.

A pull request that adds a module includes a test. Do not commit
`$HUNT_DATA`, secrets, real biographies, or PDFs.
