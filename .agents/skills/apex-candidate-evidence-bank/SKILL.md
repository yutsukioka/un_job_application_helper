---
name: apex-candidate-evidence-bank
description: Build a structured evidence bank mapping the candidate’s job history to the JD core requirements and identify gaps with mitigation strategies. Use this skill for Phase 1.3 when constructing the strategy report.
---

# apex-candidate-evidence-bank

## Purpose

This skill creates a reusable inventory of evidence linking the
candidate’s experience to each core requirement of the target role.
It also identifies critical gaps and proposes 1–2 proactive
mitigation strategies for each gap, such as leveraging transferable
skills, highlighting certifications or personal projects, or creating
targeted mini‑portfolio pieces.

## Expert lens (apply internally; do not print)

When generating this output, apply the three-expert perspective
defined in the orchestrator:
- **UN Hiring Manager**: Is the content framed to pass
  competency-based screening?
- **Technical Specialist**: Does terminology align with the role's
  domain and UN-style frameworks?
- **ATS Analyst**: Are keywords integrated naturally for system
  parsing?

Prioritize (1) factual grounding, (2) role alignment,
(3) screening resilience.

## Inputs

Required:

- `USER_JOB_HISTORY_TEXT`: the candidate’s full work history with
  duties, achievements and results.
- `JOB_DESCRIPTION_TEXT` and/or `JOB_REQUIREMENT_TEXT`: to derive
  the core requirements.

Optional:

- `TERM_EXTRACTOR`: to flag high‑starred terms.
- Output from `apex-jd-core-requirements`: to reuse previously
  identified requirements.

## Output format

Produce two main sections in Markdown:

1. `## Evidence Bank by Requirement`
   - For each core requirement, output:
     - **Requirement:** the requirement text (with star weight if
       known).
     - **Strong evidence:** 2–5 brief snippets from the user’s
       history that demonstrate this requirement. Tag each snippet
       with the role title from which it originates. Use bullet
       points or short sentences.
     - **Gap / Missing proof:** list any aspects of the requirement
       that are not directly evidenced, expressed as placeholders
       (e.g., “metric for X”, “experience with [tool]”).
     - **Mitigation strategies:** 1–2 concrete suggestions for how
       the candidate can demonstrate or develop this requirement,
       based on transferable skills, certifications, personal
       projects or other experiences mentioned elsewhere. Suggestions
       should be actionable and specific (e.g., “Highlight your
       online course project using the [tool],” “Reference your
       volunteer project managing [budget]”).

2. `## Metrics & Specifics Needed`
   - A consolidated list of placeholders from all requirements that
     the user should fill (e.g., metrics, scope details, tools,
     dates). This helps ensure all missing specifics are captured in
     one place.

## Rules

- Extract only evidence explicitly present in the user’s inputs. Do
  not invent employers, dates, tools, budgets or outcomes.
- When no evidence exists for a requirement, leave the “Strong
  evidence” section blank and focus on the gap and mitigation.
- Mitigation strategies must derive from the candidate’s existing
  background (e.g., transferable skills, related certifications,
  personal projects) and should be specific to the requirement.
- Keep each snippet concise—one sentence or phrase. Use the role
  title to tag the evidence (e.g., “Project Manager: led team of
  X”).

## Steps

1. Determine the list of core requirements (from
   `apex-jd-core-requirements` or by extracting from the JD).
2. For each requirement, scan the job history to find matching
   experiences. Collect up to 2–5 strong evidence snippets.
3. For each evidence snippet, internally classify strength
   (do not print):
   - **Strong**: direct match with metrics or significant scope.
   - **Moderate**: related experience or transferable skill.
   - **Weak/Gap**: no direct evidence found.
   Use this classification to determine whether the "Strong evidence"
   section should be populated or left blank.
4. Identify gaps where the requirement is not fully met.
5. For each gap, propose 1–2 mitigation strategies drawing on
   transferable skills, certifications, personal projects or other
   relevant experiences from the user's history.
6. Compile a unified list of all placeholders the user needs to
   supply.
7. Output the structured sections as described.
