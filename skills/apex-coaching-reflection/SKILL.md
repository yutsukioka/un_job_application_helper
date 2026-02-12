---
name: apex-coaching-reflection
description: Pose 1–2 open‑ended coaching reflection questions to the user based on the target role and organization, encouraging personal insight and interview preparation (Phase 7). Use only when completing the strategy report or when the user asks for reflective prompts.
---

# apex-coaching-reflection

## Purpose

This skill concludes the strategy report by engaging the user with
thought‑provoking questions that help them internalize their narrative
and prepare for interviews.

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

- `JOB_DESCRIPTION_TEXT`: to tailor reflection prompts to the role
  and organization.

Recommended (for higher quality):

- `apex-candidate-evidence-bank` output: to ground questions in the
  candidate's specific gaps and strengths.
- `apex-uvp-statement` output: to connect reflections to the
  candidate's unique positioning.
- Information about the organization’s mission or values, if provided.

## Output format

Return a section titled `## Coaching Reflection Prompts` with 1–2
open‑ended questions written in a conversational tone. Each question
should encourage the candidate to connect personal experiences, values
and motivations to the role and organization.

## Example (for pattern reference; do not copy verbatim)

1. "Reflecting on your years working across [countries/contexts],
   what has been the defining moment that solidified your commitment
   to [sector/mission area]? How would you convey that story to a
   panel in 60 seconds?"
2. "If the hiring manager asked you to describe the one
   accomplishment that most directly prepares you for this role,
   which would you choose and why?"

## Rules

- Keep questions open‑ended and reflective, not yes/no.
- Encourage the candidate to think about genuine motivations, personal
  stories and cultural fit.
- Align the prompts with the organization’s mission, values or sector
  when possible.

## Steps

1. Identify the organization’s field and values from the job description.
2. Craft questions that encourage the user to articulate why they are
   passionate about the role and how their experiences prepare them to
   thrive in that environment.
3. Output 1–2 questions under the specified heading.
