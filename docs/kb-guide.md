# Building your knowledge base

The knowledge base is the product. The pipeline is generic; your KB is what
makes output honest. Keep it in `$HUNT_DATA/knowledge/`, not in the Hunt
repo. Build it in this order.

## 0. Run the intake conversation

An agent should run `hunt-cv intake` **with you**, conversationally. It
drafts YAML; you verify. Nothing renders until you flip
`verified: false → true`.

Start from a copy of `example-workspace/` (fictional Jane Doe). Replace
the YAML; do not commit the result to Hunt.

The same files are the API SoT: `hunt positions|achievements|profile|skills|projects`
(plus HTTP/MCP). Agent writes stay `verified: false`. Confirm from the UI or
`hunt achievements confirm` / `hunt positions confirm`.

## 1. Positions first, with hard boundaries

For each job write `scope_facts` — the things an interviewer must never
catch you overstating:

- team size and what *you* owned vs. consumed ("team of 5 inside a
  40-engineer org — no org-wide ownership claims")
- tools exclusive to this employer (`employer_scope` on the skill)
- support-vs-own distinctions (operated a database ≠ DBA)

## 2. Achievements: one bullet, one claim, one evidence pointer

- `text`: action + scope + outcome. If it contains a number, the number
  needs a defense ("dashboard", "review doc", "manager reference").
- `evidence`: where the claim lives. "Own work." is acceptable only for
  unquantified claims.
- `verified: false` until YOU confirm it. The renderer excludes unverified
  bullets; the linter lists them for follow-up.
- Tags drive tailoring. Use consistent vocabulary (`cicd`, `observability`,
  `incident`, ...) — emphasis profiles select by tag.

## 3. Skills: calibrate ruthlessly

`production` = operated in a real job, can whiteboard it.
The linter cross-checks every `production` skill against verified
achievements — if nothing mentions it, either add the achievement or
downgrade the level. A skills line is a promise; keep it cheap to keep.

## 4. Integrity rules are yours

`integrity.yaml` starts from the example's shape:

- `forbidden_phrases`: claims that must never appear (e.g. "backup and
  restore responsibility" if you only ever supported databases)
- `line_traps`: term ↔ phrase pairs that must not co-occur on a line
- `quantifier_terms`: symbols (`%`, `x faster`) that trigger evidence checks

Add boundaries when YOU decide them. Never let an agent weaken entries.
Hunt source must not hardcode your employers.

## 5. Loop

`hunt-cv verify --lint` until clean → render → finalize → verify PDF →
rasterize and eyeball every page. Then keep the KB alive: new project?
New bullet + evidence while memory is fresh, not two years later.
