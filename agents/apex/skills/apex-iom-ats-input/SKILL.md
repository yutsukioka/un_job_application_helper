---
name: apex-iom-ats-input
description: Inspect an IOM WAVE / Oracle candidate application, extract vacancy text and screening questions, compare the application context, and map live fields to Phase 8 outputs. Also enter selected approved content into an unsubmitted draft when requested. Use for IOM ATS extraction, page inspection or draft input; never submit or sign the application.
---

# IOM ATS extraction and draft input

Use the supplied IOM vacancy URL and live application form to prepare an accurate
Phase 8 handoff or transfer selected approved content into its draft. Read
[apex-guardrails](../apex-guardrails/SKILL.md) for the shared work mode, source
grounding, truth hierarchy, quality loop and format profiles. `SKILL.md` is the
canonical contract; UI metadata adds no execution requirements.

## Scope and inputs

Choose the mode from the user's request:

- **Inspect / extract / compare:** read the public vacancy and every accessible
  application page; produce private extraction, field map and comparison artifacts.
  Do not change applicant fields, upload files, or regenerate existing documents.
- **Prepare / adapt documents:** use the inspected schema to invoke only the
  requested Phase 8 generators. Keep source corrections separate from applicant
  claims and follow the repository's update-intent gate.
- **Enter approved draft content:** change only the specified fields or attachments
  using the selected final artifacts and current-session supplied answers. This
  mode requires an entry request; extraction or skill creation alone does not
  authorize it. Existing authorization persists—do not ask for it again.

Inputs are the vacancy URL or ID, the relevant context-pack path when available,
and, for entry, the selected artifacts and field scope. Confirm vacancy identity
from the rendered title, ID and location before using any local file. Never assume
that the shared context file or newest output belongs to the open vacancy.

Keep run artifacts under a vacancy-specific ignored directory such as
`private/output/iom_ats_<job-id>_<date>/`. Include source URL, observation date,
vacancy identity, mode and verified/unverified scope. Do not put applicant facts,
credentials, session identifiers or private filenames in this reusable skill.

For document preparation with an existing inspection, read its extraction,
page map, comparison and source date first. If they match the requested vacancy
and no contradictory source is supplied, use that verified schema without
repeating sign-in or browser inspection. Keep its unknown properties explicit.
Refresh the live form when the user requests another inspection, a source change
affects the mapping, or before actual portal entry. Do not make sign-in a
prerequisite for preparing documents from a completed inspection.

## Browser requirement

Use **Codex Browser (the in-app browser)** and read the shared
[ATS browser contract](../apex-guardrails/references/ats-browser.md) before live
work. Its documented browser surface may be provided by `mcp__cua_repl` /
`cua_repl`; desktop Computer Use is excluded. Check directly exposed browser tools
as well as deferred tools, and read their current documentation. Leave unsupported
controls pending rather than switching to desktop automation.

## Read the vacancy and sign-in handoff

1. Open/reuse the supplied vacancy. Read the entire public posting, including
   nested responsibilities, qualifications, competencies, notes and Job Info.
2. Extract the three sections specified below. The questions remain pending until
   the authenticated form has been read; the public requirements are not a
   substitute for screening questions.
3. Open the normal Apply/sign-in flow. If authentication is required, show the
   page and ask the user to sign in directly, complete verification and handle any
   terms acknowledgement. Never request passwords or codes in chat. Continue
   independent local work while waiting; resume authenticated inspection only
   after the user completes sign-in. Reuse an authenticated session when present.
4. Confirm the same vacancy in the application header. Read the actual progress
   navigation. Four pages is a common observed layout, not a fixed guarantee.

## Extract and inventory the live form

Read [the page-inspection reference](references/page-inspection.md) while mapping
the form. Visit every page using its visible navigation; expand instructions and
existing record editors where needed to understand fields, then cancel unchanged
editors. Do not select answers just to reveal conditional branches. Record their
triggers and mark unobserved branches as unverified. Navigation may autosave the
existing draft; do not claim that inspection proves unchanged server state.

If a required answer blocks normal navigation, use any available normal page links
without bypassing validation. If still blocked, ask the user to complete that item
or provide its answer and entry authorization. Complete accessible sections and
report the exact unseen scope. Do not invent data to reach page four.

Write `extraction.md` with these exact headings:

- `## JOB_DESCRIPTION_TEXT`: full substantive vacancy posting, including Job Info,
  introduction, organizational context, responsibilities, qualifications and notes.
  Retain source hierarchy, especially numbered duties with lettered subitems. Strip
  navigation and promotional chrome. Preserve a separate raw capture if available;
  disclose whitespace normalization or transcription reuse.
- `## JOB_REQUIREMENT_TEXT`: exact education, experience, skills, language and
  competency requirements from the posting. Preserve logical alternatives,
  required/desirable distinctions, levels, conditional clauses and relevant
  eligibility notes. Duplication with the full JD is intentional, not another
  independent piece of evidence.
