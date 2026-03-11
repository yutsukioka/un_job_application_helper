# UN Job Application Helper — ApexStrategist AI Agent

An AI-powered multi-skill agent that helps candidates craft exceptional applications for United Nations and international organization positions. Built on the **"Exceptional Candidate Creator"** methodology, the agent analyses job descriptions, maps candidate evidence, and generates optimized application documents.

## How It Works

The agent operates in two stages:

1. **Strategy Report (Phases 1–7):** Deep analysis of the target role, candidate evidence mapping, keyword optimization, STAR story blueprints, UVP crafting, and coaching reflections.
2. **Document Generation (Phase 8):** On-demand generation of up to 7 application documents, individually or in any combination.

> **Note:** This agent supports multiple UN / international organization e‑recruitment systems (e.g., **INSPIRA**, **UNICEF**, **IOM/Oracle-style**). Set `TARGET_SYSTEM` in `inputs/application_context.md` under `## LIMITS`.

### Phase 8 Document Menu

| # | Document | Description |
|---|----------|-------------|
| 1 | Admin Profile | Headline + one optimized paragraph per job |
| 2 | CV | Full CV with header, summary, experience, education, skills |
| 3 | Cover Letter | Tailored business-letter format |
| 4 | Qualification Answers | Answers to screening questions (1000-char limit each) |
| 5 | Admin Profile (R&A Split) | Each job split into Responsibilities / Achievements |
| 6 | Competency Mapping | Skills per job with relevance scores and experience totals |
| 7 | Motivation Statement | Inspira-style motivation statement (VACC framework, 2000 chars) |

## Architecture

```
skills/                   # AI agent skills (30 skills)
├── apex-orchestrator-report/   # Main orchestrator (Phases 1-8)
├── apex-guardrails/            # Non-negotiable quality constraints
├── apex-build-context-pack/    # Assembles inputs/application_context.md
│
├── term_extractor/                   # Extract 5 high-priority terms with synonyms & examples
├── apex-jd-keyword-bank/             # Extract 20–40 expanded JD keyword phrases (optional)
├── apex-ccog-resolver/               # Dynamic CCOG entry resolution
│   ├── resource/ccog_reference_full.md  # Full ICSC CCOG database (221 entries, gitignored)
│   └── scripts/
│       ├── extract_ccog_pdf.py          # PDF → structured markdown extraction
│       └── resolve_ccog.py             # Deterministic CCOG scoring/selection
├── apex-jd-core-requirements/        # Phase 1.2 — Core requirement extraction + vacancy classification
├── apex-candidate-evidence-bank/     # Phase 1.3 — Evidence mapping & gap analysis
├── apex-progression-metric-ledger/   # Promotion-chain metric tracking
├── apex-keyword-insertion-map/       # Phase 2.2 — Keyword placement guidance
├── apex-bullet-enhancer/             # Phase 2.3 — Bullet point enhancement
├── apex-headline-summary/            # Phase 2.1 — Headline / summary optimization
├── apex-star-story-blueprints/       # Phase 3  — STAR story blueprints
├── apex-uvp-statement/               # Phase 4  — Unique Value Proposition
├── apex-cover-letter-pointers/       # Phase 5  — Cover letter strategy
├── apex-impression-tips/             # Phase 6  — Tone & language tips
├── apex-coaching-reflection/         # Phase 7  — Reflective coaching questions
├── apex-user-feedback-revision/      # Phase 7.5 — Feedback loop & gap review
│
├── apex-generate-admin-profile/           # Option 1
├── apex-generate-cv/                      # Option 2
├── apex-generate-cover-letter/            # Option 3
├── apex-generate-qualification-answers/   # Option 4
├── apex-generate-admin-profile-ra-split/  # Option 5
├── apex-generate-competency-mapping/      # Option 6
├── apex-generate-motivation-statement/    # Option 7
│
├── apex-application-audit/      # Post-generation panel-reviewer audit
├── apex-cross-doc-consistency/  # Cross-document consistency checks
├── place-holder-checker/        # Unresolved placeholder scanner
├── apex-output-lint/            # Final formatting validation (profile-based)
└── capel-fit/                   # Deterministic character-limit enforcement
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

### CCOG Database (`ccog_reference_full.md`)

The `apex-ccog-resolver` skill requires the full ICSC Common Classification of Occupational Groups (CCOG) database, stored at `skills/apex-ccog-resolver/resource/ccog_reference_full.md`. This file is **not included in the repository** because the source PDF is an ICSC publication.

To generate it:

1. Download the CCOG 2015 PDF from <https://icsc.un.org/Home/JobClassification>.
2. Place the PDF at `skills/apex-ccog-resolver/resource/CCOG_9_2015.pdf`.
3. Run the extraction script:
   ```bash
   python skills/apex-ccog-resolver/scripts/extract_ccog_pdf.py \
     --pdf skills/apex-ccog-resolver/resource/CCOG_9_2015.pdf \
     --output skills/apex-ccog-resolver/resource/ccog_reference_full.md
   ```

This produces ~221 structured entries (codes, canonical verbs, scope descriptors, level signals, and common job titles) that the resolver scores against each vacancy's JD. The resolver script (`resolve_ccog.py`) will refuse to run if this file is still a stub.

## Getting Started

### 0. Repository structure and `AGENTS.md`

This repository is designed to live inside a **parent working directory** as a `.agents/` subfolder. The parent directory holds your personal data and the agent configuration file — these are never committed.

**Expected folder layout:**

```
un_job_application_helper/          ← your workspace root (open this in VS Code)
├── AGENTS.md                       ← agent instructions (NOT in git — see below)
├── inputs/                         ← personal data (gitignored)
│   └── application_context.md
├── output/                         ← generated documents (gitignored)
├── tmp/                            ← working files (gitignored)
└── .agents/                        ← THIS git repository
    ├── skills/                     ← all AI agent skills
    ├── README.md                   ← you are here
    ├── LICENSE
    └── ...
