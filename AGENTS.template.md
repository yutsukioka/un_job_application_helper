# un_job_application_helper - Codex instructions

## Skill-first workflow

- This repository defines custom Agent Skills under `.agents/skills/`.
- Prefer using repo skills over ad-hoc writing whenever a request matches a skill description.
- If a requested skill is not visible in the current session, assume skill discovery is stale and recommend restarting Codex.

## Ad-hoc user inputs are NOT a "golden record" (IMPORTANT)

Users may paste extra facts, metrics, rewrites, or corrections in chat, or may edit
`phase1_7_strategy_report*.md` directly. Those additions may be correct, partially
correct, incomplete, ambiguous, or inconsistent with existing inputs.

The agent must not blindly propagate ad-hoc user text into outputs.

### Intent gate (hard rule)

Treat ad-hoc user input as permission to modify downstream documents ONLY when the
user explicitly signals apply intent, for example:

- "Apply these changes"
- "Update my CV using this"
- "Regenerate Phase 8 documents with these additions"
- "Revise the qualification answers/admin profile using this"

If apply intent is not explicit:
- Do NOT modify previously generated outputs.
- Do NOT treat pasted user text as a new source of truth.
- Instead, evaluate the user input and return a review artifact or a clarification checklist.

Default assumption:
- `USER_INTENT_APPLY_UPDATES = NO` unless the user clearly requests apply/regenerate/update behavior.

### Candidate Assertion Ledger (standard evaluation model)

When ad-hoc user input includes new facts, dates, titles, metrics, tools, scope, or
revised wording, split the content into atomic claims and classify each claim as a
Candidate Assertion.

Required evidence-status classes:
- `SUPPORTED`: already grounded in source inputs
- `UNSUPPORTED_BUT_PLAUSIBLE`: not contradicted, but not otherwise grounded
- `CONFLICTING`: contradicts existing titles, dates, metrics, or scope
- `AMBIGUOUS`: unclear role anchor, timeframe, unit, scope, or verb meaning

Required integration policy:
- `SUPPORTED` -> `OK_TO_INTEGRATE`
- `UNSUPPORTED_BUT_PLAUSIBLE` -> `INTEGRATE_WITH_CONFIRM_TAG` or hold as placeholder
- `CONFLICTING` -> do not integrate until resolved
- `AMBIGUOUS` -> hold as placeholder until clarified

Additional checks:
- role / organization anchor
- timeframe anchor
- metric-unit clarity
- duplicate or restatement risk
- action-verb integrity (do not flatten "oversaw" into "managed" without support)
- overlap / double-counting risk for aggregated metrics

### Revised strategy report handling

If the user edits `phase1_7_strategy_report*.md`, treat the changed or newly added
substantive content as Candidate Assertions subject to the same gate and evaluation
logic as ad-hoc chat entries.

When both a baseline and revised strategy report are available:
- compare them;
- isolate user-added or materially changed claims;
- evaluate those claims before any Phase 8 regeneration.

### Approved update patch handling

If `inputs/user_feedback_updates.md` exists, downstream generation skills may use it
only as follows:

- `## APPROVED_UPDATES`
  - may be used as additive factual input
- `## UPDATES_REQUIRING_CONFIRMATION`
  - may be surfaced only with explicit `[Confirm ...]` tags or placeholders
- `## HOLD_AS_PLACEHOLDER`
  - must remain as placeholders unless resolved
- `## DO_NOT_INTEGRATE_UNTIL_RESOLVED`
  - must not be used in generated outputs

Downstream generators must not silently absorb unresolved or conflicting updates.

### Recommended feedback loop

1. Run `apex-orchestrator-report` to produce the Phase 1-7 strategy report.
2. Run `apex-user-feedback-revision` (Phase 7.5) to:
   - surface `Gap / Missing proof`
   - surface `Mitigation strategies`
   - surface `## Metrics & Specifics Needed`
   - evaluate user edits or ad-hoc additions without blind adoption
3. After the user confirms or fills missing items, regenerate selected Phase 8 documents.

## Target application system (IMPORTANT)

This agent supports multiple UN / international organization e-recruitment systems with different field structures and limits.

- Set the target system in `inputs/application_context.md` under `## LIMITS` as:
  - `TARGET_SYSTEM: INSPIRA | UNICEF | IOM | OTHER`

High-level behavior:

