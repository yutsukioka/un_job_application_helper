---
name: apex-unicef-ats-input
description: Enter approved Option 1 responsibilities, user-specified application answers and referee details, and finalized CV and cover letter into an existing UNICEF PageUp draft. Preserve all unselected fields, stop on placeholders, save each edited page and leave the application unsubmitted.
---

# UNICEF ATS input

Transfer the user's selected application materials into the matching UNICEF PageUp draft, save the requested changes, and return to Home without submitting. This skill performs entry; it does not generate application claims, create a financial offer or revise source documents.

## Browser requirement

Use **Codex Browser (the in-app browser)** and read the shared
[ATS browser contract](../apex-guardrails/references/ats-browser.md) before live
work. Its documented browser surface may be provided by `mcp__cua_repl` /
`cua_repl`; desktop Computer Use is excluded. Check directly exposed browser tools
as well as deferred tools, and read their current documentation. Leave unsupported
controls pending rather than switching to desktop automation.

## Inputs and scope

- Identify the vacancy title and number, vacancy-specific output folder, approved Option 1 admin profile, final CV and cover letter, and the fields the user requests for this run. Use the selected vacancy's artifacts; do not assume shared inputs or the most recent output belong to it.
- Treat every applicant fact as a per-run input: nationality and residency answers, permit validity, degree selection, professional experience, country/year lists, referees, disclosure answers, fees, availability date and application source. Do not reuse facts from another application or infer answers from the vacancy's location or contract type.
- Honor authorization and corrections already given in the conversation. An explicit entry request authorizes its specified edits and saves; do not request approval again for those actions. Preserve all fields outside the requested scope.
- Use the approved text as the transfer source. Do not silently rewrite it, change dates or titles, add unsupported claims, or modify the repository's private inputs and generated outputs as part of entry. If source facts conflict and affect the requested transfer, identify the specific discrepancy before entering it.
- Keep working role maps and verification snapshots private to the current run. Do not embed applicant data, contact details, credentials, vacancy-specific filenames or session identifiers in this reusable skill.

## Prepare and check the materials

1. Read the actual Option 1 file and extract only each role's duties/responsibilities payload for **Your responsibilities**. Exclude role labels, character counts, direct-report metadata, leaving reasons and drafting notes. Standard filenames may include `option1_admin_profile.txt`; inspect the folder rather than relying on that name.
2. Map each payload by employer, job title and employment dates. Separate different jobs at the same employer. Determine the expected role count from the current request and artifacts, then compare it with the actual ATS entries; do not assume every application has eleven roles or that source order matches ATS order. Resolve missing, duplicate or ambiguous matches before updating the affected entry. Do not add, delete or reorder employment records to force a match.
3. Inspect all responsibilities payloads and the complete contents of the actual CV and cover letter to be uploaded for placeholders or unfinished instructions, including document headers, footers and tables. Inspect the user's selected final artifacts; do not substitute an earlier draft or treat a placeholder in an unrelated draft as a blocker in a clean final file. Examples include `[Confirm ...]`, `[Insert ...]`, unresolved template fields, `TBD` and substantive `TODO` notes. Judge tokens in context; ordinary citations, legitimate initials and resolved explanatory notes are not automatically placeholders.
4. If a placeholder occurs in any responsibilities payload, CV or cover letter, **stop ATS entry and uploading immediately**. Report **PLACEHOLDER ALERT**, the file and role/page, and the exact text requiring confirmation. Do not delete the placeholder, guess a replacement or continue with other ATS pages. Resume after the user resolves or explicitly approves that specific content. A placeholder confined to an untouched Option 1 metadata field, such as a separate leaving-reason note, is not a responsibilities placeholder, but it may still prevent reliable role mapping.
5. Check each field's actual limits. For numeric limits, validate exact character counts including spaces using [capel-fit](../capel-fit/SKILL.md); use [apex-output-lint](../apex-output-lint/SKILL.md) with `UNICEF_FIELD` when strict paste-field formatting applies. Do not assume a universal 2,500-character limit. Never silently truncate or substantively rewrite approved wording to fit. Do not apply field linting to the CV or cover letter.
6. Use finalized upload files in a format accepted by the portal. When conversion is needed, use the available document/PDF workflow and visually verify the converted pages without rewriting the content or replacing the originals. Inspect the actual upload files, not just a companion text version.

## Sign in and open the correct application

Use Codex Browser and its current documentation. Observe current labels and controls; do not rely on remembered element IDs, hidden endpoints or old page structure.

