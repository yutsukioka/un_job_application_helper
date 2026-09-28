---
name: apex-wmo-ats-input
description: Inspect WMO Oracle recruitment vacancies and application pages, extract vacancy text and screening questions, and map live fields to requested Phase 8 outputs. Enter selected approved content into an unsubmitted draft only when requested; keep application_context.md unchanged and never submit or sign.
---

# WMO ATS extraction and draft input

Use a supplied WMO vacancy URL and its application form to extract source text,
understand the current controls, and support selected Phase 8 documents or draft
entry. Read [apex-guardrails](../apex-guardrails/SKILL.md) for shared work modes,
source grounding, truth hierarchy, quality loop and format profiles. `SKILL.md`
is the canonical contract; UI metadata adds no execution requirements.

## Browser requirement

Use **Codex Browser (the in-app browser)** and read the shared
[ATS browser contract](../apex-guardrails/references/ats-browser.md) before live
work. Its documented browser surface may be provided by `mcp__cua_repl` /
`cua_repl`; desktop Computer Use is excluded. Check directly exposed browser tools
as well as deferred tools, and read their current documentation. Leave unsupported
controls pending rather than switching to desktop automation.

## Scope and isolation

Choose the mode from the request:

- **Inspect / extract:** read the posting and accessible application pages;
  produce extraction, field-map and handoff artifacts. Do not change applicant
  values, upload files or regenerate existing documents.
- **Prepare / adapt:** use the verified schema and vacancy evidence to invoke
  only the requested Phase 8 generators. Apply the repository's update-intent
  gate and Candidate Assertion Ledger to new applicant claims.
- **Enter selected draft content:** modify only expressly requested fields or
  attachments using selected final artifacts or current-session supplied answers.
  Extraction, skill creation and document generation do not authorize entry.
  Honor existing entry authorization without asking for it again.

Confirm the rendered vacancy title, ID and organization before using local
sources. Never assume the shared context, newest report or another agent's output
belongs to the open vacancy. Use `TARGET_SYSTEM: OTHER` and
`TARGET_ORGANIZATION: WMO` in the handoff; Oracle is a platform, not proof that
WMO shares another organization's controls or limits.

**Do not change `application_context.md`.** The user integrates the three
extracted sections manually. Do not initiate context comparison or read another
active application's context merely to extract this vacancy. Supplied context or
applicant evidence may be read for requested preparation only after confirming
its vacancy and source scope; it remains read-only in this workflow.

Save run artifacts in a new vacancy-specific ignored directory, for example
`private/output/wmo_ats_<job-id>_<date>/`, distinguishing repeated runs rather than
overwriting another active run. Include source URL, observation date, vacancy
identity, mode and inspection coverage. Keep applicant values, credentials,
session identifiers and private file names out of this reusable skill.

For local preparation from an existing extraction, read its source date, vacancy
identity and page map. Reuse verified same-vacancy information without requiring
another sign-in. Retain unknown properties. Refresh affected live controls when
inspection is requested, a source change affects the mapping, or before entry.

## Read the vacancy and hand off sign-in

Use Codex Browser and its current documentation to inspect rendered
content. Do not depend on stored element indexes, hidden application state,
private endpoints or guessed application-step URLs.

1. Open the supplied vacancy and read the full public posting, including expanded
   duties, requirements, competencies, eligibility notes and Job Info. Record
   deadlines verbatim with any portal timezone guidance.
2. Extract the description and requirements below. Qualification questions stay
   pending until the actual application questions have been read.
3. Open the normal Apply flow. Show the authentication page and ask the user to
   sign in directly, including email, verification and any terms acknowledgement.
   Never request passwords or codes in chat or accept authentication terms for
   the user. Continue independent local work while waiting. Reuse an existing
   authenticated session when present; resume only after sign-in is completed.
4. Confirm the same vacancy in the application and inspect the actual progress
   navigation. Four pages are expected in this workflow, but neither the count
   nor question location is fixed. Locate questions by their actual content;
   they may appear before the final page.

## Extract and map every accessible page

Read [the page-inspection reference](references/page-inspection.md) when inspecting
the form. Visit all available pages through visible navigation and expand relevant
instructions. Inspect existing record editors as needed and cancel unchanged
editors. Do not answer questions to expose conditional content. Record triggers
and unobserved branches. Navigation may autosave the existing draft; do not claim
that read-only intent proves unchanged server state.

If validation blocks navigation, use ordinary page links when available without
bypassing validation. If still blocked, report the exact unseen scope and ask the
user to complete the blocking item, or provide its answer and entry authorization.
Do not invent data to reach later pages. Complete accessible work meanwhile.

Write `extraction.md` with these exact headings:

- `## JOB_DESCRIPTION_TEXT`: the full substantive vacancy posting, including
  context, duties, qualifications, notes and Job Info. Preserve source headings,
  numbering and nested hierarchy; remove navigation and promotional chrome.
