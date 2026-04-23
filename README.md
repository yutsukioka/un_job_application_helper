# UN Job Application Helper — ApexStrategist AI Agent

An AI-powered multi-skill agent that helps candidates craft exceptional applications for United Nations and international organization positions. Built on the **"Exceptional Candidate Creator"** methodology, the agent analyses job descriptions, maps candidate evidence, and generates optimized application documents.

## How It Works

This is a set of **AI agent skills** designed for VS Code Copilot, OpenAI Codex, Claude Code, and compatible agent runtimes. The skills can be invoked individually or orchestrated as a pipeline. No standalone app or web UI is required — you chat with the agent, and it reads your inputs, runs skills, and writes output files directly.

1. **Single-agent mode** — Chat with the AI Agent to implement ApexStrategist workflow.
2. **Optional local multi-agent mode** — a flat peer-team workflow for VS Code using the upstream `agent_sync` coordination runtime.
Thanks for publishing the very useful tool.
<https://github.com/RPG-478/agent_sync/>
Please read the details of the mechanism and how to add `agent_sync` to this AI agent.

## Workspace layout

Open the **workspace root** (`un_job_application_helper/`) in VS Code.
Create the `agent_sync` folder under `.agents/`.

```text
un_job_application_helper/
└── .agents/                         # THIS git repository
    ├── agent_sync/                  # Install agent_sync here.
    ├── .github/
    │   ├── agents/
    │   └── hooks/
    └── ...
```

**Compatible runtimes:**
- **VS Code Copilot** — Chat with the agent in a chat window. Skills are in the `.agents/skills/` directory.
- **GitHub Copilot Coding Agent** — Run skills in a cloud sandbox via GitHub Issues or PR comments. The agent reads `AGENTS.md` for workflow rules.
- **Any agent runtime** supporting the `.agents/skills/` convention with `SKILL.md` + `agents/openai.yaml` per skill.

**Workflow overview:**

1. **Prepare inputs** — Place your employment history, the target job description, requirements, and screening questions into `inputs/application_context.md` (a structured template).
2. **Run the orchestrator** — Ask the agent to run `apex-orchestrator-report`. It chains ~15 analysis skills automatically:
   - Extracts core requirements and keyword targets from the JD
   - Resolves ICSC occupational classifications (CCOG) for register-matching
   - Maps your evidence to each requirement, identifies gaps, and proposes mitigations
   - Builds STAR story blueprints, a Unique Value Proposition, and cover letter strategy
   - Produces tone/language tips and coaching reflection questions
3. **Review the strategy report** — The agent outputs a comprehensive Phases 1–7 report. You can provide feedback or fill evidence gaps before generating documents.
4. **Generate documents** — Select from the Phase 8 menu below. Each document is tailored to the target role, grounded in your evidence, and optimized for the specific e-recruitment system (INSPIRA, UNICEF, IOM, etc.).

You can also run individual skills directly (e.g., `term-extractor`, `capel-fit`, `apex-application-audit`) without the full orchestrator pipeline.

All personal data stays local — only the reusable skill definitions are in this git repository.

> **Note:** This agent supports multiple UN / international organization e‑recruitment systems (e.g., **INSPIRA**, **UNICEF**, **IOM/Oracle-style**). Set `TARGET_SYSTEM` in `inputs/application_context.md` under `## LIMITS`.

### Phase 8 Document Menu

| # | Document | Description |
|---|----------|-------------|
| 1 | `apex-generate-admin-profile` | Admin Profile (INSPIRA \| UNICEF fields): per role, paste-ready Duties/Responsibilities field, character-controlled if numeric limits exist, plus Direct Reports and Reason for Leaving when expected by the workflow/context |
| 2 | `apex-generate-cv` | Updated CV: full CV with header, summary, experience, education, skills/certifications/languages as available |
| 3 | `apex-generate-cover-letter` | Cover Letter: tailored business-letter format |
| 4 | `apex-generate-qualification-answers` | Job Qualification Answers: screening-question answers, strict 1000-character limit per answer where required |
| 5 | `apex-generate-admin-profile-ra-split` | Admin Profile (IOM/Oracle Responsibilities & Achievements separated): per role, Responsibilities and Achievements as separate sections, bullets allowed, plus Direct Reports and Reason for Leaving |
| 6 | `apex-generate-competency-mapping` | Competency Mapping: skills per job with relevance scores and total experience per skill |
| 7 | `apex-generate-motivation-statement` | Motivation Statement: Inspira-style VACC framework motivation statement, max 2000 characters with spaces |
| 8 | `apex-generate-admin-profile-dra-split` | Admin Profile (ATS Duties, Responsibilities & Achievements separated): per role, Duties, Responsibilities, and Achievements as separate sections, bullets allowed, plus Direct Reports and Reason for Leaving. It cooperates with Option 5 for Achievements, Direct Reports, and Reason for Leaving |