1. Open or reuse the matching tab at [UNICEF candidate login](https://secure.dc7.pageuppeople.com/apply/671/cw/applicationForm/default.asp). Alternatively, open [UNICEF Careers](https://www.unicef.org/careers/) and follow **Candidate login**.
2. If authentication is needed, show the login page, alert the user to sign in directly, and pause authentication-dependent work until sign-in completes. Do not ask for passwords or codes in chat or enter credentials on the user's behalf. If already signed in, proceed.
3. Confirm the candidate **Home** page, normally [home.asp](https://secure.dc7.pageuppeople.com/apply/671/cw/applicationForm/home.asp). Locate the application by vacancy number and title, verify it is the intended draft, and choose **Complete application**. Stop if the matching draft is unavailable or ambiguous; do not create a replacement application.
4. Inspect the current page and top navigation. A draft may reopen at its last saved page. The expected sequence is Personal details → Nationality → Education → Employment History → Languages → Referee Details → Self-disclosure → Application Sourcing Feedback → Diversity and Inclusion → Document uploads → Statement → Submit. Use the live form when labels differ.

Wait for the form to finish initializing before capturing values or mapping records. Repeaters and dropdowns may initially appear blank; recheck the stable rendered form before treating a value as missing or replacing it.

## Page entry and saving

On every edited page, read back the entered values and use **Save and continue**, **Continue**, or **Save and exit**, according to the actual control. Verify that saving succeeded through the resulting page state, a save confirmation or persisted values on reopening. A click alone is not proof of saving. Do not navigate away through the header while requested changes remain unsaved.

| Page | Permitted changes for the full workflow |
| --- | --- |
| Personal details | None. Use the available continue/save-and-continue control. |
| Nationality | Only the supplied answers about nationality in the duty-station country, legal steps to change nationality, residency in that country, country of birth, and other relevant residence facts. Preserve the user's supplied wording. |
| Education | Only the supplied selection for highest completed university degree. Preserve every education record and all other controls. |
| Employment History | Only the supplied professional-experience band, developing-country experience answer and country/year text, international-experience answer and country text, emergency-context answer, and mapped **Your responsibilities** fields. |
| Languages | None. Continue without editing records or proficiency levels. |
| Referee Details | Only the supplied contact permission and referee values, including first name, last name, organization, job title, primary phone and email. Use **Add more** for additional requested referees. |
| Self-disclosure | Only the user's supplied answers to the displayed questions, fee fields and availability date. Apply the missing-overall-fee rule below. |
| Application Sourcing Feedback | Only the supplied source selection and its associated explanation, such as the UNICEF webpage specification. |
| Diversity and Inclusion | Leave blank when the user requests that and the fields are blank; do not fill optional fields. Preserve existing values unless the user explicitly asks to clear them. |
| Document uploads | Upload the approved, checked CV and cover letter into their matching slots. Preserve unrelated attachments and fields. |
| Statement / Submit | Outside this workflow unless the user separately requests statement entry. Never submit or accept final submission declarations. |

For narrower requests, perform only the authorized subset. Existing values in other sections do not imply permission to correct or normalize them. If an unexpected required question blocks progression, obtain the missing answer instead of inventing one.

### Employment details

Before each edit, verify the role against its employer, title and dates and capture the current field values for comparison. Replace only **Your responsibilities** with the mapped Option 1 payload. Preserve employment status, department, manager's name, start/end dates, company name and industry, job title, reason for leaving, position/contract type, position/job level, employment country and city, and any additional controls.

Read back the full responsibilities text and confirm it matches the intended payload without truncation. Check that the other values remain unchanged, then save through the available page/editor controls. Work through every requested employment entry, including entries revealed by expansion or pagination, and verify the saved role count against the expected count. Do not claim all roles are complete after updating only the visible entries. After the employment page is saved, verify persisted responsibilities and preserved fields before continuing.

### Referees

Match existing referee entries to the supplied people before replacing values. Fill only the requested fields; preserve unrelated contact fields. For an additional referee, first check whether that person already has an entry, then use **Add more** if needed. Verify every requested referee and the contact-permission selection before saving. Saving the user's permission for UNICEF to contact referees does not authorize the agent to send messages to those referees.

### Self-disclosure, fees and availability

Match each answer to the complete displayed question rather than applying a blanket value by page position. Personal declarations, vaccination confirmations and legal/employment-history answers must come from the user. Enter the daily fee, overall fee and availability date exactly as authorized for this application, respecting displayed currency and date format.

If the user defers the overall fee or financial offer, leave that field and any financial-offer upload unfilled. Do not calculate an overall fee from the daily rate, infer the contract's billable days, enter zero or copy an earlier application's total. If a deferred field blocks **Continue**, try the available **Save and exit** draft action and verify what persisted. After saving, reopen the draft and check whether its normal header navigation permits access to later sections; complete any accessible, authorized sections. Native draft navigation is permitted, but do not tamper with controls or validation to force access. Report the exact validation message and identify sections that are actually unsaved or inaccessible. Continue unrelated work outside the blocked ATS flow when useful.

### Document uploads and exit

Confirm the selected files correspond to this vacancy and passed the placeholder check. Read the portal's live file-type and size restrictions. Use the CV and cover-letter slots and verify the resulting filenames/upload statuses. If an existing attachment occupies a slot, follow the user's replacement scope and preserve unrelated files; do not delete attachments merely to make room for an unverified replacement.

After the requested entries and uploads, choose **Save and exit** and verify a successful save. If the portal shows **You have saved a draft of your application**, follow **Back to home** before confirming candidate **Home** with the application remaining a draft. Do not proceed into Submit, send the application, or assert a final certification. Leave the saved draft accessible to the user.

## Incomplete actions and reporting

- On a timeout or uncertain save/upload, inspect the current state before retrying so the same action does not create duplicate entries or attachments.
- If session expiry occurs, ask the user to sign in again and inspect saved state before resuming. Do not assume the last edited page persisted.
- If a protected field's validation blocks progress, preserve its value and report the exact blocker instead of changing it without authorization.
- Summarize the sections and number of employment entries actually saved, the uploaded filenames, any deferred fee/financial offer or other unfinished item, and whether Home was verified. Distinguish saved progress from attempted or unsaved entry. Always state that the application **has not been submitted**.