- `## JOB_REQUIREMENT_TEXT`: exact education, experience, skills, languages,
  competencies and relevant eligibility conditions. Preserve required/desirable
  distinctions, logical alternatives and qualifications. Duplication with the
  full description is intentional, not independent corroboration.
- `## JOB_QUALIFICATION_QUESTIONS`: exact live questions in order, help text,
  visible answer choices and any declaration within that block. Capture every
  question page wherever it appears. Keep available choices distinct from the
  applicant's selected values and preserve source typos with separate review
  notes. Mark pending or inaccessible content explicitly instead of substituting
  public requirements or another vacancy's questions.

Disclose normalization or transcription reuse; retain a source capture when
available. Do not include existing personal answers in a copy-ready question list.

Write `page_map.md` with page/section, exact label, control type, permitted choices
and selection count, required status, numeric limits and counting units with
evidence, conditional triggers, uploads, save/navigation controls and coverage.
Use `UNKNOWN` for unverified properties: no counter or `maxlength` does not prove
`UNLIMITED`. Restrict DOM evidence to the rendered current page, since Oracle may
retain hidden controls from other steps. Record labels and schema instead of
sensitive identity values whenever values are unnecessary.

## Phase 8 handoff

Write `handoff.md` linking the extraction and page map, stating verified versus
pending scope, and mapping only requested outputs to observed destinations.
Treat all schema examples from other ATS skills as unverified for WMO.

| Output | Map only when the corresponding live control exists |
|---|---|
| Option 1 — [admin profile](../apex-generate-admin-profile/SKILL.md) | Combined employment narrative; reconcile its INSPIRA/UNICEF defaults with the actual WMO field instructions before preparing a payload. |
| Options 2/3 — [CV](../apex-generate-cv/SKILL.md), [cover letter](../apex-generate-cover-letter/SKILL.md) | Supporting files in the verified upload categories, formats and limits. |
| Option 4 — [qualification answers](../apex-generate-qualification-answers/SKILL.md) | Exact permitted labels for choice controls; narratives only for text controls. Supply the page map so observed limits take precedence over legacy defaults. |
| Option 5 — [RA profile](../apex-generate-admin-profile-ra-split/SKILL.md) | Two separate role narratives, such as duties/responsibilities and results/achievements. Map each payload to the exact observed label; include Direct Reports and Reason for Leaving only in matching controls. |
| Option 6 — [competency mapping](../apex-generate-competency-mapping/SKILL.md) | Internal evidence and non-overlapping experience support; relevance scores are not portal proficiency. |
| Option 7 — [motivation statement](../apex-generate-motivation-statement/SKILL.md) | An actual matching motivation field with its observed instructions; do not impose the Inspira default cap as a WMO limit. |
| Option 8 — [DRA profile](../apex-generate-admin-profile-dra-split/SKILL.md) | Separate Duties, Responsibilities and Achievements only where the form separates them. |
| Option 9 — [skills/languages](../apex-generate-skills-proficiency/SKILL.md) | Actual controls, dimensions and labels. Do not add proficiency, years, certification or skill-type fields that the verified schema lacks. |

Separate clean payloads from role headers, counts, evidence notes and unresolved
items. Identity, administrative and personal/legal answers require current,
question-specific user input; job history or an empty field is not an answer.
Leave completeness attestations for the user.

Validate exact final payloads with [capel-fit](../capel-fit/SKILL.md) when numeric
limits apply, using the observed counting unit. Do not fit unknown/unlimited
fields without a user-provided numeric cap or shorten controlled option labels.
Use [apex-output-lint](../apex-output-lint/SKILL.md) only where strict paste-field
constraints apply, selecting the profile by actual format; do not lint CVs or
cover letters by default.

## Enter and verify selected changes

Before entry, confirm the vacancy and final artifact scope; hold payloads with
unresolved placeholders or conflicting facts. Match employment records by employer,
title and dates, not list position. Preserve unselected fields and records. Do not
add, delete, reorder or recreate records merely to force a match.

Refresh affected field labels, options, limits and required status before changes.
If the form differs, update the map and affected payload first. Keep unaffected
verified work. Upload only authorized files after checking their content, vacancy,
format and replacement scope; existing filenames alone do not prove relevance.

Read back entered values and check truncation. Verify persistence using the actual
Save or autosave mechanism; Next does not universally save. If saving is uncertain,
inspect state before retrying. Report entered, saved, deferred and unverified scope
separately, and leave the draft accessible.

**Never submit, sign/e-sign, assert completeness, accept final certifications or
perform an equivalent final action.** Leave those controls unchanged for the user.
Preserve notification and other consent preferences unless their change is
specifically requested. State clearly that the application was not submitted.