## Architecture

```
skills/                   # AI agent skills and diagnostic utilities
├── apex-orchestrator-report/   # Main orchestrator (Phases 1-8)
├── apex-guardrails/            # Non-negotiable quality constraints
├── apex-build-context-pack/    # Assembles inputs/application_context.md
│
├── term-extractor/                   # Extract 5 high-priority terms with synonyms & examples
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
├── apex-generate-admin-profile-dra-split/ # Option 8
│
├── apex-application-audit/          # Post-generation panel-reviewer audit
├── apex-cross-doc-consistency/      # Cross-document consistency checks
├── place-holder-checker/            # Unresolved placeholder scanner
├── apex-output-lint/                # Final formatting validation (profile-based)
├── capel-fit/                       # Deterministic character-limit enforcement
│   └── scripts/
│       ├── normalize_text.py
│       ├── charcount.py
│       └── fit_entry.py
│
├── apex-agent-sync-protocol/        # Multi-agent coordination contract
├── deterministic-skill-router/      # Rule-based skill routing diagnostics
├── agent-test-suite/                # End-to-end diagnostic test harness
├── agent-execution-tracer/          # Invocation trace logger
├── agent-functionality-tester/      # Format compliance checker
├── agent-reasoning-auditor/         # Reasoning-summary auditor
├── skill-failure-analyzer/          # Root-cause analysis for failures
├── evidence-ranking-engine/         # Evidence ranking diagnostics
├── prompt-repair-engine/            # SKILL.md repair recommendations
└── skill-confidence-scorer/         # Candidate-skill confidence scoring

inputs/                   # User-specific data (gitignored — contains PII)
└── application_context.md
```

Each skill consists of:
- `SKILL.md` — Canonical skill definition, behavior contract, steps, guardrails, and output format
- `agents/openai.yaml` — Thin runtime adapter for Codex/OpenAI UI metadata, default prompt framing, invocation policy, and dependencies

For every skill, `SKILL.md` is the canonical behavior contract.

`agents/openai.yaml` is a runtime adapter for Codex/OpenAI UI metadata,
default prompt framing, invocation policy, and dependencies. It must not
contain operational requirements that are absent from `SKILL.md`.

When `SKILL.md` and `agents/openai.yaml` conflict, `SKILL.md` wins.
Update `agents/openai.yaml` to match `SKILL.md`.

GitHub Copilot should rely on `SKILL.md`. Any file Copilot must read
should be linked or referenced from `SKILL.md`; do not require Copilot
to read `agents/openai.yaml` during normal skill execution.

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

This produces 221 structured entries (codes, canonical verbs, scope descriptors, level signals, and common job titles) that the resolver scores against each vacancy's JD. The resolver script (`resolve_ccog.py`) will refuse to run if this file is still a stub.

## Getting Started

### 0. Repository structure and `AGENTS.md`

This repository is designed to live inside a **parent working directory** as a `.agents/` subfolder. The parent directory holds your personal data and the agent configuration file — these are never committed.

**Expected folder layout:**