- `## JOB_QUALIFICATION_QUESTIONS`: exact question wording in live order, help text,
  all visible choices and any completeness declaration within the question block.
  Keep source typos; put corrections in review notes. Do not include applicant
  answers in the list of available options. Capture every question page if the
  questions move or span more than one page.

Write `page_map.md` containing each page and section, exact field labels, control
types, selection cardinality, required flags, limits/units and their source,
conditional triggers, upload restrictions, save/navigation controls and inspection
coverage. Mark unknown properties `UNKNOWN`; no counter or `maxlength` does not
mean `UNLIMITED`. Filter DOM observations to the rendered current page: Oracle can
retain controls for other steps in hidden DOM. Checkbox-like accessibility labels
can represent single-select pills; verify cardinality without changing the answer.

Record displayed deadlines verbatim with the portal's timezone guidance. Do not
silently equate a device-local date with the narrative Geneva closing date.
Keep sensitive identity values out of the field map when labels/schema suffice.

## Compare and hand off to Phase 8

Compare the three extracted sections against the current context by section,
normalizing whitespace only for comparison. Separate missing substantive text,
changed wording/options, lost hierarchy, display artifacts and unknown properties.
Verify source changes independently; user edits are not automatically evidence.

For comparison-only requests or a context used by another active agent, write
`comparison.md` and a scoped proposed replacement rather than overwriting the
context. When a context update is explicitly requested, re-read it immediately
before writing, preserve unrelated sections and unknown keys, and reconcile
concurrent changes instead of replacing the whole file. Candidate facts remain
subject to the Candidate Assertion Ledger and controlled feedback patch.

Write `handoff.md` linking the extraction, page map and comparison and identifying
which requested outputs can proceed. Use `TARGET_SYSTEM: IOM`; keep field-specific
constraints in `PORTAL_FIELD_SCHEMA` or the linked page map rather than imposing
one global narrative cap.

| Phase 8 output | IOM use, only when the live control exists |
|---|---|
| Option 5 — [RA profile](../apex-generate-admin-profile-ra-split/SKILL.md) | Responsibilities and Achievements separately; Direct Reports and Reason for Leaving in their own controls. Role labels and review notes are not field payloads. |
| Options 2/3 — [CV](../apex-generate-cv/SKILL.md), [cover letter](../apex-generate-cover-letter/SKILL.md) | Vacancy-specific supporting files in the observed upload area and accepted format. Check existing files for another vacancy. |
| Option 4 — [qualification responses](../apex-generate-qualification-answers/SKILL.md) | Exact permitted choices for choice controls; narratives only for actual text fields. Put evidence explanations outside selectable values. Personal disclosures require the user's answers. |
| Option 6 — [competency mapping](../apex-generate-competency-mapping/SKILL.md) | Internal evidence and non-overlapping experience calculations; relevance scores are not portal proficiency or years. |
| Option 9 — [skills and languages](../apex-generate-skills-proficiency/SKILL.md) | Preserve the actual schema: e.g. Skill / Years of Experience / Skill Type, and separate Reading / Writing / Speaking, Native and Study Language. Do not add High/Medium/Low fields when absent. |
| Options 1/7/8 | Use only for an actually matching requested field; do not paste a motivation statement or DRA block into unrelated IOM controls. |

Use [capel-fit](../capel-fit/SKILL.md) for final exact validation when a numeric
limit exists, with the observed counting unit. Use `IOM_RA` from
[apex-output-lint](../apex-output-lint/SKILL.md) only when strict paste-field
formatting applies; do not lint CVs or cover letters by default.

## Enter selected content and verify

Before entry, verify the selected artifacts belong to this vacancy and are final
within the user's requested scope. Hold affected payloads with unresolved
placeholders or conflicting facts; keep useful independent work moving. Match
each employment record by employer, title and dates, never list position. Preserve
unselected fields and existing records; do not add/delete/reorder entries to force
a match. Do not silently alter immutable job titles or dates to fit.

Read back the full entered payload, check for truncation and verify persistence
after the form's actual Save or autosave mechanism. Use record-level Save when
provided; Next is not universally a save command. If saving is uncertain, inspect
the current state before retrying. Re-inspect changed labels, options, required
fields and limits before each subsequent edit; update the map and affected payload
instead of relying on an earlier snapshot.

Upload only authorized, checked files, using the current restrictions and approved
replacement scope. Verify filenames and status; a filename alone does not prove
the document's vacancy or contents. Highest-degree evidence and work-rights proof
are separate from the CV and cover letter when the portal requires them.

**Never click Submit, sign/e-sign, assert completeness, accept final submission
declarations or complete any equivalent final action.** The user completes those
actions. Leave final certifications and signature fields unchanged. Preserve
notification preferences unless specifically requested. Leave the draft accessible
and state what was read, entered, verified saved, deferred or unverified, and that
the application was not submitted.
