---
name: apex-generate-skills-proficiency
description: >-
  Generate Skill / Certification / Language entries and evidence-based
  proficiency from a vacancy and applicant job history, using the supplied
  portal schema or High / Medium / Low for the World Bank workflow. Use for Phase 8 Option 9 or requests
  for application skill proficiency entries, rather than Option 6 role
  relevance scores or total experience calculations.
---

# apex-generate-skills-proficiency

## Purpose and scope

Produce Phase 8 **Option 9: Skill / Certification / Language and Proficiency**.
Use the vacancy to select relevant entries and the applicant's evidence to
assess proficiency. Generate only this deliverable; an invocation does not
authorize editing other application documents or entering/submitting data
in a recruitment portal.

Follow `AGENTS.md` and the shared work mode, source grounding, truth
hierarchy, quality loop and format rules in
[apex-guardrails](../apex-guardrails/SKILL.md). Use `AUTHORING` and
`strategy_markdown` for the review artifact. `SKILL.md` is the behavior
contract; no operational instructions depend on `agents/openai.yaml`.

## Inputs

Required, from supplied text, referenced files or the active context pack:
- `JOB_DESCRIPTION_TEXT`: the particular vacancy; include selection criteria.
- `USER_JOB_HISTORY_TEXT`: the applicant's actual roles and contributions.

Use when available:
- `JOB_REQUIREMENT_TEXT` and `JOB_QUALIFICATION_QUESTIONS`.
- `USER_CERTIFICATIONS_TEXT`: exact credentials, issuer, award date and
  validity/expiry status where applicable; may already appear in job history.
- `USER_LANGUAGES_TEXT`: language levels and evidence of actual use or
  assessments; may already appear in job history.
- `PORTAL_SKILL_ENTRIES`: actual selectable names and types, with IDs if
  supplied. A `SKILLS_TAXONOMY` is authoritative for portal choices only
  when its provenance says it is that portal's list.
- `PORTAL_PROFICIENCY_GUIDANCE`: actual choices and item-specific help,
  especially the meaning of proficiency for certification entries.
- `PORTAL_ENTRY_MODE`: `CONTROLLED_LIST`, `FREE_TEXT` or `UNKNOWN`.
- `PORTAL_FIELD_SCHEMA` or a supplied vacancy-specific ATS page map: actual
  skill/language fields, choices and required properties. This schema takes
  precedence over the default two-column proficiency output below.
- `SKILL_CERTIFICATION_LANGUAGE_EVIDENCE_MAP`: the current vacancy's Phase
  1.4 map, supplied separately or embedded under `Skill / Certification /
  Language Evidence Map` in its strategy report, plus related Phase 7.5
  follow-up when available.
- Approved feedback updates, evidence bank, strategy report and companion
  application documents for grounding and consistency.
- Field-specific limits and the requested output location.

If either required source is unavailable, identify the missing source and
return a focused input checklist rather than assigning personal ratings.
Do not require a strategy report, taxonomy, certification or language list
to make progress when the vacancy and job history are available. Missing
optional details hold only the affected entries.

## Reuse of Phase 1.4 and Phase 7.5

Use the map produced by
[apex-candidate-evidence-bank](../apex-candidate-evidence-bank/SKILL.md) as
an evidence index. Match its vacancy and source scope to the current inputs,
and verify referenced applicant facts before relying on them. Reuse its
personal-action details, language activities, credential status and item
IDs in the evidence section. **This skill remains the sole owner of final
portal proficiency recommendations**; Phase 1.4 records capability without
assigning levels.

Apply the current approved feedback patch and Phase 7.5 decisions to affected
items. Check whether supplied sources resolve an earlier gap before asking
again. A previously supported item may need review if its sources or the
vacancy changed. Do not inherit a rating or a claim just because someone
inserted it into the map. Use current raw sources and flag stale/conflicting
parts; do not overwrite the strategy report or silently absorb its edits.

If the map is absent, incomplete, stale or for another vacancy, continue
from the JD and job history using the selection steps below. Fill missing
analysis within this deliverable without rerunning the whole strategy
pipeline. A missing taxonomy still permits explicitly draft labels.
Unresolved entries remain in the confirmation section while supported
entries proceed. Phase 1.4 coverage supplements the top 5–7 requirement
summary; ensure other relevant vacancy skills, certifications and languages
are not lost when selecting the final entries.

