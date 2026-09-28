---
name: apex-unops-ats-input
description: >-
  Inspect UNOPS Careers Marketplace vacancies and forms, or enter and revise
  selected approved duties, Position Areas, skills, screening answers and
  attachments through Codex Browser. Verify saved changes, preserve unselected
  profile data and leave the application unsubmitted.
---

# UNOPS ATS inspection and draft input

Use the selected UNOPS Careers Marketplace vacancy and its actual form to inspect
controls or transfer approved content. Read [apex-guardrails](../apex-guardrails/SKILL.md)
for grounding, truth hierarchy and format profiles. This file is canonical;
`agents/openai.yaml` adds no operational requirements.

## Mode and authorization

- **Inspect / extract / compare:** read accessible pages and prepare a field map
  or comparison. Do not alter answers, profile selections or attachments.
- **Enter / revise:** apply only the fields and final artifacts selected by the
  user, including corrections already authorized in this conversation. An entry
  request authorizes its edits and draft saves; do not ask again for resolved
  mappings or approved actions.
- **Prepare missing content:** invoke only expressly requested generators or
  [apex-unops-application-fit](../apex-unops-application-fit/SKILL.md) selection
  work. Ordinary transfer does not authorize new claims, rewrites or regeneration.

Skill creation, analysis, Option 10 selection and document generation alone do
not authorize portal writes. Never submit, sign/e-sign, assert completeness,
accept final certifications, withdraw/delete an application or send messages.
Do not create a replacement draft to repair a failed save. Create an application
only when starting one is within the user's request, after checking for an
existing target application through the normal UI.

## Browser requirement

Use **Codex Browser (the in-app browser)** and read the shared
[ATS browser contract](../apex-guardrails/references/ats-browser.md) before live
work. Its documented browser surface may be provided by `cua_repl`; desktop
Computer Use is excluded. A failed upload does not authorize a native-app fallback.

## Establish vacancy and source scope