```

**Setting up `AGENTS.md`:**

VS Code Copilot (and compatible agent runtimes) reads `AGENTS.md` from the **workspace root** to load skill definitions, workflow constraints, and safety rules. This file is **required** for the agent to work correctly.

1. Copy the template from this repository to the workspace root:
   ```bash
   cp .agents/AGENTS.template.md AGENTS.md
   ```
   If no template exists yet, create `AGENTS.md` at the workspace root (`un_job_application_helper/AGENTS.md`) with the skill-first workflow rules, ad-hoc input handling, candidate assertion ledger, target system configuration, and the full skill listing. See the current `AGENTS.md` in the workspace for reference.

2. `AGENTS.md` sits **outside** the `.agents/` git repo and is **not committed** — it stays local to your workspace. This is by design: it may reference local paths and personal workflow preferences.

3. When you `git clone` this repo into a new machine, you need to recreate `AGENTS.md` at the parent directory level before the agent skills will activate.


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

Optional (recommended for improved ATS/keyword work):
- `JD_KEYWORD_BANK` — Expanded JD keyword phrases (20–40 phrases)

Also set the target application system in `## LIMITS`:
- `TARGET_SYSTEM: INSPIRA | UNICEF | IOM | OTHER`

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

Optional (recommended):
> "Run `apex-jd-keyword-bank` on the job description in my context file and paste it into `JD_KEYWORD_BANK`."

Once you are satisfied, paste the output into the `JD_KEYWORD_BANK` section
of `inputs/application_context.md` (or let the agent do it for you).

### 3. Run the orchestrator

You can run the full workflow by prompting the agent with clear instructions.

**Example Prompt (Full Run):**
> "Please run the UN Job Application Helper on my data.
> 1. Read the file `inputs/application_context.md` in this repository.
> 2. Run `term_extractor` on the job description in my context file and update `inputs/application_context.md`.
> 3. Use `apex-orchestrator-report` to generate the multi‑phase strategy report (phases 1–7).
>    The orchestrator will propose a vacancy-type classification and ask me to confirm before proceeding with CCOG resolution and the full report.
> 4. Once that's done, I want Option 1, 2, 3, and 4 from the Phase 8 menu. Use `apex-generate-admin-profile` to create a headline and one paragraph per job, and apply `capel-fit` to enforce the character limits from my context file."

The `apex-orchestrator-report` skill will:
1. Parse your context file
2. Propose a vacancy-type classification and request confirmation (Mode A)
3. After confirmation, run CCOG resolution and produce the strategy report (Phases 1–7, Mode B)
4. Present the Phase 8 menu
5. Generate documents based on your selection

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

The git repository (`.agents/`) contains **only** reusable skill definitions and scripts — no personal data.

All personal data lives in the **parent directory** (`un_job_application_helper/`) which is not a git repository:
- `inputs/` — employment history, application context (gitignored within `.agents/`)
- `output/` — generated documents (gitignored)
- `tmp/` — working files (gitignored)
- `AGENTS.md` — local workspace config (outside git scope)

Never commit `application_context.md` or any file from `inputs/` / `output/` / `tmp/` to a public repository.

## Revision History

| Date | Changes |
|------|------|
| Mar 2026 | Added `apex-ccog-resolver` with full ICSC CCOG 2015 database extraction (221 entries); PDF extraction script (`extract_ccog_pdf.py`); two-pass orchestrator flow with vacancy-type confirmation gate; JD-only CCOG resolution design; added `AGENTS.template.md` and setup instructions |
| Feb 2026 | Added `apex-generate-motivation-statement` (Option 7, VACC framework); added `apex-progression-metric-ledger` for promotion-chain metric tracking; added `apex-application-audit` for post-generation review; added `place-holder-checker` utility; added `apex-user-feedback-revision` (Phase 7.5) feedback loop |
| Feb 2026 | Added `apex-cross-doc-consistency` for multi-document validation; added `apex-headline-summary` for ATS-optimized headline generation |
| Jan 2026 | Initial release — ApexStrategist pipeline with Phases 1–7 strategy report, Phase 8 document generation (Options 1–6), `capel-fit` character enforcement, `apex-output-lint` formatting validation, multi-system support (INSPIRA, UNICEF, IOM) |

## License

This project is licensed under the **Apache License 2.0** — see [LICENSE](LICENSE) for details.