For World Bank, use `TARGET_SYSTEM: OTHER` with explicit World Bank context
under the existing repository enumeration; do not rewrite the context pack
just to run this generator. Default to **High / Medium / Low** for this
World Bank workflow. If another portal supplies a different scale, preserve
that scale and its definitions rather than imposing the World Bank labels.
When the employer or its scale is unspecified and no supplied schema establishes
that a proficiency field is absent, still draft High / Medium /
Low recommendations, clearly marked as an advisory scale to verify; do not
describe those choices as confirmed portal options.

## Selection and proficiency

### Portal schemas without an overall proficiency field

When the supplied live schema has no proficiency field, do not add a
High / Medium / Low value to the portal-ready output. For an IOM form with
Skill, Years of Experience and Skill Type, prepare those exact columns;
use its observed skill-type choices and distinguish a free-text Skill
input from a catalog used only as label guidance. Preserve verified taxonomy
provenance. Calculate years only from supported relevant intervals without
double-counting overlapping roles; use Option 6's evidence if available,
not its relevance scores. If intervals or required rounding are uncertain,
hold that years value for clarification instead of inferring it from a rating.

For language controls with separate Reading, Writing and Speaking fields,
retain those dimensions and exact allowed levels. Native and Study Language
are separate factual controls, not proficiency conversions. Do not translate
High/Medium/Low or CEFR mechanically into Advanced/Expert or other portal
labels. Keep unsupported dimensions in the review section. Do not create a
certification row/editor when the verified portal has no corresponding field;
note an observed supporting-document destination separately if applicable.

For these schemas, replace the default entry table with the observed columns
and retain the evidence and confirmation sections. The generic rating rubric
below applies only where a proficiency recommendation is actually requested.

1. Extract atomic requirements and relevant duties. Preserve required vs
   preferred status, logical alternatives and combinations such as “at
   least three areas.” Treat background descriptions as context, not
   automatically as applicant requirements.
2. Prioritize required technical skills, languages and credentials, then
   recurring duties and supported desirable capabilities. Include distinct
   relevant entries without a fixed quota or redundant synonyms.
3. Map each candidate entry to evidence: role/organization, period or
   source locator, personal action, independence, complexity and results.
   Dates may be unknown without preventing a rating when capability is
   otherwise clear; do not invent dates, durations or recency.
4. Match supplied portal names exactly. Use a genuine equivalent only if
   its scope matches the evidence; keep the JD phrase and mapping rationale
   in the review table. If no accurate controlled-list choice exists, hold
   the item instead of inventing a selectable name. If no portal list is
   available, propose concise factual names and mark them as draft labels.
5. Assign proficiency from demonstrated capability using portal definitions
   first. Without definitions, use the advisory rubric below and state
   that it is an internal recommendation, not an official WBG standard.

| Proficiency | Advisory interpretation for skills |
|---|---|
| High | Repeatedly delivers demanding work independently, resolves unfamiliar problems, and can explain or guide the approach. |
| Medium | Performs standard professional tasks independently; needs help with unfamiliar or more difficult work. |
| Low | Has introductory knowledge or limited practice and needs substantial guidance. |

High does not require a management title or global-expert status. Do not
infer a rating from years, seniority, JD importance, keyword frequency,
training attendance or Option 6's 3/2/1 relevance scores. Do not force a
distribution across levels. Specific evidence can justify High; modest
wording is not a reason to under-rate it. Missing evidence is **unknown**,
not Low. If only exposure is supported, rate that narrower capability when
an accurate label exists; otherwise hold it.

Preserve action ownership: overseeing a team using a tool does not prove
personal operation of it. Distinguish transferable skills from Bank-specific
procedures. Job relevance may change across vacancies; the same supported
capability should not receive a higher rating just because it is required.

## Language and certification decisions

**Language:** assess the actual reading, writing, listening and speaking
needed for the role. Strong evidence of professional use across these
activities can support High under the advisory rubric; independent routine
use with support for demanding tasks can support Medium; demonstrated
basic use can support Low. An uneven profile needs a defensible overall
rating for the stated job tasks or a targeted clarification. Do not infer
language from nationality, name, duty station or employer, and do not assume
an English CV proves English proficiency. Preserve supplied test results or
CEFR levels as evidence without inventing an official portal conversion.

**Certification:** verify that the named credential is actually earned;
record issuer and known dates/status in the evidence section. Training,
exam preparation and a credential award are different facts. Do not infer
current validity or continuing certification solely from an old award date.
Follow credential-specific portal instructions when supplied. If a
compulsory proficiency field has no defined meaning for credentials, show
the verified credential under items requiring confirmation with
`[Confirm how this certification's proficiency must be rated]`. Continue
the skill and language entries. Do not equate held = High, in progress = Low,
or possession with proficiency in the associated discipline. That discipline
can be a separate skill only when independently supported by work evidence.

