# UN Job Application Helper — ApexStrategist AI Agent

An AI-powered multi-skill agent that helps candidates craft exceptional applications for United Nations and international organization positions. Built on the **"Exceptional Candidate Creator"** methodology, the agent analyses job descriptions, maps candidate evidence, and generates optimized application documents.

## How It Works

The agent operates in two stages:

1. **Strategy Report (Phases 1–7):** Deep analysis of the target role, candidate evidence mapping, keyword optimization, STAR story blueprints, UVP crafting, and coaching reflections.
2. **Document Generation (Phase 8):** On-demand generation of up to 6 application documents, individually or in any combination.

### Phase 8 Document Menu

| # | Document | Description |
|---|----------|-------------|
| 1 | Admin Profile | Headline + one optimized paragraph per job |
| 2 | CV | Full CV with header, summary, experience, education, skills |
| 3 | Cover Letter | Tailored business-letter format |
| 4 | Qualification Answers | Answers to screening questions (1000-char limit each) |
| 5 | Admin Profile (R&A Split) | Each job split into Responsibilities / Achievements |
| 6 | Competency Mapping | Skills per job with relevance scores and experience totals |

## Architecture

```
.agents/skills/           # 20 AI agent skills
├── apex-orchestrator-report/   # Main orchestrator (Phases 1-8)
├── apex-guardrails/            # Non-negotiable quality constraints
├── apex-build-context-pack/    # Assembles inputs/application_context.md
│
├── apex-jd-core-requirements/        # Phase 1.2 — Core requirement extraction
├── apex-candidate-evidence-bank/     # Phase 1.3 — Evidence mapping & gap analysis
├── apex-keyword-insertion-map/       # Phase 2.2 — Keyword placement guidance
├── apex-bullet-enhancer/             # Phase 2.3 — Bullet point enhancement
├── apex-star-story-blueprints/       # Phase 3  — STAR story blueprints
├── apex-uvp-statement/               # Phase 4  — Unique Value Proposition
├── apex-cover-letter-pointers/       # Phase 5  — Cover letter strategy
├── apex-impression-tips/             # Phase 6  — Tone & language tips
├── apex-coaching-reflection/         # Phase 7  — Reflective coaching questions
│
├── apex-generate-admin-profile/           # Option 1
├── apex-generate-cv/                      # Option 2
├── apex-generate-cover-letter/            # Option 3
├── apex-generate-qualification-answers/   # Option 4
├── apex-generate-admin-profile-ra-split/  # Option 5
├── apex-generate-competency-mapping/      # Option 6
│
├── apex-output-lint/     # Final formatting validation
└── capel-fit/            # Deterministic character-limit enforcement
    └── scripts/
        ├── normalize_text.py
        ├── charcount.py
        └── fit_entry.py

inputs/                   # User-specific data (gitignored — contains PII)
└── application_context.md
```

Each skill consists of:
- `SKILL.md` — Full skill definition, steps, guardrails, and output format
- `agents/openai.yaml` — Interface config (display name, description, default prompt)

## Getting Started

### 1. Prepare your inputs

Copy the template and fill in your data:

```bash
cp inputs/application_context.template.md inputs/application_context.md
```

The context file requires 11 input sections:
- `USER_JOB_HISTORY_TEXT` — Your employment history
- `USER_ADMIN_PROFILE_TEXT` — Existing e-recruitment profile text
- `JOB_DESCRIPTION_TEXT` — Target job description
- `JOB_REQUIREMENT_TEXT` — Requirements section of the JD
- `JOB_QUALIFICATION_QUESTIONS` — Screening questions
- `TERM_EXTRACTOR` — Key terms with star weights
- `SKILLS_TAXONOMY` — Skills categorization
- `CHAR_LIMIT` / `TARGET_LOW` / `TARGET_HIGH` / `WORD_TARGET` — Length constraints

### 2. Run the orchestrator

Invoke the `apex-orchestrator-report` skill. It will:
1. Parse your context file
2. Generate the strategy report (Phases 1–7)
3. Present the Phase 8 menu
4. Generate documents based on your selection

### 3. (Optional) CAPEL character fitting

The `capel-fit` scripts enforce strict character limits for e-recruitment fields:

```bash
python .agents/skills/capel-fit/scripts/normalize_text.py < input.txt
python .agents/skills/capel-fit/scripts/charcount.py < input.txt
python .agents/skills/capel-fit/scripts/fit_entry.py --limit 4000 --low 3600 --high 3950 < input.txt
```

## Requirements

- Python 3.9+ (for CAPEL scripts — standard library only, no external dependencies)
- An AI agent runtime that supports the `.agents/skills/` convention

## Privacy

The `inputs/` directory is gitignored by default because it contains personal employment data. Never commit your `application_context.md` to a public repository.

## License

This project is licensed under the **GNU General Public License v3.0** — see [LICENSE](LICENSE) for details.
