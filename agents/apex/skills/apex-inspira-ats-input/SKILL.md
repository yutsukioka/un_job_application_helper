---
name: apex-inspira-ats-input
description: Start an Inspira application by copying the most recent prior application, or update an existing draft with approved duties, qualification answers, motivation and requested attachments. Preserve unselected fields, verify saved changes and leave the application unsubmitted.
---

# Inspira ATS input

Transfer approved application artifacts into the correct draft at `https://inspira.un.org/`. This skill fills selected fields; it does not draft new claims, regenerate documents or submit an application. A duties-only request authorizes only the duties workflow. Perform the additional steps below only when included in the user's request.

## Browser requirement

Use **Codex Browser (the in-app browser)** and read the shared
[ATS browser contract](../apex-guardrails/references/ats-browser.md) before live
work. Its documented browser surface may be provided by `mcp__cua_repl` /
`cua_repl`; desktop Computer Use is excluded. Check directly exposed browser tools
as well as deferred tools, and read their current documentation. Leave unsupported
controls pending rather than switching to desktop automation.

## Inputs and boundaries

- Announce this skill and identify the selected vacancy number, artifact folder and options it consumes. Resolve paths on the current computer. Use the vacancy-specific approved artifacts rather than assuming the latest shared input file belongs to this vacancy.
- Obtain the approved Option 1, Option 4 and Option 7 artifacts and finalized CV/cover-letter files required by the requested scope. Standard repository names include `option1_admin_profile.txt`, `option4_qualification_answers.txt` and `option7_motivation_statement.txt`; inspect the actual files instead of assuming their names or structure.
- Treat vacancy numbers, filenames, dates, role counts and applicant details as task inputs. Do not embed personal facts, credentials, previous application text or session-specific identifiers in this skill or reusable examples. Keep the run stateless; any necessary working snapshots belong only to the current private task or temporary storage.
- The user's selected final text is the transfer source. Use the corresponding job history and vacancy context only as needed to resolve mappings or identify contradictions. Follow [apex-guardrails](../apex-guardrails/SKILL.md) for grounding. Do not silently rewrite approved text, add claims or change protected ATS fields to reconcile a discrepancy.
- Honor existing authorization and clarifications in the conversation. Do not ask the user again about a mapping or action already resolved, except where the active browser policy explicitly requires action-time confirmation.

| Application region | Approved source | Permitted change in the full workflow |
| --- | --- | --- |
| Job Requirements — Step 2 | Option 4 qualification answers | Corresponding answer text only; usually 1,000 characters per answer |
| Education/Languages — Step 3 | Existing application | No changes |
| Experience/References — Step 4 | Option 1 `DUTIES_RESPONSIBILITIES:` payload for each role | Summary of duties only; maximum 1,000 characters including spaces |
| Motivation Statement — Step 5 | Option 7 | Motivation text only; maximum 2,000 characters including spaces |
| Other information — Step 6 | Final CV and cover letter PDFs | Replace only the requested CV and cover letter attachments |
| Review/Submit — Step 7 | Outside this skill | No submission or final certification |

Option 7 belongs to the motivation field. If a request assigns it to screening questions, explain the mapping and resolve that specific ambiguity unless it was already clarified in the conversation. Match answers to the actual questions, not merely their position in a list.

## Prepare exact field payloads before entry

1. Extract only the text that belongs in each selected field. Exclude source labels such as `ROLE:`, `DUTIES_RESPONSIBILITIES:`, `ANSWER:`, character counts, direct-report metadata and leaving-reason notes. Preserve the approved words and punctuation within the payload.
2. Build a role map using employer, job title and dates. Handle multiple roles at one employer separately. Determine the role count from this application and the approved source; never assume a fixed count. If a role is absent or cannot be matched unambiguously, hold that role and explain the discrepancy. Do not add/delete employment entries or alter titles/dates without separate authorization.
3. Check the actual payloads for placeholders and unfinished drafting instructions. If one occurs in text to be entered, prominently report **PLACEHOLDER ALERT**, quote the exact text, name its destination field and request a corrected or explicitly approved replacement before filling that affected field. Continue independent, unblocked work.
4. Distinguish payload issues from metadata issues. A placeholder in an untouched leaving-reason field, staff-count note or role/date annotation is not a placeholder in `DUTIES_RESPONSIBILITIES:`. Do not block clean duties or request permission to preserve fields the user already told you to preserve. Mention outside-field notes only when relevant or when a broader source review is requested, clearly identifying their location. An ambiguous role/date annotation can still block mapping that specific role.
5. Validate exact character counts including spaces. Apply or verify existing [capel-fit](../capel-fit/SKILL.md) checks and [apex-output-lint](../apex-output-lint/SKILL.md) with `INSPIRA_FIELD` for paste-ready text. Compare with the current portal limits and any stricter user limit. Do not silently truncate or accept a substantive rewrite produced during fitting; resolve any required wording change before entering it. Do not apply field linting to the CV or cover letter.
6. Prepare the requested PDFs before deleting existing attachments. If conversion is needed, use the available documents and PDF skills and their render-and-verify workflows. Inspect every page for clipping, font substitution, lost text, altered pagination, overlaps and broken hyperlinks. Preserve the finalized Word originals and their formatting. Do not redesign or shorten them to repair a conversion issue. Respect the user's explicit document-level placeholder exceptions; such an exception does not authorize placeholders in ATS fields.

