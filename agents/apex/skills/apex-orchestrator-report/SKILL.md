---
name: apex-orchestrator-report
description: >-
   Produce the full Exceptional Application Strategy Report (Phases 1-7)
   and then present the Phase 8 document generation menu. This
   orchestrator sequences: core requirements, evidence mapping, Skill /
   Certification / Language evidence preparation, headline
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

When invoked without a populated `private/inputs/application_context.md`, present
the following greeting before any analysis:

Read from `private/inputs/application_context.md`. Expected sections:
- `USER_JOB_HISTORY_TEXT`
- `USER_ADMIN_PROFILE_TEXT`
- `JOB_DESCRIPTION_TEXT`
- `JOB_REQUIREMENT_TEXT`
- `JOB_QUALIFICATION_QUESTIONS`
- `TERM_EXTRACTOR`
- (Optional) `JD_KEYWORD_BANK`
- `SKILLS_TAXONOMY`
- `LIMITS` (must include `TARGET_SYSTEM` and character guidance if applicable)
- (Optional) `USER_CERTIFICATIONS_TEXT`, `USER_LANGUAGES_TEXT`,
  `PORTAL_SKILL_ENTRIES`, `PORTAL_ENTRY_MODE` and `PORTAL_PROFICIENCY_GUIDANCE`;
  missing optional evidence or portal labels does not block the strategy report.

If critical sections are missing (especially `USER_JOB_HISTORY_TEXT` or `JOB_DESCRIPTION_TEXT`),
stop and recommend running `apex-build-context-pack`.

## Output format

- Use Markdown headings for Phases 1–7.
- Provide concise, actionable bullets (avoid walls of text).
- Conclude with the Phase 8 menu (10 selectable items).
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
1.4 Present **Skill / Certification / Language Evidence Map** using the additional
`## Skill / Certification / Language Evidence Map` section returned by that same
`apex-candidate-evidence-bank` invocation. Preserve this exact section heading and
identify it as Phase 1.4 immediately after Phase 1.3; do not rerun the bank or
duplicate the map in Phase 1.3. Follow the [evidence-bank contract](../apex-candidate-evidence-bank/SKILL.md)
for the map's fields and source handling. Cover relevant skills, credentials,
and languages across the full vacancy, including items beyond the top 5-7 core
requirements. Preserve source anchors, demonstrated capability, missing details,
and portal-label provenance when supplied. This prepares evidence only: final
portal labels and High / Medium / Low recommendations belong to Option 9.

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

### Phase 7.5 — Feedback when requested or when new assertions are supplied
Use `apex-user-feedback-revision` to surface the Phase 1.4 map's specific
evidence gaps alongside the existing missing-proof review. Route new or revised
facts through its intent gate and Candidate Assertion Ledger. Keep unresolved
Option 9 items separate; supported entries may proceed when Option 9 is selected.
This adds no new global approval gate and does not change the Phase 8 selection
requirement.

### Phase 8 — Menu presentation (no generation yet)
Present the menu below and stop.

## Phase 8 menu (exact text)

---

**Phase 8: Document Generation (User-Activated)**

Select one or more of these 10 items to generate. You may choose any
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

9. **`apex-generate-skills-proficiency` — Option 9**
   Skill / Certification / Language: vacancy-aligned entries with evidence-based High, Medium, or Low proficiency, plus separate evidence and confirmation notes. Uses supplied portal labels exactly; otherwise proposes draft labels. Proficiency is separate from Option 6 relevance scores and tenure.

10. **`apex-unops-application-fit` — Option 10**
    UNOPS application fit: evidence-backed Position Areas, exact dictionary skills, requirement coverage and a role/skills companion. Applies to UNOPS only; other documents require their own selection. “All” includes this option only for an identified UNOPS target.

Reply with your selection(s). I will generate only the selected items.

UNESCO applicants may also select named outputs without changing the numbered options:

- `apex-generate-unesco-employment-history`: EHF drafting with its companion
  expertise map and role-to-narrative evidence links.
- `apex-select-domain-of-expertise`: verified area/subarea selections and
  ranked supported experience, with a companion EHF and shared evidence ledger.
- `apex-curate-publications`: publication review or exact portal entries.

These skills support direct raw-input invocation without Phases 1–7. A UNESCO
authoring request to either expertise or EHF activates their paired workflow
once; respect review-only and explicit narrower scope. Apply each canonical
skill's fields, sources and limits; other outputs remain separately selected.
Do not dispatch them to Option 1, Option 4, Option 6 or Option 9 merely because
their inputs mention history, a CV, screening or skills. Their runtime is
single-agent; selecting one does not expand the ensemble's supported outputs.

---

## Phase 8 generation guidance (for when the user selects)
For an identified UNOPS target, first apply the
[UNOPS preparation hook](../apex-guardrails/SKILL.md#unops-phase-8-preparation).
Run it once and pass its current fit plan to the selected generators. A standalone
Option 10 selection does not select any other documents. Options 1–9 retain
their meaning; actual UNOPS field constraints take precedence over platform
analogies or fallback caps. Missing constraints hold affected portal values only.

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

- Option 9: structured entry table and separate evidence/confirmation sections under `apex-generate-skills-proficiency`. Pass the current Phase 1.4 evidence map and controlled Phase 7.5 updates when available; its direct JD/job-history route remains valid when a map is absent. Use its advisory proficiency rubric unless the application provides definitions. Do not assign proficiency from JD priority, Option 6 relevance, or tenure alone; keep unresolved ratings outside the entry table. No strict lint or CAPEL by default; apply numeric field limits only when supplied. This option prepares an artifact and does not write to a recruitment portal.

- Option 10: `strategy_markdown` review and JSON sidecars; clean companion values only for observed fields. Follow `apex-unops-application-fit`; no portal writes.

Ensemble v2 currently supports Options 1-4 and 7. Generate Options 5, 6, 8, 9, and 10 with their single-agent skills; selecting them does not expand the ensemble scope.

If the user generates multiple documents, recommend running `apex-cross-doc-consistency` to flag any mismatches.

## Rules

- Do not invent facts; use placeholders.
- Do not paste star symbols (★) into final application text outputs.
- Do not generate Phase 8 documents until the user selects.
