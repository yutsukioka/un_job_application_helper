# un_job_application_helper - Codex instructions

## Skill-first workflow

- This repository defines custom Agent Skills under `agents/apex/skills/`.
- Prefer using repo skills over ad-hoc writing whenever a request matches a skill description.
- If a requested skill is not visible in the current session, assume skill discovery is stale and recommend restarting Codex.

## Skill contract source of truth

For every skill, `SKILL.md` is the canonical behavior contract.

`agents/openai.yaml` is a runtime adapter for Codex/OpenAI UI metadata,
default prompt framing, invocation policy, and dependencies. It must not
contain operational requirements that are absent from `SKILL.md`.

When `SKILL.md` and `agents/openai.yaml` conflict, `SKILL.md` wins.
Update `agents/openai.yaml` to match `SKILL.md`.

GitHub Copilot should rely on `SKILL.md`. Any file Copilot must read
should be linked or referenced from `SKILL.md`; do not require Copilot
to read `agents/openai.yaml` during normal skill execution.

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

If `private/inputs/user_feedback_updates.md` exists, downstream generation skills may use it
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

1. Run `apex-orchestrator-report` to produce the Phase 1-7 strategy report, including Phase 1.4 Skill / Certification / Language Evidence Map from the existing evidence-bank invocation.
2. Run `apex-user-feedback-revision` (Phase 7.5) to:
   - surface `Gap / Missing proof`
   - surface `Mitigation strategies`
   - surface `## Metrics & Specifics Needed`
   - surface Phase 1.4 item-specific evidence gaps for Option 9 (independence/complexity, language activities, credential status, or portal labels)
   - evaluate user edits or ad-hoc additions without blind adoption
3. After the user confirms or fills missing items, regenerate selected Phase 8 documents. Supported Option 9 entries may proceed on selection while unresolved items remain separate; Phase 1.4 adds no global approval gate.

## Target application system (IMPORTANT)

This agent supports multiple UN / international organization e-recruitment systems with different field structures and limits.

- Set the target system in `private/inputs/application_context.md` under `## LIMITS` as:
  - `TARGET_SYSTEM: INSPIRA | UNICEF | IOM | OTHER`

High-level behavior:

- **INSPIRA**: strict character limits for specific fields (e.g., "Summary of duties..." often 1000 chars incl. spaces). Typically separate fields exist such as "Reason for leaving".
- **UNICEF**: "Your responsibilities" field is often longer (e.g., 2500 chars incl. spaces). "Reason for leaving" is typically separate.
- **IOM** (and some Oracle-based systems): often separate "Responsibilities" and "Achievements" (may be unlimited). Prefer content quality over compression unless a numeric limit is provided.
- **UNOPS**: use `TARGET_SYSTEM: OTHER` and `TARGET_ORGANIZATION: UNOPS`. Option 10 prepares exact catalog skills and evidenced Position Areas; use observed field limits and keep unknown limits unresolved. Selected UNOPS generators share its fit plan through the guardrails preparation hook.
- **World Bank**: use `TARGET_SYSTEM: OTHER`; Option 9 prepares Skill / Certification / Language entries with the confirmed High / Medium / Low choices. These proficiency recommendations use an advisory evidence rubric, not an official World Bank scoring scale.

## CAPEL / character control

- For any strict character-band fields (CAPEL), always validate/finalize using `capel-fit` and ensure output is within TARGET_LOW-TARGET_HIGH characters (with spaces).
- Do not rely on approximate character counting when the user provided numeric limits.
- If a field is unlimited or `CHAR_LIMIT: UNLIMITED`, do not CAPEL-fit unless the user provides a numeric limit anyway.

## Output linting profiles (apex-output-lint)

`apex-output-lint` supports multiple lint profiles. Use the profile matching the target system/field:

- `INSPIRA_FIELD`: single paragraph, ASCII punctuation, no bullets/tabs, strict whitespace.
- `UNICEF_FIELD`: similar to Inspira but tuned for longer fields (still plain-text safe).
- `IOM_RA`: allows headings and hyphen bullets for Responsibilities/Achievements sections.
- `ATS_DRA`: allows headings and hyphen bullets for Duties/Responsibilities/Achievements sections.

Use output linting only when strict paste-into-field constraints apply.
Do NOT lint CV or cover letter unless the user explicitly asks.

## Skills