For World Bank-specific questions about these distinctions, read
[World Bank context and sources](references/world-bank-context.md). It
provides source boundaries, not an official three-level scoring system.

## Updates and unresolved claims

Apply the intent gate and Candidate Assertion Ledger in `AGENTS.md` to
ad-hoc facts and substantive strategy-report changes. Generate/update this
document when requested; an unrelated pasted assertion does not authorize
regeneration. Check changed claims against baseline sources rather than
treating an edited strategy report as proof of itself.

If `private/inputs/user_feedback_updates.md` is present, use only its
`APPROVED_UPDATES` as additive factual input. Keep
`UPDATES_REQUIRING_CONFIRMATION` visibly tagged and `HOLD_AS_PLACEHOLDER`
unresolved in the confirmation section. Exclude facts under
`DO_NOT_INTEGRATE_UNTIL_RESOLVED` from the entry table.

Use `SUPPORTED`, `UNSUPPORTED_BUT_PLAUSIBLE`, `CONFLICTING` and `AMBIGUOUS`
for evidence status. The latter three belong in the confirmation section
with the precise missing fact or conflict. A supported skill with an
unknown required level is also held there; do not substitute Low for uncertainty.
Retain an entry if independent, non-conflicting evidence supports both its
name and rating without relying on the unresolved claim.

## Deliverable

Write to the user's requested or active workflow output destination. For a
standalone run without one, use `private/output/09_skills_proficiency.md`.
Do not overwrite a different vacancy's artifact; follow the active naming
convention or add its job identifier to the filename. If the user requests
chat-only output, return it there instead.

For a verified schema without an overall proficiency field, apply the schema
override above to the entry table and the checks below; do not hold an otherwise
supported entry merely because a nonexistent rating is absent. State the actual
field basis instead of an overall proficiency basis.

Begin with the vacancy identifier/title and two concise notes: the label
basis (supplied portal list, permitted free text, or unverified draft names)
and the proficiency basis (supplied definitions or advisory assessment).
For an all-draft list, mark draft status in this note. For mixed label
sources, identify each entry's label status by ID in the evidence section.
Keep draft markers out of the item-name and proficiency cells.

### 1. Skill / Certification / Language and Proficiency

| ID | Type | Skill / Certification / Language | Proficiency |
|---|---|---|---|
| E1 | Skill / Certification / Language | Item name only | One allowed proficiency value |

One distinct item per row, ordered by JD priority, with supported entries
only and one allowed proficiency value each. IDs and Type are review aids,
not extra portal fields. Copy only the item name and chosen proficiency
into the corresponding portal controls. Keep citations, rationale and
confirmation tags outside those two values. An empty table is acceptable
when nothing can be rated; state the reason without fabricating rows.

### 2. Evidence and JD alignment

| Entry ID | JD criterion / priority | Evidence and source | Rating basis |
|---|---|---|---|
| E1 | Required / preferred / relevant duty, with criterion | Role, date or source locator; concrete contribution | Short evidence-based justification |

Provide concise observable evidence, not hidden deliberations or invented
numeric scores. Distinguish an authored rating from a self-reported level.
Include certification status or language dimensions here where relevant.

### 3. Items requiring confirmation

| Item or unmet criterion | Evidence status / issue | What is needed |
|---|---|---|
| Affected item | Claim status, unknown rating, missing portal match or credential interpretation | Precise fact, choice or field definition to confirm |

Include unsupported required criteria as gaps, unresolved assertions and
unverified portal labels here; if none remain, write `None.` Unverified
names may still appear as explicitly draft names in section 1, but an
unsupported proficiency must never appear as a ready choice.

## Final checks

- Each rated entry has a JD connection, accurate scope, source evidence
  and a proficiency justified independently of its importance or tenure.
- Controlled-list names and allowed proficiency values match the inputs.
- No unresolved claim or credential validity assumption entered the table.
- Existing CV, cover letter and answers do not contradict the entries;
  flag discrepancies here rather than silently editing those documents.
- Do not run strict field lint on the Markdown review tables. Use
  [capel-fit](../capel-fit/SKILL.md) only for explicit numeric limits on
  editable text fields; never shorten controlled labels or proficiency
  choices to satisfy an unrelated context-wide character limit.

Return a concise completion note and artifact link. Treat generated rows
as proposed application content, not evidence of submission or acceptance.
