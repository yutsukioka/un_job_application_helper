---
name: apex-orchestrator-report
description: >-
   Produce the full Exceptional Application Strategy Report (Phases 1-7)
   and then present the Phase 8 document generation menu. This
   orchestrator sequences: core requirements, evidence mapping, headline
   optimization, keyword planning, bullet enhancements, STAR blueprints,
   UVP, cover pointers, impression tips, and coaching reflections. It
   then stops and waits for the user's Phase 8 selections.
---

# apex-orchestrator-report

## Purpose

This skill orchestrates the production of the "Exceptional Application
Strategy Report". It sequences the key analytical phases (1-7) and
applies cross-cutting guardrails and process rules so that the final output
closely follows the structure and intent of the original prompt. After
generating the report, it presents the Phase 8 menu of document
generation options and stops, awaiting the user's selection.

The orchestrator must be system-aware:
- INSPIRA and UNICEF often have strict character limits for responsibilities/duties fields.
- IOM/Oracle systems often separate Responsibilities and Achievements and may allow longer content.

## Shared definitions

Apply the expert lens, collaboration rules, guardrails, quality loop
protocol, internal CAPEL generation technique, guiding principles, and
error handling patterns defined in `apex-guardrails`. Do not duplicate
those sections here.

Use format profile: `strategy_markdown` (Markdown headings and bullet points allowed).

## Inputs

When invoked without a populated `inputs/application_context.md`, present
the following greeting before any analysis:

Read from `inputs/application_context.md`. Expected sections:
- `USER_JOB_HISTORY_TEXT`
- `USER_ADMIN_PROFILE_TEXT`
- `JOB_DESCRIPTION_TEXT`
- `JOB_REQUIREMENT_TEXT`
- `JOB_QUALIFICATION_QUESTIONS`
- `TERM_EXTRACTOR`
- (Optional) `JD_KEYWORD_BANK`
- `SKILLS_TAXONOMY`
- `LIMITS` (must include `TARGET_SYSTEM` and character guidance if applicable)

If critical sections are missing (especially `USER_JOB_HISTORY_TEXT` or `JOB_DESCRIPTION_TEXT`),
stop and recommend running `apex-build-context-pack`.

## Output format

- Use Markdown headings for Phases 1–7.
- Provide concise, actionable bullets (avoid walls of text).
- Conclude with the Phase 8 menu (8 selectable items).
- Do not generate Phase 8 documents until user chooses.

## Steps

### Phase 0 - Setup sanity check (brief)
1. Confirm `TARGET_SYSTEM` from `LIMITS:`
   - INSPIRA | UNICEF | IOM | OTHER
   If missing, state: "TARGET_SYSTEM not provided; defaulting to OTHER."

2. Note character constraints from LIMITS:
   - If CHAR_LIMIT is numeric: treat Admin Profile duties/responsibilities fields as character-limited.
   - If CHAR_LIMIT is UNLIMITED: prioritize content density over compression.

### Phase 1 - Deep Analysis & Alignment
1.1 Assimilation: synthesize job history + JD + requirements + TERM_EXTRACTOR.
1.2 Use `apex-jd-core-requirements` to identify the top 5-7
core requirements + knockout criteria if present.
1.3 Use `apex-candidate-evidence-bank` to map candidate's evidence to each core requirement and identify gaps with 1-2 concrete mitigation strategies per gap.

(Optional) If the JD is complex and `JD_KEYWORD_BANK` is missing, recommend running:
- `apex-jd-keyword-bank` and pasting into JD_KEYWORD_BANK.

### Phase 2 — Admin Profile enhancement protocol
2.1 Use `apex-headline-summary` to generate one headline line.
2.2 Use `apex-keyword-insertion-map` (and JD_KEYWORD_BANK if available) to define 8-12 must-use phrases and specify where to insert them.
2.3 Use `apex-bullet-enhancer` to produce 2–3 example rewrites.