1. Identify the vacancy URL/ID, selected field scope, relevant local artifacts and
   any existing approved change set. Verify the rendered title, ID and organization
   before editing. Use the supplied vacancy or a link observed on
   [UNOPS Careers Marketplace](https://careers.unops.org/); do not construct
   application-step URLs or infer draft/submitted status from a URL's name.
2. Use the selected vacancy's context/history and final artifacts. The shared
   `application_context.md` or newest file may belong to another application.
   Use `TARGET_SYSTEM: OTHER` and `TARGET_ORGANIZATION: UNOPS` in the handoff;
   keep source files and unrelated generated documents unchanged during entry.
3. Reuse same-vacancy extraction and selection artifacts when their source dates,
   facts and scope remain applicable. Refresh affected live controls before entry.
   Preparation from a completed inspection does not require another sign-in.
4. Read the current fit plan, role companion and any later live-selection sidecar
   when Position Areas/skills are selected. Verify their vacancy, evidence and
   provenance; a later sidecar supersedes only the choices it actually verifies.
   Duties-only or attachment-only requests do not require rerunning Option 10.
5. Keep applicant facts, credentials, contacts, session IDs and real filenames out
   of this reusable skill. Store necessary run artifacts only in the selected
   vacancy's ignored output folder or temporary storage, with source date and scope.

Show the sign-in page when authentication is required and let the user complete
it directly. Continue independent preparation while waiting. Once signed in,
recheck vacancy and current application status. A submitted/closed application
is not an editable draft; use only an observed, authorized revision workflow or
report the limitation. Do not resubmit or recreate it.

## Inspect the form and resolve profile scope

Read [the UNOPS field guide](references/page-inspection.md) when inspecting or
mapping the form. It contains dated observations, not a universal schema.
Record exact labels, choices, control type, cardinality, required status, numeric
limits/counting units, save mechanism, upload restrictions and observed page scope.
No visible maximum means `UNKNOWN`, not `UNLIMITED`.

Distinguish application fields from shared candidate-profile fields. Work history,
Position Areas, skills and attachments can appear inside an application without
proving they are isolated to it. Record scope as `APPLICATION`, `PROFILE` or
`UNKNOWN`, including what the UI actually establishes about cross-application use.

Honor explicit profile-edit authorization already given. If the request covers
the displayed profile section, explain known or unknown propagation and carry
out that scoped change; do not impose a new blanket approval gate. If the user
requested application-only edits and the control saves shared profile data, hold
that control and seek the specific scope decision after preparing its change set.
Do not promise vacancy isolation when propagation is unknown. Do not change other
applications to test it.

Use visible navigation, expanding existing editors and cancelling unchanged ones
as needed. Do not select answers to reveal branches in inspection mode. Record
conditional triggers and unseen branches. If required data blocks navigation,
use normal available section links without bypassing validation, complete accessible
work, and report the exact blocker. Navigation may autosave existing data; read-only
intent is not proof of zero server writes.

For extraction requests, write `extraction.md` with `## JOB_DESCRIPTION_TEXT`,
`## JOB_REQUIREMENT_TEXT` and `## JOB_QUALIFICATION_QUESTIONS`. Preserve full
substantive text, requirement alternatives, required/desirable distinctions,
question wording, help and choices. Questions come from the live question page,
not inferred public requirements. Keep current applicant answers outside the
question inventory. Record deadlines verbatim with the portal's timezone guidance.
Write `page_map.md` and, when requested, `comparison.md`/`handoff.md`; do not change
the context pack unless separately requested through the preparation workflow.

## Prepare the exact change set

| Selected destination | Transfer source and boundary |
|---|---|
| Description of Duties | Approved role narrative from the UNOPS companion or selected Option 1/5/8 output that matches the live structure. Exclude role labels, counts, notes and unrelated RA/DRA headings. Do not combine split fields by assumption. |
| Position Area | Evidence-backed, exact live option for that historical role from Option 10. Verify primary-function semantics/cardinality where provided. |
| Skills | Approved exact labels from the fit plan and current live selections. Keep profile skills and per-role assignments separate. |
| Qualification questions | Selected Option 4 text mapped to the complete current question; choice values only for choice controls. |
| Direct supervision or other employment metadata | Only explicitly selected, source-supported values in the corresponding controls. An ordinary duties update leaves them intact. |
| CV / cover letter / other attachments | The user's selected final, vacancy-matching file in the actual matching slot. Preserve unrelated files. |
| Personal, education, language, reference or disclosure fields | Only separately requested and supported answers; preserve all others, including saved declarations and consent preferences. |

Match employment entries by employer, title and dates, never by list position or
a fixed role count. Distinguish successive jobs at the same employer. Missing or
ambiguous matches block that record only. Add/delete/reorder records or correct
titles/dates only within explicit scope; never do so merely to force a match.
Do not total temporary field teams with permanent direct reports unless the
question and approved evidence support that count.

Inspect each actual payload and upload file, including headers, footers and tables,
for unfinished text. Hold affected fields/files with unresolved facts or placeholders;
continue independent clean edits. Quote the specific gap outside the portal.
Placeholders in untouched metadata or superseded drafts do not block a clean final
file unless they prevent matching it. New claims require the repository's controlled
feedback process; entering a pre-existing choice does not validate its evidence.

Apply [capel-fit](../capel-fit/SKILL.md) exact validation only for supported numeric
limits, with the observed counting unit. Never inherit Inspira's or UNICEF's caps,
truncate approved wording, or shorten controlled labels. Use
[apex-output-lint](../apex-output-lint/SKILL.md) only for an applicable strict field
profile; do not lint CVs or cover letters by default. Resolve any substantive
wording change before entering it.

## Enter and verify

Capture only necessary before-values privately. Immediately before each change,
recheck the role/question, live control and current value. Preserve concurrent user
edits outside scope; if the intended target changed, reconcile that change instead
of overwriting it from a stale snapshot. Work sequentially through record editors.

- **Duties:** replace only the mapped payload, read back the entire value, check
  truncation and protected fields, then use the actual record and page save controls.
- **Position Areas:** select an observed exact option. Duplicate labels may have
  different live values; do not guess their equivalence. Hold ambiguous choices
  unless live guidance distinguishes them. Target relevance cannot create a new
  historical function.
- **Skills:** inspect existing selections and complete search results. Keep labels
  containing commas as single values; catalog row numbers are not ATS option IDs.
  Distinguish add-only from replace-all scope. Do not remove existing selections to
  meet a count without replacement authorization. A requested budget is not a
  platform limit. If a dated label is unavailable, do not silently substitute it:
  use the fit skill to assess an exact live alternative and record evidence/reason.
  Apply it only if the user's scope covers selection changes; otherwise hold that
  choice for review. Do not add proficiency or per-role controls absent from the UI.
- **Questions:** use narrative fields for narratives and actual permitted options
  for choices. Preserve unselected saved choices. Do not turn an eligibility gap,
  missing certificate or blank personal declaration into a guessed Yes/No. Deferred
  answers stay deferred; do not provide filler to advance.
- **Attachments:** inspect the selected final file and live type/size/count limits.
  Convert only as needed through the document/PDF workflow and verify the actual
  upload version. Prepare replacements before removing anything. Upload first when
  possible; remove only the identified obsolete file within replacement scope.
  Verify the resulting filename/category/status and any required page save.

Read back complete changed values and verify persistence by reopening the saved
section/draft or another clear persisted-state observation. A click, closed editor,
selected file or Next navigation alone does not prove saving. Check every requested
record, including paginated entries; distinguish already-matching values from edits.
Reconcile the final skill set against the intended set, not just the count.

On timeout or expiry, inspect current saved state before retrying and let the user
reauthenticate if needed. After a repeated technical failure with no new supported
recovery path, stop that operation and report it; do not repeat indefinitely or
switch surfaces. For upload controls unsupported by Codex Browser, leave that upload
pending and offer the exact prepared file for the user's manual upload, then verify.

## Completion

Leave the verified draft accessible for the user's final review. Record changed,
already-matching, saved, deferred and unverified items separately in
`portal_update_record.md`, including counts, final attachment names, selected-source
versions and known/unknown profile propagation. Record skill substitutions in a
dated live-selection sidecar; do not silently rewrite the original fit snapshot.
Keep sensitive before-values out of the public summary.

Return a concise summary of actual saved changes, outstanding actions and file
paths. Never claim all work complete when an upload/save is only attempted.
State that no submission was performed; describe observed application status
accurately rather than claiming an already-submitted application is unsubmitted.