```
un_job_application_helper/          ← your workspace root (open this in VS Code)
├── AGENTS.md                       ← agent instructions (NOT in git — see below)
├── contracts/                      ← the metric ledger (NOT in git - see below)
│   └── metric_ledger_contract.md
├── inputs/                         ← personal data (gitignored)
│   └── application_context.md
├── output/                         ← generated documents (gitignored)
├── tmp/                            ← working files (gitignored)
└── .agents/                        ← THIS git repository
    ├── agent_sync/                  # Install agent_sync here.
    ├── .github/
    │   ├── agents/                 ← Six agents definition files
    │   └── screening-lead.agent.md
    │   ├── technical-lead.agent.md
    │   ├── ats-format-lead.agent.md
    │   ├── qa-auditor.agent.md
    │   ├── independent-panel-evaluator.agent.md
    │   └── independent-shortlisting-redteam.agent.md
    │   └── hooks/
    │   └── block_questions.py    │   ├── block_stop.py
    │   └── check_messages.py
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
your context file. Use the `term-extractor` skill to analyse the job
description and extract 5 high-priority terms with synonyms, star ratings,
and resume-ready examples:

> "Run `term-extractor` on the job description in my context file."

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
> 2. Run `term-extractor` on the job description in my context file and update `inputs/application_context.md`.
> 3. Use `apex-orchestrator-report` to generate the multi‑phase strategy report (phases 1–7).
>    The orchestrator will propose a vacancy-type classification and ask me to confirm before proceeding with CCOG resolution and the full report.
> 4. Once that's done, I want Option 1, 2, 3, and 4 from the Phase 8 menu. Use `apex-generate-admin-profile` to create paste-ready duties/responsibilities fields per role, and apply `capel-fit` only where my context file provides numeric limits."

The `apex-orchestrator-report` skill will:
1. Parse your context file
2. Propose a vacancy-type classification and request confirmation (Mode A)
3. After confirmation, run CCOG resolution and produce the strategy report (Phases 1–7, Mode B)
4. Present the Phase 8 menu
5. Generate documents based on your selection

### 4. (Optional) CAPEL character fitting

The `capel-fit` scripts enforce strict character limits for e-recruitment fields:

```bash
python3 skills/capel-fit/scripts/normalize_text.py < input.txt
python3 skills/capel-fit/scripts/charcount.py < input.txt
python3 skills/capel-fit/scripts/fit_entry.py --char-limit 4000 --target-low 3600 --target-high 3950 < input.txt
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

## Metadata Review Hygiene

When reviewing skill metadata, dumps, or repository scans, exclude:
- `.agents/.venv`
- `.agents/.git`
- `.agents/agent_sync`
- `.agents/logs`
- large resource files unless they are directly needed for the task

## Revision History

| Date | Changes |
|------|------|
| Mar 17, 2026 | Added `apex-generate-admin-profile-dra-split` skill (Option 8) |
| Mar 16, 2026 | Added diagnostic/testing skills, including execution tracing and functionality checks |
| Mar 11, 2026 | Added `apex-ccog-resolver` with full ICSC CCOG 2015 database extraction (221 entries); PDF extraction script (`extract_ccog_pdf.py`); two-pass orchestrator flow with vacancy-type confirmation gate; JD-only CCOG resolution design; added `AGENTS.template.md` and setup instructions |
| Mar 4, 2026 | Updated README |
| Feb 13, 2026 | Merged pull request #1 (`devin/1770931524-skill-revisions`) implementing 16 skill revision points for the ApexStrategist agent |
| Feb 11, 2026 | Added `apex-generate-motivation-statement` (Option 7, VACC framework); added `apex-progression-metric-ledger` for promotion-chain metric tracking; added `apex-application-audit` for post-generation review; added `place-holder-checker` utility; added `apex-user-feedback-revision` (Phase 7.5) feedback loop |
| Feb 2026 | Added `apex-cross-doc-consistency` for multi-document validation; added `apex-headline-summary` for ATS-optimized headline generation |
| Feb 12, 2026 | Initial repository setup: created `master`; added `application_context.template.md`; updated `.gitignore` and README |
| Jan 2026 | Initial release — ApexStrategist pipeline with Phases 1–7 strategy report, Phase 8 document generation (Options 1–8), `capel-fit` character enforcement, `apex-output-lint` formatting validation, multi-system support (INSPIRA, UNICEF, IOM) |

## License

This project is licensed under the **Apache License 2.0** — see [LICENSE](LICENSE) for details.