### Phase 3 — STAR story blueprints
Use `apex-star-story-blueprints` to generate 3–4 STAR blueprints tied to critical requirements.

### Phase 4 — UVP statement
Use `apex-uvp-statement` to craft a 1–2 sentences Unique Value Proposition.

### Phase 5 — Cover letter integration pointers
Use `apex-cover-letter-pointers` to provide 2-3 strategic cover letter recommendations.

### Phase 6 — Impression maximizer tips
Use `apex-impression-tips` to provide tone/language recommendations and final review advice.

### Phase 7 — Coaching reflection
Use `apex-coaching-reflection` to pose 1-2 open-ended reflection questions.

### Phase 8 — Menu presentation (no generation yet)
Present the menu below and stop.

## Phase 8 menu (exact text)

---

**Phase 8: Document Generation (User-Activated)**

Select one or more of these 8 items to generate. You may choose any
combination (for example, "1, 3, 4" or "all"):

1. **`apex-generate-admin-profile` — Option 1**
   Admin Profile (INSPIRA | UNICEF fields): per role, paste-ready Duties/Responsibilities field, character-controlled if numeric limits exist, plus Direct Reports and Reason for Leaving when expected by the workflow/context.

2. **`apex-generate-cv` — Option 2**
   Updated CV: full CV with header, summary, experience, education, skills/certifications/languages as available.

3. **`apex-generate-cover-letter` — Option 3**
   Cover Letter: tailored business-letter format.

4. **`apex-generate-qualification-answers` — Option 4**
   Job Qualification Answers: screening-question answers, strict 1000-character limit per answer where required.

5. **`apex-generate-admin-profile-ra-split` — Option 5**
   Admin Profile (IOM/Oracle Responsibilities & Achievements separated): per role, Responsibilities and Achievements as separate sections, bullets allowed, plus Direct Reports and Reason for Leaving.

6. **`apex-generate-competency-mapping` — Option 6**
   Competency Mapping: skills per job with relevance scores and total experience per skill.

7. **`apex-generate-motivation-statement` — Option 7**
   Motivation Statement: Inspira-style VACC framework motivation statement, max 2000 characters with spaces.

8. **`apex-generate-admin-profile-dra-split` — Option 8**
   Admin Profile (ATS Duties, Responsibilities & Achievements separated): per role, Duties, Responsibilities, and Achievements as separate sections, bullets allowed, plus Direct Reports and Reason for Leaving. It cooperates with Option 5 for Achievements, Direct Reports, and Reason for Leaving.

Reply with your selection(s). I will generate only the selected items.

---

## Phase 8 generation guidance (for when the user selects)
When generating documents, apply the correct format profiles:

- Option 1: `inspira_field_strict` or `unicef_field_strict` (based on TARGET_SYSTEM).  
  Use `capel-fit` when numeric limits exist. Use `apex-output-lint` with the matching lint profile for paste-ready field text.

- Option 2: `cv_document` (do not apply strict output lint unless user asks).

- Option 3: `cover_letter_document` (do not apply strict output lint unless user asks).

- Option 4: strict single-paragraph answers; use `capel-fit` and `apex-output-lint` if numeric limits are present.

- Option 5: `iom_ra_split` (bullets allowed). Use `apex-output-lint` only if the user requests IOM-style linting.

- Option 6: mapping document output; no strict lint by default.

- Option 7: `inspira_field_strict`; validate with `capel-fit` in the
  1950–2000 band.

- Option 8: `ats_dra_split` (bullets allowed). Cooperates with Option 5 (`apex-generate-admin-profile-ra-split`) for Achievements, Direct Reports, and Reason for Leaving. Use `apex-output-lint` only if user requests linting.

If the user generates multiple documents, recommend running `apex-cross-doc-consistency` to flag any mismatches.

## Rules

- Do not invent facts; use placeholders.
- Do not paste star symbols (★) into final application text outputs.
- Do not generate Phase 8 documents until the user selects.
