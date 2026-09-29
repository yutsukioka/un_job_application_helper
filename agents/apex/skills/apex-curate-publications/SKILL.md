---
name: apex-curate-publications
description: >-
  Review an applicant's publications and written outputs, assess attribution,
  status and relevance, and prepare supported publication entries for UNESCO
  or supplied application fields. Use for inclusion reviews, selected-publication
  lists or requested publication-section updates; a CV supplied as evidence
  does not request a CV rewrite.
---

# Publication review and application entries

Apply the work mode, guardrails, truth hierarchy and quality loop in
[apex-guardrails](../apex-guardrails/SKILL.md), plus repository `AGENTS.md`'s
intent gate and Candidate Assertion Ledger. Use `AUTHORING` for curation and
`strategy_markdown` for review material. This file is the behavior contract;
operational requirements do not depend on the runtime adapter.

## Scope and inputs

Determine the mode from the task and existing authorization:

- `REVIEW`: recommend what to include and why; leave existing application
  outputs and feedback patches unchanged.
- `PREPARE_ENTRIES`: create the requested publication deliverable only.
- `APPLY_UPDATES`: revise only existing publication sections or named outputs
  that the user expressly requested. Pasted additions and a CV supplied as
  evidence do not authorize unrelated document changes.

Use the raw publication list from chat or files directly. A strategy report,
Phase 7.5 run, CV, DOI, public URL or vacancy is not a prerequisite. Use
`USER_PUBLICATIONS_TEXT`, the destination and available
`PORTAL_PUBLICATION_GUIDANCE`, `PORTAL_FIELD_SCHEMA`, source citations/files,
applicant identity, JD and previous selected list. Without a vacancy, curate
for the requested profile/master-list scope rather than pretending to make a
vacancy-specific selection. Without a publication list, return a focused input
checklist; do not create works from a job history.

For an unspecified destination, provide a bibliographic review or the requested
general selected list. Use the UNESCO adapter only for UNESCO profile entries.
Under the repository's target enumeration, UNESCO uses `TARGET_SYSTEM: OTHER`
plus explicit UNESCO organization/profile context. Do not rewrite the context
pack merely to run this skill.

## Record and assess evidence

Give each work a stable item ID. Record its exact published title, authors/order
or corporate author, applicant role/contribution, type, status, known date/year,
outlet/issuer, language, identifiers/links when available, source locator,
version group and relevance. Missing optional bibliography details remain
unknown; a DOI is not mandatory.

Keep factual support, destination suitability and relevance separate. Classify
material assertions as `SUPPORTED`, `UNSUPPORTED_BUT_PLAUSIBLE`, `CONFLICTING`
or `AMBIGUOUS`, with the source or specific gap. An original applicant-provided
bibliography can support facts it actually states; label it applicant-provided
without claiming external verification. Check new ad-hoc assertions against
available baseline sources. A bare title does not establish the applicant's
authorship; a title-only search match does not establish work identity.
Use supplied credit lines/title pages, repositories or publisher records to
resolve uncertainties where useful. A missing search result does not prove
nonexistence. Do not send private documents to public services for verification.

Honor controlled feedback when present: `APPROVED_UPDATES` may add facts;
`UPDATES_REQUIRING_CONFIRMATION` remain tagged in review;
`HOLD_AS_PLACEHOLDER` remains unresolved; facts under
`DO_NOT_INTEGRATE_UNTIL_RESOLVED` stay out of clean entries. Unsupported,
conflicting or ambiguous material claims cannot become clean facts. Other
independently supported records can proceed without those claims.

Read [publication-selection.md](references/publication-selection.md) for the
type-by-type inclusion matrix, attribution/status examples and source boundaries.
Preserve author, co-author, editor, translator and contributor distinctions.
Funding, supervision, reviewing, acknowledgement or publisher employment alone
does not establish authorship. Distinguish published, accepted/in-press, public
preprint/working paper, submitted, draft and internal/unpublished work. Never
infer peer review, acceptance, dates, identifiers or dissemination.

## Selection and truthful representation

For each work assess independently:

1. Are identity, material bibliography, applicant attribution and status supported?
2. Can the destination represent it without misleading omissions or changing
   its title, type, role or status?
3. Does it add useful evidence for the requested vacancy/profile scope?

Assign one decision separately from evidence status:

| Decision | Meaning |
|---|---|
| `INCLUDE` | Supported, suitable and useful; required fields complete; truthful representation possible. |
| `CONSIDER` | Potentially useful, but relevance, destination scope or a material representation choice needs review. |
| `HOLD` | A material factual conflict, required-field gap, attribution/status ambiguity or field-limit mismatch prevents entry. |
| `USE_ELSEWHERE/OMIT` | Better represented as experience/another output, a redundant version, outside scope or not useful for this selection. |

**Only `INCLUDE` items may appear in clean destination entries**, and only
when entries were requested. Supported bibliography alone is insufficient.
Material qualifications must be visible to the destination's reader, not only
in a private ledger. If necessary contributor/editor/preprint/in-press wording
cannot be represented using permitted controls without misusing them, recommend
another placement or hold the item. This is not a journal-only or sole-author
rule: co-authorship of a published work can be truthful without implying sole
authorship. Preserve full credits in the ledger and never call it sole-authored.

