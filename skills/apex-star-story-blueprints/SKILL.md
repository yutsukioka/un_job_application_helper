---
name: apex-star-story-blueprints
description: Generate 3–4 STAR story blueprints tied to critical job requirements using the candidate’s evidence. Use this skill during Phase 3 or when the user asks for STAR examples. Do not draft cover letters here.
---

# apex-star-story-blueprints

## Purpose

This skill creates structured STAR (Situation, Task, Action, Result)
blueprints to help the candidate articulate compelling examples that
align with the most important role requirements.

## Shared definitions

Apply the expert lens, collaboration rules, guardrails, quality loop
protocol, and guiding principles defined in `apex-guardrails`.

## Inputs

Required:

- `USER_JOB_HISTORY_TEXT`: to source real examples.
- `JOB_DESCRIPTION_TEXT`: to identify the requirements.

Optional:

- `TERM_EXTRACTOR`: to prioritize high‑starred requirements.
- `apex-candidate-evidence-bank` output: for quick evidence lookup.

## Output format

For each selected requirement, output:

* **Targeted requirement:** the specific competency or requirement,
  including its star weight if known.
* **Relevant experience selected:** identify the role or project from
  the user’s history to base the story on.
* **STAR blueprint:** a breakdown with headings:
  - Situation:
  - Task:
  - Action:
  - Result:
* **Tailoring note:** a one‑sentence suggestion on how to align the
  language and tone with the organization’s mission or context.

Create 3–4 such blueprints.

## Example (for pattern reference; do not copy verbatim)

* **Targeted requirement:** Results-Based Management
* **Relevant experience selected:** Programme Manager, [Org], [Country]
* **STAR blueprint:**
  - Situation: The programme lacked a coherent M&E framework, causing
    reporting delays across [N] field offices.
  - Task: Design and deploy a unified results framework within
    [timeframe].
  - Action: Developed a logic model aligned to [Framework], trained
    [N] staff on indicator protocols, and integrated real-time
    dashboards using [Tool].
  - Result: Reduced reporting turnaround by [X]%, achieved [Y]% data
    completeness, and received commendation from [Donor].
* **Tailoring note:** Emphasize alignment with the organization's
  commitment to evidence-based programming.

## Selection rules

- Choose requirements that are emphasized in the job description or
  have high star ratings (★★★ or above) in the term extractor.
- Only select requirements for which there is clear evidence in the
  user's history. Use placeholders for missing metrics.
- **Prefer examples where the user's impact is evidenced by metrics or
  significant scope** (e.g., budget managed, people served, percentage
  improvements). These make the strongest STAR stories.

## Steps

1. Identify 3–4 critical requirements to highlight.
2. Match each requirement to a compelling experience from the
   candidate’s history.
3. Draft the STAR blueprint, ensuring each element clearly describes
   the scenario, what was done and what was achieved.
4. Add a tailoring note per blueprint.
