# Tailoring workflow (emphasis, never facts)

Hunt stores applications in SQLite (later). Until then, tailor by writing
an `emphasis.yaml` **in your workspace** (not in this git repo) and passing
it to render.

## 1. Read the posting as input, not as text to echo

Capture the job description somewhere in `$HUNT_DATA` if you need it. It
is input for emphasis, not copy for the headline.

## 2. Write emphasis.yaml

```yaml
emphasis_profile: generic        # key from achievements.yaml emphasis_profiles
output_name: Jane_Doe_CV         # NEUTRAL — never company/role/JD echo
profile_text: |
  3–5 lines mapping your verified evidence to the role's actual outcomes.
  Rewrite JD phrasing; do not copy it.
show_clients: true
show_levels: false
include_achievements:            # ordered per position; unknown ids fail the build
  widgetcorp: [wc-slo, wc-gitops, wc-k8s]
  examplecorp: [ec-migration, ec-ansible]
projects: [home-lab]
project_bullets: 1
```

Rules:

- Unknown keys/ids → build fails loudly (schema validation).
- If a JD requirement has no verified achievement behind it, surface the gap
  to the human. Do not stretch wording.
- Headline/profile must survive the "would I say this to that manager?" test.
- Unverified ids listed in `include_achievements` are skipped, not rendered.

## 3. Build + gate

```bash
export HUNT_DATA=/path/to/your-workspace
hunt-cv render --emphasis "$HUNT_DATA/emphasis.yaml"
hunt-cv finalize "$HUNT_DATA/attachments/cv/Jane_Doe_CV.pdf"
hunt-cv verify "$HUNT_DATA/attachments/cv/Jane_Doe_CV.pdf" --json --expect "<keyword>"
```

Then rasterize and visually inspect every page. Text-pass is necessary,
not sufficient.

## 4. A PDF is not a send

Hunt does not submit applications or send mail. Record sends outside the
PDF pipeline (later: application events in SQLite).
