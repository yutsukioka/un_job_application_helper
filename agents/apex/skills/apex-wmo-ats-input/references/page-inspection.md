# WMO page-inspection guide

Use this guide for live inspection, not as a stored WMO form schema. Establish the
actual page names and order from the current application. Record every page even
if it has no Phase 8 destination. Verify the expected four-page flow, and locate
the questions wherever they appear rather than assuming the final page.

## Coverage record

For every section capture:

- Exact page/section and field labels; control type and rendered instructions.
- Available choices and permitted selection count, distinct from current answers.
- Required status and its evidence, including mandatory wording even when an HTML
  `required` attribute is absent.
- Numeric limits, units and evidence; use `UNKNOWN` when not established.
- Conditional trigger, whether observed, and scope that remains inaccessible.
- Upload categories, allowed types, size/count limits and replacement behavior.
- Actual Save, Cancel, Next, Back, page navigation and final-action controls.
- Requested Phase 8/source mapping, observation date and inspection scope.

Oracle accessibility can represent single-select pills as checkboxes. Determine
cardinality from live control behavior already observable or instructions without
changing an answer. Filter out hidden controls belonging to another step. Do not
trigger validation or submit to discover constraints.

Visible labels and instructions determine field meaning. Oracle may reuse a
generic technical element ID for a differently labelled organizational field;
never infer semantic meaning from the ID. Use technical attributes as evidence
only for the property they actually establish, such as a text-length limit.

## Applicant and record sections

Read contact, identity, education, employment, language, skills and other sections
only where present. Capture field schema and instructions without transcribing
unneeded personal values. Do not unmask identifiers for inspection.

Inventory existing record headers and counts. Inspect representative editors to
learn the schema, close them unchanged with Cancel, and state the sampling scope.
Inspect every selected record individually before later entry. Do not claim every
record body was read after inspecting one example.

For employment, determine whether duties, responsibilities and achievements share
one field or are separate. Identify separate leaving reasons and supervisor/direct
report controls only if present. A numeric headcount is not a narrative field;
technical string length does not establish a valid headcount range.

For education and credentials, preserve exact titles, dates and completion status.
For skills and languages, determine free-text versus controlled entries and each
rating dimension separately. Do not translate another portal's scale, proficiency
rating or experience duration into a WMO value without evidence and current labels.

## Questions, attachments and final controls

Read the entire question block, help text and every visible choice in live order.
Inspect expanded declarations and final terms as text without accepting them.
Separate job-evidence questions from identity, nationality, work rights, prior
service, relatives, disciplinary history, sourcing, consent and attestations.
Keep personal answers outside the extracted question/options list.

Questions may span pages. Record conditional triggers without selecting an answer
to open a branch; mark those branches `UNOBSERVED_CONDITIONAL`. If required fields
block access, identify the precise user action needed and keep inaccessible scope
pending rather than claiming complete inspection.

Read upload instructions even when optional-looking controls appear. Distinguish
CV/cover-letter uploads from degree evidence or other supporting documents by
actual labels. Do not replace unrelated attachments during inspection or narrative
entry. Final certifications may appear inside the questions as well as near Submit.

## Change readiness

When another vacancy or a changed form is inspected, refresh its navigation and
affected editors. New pages, altered options, changed labels or limits invalidate
the affected prior mapping. Retain unaffected verified information and document
the changed scope; do not copy applicant answers between vacancies.
