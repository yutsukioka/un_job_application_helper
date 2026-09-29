---
name: apex-select-domain-of-expertise
description: Map job-history evidence broadly to UNESCO expertise areas, rank supported experience and prepare exact portal choices together with a consistent Employment History Form. Use for expertise or paired EHF preparation and review; retain another application's own controls when supplied.
---

# Domain of Expertise

Prepare evidence-grounded expertise and linked employment narratives. Follow
repository `AGENTS.md` and the source-grounding, controlled-update and authoring
rules in [apex-guardrails](../apex-guardrails/SKILL.md). Research about the field
does not authorize generating candidate entries or changing earlier outputs.

## Inputs and scope

Use applicant history, a CV, supplied role/project evidence or a supported
publication list. For a vacancy-specific recommendation, use that vacancy's
requirements. A reusable-profile request may proceed from the professional
history alone; identify this broader scope. If applicant evidence is absent,
show the requested taxonomy information and a focused evidence checklist rather
than inventing candidate expertise.

Use an existing evidence bank, Phase 1.4 map or approved feedback patch when
available. Check its applicant/vacancy/source scope and substantive assertions;
do not require a strategy report or the full APEX pipeline for direct use.
Treat a publication's subject and demonstrated contribution as possible domain
evidence, not automatic proof of years of practice. Do not use an existing
portal selection as independent evidence of capability or tenure.

Prefer `USER_JOB_HISTORY_TEXT` and controlled `APPROVED_UPDATES` when supplied.
A previously tailored Admin Profile is a chronology/coverage reference, not
the framing or keyword anchor. Use a CV as factual input when raw history is
unavailable, retaining its uncertainties and source identity.

## Required cooperation with employment history

For UNESCO authoring, invoking this skill **also invokes
[apex-generate-unesco-employment-history](../apex-generate-unesco-employment-history/SKILL.md)**.
Read both contracts and the [shared evidence and linkage contract](references/paired-evidence-contract.md).
Build or refresh one application-scoped evidence ledger, then prepare or
synchronize the expertise map, EHF content and crosswalk in one run. The user
does not need to name both skills or separately approve this normal paired
work. The entry skill coordinates the run; the companion participates once
and does not call back recursively.

Honor explicit narrower scope. Review-only requests review consistency in
both artifacts without rewriting them. Research, taxonomy explanations and
skill-maintenance requests do not create applicant documents. Ad-hoc facts
still pass the existing apply-intent and Candidate Assertion gates. Do not
generate a UNESCO EHF for another organization's expertise controls.

## Exact UNESCO choices

The bundled **13 September 2026** snapshot contains **19 Areas, 420 Subareas,
and four Years of Experience choices**. It is grounded in one complete UNESCO
portal capture. Do not ask the applicant to reconstruct these known choices.
Keep the snapshot date visible; a snapshot does not guarantee future options.

Use [the offline query helper](scripts/query_domain_reference.py) to inspect
branches and validate choices. For a broad authoring run, review the full
verified inventory area by area; a focused review may inspect only affected
branches. Its default reference resolves within this skill:

```sh
python3 scripts/query_domain_reference.py
python3 scripts/query_domain_reference.py --area 38743
python3 scripts/query_domain_reference.py --area 38743 --subarea-id 41155 --experience-id 40052
python3 scripts/query_domain_reference.py --validate-rows proposed_rows.json
```

Paths in these examples are relative to the skill folder; resolve them before
execution. `--area` accepts an exact label or ID. The last command checks a JSON
array of rows containing `area_id`, `subarea_id`, and `experience_id`; supplied
`area_label`, `subarea_label`, and `experience_label` must also match. Helpers
validate option identity and structure, not the applicant's factual eligibility.

Read [the compact reference](references/unesco-domain-reference-2026-09-13.json)
directly only when needed. For field structure, read
[the expertise schema](references/unesco-expertise-fields-2026-09-13.json).
Read [provenance](references/unesco-taxonomy-provenance.md) when auditing or
refreshing; detailed response bodies and applicant data are not bundled.

