---
name: apex-candidate-evidence-bank
description: Build a structured evidence bank mapping the candidate’s job history to the JD core requirements and identify gaps with mitigation strategies. Also produce a consolidated “Metrics & Specifics Needed” list and an “Employment Record Fields Checklist” per role (Direct Reports, scope, stakeholders, etc.). Use this skill for Phase 1.3 when constructing the strategy report.
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

## Output format

Return exactly these sections:

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
## Employment Record Fields Checklist (Per Role)

For each role identified in `USER_JOB_HISTORY_TEXT`, output:

- Role:
- Direct reports (number/type): <known or [Confirm]>
- Budget/resource scope: <known or [Confirm]>
- Stakeholders/partners: <known or [Confirm]>
- Location/duty station: <known or [Confirm]>
- Contract type (if relevant): <known or [Confirm]>
- Reason for leaving: <short standard phrase OR "Select one: option1 / option2 / option3">

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
