---
name: apex-star-story-blueprints
description: Generate 3–4 STAR story blueprints tied to critical job requirements using the candidate’s evidence. Each blueprint includes a condensed, one-line achievement bullet that can be reused in CV bullets, INSPIRA_FIELD, UNICEF_FIELD, IOM_RA(on Achievements field), qualification answers, and cover letter paragraphs.  Use this skill during Phase 3 or when the user asks for STAR examples. Do not draft cover letters here.
---

# apex-star-story-blueprints

## Purpose

This skill creates structured STAR (Situation, Task, Action, Result)
blueprints to help the candidate articulate compelling examples that
align with the most important role requirements.
It also produces a condensed “achievement bullet” per story that can be reused across Phase 8 outputs.

## Shared definitions

Apply the expert lens, collaboration rules, guardrails, and quality loop
protocol defined in `apex-guardrails`.
Output format profile: `strategy_markdown`.

## Inputs

Required:

- `USER_JOB_HISTORY_TEXT`: to source real examples.
- `JOB_DESCRIPTION_TEXT`: to identify the requirements.

Optional:

- `TERM_EXTRACTOR`: to prioritize high‑starred requirements.
- `JD_KEYWORD_BANK`: an expanded 20–40 phrase keyword bank.
- `apex-candidate-evidence-bank` output: for quick evidence lookup.
- `apex-jd-core-requirements`: the top 5–7 core requirements from the job description and requirements text, and separately list any explicit knockout/eligibility criteria (degree, years, languages, licenses).

## Output format

Return:

## STAR Story Blueprints (3–4)
For each blueprint, include:
- **Targeted requirement:** the specific competency or requirement that are emphasized in the job description, including high star ratings (★★★ or above) in the `TERM_EXTRACTOR`.
- **Keywords to weave:** (term text only)
- **Relevant experience selected:** identify the role or project from the user’s history to base the story on.
- **STAR blueprint:** a breakdown with headings:
  - Situation: (1–2 sentences)
  - Task: (1 sentence)
  - Action: (3–5 bullets)
  - Result: (1–2 sentences;quantify if supported; otherwise placeholders)
- **Tailoring note:** a one‑sentence suggestion on how to align the
  language and tone with the organization’s mission or context.

## Example (for pattern reference; do not copy verbatim)

* **Targeted requirement:** Results-Based Management
* **Keywords to weave:** results framework, M&E, indicators
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

## Rules

- Use only select requirements for which there is clear evidence in the
  user's history. Use placeholders for missing metrics; do not invent metrics or context.
- Prefer examples with measurable outcomes or clear deliverables; where the user's impact is evidenced by metrics or significant scope (e.g., budget managed, people served, percentage improvements). These make the strongest STAR stories. Otherwise use placeholders.
- Avoid long narratives; keep each section concise.
- Do not include star symbols (★) in the “Keywords to weave” or bullets.

## Steps

1. Select 3–4 high-impact requirements emphasized in the JD.
2. Match each to the strongest evidence instance from the user’s history.
3. Draft STAR components with brevity and clarity.
4. Write one condensed achievement bullet per story suitable for CV/IOM achievements.
5. Add one tailoring note per story focused on JD language mirroring.
