---
name: apex-generate-unesco-employment-history
description: Draft or fill a UNESCO Employment History Form together with a broad, evidence-linked expertise map, preserving the applicable Word template and vacancy instructions. Use for paired EHF preparation, updates or review; ordinary CVs and online profile fields remain separate outputs.
---

# UNESCO Employment History Form

Prepare the requested EHF content or document. Apply the source-grounding and
controlled-update rules in repository AGENTS.md and
[apex-guardrails](../apex-guardrails/SKILL.md). This skill can start directly
from raw employment history. A strategy report, evidence bank or approved
feedback patch is useful when available, but is not a prerequisite.

## Required cooperation with expertise mapping

For authoring, invoking this skill **also invokes
[apex-select-domain-of-expertise](../apex-select-domain-of-expertise/SKILL.md)**.
Read both contracts and the
[shared evidence and linkage contract](../apex-select-domain-of-expertise/references/paired-evidence-contract.md).
Use one application-scoped ledger for broad taxonomy coverage, role evidence,
3/2/1 role relevance, qualifying practice periods and vacancy priorities.
Prepare or synchronize the expertise map, EHF content and crosswalk together;
the user does not need to name both skills or separately approve this normal
paired work. The entry skill coordinates; the companion participates once
without recursively invoking the entry skill.

Prefer primary job history and controlled `APPROVED_UPDATES`. A prior tailored
Admin Profile is a chronology/coverage reference, not a new framing authority.
Existing expertise rows, EHF prose or a crosswalk are derivative artifacts:
reuse them only after checking the current applicant, vacancy, source revisions
and evidence. Do not use newly written EHF text to prove its own expertise.

Honor explicit narrower scope. Review-only requests assess both artifacts and
return proposed changes without rewriting them. Research and skill maintenance
do not generate applicant documents. New assertions retain the existing
apply-intent and Candidate Assertion gates; missing information holds only
affected claims or fields, not independent supported work.

## Establish the applicable form

Use the vacancy instructions, applicant history, requested output format and
any supplied template. A vacancy-specific or user-supplied form takes priority
over the bundled standard form. Read [form reference](references/form-reference.md)
for the verified standard fields, provenance and online/file distinction.

For a standard UNESCO EHF without another specified template, use the bundled
[official DOCX](assets/unesco-employment-history-2025-02.docx), retrieved from
the official candidate-profile link on 13 September 2026. Identify this version
in the review. Do not force the applicant to retrieve this available template.
If a vacancy points to a different or newer form, inspect that form first.
Without a usable applicable form, draft the grounded content and mark the
native document as pending the template; do not label a reconstructed layout
an official completed form.

Read only the relevant applicant sources. Without a vacancy, factual transfer
can proceed as an untailored EHF. A request to assess new claims does not
authorize regeneration of earlier application outputs.

## Prepare the content

1. List every employment in present/recent-first order, including successive
   positions with one employer. Use a separate block for each job. Five sample
   blocks are not a maximum; remove unused blocks and clone the native pattern
   when more are needed. Preserve all labels and requested fields.
2. Anchor each role to its employer, title, dates and source. Keep employer,
   client and assignment relationships accurate. Do not silently drop older
   roles to make the form resemble a selected-experience CV.
3. Distinguish responsibilities (remit and personal actions) from achievements
   (attributable outputs and outcomes). Tailor emphasis to the vacancy using
   supported results and scale; do not invent metrics or upgrade verbs.
   For every supported role–domain link in the shared ledger, include its
   concrete evidence in that role's responsibilities or achievements, using
   both where each is established. One natural sentence may support several
   related domains. Do not paste taxonomy lists, scores, aggregate skill years
   or evidence identifiers into the form, and do not invent an achievement
   merely because a skill is established as a responsibility.
   Preserve broader supported skills while leading with vacancy-critical work;
   describe episodic work as a specific activity rather than continuous remit.
   An uncertain experience band does not invalidate a clear role-linked action.
   Hold unallocated sequence-level evidence outside individual job blocks until
   its role anchor is supported.
4. Preserve date precision. The standard form asks DD/MM/YYYY; month/year-only
   evidence requires a review item, not invented first or last days. For an
   evidenced ongoing post, use a supported ongoing designation, such as
   `Present`, and flag any vacancy instruction requiring another convention.
5. Fill names, salary, direct reports and UN grade only from evidence. Retain
   salary currency, period and gross/net basis when known. The exact label is
   `Current Annual Salary (approx.)` in every block: do not silently substitute
   a past ending salary or annualize a fee without a supported explanation.
   Unknown is neither zero nor not applicable. UN grade applies only when
   supported; use `Not applicable` only when established.
6. Reconcile chronology and claims with the paired expertise map and update its
   evidence links after narrative edits. Compare other supplied application
   materials without automatically rewriting them. Keep unresolved facts and
   Candidate Assertion Ledger findings in a separate review, outside the final
   form text.

## Generate and verify

For native Word output, read [native filling](references/native-filling.md).
Use its tested local helper for the bundled template. It preserves the source
package and fills the actual Word form fields and narrative cells. It rejects
other template versions so they receive fresh template inspection and an
appropriate document workflow. Use the available documents skill for DOCX
authoring and PDF tools when the requested deliverable requires PDF.

No narrative character limit was found in the bundled EHF. Do not inherit
Inspira, UNICEF, screening-answer or online-profile limits. Its two name
controls do contain a native `maxLength=20`: do not shorten a legal name to
fit. Record a conflict for overlength names and preserve the full name in the
review until the applicant or applicable instructions resolve it.

For any actual numeric requirement, finalize the exact intended text through
[capel-fit](../capel-fit/SKILL.md), using its native-text validation path rather
than ASCII normalization. The Word controls' Unicode counting semantics have
not been tested; the helper uses a conservative UTF-16 name check and reports
that assumption. Never present it as a verified UNESCO counting rule.

Render the completed document and inspect every page for visible field values,
unchanged labels, clipped or missing text, block breaks, readable typography
and correct footer numbering. Verify package preservation and text coverage.
If layout verification cannot run, deliver a clearly identified review draft
and state that layout remains unverified. Do not claim it is submission-ready.

## Deliver

Return the EHF artifact or content draft, companion expertise map/ranking and
crosswalk, a concise completeness result, and source-linked unresolved fields
when needed. For file-backed authoring, run the shared contract's link validator;
for chat-only content, check the same evidence-to-role links without forcing
file creation. Verify native output by comparing the rendered EHF with the
same narrative text. A clean artifact must not contain internal citations,
QA notes or confirmation placeholders.
Incomplete information may be left blank in a review draft, clearly identified
outside the document; it must not be mistaken for a fully completed form.

Do not automatically generate the online Employment History section, a CV,
cover letter or proposal appendix. Additional attachments depend on the
applicable vacancy or direct recruitment instructions. This skill does not
upload documents or edit/submit a live application.
