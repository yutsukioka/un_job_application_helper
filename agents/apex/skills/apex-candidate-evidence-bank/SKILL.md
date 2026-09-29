---
name: apex-candidate-evidence-bank
description: >-
  Map applicant evidence to JD requirements and identify gaps in Phase 1.3.
  Prepare the Phase 1.4 Skill / Certification / Language Evidence Map for
  Option 9, recording demonstrated capability, language use and credential
  status without assigning final proficiency. Use during strategy-report
  preparation or when explicitly asked for the evidence map.
---

# apex-candidate-evidence-bank

## Purpose

This skill creates a reusable inventory of evidence linking the
candidate’s experience to each core requirement of the target role.
It also identifies critical gaps and proposes 1–2 proactive
mitigation strategies for each gap, such as leveraging transferable
skills, highlighting certifications or personal projects, or creating
targeted mini‑portfolio pieces.

Additionally, it produces an “employment record readiness” checklist to support systems like INSPIRA/UNICEF/IOM, which often require:
- supervisory scope / direct reports,
- programme/project scope (budgets, partners, geography),
- and “reason for leaving” per role (kept short and diplomatic).

The same source review also produces **Phase 1.4: Skill / Certification /
Language Evidence Map**, preparing the evidence that
[Option 9](../apex-generate-skills-proficiency/SKILL.md) needs. This is
analysis, not a Phase 8 entry list: final labels and proficiency choices
remain Option 9's responsibility.

## Shared definitions

Apply the expert lens, collaboration rules, guardrails, quality loop
protocol, and guiding principles defined in `apex-guardrails`.
Output format profile: `strategy_markdown`.

## Inputs

Required:

- `USER_JOB_HISTORY_TEXT`: the candidate’s full work history with duties, achievements and results.
- `JOB_DESCRIPTION_TEXT` and/or `JOB_REQUIREMENT_TEXT`: to derive the core requirements.

Optional:

- Output from `apex-jd-core-requirements`: preferred source of identified requirements.
- `TERM_EXTRACTOR`: to flag high‑starred terms.
- `JD_KEYWORD_BANK`: an expanded 20–40 phrase keyword bank.
- `USER_ADMIN_PROFILE_TEXT`: user's current administrative profile.
- `JOB_QUALIFICATION_QUESTIONS`: additional explicit screening criteria.
- `USER_CERTIFICATIONS_TEXT` and `USER_LANGUAGES_TEXT`, if supplied separately
  from job history.
- `PORTAL_SKILL_ENTRIES`, `PORTAL_ENTRY_MODE` and
  `PORTAL_PROFICIENCY_GUIDANCE`: actual choices, provenance and item-specific
  instructions, if available. A personal `SKILLS_TAXONOMY` is not a verified
  portal list without that provenance.
- Approved feedback updates and an earlier map for the **same vacancy**,
  for targeted refreshes and consistent item IDs.

Missing optional inputs do not prevent the map. Treat each missing or
conflicting detail as an issue for the affected item, not the whole report.

## Output format

Return the following four sections. For an explicit Phase 1.4-only request,
return only the fourth section. During a full strategy run, build all four
in one source review; the orchestrator places the fourth under Phase 1.4
without invoking the evidence bank a second time.

1. `## Evidence Bank by Requirement`
   - For each core requirement, output:
     - **Requirement:** the requirement text (with star weight if known).
     - **Screening language to mirror:** from JD phrases / term text only.
     - **Strong evidence:** 2–5 brief snippets from the user’s
       history that demonstrate this requirement.
        - <Role tag>: <1 concise evidence snippet> Tag each snippet with the role title from which it originates. Use bullet points or short sentences.
     - **Gap / Missing proof:** list any aspects of the requirement
       that are not directly evidenced.
        - <placeholder(s) for missing proof>expressed as placeholders (e.g., “metric for X”, “experience with [tool]”).
     - **Mitigation strategies:** 1–2 concrete suggestions for how
       the candidate can demonstrate or develop this requirement,
       based on transferable skills, certifications, personal
       projects or other experiences mentioned elsewhere.
       - <specific actions the candidate can take or angles to emphasize> Suggestions should be actionable and specific (e.g., “Highlight your online course project using the [tool],” “Reference your volunteer project managing [budget]”).