## Access and navigate the correct draft

- Use Codex Browser and its current documentation. Read [browser reliability notes](references/browser_reliability.md) when working with Inspira's frames, dialogs and repeated employment edits. Do not use hidden page APIs, session tokens or direct HTTP calls to bypass the interface.
- Before starting a new application, follow **Initialize from the most recent application** below. Do not advance through the initial copy/start choices into an empty application by default.
- Identify the application URL for the selected position from the current vacancy context, user-provided URL or matching Inspira vacancy record. Open that URL directly in Codex Browser (or reuse the matching existing tab), then verify the vacancy identity shown by Inspira before continuing. Do not hard-code an example vacancy URL or carry a URL from another task into this run. If only a public job-detail URL is available, open it in Codex Browser and use its visible Apply path to reach the application.
- If login is needed, present the sign-in page and ask the user to enter credentials directly in the Codex Browser tab and report when signed in. Do not request passwords or authentication codes in chat. If already signed in, proceed without repeating the login request.
- After sign-in, inspect the application step shown. If it is **Welcome — Step 1 of 7**, review its required controls before advancing. For controls labeled **Please select one of the options.**, **Please specify.**, or **If yes, which one?**, select or enter only an answer supported by the user's confirmed information or an approved application artifact. These labels are prompts, not answers; do not choose a substantive response by guessing. If a required answer is unavailable, leave that control unchanged and ask the user for that specific information before proceeding past the Welcome page. Preserve unrelated controls.
- When the required Welcome-page controls are complete, activate the lower-right **Next** button. Verify that the page heading is **Job Requirements — Step 2 of 7** before entering Option 4 answers. If the application opens at another saved step, follow the current-step instructions below instead; do not repeat Welcome-page selections or assume the initial page.
- If navigation began at Inspira's home or **My Applications**, locate the requested vacancy number and verify its title and draft status before editing. Select **Open Application**, not application deletion, candidate printing or another vacancy with a similar title. If the provided position URL opens the application directly, verify its vacancy number, title and draft status there instead of navigating away.
- Inspect the current step. A draft may reopen at its last saved step. Use the upper step navigation when necessary; do not assume the initial page or continue from an uncertain location.
- Treat snapshots and attachment-row indices as temporary. Re-identify the target after any navigation, upload, deletion, dialog close or save.

### Initialize from the most recent application

- When starting a new application, use Inspira's visible copy-from-previous-application option before proceeding. Default to the most recent eligible prior application, ordered by the portal's displayed application creation date; exclude the current target draft. Follow an explicit user choice of a different source or a blank application.
- Inspect the actual copy controls and source records. Verify the source vacancy/title/date and that it contains reusable employment, education and reference information. If the most recent source is empty or unavailable, inspect the next most recent eligible source and explain the fallback; do not silently initialize an empty application.
- After copying, verify that the expected employment, education and reference records actually appear before advancing or replacing target-specific content. A selected source or a successful click alone does not establish that the copy completed. Keep prior-vacancy screening answers, motivation and attachments separate from the current vacancy's approved replacements.
- For an existing target draft, preserve its work and inspect its current contents first. Do not restart, delete or recreate the application to recover the initial copy choice. If required records are absent and copying is no longer available, identify the missing sections and use any existing authorization for manual completion. When the user requests completion from scratch, use the current approved application documents first and a supplied P11 or other applicant records for missing factual fields; keep unsupported mandatory details unresolved rather than guessing. This explicit scope can include record creation, while an ordinary duties-only update still cannot.
- Save and reopen the initialized or manually completed draft to verify copied/entered records and the selected target-specific updates. Leave final certification and submission to the applicant.

## Fill the selected application sections

### Job Requirements — Step 2

Match every approved Option 4 answer to the displayed criterion. Replace only its answer text. Leave existing eligibility checkboxes and other controls unchanged. If a checkbox conflicts with the intended answer, alert the user rather than silently changing it. Read back the full entered text and its exact character count, then use the lower-right **Next** button.