Follow requested count/scope first. Otherwise prioritize relevance, substantive
attributable work and a checkable record; recency is secondary. Do not impose
a universal publication count, age cutoff, English-only or journal-only rule.
Preserve titles, author spellings, diacritics and identifiers. Put an optional
English gloss in review material unless the destination permits it; never
invent an official translation or insert JD keywords into an exact title.

Group reposts and preprint/final versions without inflating the count. Prefer
the final record where suitable. Retain distinct chapters, translations or
follow-on works only when separate identity/contribution is supported.

## UNESCO profile adapter

Read [the dated field schema](references/unesco-publication-fields-2026-09-13.json)
for UNESCO rows. It contains sanitized observed configuration, not universal
policy. Current applicable field/vacancy instructions take precedence; flag
incompatible current requirements instead of guessing.

The captured Publications section is optional. Each added row has:

| Exact label | Field ID | Control | Required within a row |
|---|---|---|---|
| Title of Publication | `Titlepublications` | Text; observed client maximum 4000 | Yes |
| Year of Publication | `YearPublication` | Picklist `yearPublication` | No |
| Domain | `description` | Text; observed client maximum 4000 | Yes |

Return these values separately, in that order. Title is the exact work title,
not a citation. Domain is concise text about the work's subject, derived from
the work or a supported bibliographic description. This subject interpretation
is editorial; the capture provides no more specific help definition.
**Publication Domain is not the Domain of Expertise dropdown.** Do not insert
taxonomy IDs, require category/subcategory choices, or derive it solely from a
vacancy or the applicant's selected expertise. Hold the required Domain when
the work's subject cannot be established.

There is no dedicated author, publisher, URL/DOI, status, summary or upload
control in the captured row. Keep these records in the ledger; do not invent
fields, pack a citation into Title, or repurpose Domain as an attribution/status
box without instructions permitting that use. A private ledger cannot cure a
misleading portal row; apply the representation test above.

The publication-year options were not captured. Fill Year only when a supported
publication year matches a verified available choice; use an ID only if supplied
with that choice. Never infer choices from the calendar or invent an ID.
Otherwise leave the **optional portal year blank**, keep a known year in the
ledger and explain the blank outside the field. This alone does not prevent
`INCLUDE` or block supported Title + Domain. An unknown year is different from
unknown published status or work identity.

`maxEntries:0` means neither zero allowed rows nor a proven unlimited server
maximum. Do not impose or promise a row count without applicable instructions.
A text maximum is not a target length; the dropdown's generic string-length
metadata is not a narrative budget.

## Deliverable and validation

Honor the user's destination, format and chat-only preference. For a standalone
saved deliverable with no specified path, use `private/output/publications_review.md`
in REVIEW or `private/output/publication_entries.md` in PREPARE_ENTRIES. Avoid
overwriting another vacancy's artifact; add the active vacancy identifier when
needed. APPLY_UPDATES follows the named destination and preserves unrelated text.

Provide:

1. Mode/scope and field-rule basis, including snapshot date where used.
2. Item ledger: ID, exact citation/record, applicant attribution/status, evidence
   status with source locator, relevance, decision and short reason. Include
   material unknowns and version relationships.
3. When requested, clean `INCLUDE` entries only. For UNESCO, use the exact three
   column labels above; keep IDs, counts, evidence links and notes outside the
   literal field values. A blank optional Year cell contains no placeholder,
   “unknown,” dash or guessed choice. Separate ready and held lists clearly.
4. Focused unresolved items/alternative placements. Explain an empty clean list
   without fabricating rows. A suggestion to place something in the CV or EHF
   does not authorize editing those documents.

For numeric text limits, read [capel-fit](../capel-fit/SKILL.md) and call
[its exact-string validator](../capel-fit/scripts/validate_text.py) on each final
literal field value. For the captured HTML maxlength, use the explicit `utf16`
unit as a conservative client check:

```bash
python3 agents/apex/skills/capel-fit/scripts/validate_text.py \
  --file FIELD_VALUE.txt --char-limit 4000 --unit utf16 --json
```

Run from the repository root or resolve the linked script relative to this
skill. The input file contains the exact field string, without a convenience
trailing newline. Preserve Unicode and whitespace. Keep reported code-point
and UTF-16 counts outside the values. Do not normalize, automatically replace
phrases, truncate or add filler to titles, identifiers or contribution-sensitive
text. An over-limit exact title is a held mismatch, not permission to shorten
its identity. Revise a long Domain deliberately with supported subject matter
and validate again. No minimum-fill target applies unless requested. Unknown
limits remain unknown; never fit dropdown values or IDs.

The snapshot does not establish server validation or portal-counter equivalence.
Verify against the actual control when available and do not claim that test
occurred without evidence. Do not run INSPIRA/UNICEF ASCII or one-paragraph
lint on bibliography/review tables. Do not upload or submit through this skill.

Return the artifact link and material held items. Generated entries are proposed
application content, not evidence of submission, acceptance or hiring probability.