2. `## Metrics & Specifics Needed`
   - A consolidated bullet list of all missing specifics the user should supply
   - Metrics (%, #, time saved)
   - Scope (budget, partners, geography, caseload)
   - Tools/Systems
   - Supervision (direct reports)
   - Compliance/Framework references (if the JD expects them)
  This helps ensure all missing specifics are captured in one place.
3. `## Employment Record Fields Checklist (Per Role)`

For each role identified in `USER_JOB_HISTORY_TEXT`, output:

- Role:
- Direct reports (number/type): <known or [Confirm]>
- Budget/resource scope: <known or [Confirm]>
- Stakeholders/partners: <known or [Confirm]>
- Location/duty station: <known or [Confirm]>
- Contract type (if relevant): <known or [Confirm]>
- Reason for leaving: <short standard phrase OR "Select one: option1 / option2 / option3">

### 4. Skill / Certification / Language Evidence Map (Phase 1.4)

Use the exact output heading `## Skill / Certification / Language Evidence Map`.
Identify the vacancy and source inputs used. In the full strategy report,
preserve this heading inside Phase 1.4 (heading depth may change).

Scan the full JD, requirements and supplied screening questions for distinct
relevant skills, certifications and languages, including items outside the
top 5–7 summary. Preserve required/preferred status and linked conditions
such as alternatives or “at least three areas.” A capability mentioned only
in organizational background is not automatically an applicant requirement.
Include relevant items with missing evidence so that Phase 7.5 can surface
them; omit unrelated skills and redundant synonyms.
If only partial vacancy text is supplied, state that coverage is limited to
those inputs; do not claim that the full vacancy has been checked.

Use compact records or a table with evidence details. Each item must include:

- **ID:** `SCL-01`, `SCL-02`, etc.; retain IDs for unchanged items when
  refreshing the same vacancy's map.
- **Item / Type:** concise capability, credential or language name;
  `Skill`, `Certification` or `Language`.
- **JD requirement / Priority:** source section or question and the
  relevant wording; required, preferred or relevant duty. Keep compound
  conditions attached to each affected item.
- **Evidence / Source:** actual personal contribution, role/organization,
  known timeframe and source locator. Preserve distinct roles and the
  scope of each contribution; do not combine metrics or infer active
  practice from a role's full duration.
- **Demonstrated capability / Status:** use the type-specific details below.
- **Evidence status:** apply `SUPPORTED`, `UNSUPPORTED_BUT_PLAUSIBLE`,
  `CONFLICTING` or `AMBIGUOUS` to substantive claims under `AGENTS.md`.
  Distinguish supported facts from unknown dimensions in the same record.
  A required item with no applicant evidence is a missing-evidence gap,
  not evidence of Low proficiency.
- **Portal match:** exact supplied label/type, a scope-matched equivalent
  with explanation, or `Unverified draft label` / `No accurate supplied
  match`. Preserve the conceptual item even when no portal equivalent is
  known. Do not invent selectable entries or force keyword matches.
- **Gap / Missing proof:** specific missing fact or field interpretation,
  with a targeted `[Confirm ...]` tag; write `None` when no material gap
  remains. Do not ask the user to reconfirm already supported details.

Type-specific evidence:

| Type | Details to record when evidenced |
|---|---|
| Skill | Personal performance versus oversight; independence and guidance needed; task complexity; repeated use or recency; concrete outputs/results. Distinguish direct from transferable experience and general from Bank-specific procedures. |
| Certification | Exact credential and issuer; earned versus studied toward; award date; known validity, expiry or renewal. Separate holding a credential from applying the discipline. Preserve any undefined compulsory certification-rating field as a field-interpretation gap. |
| Language | Speaking, listening, reading and writing activities relevant to the job; independence/support; professional use and timeframe; supplied test results or self-reported levels, identified as such. Keep uneven abilities visible. |

Do not infer language ability from nationality, workplace or the language
of a CV. Do not infer current certification validity from an old award.
Missing dates or metrics need not prevent documenting otherwise clear
capability; identify only gaps material to the proposed assessment.

Describe capability in observable terms, for example “independently prepared
routine analyses; sought help for unfamiliar methods.” **Do not assign or
recommend High / Medium / Low or another portal proficiency choice here.**
Preserve an existing source assessment as attributed evidence, without
turning it into an authored rating. Do not use Option 6 scores, tenure or
the evidence-ranking engine's scores as proficiency evidence.

Include the map's material gaps in `## Metrics & Specifics Needed`, using
item IDs to avoid repeating the same question. Group language capability,
credential status and portal interpretation gaps separately from numerical
metrics. In a Phase 1.4-only output, keep those gaps in their item records.

The map is a derived index, not a new factual authority. Apply the intent
gate and feedback-patch section restrictions in `AGENTS.md`; newly edited
map/report claims require the same evaluation as other ad-hoc assertions.
Keep unresolved facts visibly tagged. Do not regenerate applicant documents
or write an approval patch as part of this skill.

## Rules

- Extract only evidence explicitly present in the user’s inputs. Do
  not invent employers, dates, tools, budgets or outcomes.
- Use `apex-jd-core-requirements` output when available; otherwise
  extract the core requirements directly from the JD text.
- When no evidence exists for a requirement, leave the “Strong
  evidence” section blank and focus on the gap and mitigation.
- Express missing metrics, tools, scope, or stakeholder detail as
  bracketed placeholders.
- Mitigation strategies must derive from the candidate’s existing
  background (e.g., transferable skills, related certifications,
  personal projects) and should be specific to the requirement.
- Keep mitigation strategies realistic and grounded in the candidate's
  actual background.
- Keep each snippet concise—one sentence or phrase. Use the role
  title to tag the evidence (e.g., “Project Manager: led team of
  X”).

## Steps

1. Determine the list of core requirements (from `apex-jd-core-requirements` output if provided; otherwise extract 5-7 requirements from the JD/requirements text).
2. For each requirement, scan the job history to find matching
   experiences. Collect up to 2–5 strong evidence snippets(tagged by role).
3. For each evidence snippet, internally classify strength
   (do not print):
   - **Strong**: direct match with metrics or significant scope.
   - **Moderate**: related experience or transferable skill.
   - **Weak/Gap**: no direct evidence found.
   Use this classification to determine whether the "Strong evidence"
   section should be populated or left blank.
4. Identify gaps where the requirement is not fully met and express
   missing proof as bracketed placeholders.
5. For each gap, propose 1–2 mitigation strategies drawing on
   transferable skills, certifications, personal projects or other
   relevant experiences from the user's history.
6. Compile a unified list of all placeholders the user needs to
   supply.
7. Build the “Employment Record Fields Checklist” per role (Direct Reports, scope, reason for leaving options).
8. Build the Phase 1.4 map from the full vacancy and applicant sources using
   the contract above; retain supported evidence beyond the top 5–7 summary.
   If only Phase 1.4 was requested, skip the other output sections.
9. Check traceability, type-specific evidence and gaps. Leave final
   proficiency assessment to Option 9 and gap follow-up to
   [apex-user-feedback-revision](../apex-user-feedback-revision/SKILL.md).