### Education/Languages — Step 3

Make no changes. Use **Next** to continue. Do not open education or language editors to normalize or correct existing information.

### Experience/References — Step 4

Before the first edit, capture the existing employment rows, reference rows and employment-status control values for later comparison. Do not include these private values in the public completion summary.

For each mapped role:

1. Identify its employer, title and dates in the current Work Experience table. Open the corresponding pencil/**Edit** control. Do not select its Delete control.
2. Verify the employment editor belongs to that role. Snapshot its editable field values before changing anything, including dates, job title, employer, supervisor details, employment type and leaving reason. Keep these snapshots private to the current task.
3. Replace only **Summary of duties, including detail of supervisory / managerial responsibilities and number and kind of employees directly supervised by you** with that role's approved Option 1 duties payload. Never paste the entire Option 1 role block. Do not fill separate direct-report or leaving-reason fields.
4. Read back the full text, confirm an exact match and a maximum of 1,000 characters, and compare all other editable controls with their pre-edit values. Exclude generated character counters from the comparison. If an unrelated field changed, resolve the unintended change before saving; do not overwrite a concurrent user edit.
5. Activate **Save** inside the employment editor and verify completion. A closed editor or later reopened saved value establishes progress; a click alone does not. If a delay or error leaves the result uncertain, alert the user promptly and inspect the actual state before retrying.

After all roles, preserve **Employment status and history in the UN system and related organizations** and **References other than supervisors** exactly. Compare titles/dates, reference rows and employment-status controls against the initial page. Save the draft, then reopen each edited role to verify persisted duties and protected values; exit verification editors with **Cancel** without changing them. Use **Next** to proceed when the full workflow includes later steps.

### Motivation Statement — Step 5

Replace only Motivation Statement with Option 7. Read it back, confirm an exact match and a maximum of 2,000 characters, then use **Next**.

### Other information — Step 6

Before changing attachments, capture their filenames, descriptions and types, plus the existing Final Questions and Personal Information control values, for later comparison.

1. Identify the existing CV and cover letter by their **Description**, recording the exact filenames and attachment types. Similar filenames or multiple CV descriptions require careful mapping; do not delete an unrelated record or a newly uploaded replacement. Preserve every other attachment, including work samples, degrees, performance reviews and identification documents.
2. Read the current upload restrictions. If the filename limit is 30 characters and an approved PDF name exceeds it, create a byte-identical upload copy with a shorter descriptive name containing the document type and vacancy identifier. Preserve the original filenames. Explain the shortened upload names briefly.
3. When capacity permits, upload the verified replacement PDFs before removing the old pair. This keeps working attachments present until the replacements are confirmed. Set their descriptions to **CV** and **Cover Letter** and retain the corresponding attachment types. If capacity requires removal first, prepare both PDFs and identify the exact old files before any deletion.
4. Follow the active browser policy for deletion. Where removal requires fresh action-time confirmation, present the exact old filenames and verified replacements when ready, and obtain that confirmation. Batch both deletions into one request when permitted. Reuse that confirmation for the same identified pair; do not repeat it unless scope or risk changes. An initial replacement request may not satisfy an action-time requirement.
5. Remove only the identified old files. After each removal, inspect the updated rows before selecting the next file: indices can shift. Verify both obsolete names are absent and both current PDFs have the correct descriptions. Do not infer completion from accepting a confirmation dialog; some deletions take effect only when the page is saved.
6. Do not change **Final Questions**, **Personal Information** or any unselected fields. Compare those controls with the pre-edit snapshot. Activate **Save**, not Next to Review/Submit, and verify the portal explicitly reports the in-progress application was saved but not submitted.

## Failures and completion

- When a browser action times out, check whether it already completed before retrying. For a validation error, describe the exact affected field and leave unrelated values intact. Continue useful independent work when it is safe to do so.
- If automatic approval review rejects an action, do not bypass it with another tool or interaction method. Explain the rejected action and stated reason, finish unaffected work, then request the specific missing confirmation. A technical interaction failure without an approval rejection may be retried through another documented Codex Browser method after inspecting the current state; never through Computer Use.
- Recheck saved qualification answers and motivation text when included in the scope. Verify that every requested role was updated, every replacement attachment is present and protected information remains unchanged. Report unresolved items individually instead of claiming the whole task is complete.
- Leave the saved draft accessible to the user, using the browser's supported tab-retention mechanism if applicable. Never click **Submit**, accept final application certifications or imply submission occurred.
- Return a concise summary of the changes actually saved, any remaining blocker or affected-field placeholder, and the explicit statement that the application **has not been submitted**. Do not repeat irrelevant placeholders from protected metadata as if they were entered into the ATS.