The observed optional `Expertise` section has three required controls per row:

| Label | Field ID | Picklist |
|---|---|---|
| Area of Expertise | `AreaExpertise1` | `AreaExpertise` |
| Sub area of expertise | `AreaExpertise2` | `SubArea` |
| Years of Experience | `YearsOfExperience1` | `YearsOfExperience` |

No positive row cap was established; `maxEntries:0` does not prove a zero or
unlimited cap. `inputLength:4000` is not a writing budget for these dropdowns.
The experience labels/IDs are `1-3`/`40051`, `4-7`/`40052`, `8-10`/`40053`, and
`10 +`/`40050`. No Selection is a placeholder, not zero experience. No explicit
under-one-year band was observed.

## Broaden, substantiate and rank

Adapt the method of
[apex-generate-competency-mapping](../apex-generate-competency-mapping/SKILL.md),
using this skill's verified Area/Subarea reference instead of `SKILLS_TAXONOMY`.
Do not inherit Option 6's two-heading format, top-10–12 limit, generic skill
types or whole-role duration shortcut. Its separate invocation is not required.

1. Examine every supplied role and the full verified taxonomy for evidenced
   responsibilities, personal actions and attributable outputs, including
   skills outside the immediate vacancy. Record a disposition for each
   reviewed pair: mapped, adjacent/duplicate alternative, or not established;
   leave unreviewed branches explicit if coverage is incomplete. Broaden the
   evidence inventory without claiming that more portal entries improve
   screening. Interest, an employer's mission and incidental exposure do not
   establish practice.
2. Map supported scope to exact Area/Subarea pairs. Verify IDs, their parent
   relationship and enabled flags with the helper. Keep labels verbatim,
   including portal spelling/case; use synonyms only for matching. A personal
   skills taxonomy, CCOG code, or UNESCO programme sector is not a portal list.
3. Store role-linked evidence and source locators in the shared ledger. Give
   each evidenced role–domain link a relevance score: **3** = central/repeated,
   **2** = regular/important, **1** = occasional/supporting. Scores describe
   use in that historical role, not vacancy importance, proficiency, FTE or
   how many years to credit. Keep actions, results, authority boundaries,
   practice periods and unresolved facts separately traceable.
4. Calculate qualifying practice for each subarea as below. A row is ready
   only when its exact choices, demonstrated scope and experience choice are
   supported. Keep incomplete rows in review while independent ready rows
   proceed. It is acceptable to have no ready rows.
5. Rank domains by the lower bound of counted experience, highest first;
   break ties by upper bound, then exact Area/Subarea labels. Show ranges and
   additional undated practice, with wholly undated rows last, not as zero.
   Document any display conversion to approximate years; it must not determine
   a portal band. Retain all supported mapped domains without a fixed quota.
6. Mark vacancy connection separately as **CORE**, **SUPPORT** or **BROADER**.
   Also list all material vacancy skills and requirements, including those
   without an exact dropdown match or applicant proof. Do not bury a core
   requirement because its evidenced duration is short. Without a JD, report
   profile-wide evidence and leave vacancy priority unassessed.
7. Pass every supported role-linked action/output to the companion EHF skill.
   Verify that the corresponding role's responsibilities or achievements
   express that evidence naturally, using the shared crosswalk. An unresolved
   Years band does not block an otherwise supported narrative action; an
   unresolved role anchor does.

## Experience periods and bands

Record `EXPERIENCE_AS_OF_DATE`, using the assessment date unless another cutoff
is supplied. Record source-linked qualifying intervals, preserving date
precision. A fresh statement that a role is current can support the cutoff;
an old CV's “present” does not establish indefinite ongoing employment. Hold
that interval until available sources establish current status.

Count only evidenced practice periods. Do not allocate an entire career or
role to every subarea when relevant activity occurred in a shorter project.
Union overlapping intervals per subarea. Distinct domains may each use the
same calendar period when actual work supports both; never sum those totals
as additional career years. Do not invent full-time equivalents or UNESCO
rules for part-time work, internships or education.

