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
skills/                   # 21 AI agent skills
├── apex-orchestrator-report/   # Main orchestrator (Phases 1-8)
├── apex-guardrails/            # Non-negotiable quality constraints
├── apex-build-context-pack/    # Assembles inputs/application_context.md
│
├── term_extractor/                   # Extract 5 high-priority terms with synonyms & examples
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

### 1. Prepare or Update your inputs

**Manual Setup:**
Copy the template and fill in your data:

```bash
cp inputs/application_context.template.md inputs/application_context.md
```

**Updating via Agent:**
You can ask the agent to build or update your context pack using the `apex-build-context-pack` skill.
- "Please update `inputs/application_context.md` with the attached job description."
- "Extract the requirements from this PDF and put them into the `JOB_REQUIREMENT_TEXT` section of my context file."

The context file requires 11 input sections:
- `USER_JOB_HISTORY_TEXT` — Your employment history
- `USER_ADMIN_PROFILE_TEXT` — Existing e-recruitment profile text
- `JOB_DESCRIPTION_TEXT` — Target job description
- `JOB_REQUIREMENT_TEXT` — Requirements section of the JD
- `JOB_QUALIFICATION_QUESTIONS` — Screening questions
- `TERM_EXTRACTOR` — Key terms with star weights
- `SKILLS_TAXONOMY` — Skills categorization
- `CHAR_LIMIT` / `TARGET_LOW` / `TARGET_HIGH` / `WORD_TARGET` — Length constraints

#### Guide: How to write `USER_JOB_HISTORY_TEXT`

The `USER_JOB_HISTORY_TEXT` section is the foundation of your application. The quality of the agent's output depends directly on the detail and structure of this input.

**Goal:** Provide a detailed evidence record that supports strategy analysis, document generation, and competency mapping.

**Best Source Documents:** Build each role from evidence, not just memory. Use:
1. Employment contracts and official job descriptions.
2. Performance reviews (PER/ePAS) and progress reports.
3. Quantifiable project reports, donor submissions, and financial records.
4. Field mission reports and training logs.

**Recommended Structure per Role:**
1. **Header:** `Job Title | Organization | Dates | Contract Type`
2. **Key Achievements (3-6 bullets):** Focus on outcomes with numbers using the "action + scope + result + metric" structure.
3. **Duties:** Include operational details, tools used, coordination scope, compliance tasks, and specific stakeholder names.
4. **Currency:** Use USD as the primary currency; add local currency in parentheses if needed.

**Quality Rules:**
- Trace facts to source documents.
- Quantify results whenever possible (USD, counts, %, timelines).
- Do not merge distinct roles unless they were formally one position.
- Provide as much detail as possible; the agent will synthesize it for you.

### 2. Run the Term Extractor (prerequisite)

Before running the orchestrator, populate the `TERM_EXTRACTOR` section of
your context file. Use the `term_extractor` skill to analyse the job
description and extract 5 high-priority terms with synonyms, star ratings,
and resume-ready examples:

> "Run `term_extractor` on the job description in my context file."

**Please examine the extracted terms for your review, and make sure they
sound reasonable for the JD. Then proceed to the next step. If you are not
confident in the terms, do not include the `application_context.md` file
and re-run extraction with adjusted inputs.**

Once you are satisfied, paste the output into the `TERM_EXTRACTOR` section
of `inputs/application_context.md` (or let the agent do it for you).

### 3. Run the orchestrator

You can run the full workflow by prompting the agent with clear instructions.

**Example Prompt (Full Run):**
> "Please run the UN Job Application Helper on my data.
> 1. Read the file `inputs/application_context.md` in this repository.
> 2. Use `apex-orchestrator-report` to generate the multi‑phase strategy report (phases 1–7).
> 3. Once that’s done, I want Option 1, 2, 3, 4, and 5 from the Phase 8 menu: an Admin Profile. Use `apex-generate-admin-profile` to create a headline and one paragraph per job, and apply `capel-fit` to enforce the character limits from my context file."

The `apex-orchestrator-report` skill will:
1. Parse your context file
2. Generate the strategy report (Phases 1–7)
3. Present the Phase 8 menu
4. Generate documents based on your selection

### 4. (Optional) CAPEL character fitting

The `capel-fit` scripts enforce strict character limits for e-recruitment fields:

```bash
python skills/capel-fit/scripts/normalize_text.py < input.txt
python skills/capel-fit/scripts/charcount.py < input.txt
python skills/capel-fit/scripts/fit_entry.py --limit 4000 --low 3600 --high 3950 < input.txt
```

## Requirements

- Python 3.9+ (for CAPEL scripts — standard library only, no external dependencies)
- An AI agent runtime that supports the `skills/` convention (this repo is designed to live at `.agents/` in your project)

## Privacy

The `inputs/` directory is gitignored by default because it contains personal employment data. Never commit your `application_context.md` to a public repository.

## License

This project is licensed under the **Apache License 2.0** — see [LICENSE](LICENSE) for details.