- `apex-match-live-vacancies`: Rescan the live vacancy database against candidate evidence, review eligibility and earlier omissions, and update a selected shortlist while preserving existing entries, statuses and notes.
- `apex-build-context-pack`: Build or refresh `private/inputs/application_context.md` with all raw application inputs.
- `apex-application-preparation`: Archive the outgoing application context byte-for-byte under its position name, extract a selected live-database vacancy into the three job-input sections, reset prior classification/keywords, and set source-qualified ATS limits while preserving candidate evidence.
- `term-extractor`: Extract exactly five high-priority terms from a job description with star ratings, ATS synonyms, JD-grounded rationale, and resume-ready examples in a strict four-line format.
- `apex-jd-keyword-bank`: Extract a larger 20-40 phrase keyword bank from the JD (optional; complements `term-extractor`).
- `apex-ccog-resolver`: Dynamically resolve relevant CCOG entries from the full ICSC database for a specific vacancy. Reads the full database, scores entries against JD signals and the user-confirmed vacancy-type classification only, selects a compact 10-20 entry subset, and clears the full database from context.

- `apex-jd-core-requirements`: Extract the top 5-7 core requirements (and any knockout criteria) from the job description and requirement text.
- `apex-candidate-evidence-bank`: Map job-history evidence to JD core requirements (Phase 1.3), identify gaps with mitigation ideas, and prepare the Phase 1.4 Skill / Certification / Language Evidence Map across the full vacancy. Capture source-grounded capability and missing information; final proficiency remains Option 9's responsibility.
- `apex-keyword-insertion-map`: Identify 8–12 must-use phrases and specify where to place them across relevant Phase 8 outputs.
- `apex-bullet-enhancer`: Rewrite 2-3 existing job bullets with stronger action verbs, measurable outcomes, and keyword alignment.
- `apex-star-story-blueprints`: Generate 3-4 STAR story blueprints tied to critical requirements.
- `apex-uvp-statement`: Produce a concise 1–2 sentence UVP tailored to the role and organization.
- `apex-cover-letter-pointers`: Provide strategic recommendations for tailoring a cover letter to the target role.
- `apex-impression-tips`: Provide tone/language guidance and final polish tips to improve application impact.
- `apex-coaching-reflection`: Generate 1–2 open‑ended reflection questions for interview and role-fit preparation.
- `apex-user-feedback-revision` (Phase 7.5): Extract missing-proof items, including Phase 1.4 evidence gaps needed for Option 9, and evaluate user edits/ad-hoc additions using an intent gate and Candidate Assertion Ledger. Optionally writes `private/inputs/user_feedback_updates.md` for controlled regeneration.

Phase 8 document generation options (current mapping):

- `apex-generate-admin-profile` (Option 1): Admin Profile (INSPIRA | UNICEF fields): per role, paste-ready Duties/Responsibilities field, character-controlled if numeric limits exist, plus Direct Reports and Reason for Leaving when expected by the workflow/context.
- `apex-generate-cv` (Option 2): Updated CV: full CV with header, summary, experience, education, skills/certifications/languages as available.
- `apex-generate-cover-letter` (Option 3): Cover Letter: tailored business-letter format.
- `apex-generate-qualification-answers` (Option 4): Job Qualification Answers: screening-question answers, strict 1000-character limit per answer where required.
- `apex-generate-admin-profile-ra-split` (Option 5): Admin Profile (IOM/Oracle Responsibilities & Achievements separated): per role, Responsibilities and Achievements as separate sections, bullets allowed, plus Direct Reports and Reason for Leaving.
- `apex-generate-competency-mapping` (Option 6): Competency Mapping: skills per job with relevance scores and total experience per skill.
- `apex-generate-motivation-statement` (Option 7): Motivation Statement: Inspira-style VACC framework motivation statement, max 2000 characters with spaces.
- `apex-generate-admin-profile-dra-split` (Option 8): Admin Profile (ATS Duties, Responsibilities & Achievements separated): per role, Duties, Responsibilities, and Achievements as separate sections, bullets allowed, plus Direct Reports and Reason for Leaving. It cooperates with Option 5 for Achievements, Direct Reports, and Reason for Leaving.
- `apex-generate-skills-proficiency` (Option 9): Skill / Certification / Language entries with High, Medium, or Low proficiency, selected from the vacancy and grounded in applicant evidence. Use the current Phase 1.4 evidence map and controlled Phase 7.5 updates when available; direct JD/job-history generation remains supported. Keep proficiency separate from Option 6 relevance scores and tenure; keep unresolved entries in a separate review section.

- `apex-unops-application-fit` (Option 10): UNOPS fit plan and role/skills companion, with exact dictionary labels, evidence-backed Position Areas, requirement coverage and unresolved field constraints. Standalone invocation does not regenerate other outputs; selected UNOPS documents reuse the fit plan.