Use [the interval helper](scripts/calculate_experience.py) for repeatable date
bounds and overlap removal. Read its [input and interpretation contract](references/experience-ledger.md)
when using it. It produces covered-day bounds and merged periods, **not an
automatic portal-band assignment**. It cannot establish whether an interval's
work qualifies. Missing intervals and unresolved current status remain visible.

Map the supported duration to a band only when its interpretation is clear.
Assess the row's total relevant experience, not a convenient subset. If an
identified unresolved relevant period could change that row's band or move it
into a rounding gap, hold its band and retain the verified pair and known
duration in review. A caveat outside a ready row does not resolve its bounded
Years choice. Independently supported rows can still proceed; missing evidence
elsewhere is not a global gate.
Hold exactly ten years (`8-10` and `10 +` overlap textually), under-one-year
experience, and fractional-year boundaries requiring unsupported rounding.
Do not round up to qualify. If uncertain date bounds cross bands, retain the
domain evidence and identify the precise missing fact/interpretation. Do not
substitute Option 9 High/Medium/Low or Option 6 relevance scores for these values.

## Newer or different portal evidence

For another organization, use its own verified configuration; the helper can
accept a compatible `--reference` without changing the bundled UNESCO data.
For newer UNESCO evidence, reconcile exact IDs, labels, parents and enabled
states. Preserve source/date distinctions; do not mix contradictory snapshots
or overwrite the reusable catalog with an applicant's selected subset.

Complete coverage is necessary to claim an exhaustive catalog. It is not a
gate on an individually observed, enabled exact pair: a partial newer capture
can confirm that pair without proving that omitted choices were removed. State
the reference scope. Keep unavailable matches provisional rather than inventing
choices. A separately requested rating must use its own observed scale.

## Deliver and verify

Use the requested destination or the active workflow's naming convention.
Store the shared ledger and crosswalk alongside the paired artifacts, scoped
to this applicant/application and the current source revisions, as specified
in the shared contract. These are task artifacts, not persistent memory or
additions to the reusable taxonomy. Chat-only requests keep the same logical
evidence links without forcing filesystem persistence.

Include:

- **Ready entries:** for UNESCO, exact Area label/ID, Subarea label/ID and Years label/ID.
  IDs are selection/review aids, not invented extra form controls. Keep source
  notes and explanations outside values copied into the three controls.
- **Broad evidence mapping and duration ranking:** skills per role with 3/2/1
  relevance, exact taxonomy category, per-row source locators, supported actions/scope,
  relevant periods, as-of date, overlap treatment, duration/band rationale and
  vacancy connection or profile-wide purpose.
- **Vacancy requirements:** all material skills/requirements, core flags,
  matching evidence and gaps, including requirements outside the taxonomy.
- **Linked EHF content and crosswalk:** the companion's EHF draft or updated
  native form, with exact responsibility/achievement locators for each supported
  role–domain link. Keep review identifiers outside the clean application text.
- **Items requiring clarification:** unsupported, ambiguous or conflicting
  claims, unknown practice dates, unavailable choices, and unresolved bands.

Apply the Candidate Assertion Ledger statuses from `AGENTS.md` to new claims.
Use only `APPROVED_UPDATES` as additive confirmed input. Keep confirmation-tagged
and placeholder sections unresolved; exclude `DO_NOT_INTEGRATE_UNTIL_RESOLVED`
claims from ready entries. Do not silently inherit assertions inserted into a
strategy report. Supported independent evidence may resolve an affected item.

Validate every ready UNESCO row with `--validate-rows`. For another portal,
follow its actual controls; use pair validation without an experience ID if
it has no Years field rather than inventing one. For paired UNESCO file-backed
authoring, run the shared contract's link validator and reconcile the EHF.
For chat-only content, check the same evidence-to-role links without forcing
file creation. Compare supplied CV and screening answers, flagging conflicts
without automatically rewriting those other outputs.
Do not CAPEL-fit or lint controlled labels/IDs as prose. Finish with the artifact
link and any material unresolved items; do not enter or submit profile data.