- **INSPIRA**: strict character limits for specific fields (e.g., "Summary of duties..." often 1000 chars incl. spaces). Typically separate fields exist such as "Reason for leaving".
- **UNICEF**: "Your responsibilities" field is often longer (e.g., 2500 chars incl. spaces). "Reason for leaving" is typically separate.
- **IOM** (and some Oracle-based systems): often separate "Responsibilities" and "Achievements" (may be unlimited). Prefer content quality over compression unless a numeric limit is provided.

## CAPEL / character control

- For any strict character-band fields (CAPEL), always validate/finalize using `capel-fit` and ensure output is within TARGET_LOW-TARGET_HIGH characters (with spaces).
- Do not rely on approximate character counting when the user provided numeric limits.
- If a field is unlimited or `CHAR_LIMIT: UNLIMITED`, do not CAPEL-fit unless the user provides a numeric limit anyway.

## Output linting profiles (apex-output-lint)

`apex-output-lint` supports multiple lint profiles. Use the profile matching the target system/field:

- `INSPIRA_FIELD`: single paragraph, ASCII punctuation, no bullets/tabs, strict whitespace.
- `UNICEF_FIELD`: similar to Inspira but tuned for longer fields (still plain-text safe).
- `IOM_RA`: allows headings and hyphen bullets for Responsibilities/Achievements sections.

Use output linting only when strict paste-into-field constraints apply.
Do NOT lint CV or cover letter unless the user explicitly asks.

## Skills

- `apex-build-context-pack`: Build or refresh `inputs/application_context.md` with all raw application inputs.
- `term_extractor`: Extract exactly five high-priority terms from a job description with star ratings, ATS synonyms, JD-grounded rationale, and resume-ready examples in a strict four-line format.
- `apex-jd-keyword-bank`: Extract a larger 20-40 phrase keyword bank from the JD (optional; complements `term_extractor`).
- `apex-ccog-resolver`: Dynamically resolve relevant CCOG entries from the full ICSC database for a specific vacancy. Reads the full database, scores entries against JD and candidate history, selects a compact 10-20 entry subset, and clears the full database from context.

- `apex-jd-core-requirements`: Extract the top 5-7 core requirements (and any knockout criteria) from the job description and requirement text.
- `apex-candidate-evidence-bank`: Map job-history evidence to JD core requirements and identify gaps with mitigation ideas.
- `apex-keyword-insertion-map`: Identify must-use phrases and specify where to place them in Admin Profile entries.
- `apex-bullet-enhancer`: Rewrite 2-3 existing job bullets with stronger action verbs, measurable outcomes, and keyword alignment.
- `apex-star-story-blueprints`: Generate 3-4 STAR story blueprints tied to critical requirements.
- `apex-uvp-statement`: Produce a concise 1-2 sentence UVP tailored to the role and organization.
- `apex-cover-letter-pointers`: Provide strategic recommendations for tailoring a cover letter to the target role.
- `apex-impression-tips`: Provide tone/language guidance and final polish tips to improve application impact.
- `apex-coaching-reflection`: Generate 1-2 open-ended reflection questions for interview and role-fit preparation.
- `apex-user-feedback-revision` (Phase 7.5): Extract missing-proof items from the strategy report and evaluate user edits/ad-hoc additions using an intent gate and Candidate Assertion Ledger. Optionally writes `inputs/user_feedback_updates.md` for controlled regeneration.

Phase 8 document generation options (current mapping):

- `apex-generate-admin-profile` (Option 1): INSPIRA + UNICEF style fields (character-limited; paste-ready). May include Direct Reports and Reason for Leaving as separate outputs if requested by the user/workflow.
- `apex-generate-cv` (Option 2): CV (document format; bullets allowed).
- `apex-generate-cover-letter` (Option 3): Cover Letter (document format).
- `apex-generate-qualification-answers` (Option 4): Qualification answers (1000-char limit; used by Inspira and other systems).
- `apex-generate-admin-profile-ra-split` (Option 5): IOM (Responsibilities + Achievements separated; often unlimited unless limits provided).
- `apex-generate-competency-mapping` (Option 6): Skills per job with relevance scores and experience totals.

Utilities / enforcement:

- `apex-guardrails`: Enforce workflow constraints such as source-grounding, placeholder use, keyword integrity, and format profiles.
- `apex-output-lint`: Validate and minimally fix formatting for e-recruitment field constraints (profile-based).
- `capel-fit`: Normalize and fit text to strict character limits and target bands using deterministic scripts.

## Skill Sources

Each skill definition is located at:

- `.agents/skills/<skill-name>/SKILL.md`