Named UNESCO deliverables (direct invocation; numbered options unchanged):

- `apex-generate-unesco-employment-history`: prepare/fill the UNESCO EHF and synchronize its role narratives with the companion expertise map; preserve document fields and layout.
- `apex-select-domain-of-expertise`: broadly map job-history evidence to the verified UNESCO inventory, rank supported experience, and synchronize the companion EHF through a shared evidence ledger and crosswalk.
- `apex-curate-publications`: review attribution/status and prepare the actual Title / optional Year / free-text Domain publication fields.

These skills accept raw inputs directly; they do not require Phases 1–7 or a completed context pack. Use `TARGET_SYSTEM: OTHER` with `TARGET_ORGANIZATION: UNESCO` when a context pack is used. Preserve immutable titles and controlled labels; use `capel-fit` exact validation for numeric native-text limits. Keep unresolved records outside clean portal rows. They prepare artifacts and do not submit profile changes.

For UNESCO authoring, either the expertise or EHF skill activates the paired
workflow in their canonical contracts, once per run. Review-only and explicit
scope restrictions remain in force. Publications and other application outputs
are separate; invoking the pair does not request their regeneration.

Word document adaptation (approved-content transfer):

- `apex-adapt-word-unu-p11`: transfer selected Phase 8 outputs into the UNU P11
  Word form, preserving native controls, tables and formatting; validate P11
  word/character limits and keep unresolved personal declarations separate.

- `apex-adapt-word-cv`: transfer approved UVP, Summary, Skills and role bullets
  into an existing Word CV while preserving protected content and original
  formatting. Use for faithful DOCX adaptation, not CV regeneration or redesign.
- `apex-adapt-word-cover-letter`: replace approved date/body text in an existing
  Word cover letter and save under the requested name while preserving its
  formatting, salutation, contacts and sign-off. Also supplies the shared OOXML
  helper used by the Word CV adapter.

Utilities / enforcement:

The ATS input skills below use Codex Browser (the in-app browser), not
Computer Use. Their documented unified-runtime browser surface is permitted;
native-app and desktop APIs are excluded. If the browser route is unavailable,
keep the affected step pending and continue independent local work.

- `apex-unops-ats-input`: inspect UNOPS Careers Marketplace forms and enter or
  revise selected approved duties, Position Areas, skills, screening answers and
  attachments through Codex Browser. Verify saved values, distinguish shared
  profile scope, preserve unselected fields and leave submission to the user.
- `apex-unicef-ats-input`: enter approved Option 1 responsibilities, selected
  application answers and referee details, and final CV/cover-letter files into
  an existing UNICEF PageUp draft. Preserve unselected fields, verify saved
  changes, and leave the application unsubmitted.
- `apex-inspira-ats-input`: fill selected Inspira draft fields from approved
  Options 1, 4 and 7 and replace requested CV/cover-letter attachments. Preserve
  unselected fields, verify saved changes, and never submit or certify the application.
- `apex-iom-ats-input`: inspect IOM WAVE / Oracle vacancies and application pages,
  extract the three job-input sections, compare the context, and map live fields
  to Phase 8. Enter selected approved draft content only when requested; never
  submit, sign or assert completeness. Keep comparison artifacts separate from
  a context being used by another active run.
- `apex-wmo-ats-input`: inspect WMO Oracle vacancies and application pages,
  extract the three job-input sections, and map observed fields to requested
  Phase 8 outputs. Enter selected approved draft content only when requested;
  keep `application_context.md` unchanged and never submit or sign.

- `apex-guardrails`: Enforce workflow constraints such as source-grounding, placeholder use, keyword integrity, and format profiles.
- `apex-output-lint`: Validate and minimally fix formatting for e-recruitment field constraints (profile-based).
- `capel-fit`: Normalize and fit text to strict character limits and target bands using deterministic scripts.

## Skill Sources

Each skill definition is located at:

- `agents/apex/skills/<skill-name>/SKILL.md`

ATS skills use the `apex-<organization>-ats-input` naming pattern; Word adapters
use `apex-adapt-word-<document>`. Keep each folder name, `SKILL.md` frontmatter
name and `agents/openai.yaml` invocation in
agreement. This catalog documents routing; Codex `$` discovery also requires the
skill to be exposed in a scanned location. This setup uses reversible symlinks
at `~/.agents/skills/<skill-name>` pointing to the canonical repository folders.
Restart Codex if a newly installed or renamed skill does not appear.
