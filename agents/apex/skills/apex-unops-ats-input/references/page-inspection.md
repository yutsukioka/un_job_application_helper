# UNOPS Careers Marketplace field guide

Read during live mapping. This is a sanitized record of form behavior observed
on 27 September 2026, not a fixed vacancy schema or browser API specification.
Refresh all affected controls before writing. No applicant values are retained.

## Vacancy and navigation

The public host is `careers.unops.org`, under Careers Marketplace. Follow supplied
or observed vacancy links and the visible application flow. An observed editable
form used a route named `ApplicationConfirmation`; that name did not establish
submission. Verify the actual title, ID, status and action labels.

The inspected flow exposed Personal info, work-history entries, Skills and an
Application Questions page. Do not assume a fixed page count, question count,
employment count or step order. Rendered required text and HTML attributes are
both evidence; missing attributes do not cancel visible instructions.

## Work history

- An observed narrative control was **Description of Duties**. Determine whether
  it is combined or split before selecting an Option 1/5/8 payload.
- **Position Area** was a single-select field for each historical role. Its
  inventory contained duplicate display labels and a misspelling. Preserve actual
  spelling; use observed control values to distinguish choices, not guessed IDs.
  A label such as Programme/Project/Monitoring and Evaluation is an option only
  when actually present. Do not substitute job categories or CCOG classifications.
- Primary-function versus any-relevant-function guidance was not established by
  that capture. Use the fit plan's conservative role mapping and record unresolved
  semantics; do not claim an official definition that was never observed.
- Supervisory counts, role titles and employers were separately editable. Their
  presence does not include them in a duties-only request. Preserve exact dates,
  employment type, locations and other unselected controls.
- Check loaded values after initialization before interpreting blanks as missing
  data. Reopen each requested record to verify the saved value and mapping.

## Skills

The inspected **Skills** multi-select displayed **Select up to 20 options** on a
profile page. This is dated field-specific evidence, not a guaranteed universal
limit. Record current instructions and whether the count is global, per-role or
application-specific. Profile-to-other-application propagation was unverified.

The linked skill dictionary and actual search results did not always expose the
same labels. Search the complete intended label, inspect live results and preserve
the exact selected option. If unavailable, record that fact and use the fit workflow
to assess an evidenced alternative within the user's authorization. Do not map a
broader skill to a similarly named software product or assign a catalog No as an
ATS value. Verify exact final labels and set membership after saving.

The capture did not establish a per-role skills editor or a High/Medium/Low scale.
Do not manufacture either from the local evidence crosswalk or Option 9 defaults.

## Application questions

Narratives and required choice controls coexisted. Read complete questions and
choices in order; match by text, not remembered numbering. Record help, mandatory
status, conditional content and the difference between offered and saved options.

No displayed numeric cap or textarea `maxlength` was exposed for the inspected
narratives. Their limit remains unknown; another organization's 1,000-character
default is not UNOPS guidance. A successful save confirms that exact payload at
that time, not an unlimited field. Do not treat existing answers as proof of a
credential, skill, language or personal fact absent from approved evidence.

## Uploads, persistence and handoff

Inspect actual file slots, allowed formats, size/count limits and replacements.
Do not infer that the page accepts DOCX or requires PDF from another ATS. A
documented file-chooser call can time out: inspect filenames and upload state
before retrying. If no supported browser upload route works, retain the prepared
file for a manual user handoff. Never control the native chooser with desktop APIs.

Reopening changed sections was useful for verifying persistence. Use the current
record/page save mechanisms and don't infer that all steps save identically.
Final certifications and Submit remain for the user. Report accessible, saved,
deferred and unverified scope precisely.
