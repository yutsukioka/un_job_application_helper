---
name: apex-uvp-statement
description: Produce a concise 1–2 sentence Unique Value Proposition (UVP) statement tailored to the role and organization using only the candidate’s evidence. Use this skill during Phase 4 or when the user asks for a professional summary or branding line.
---

# apex-uvp-statement

## Purpose

This skill synthesizes the candidate’s strongest matches to the job
requirements into a compelling statement of value. It highlights
unique strengths and alignment with the organization’s mission in a
concise way.

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

- `USER_JOB_HISTORY_TEXT`: for evidence of the candidate's skills and
  achievements.
- `JOB_DESCRIPTION_TEXT`: to understand what the role needs.

Recommended (for higher quality):

- `apex-jd-core-requirements` output: to anchor the UVP in the most
  critical requirements.
- `TERM_EXTRACTOR`: to ensure high-star terms appear naturally.

## Output format

Return a single line prefaced by `UVP:` followed by 1–2 sentences
describing why the candidate is an exceptional fit. Use JD language
naturally without keyword stuffing. If a key detail (metric,
technology, scope) is missing, insert a placeholder.

## Example (for pattern reference; do not copy verbatim)

UVP: A results-driven programme management specialist with [X]+
years leading multi-stakeholder humanitarian and development
initiatives across [N] countries, combining deep expertise in
[Domain A] and [Domain B] with a proven record of delivering
measurable outcomes in complex, resource-constrained environments.

## Rules

- Do not invent experiences; base the statement on actual evidence.
- Keep it concise—no more than two sentences.
- Clearly tie the candidate’s strengths to the role’s critical needs.

## Steps

1. Identify 2–3 major matches between the candidate’s experience and
   the job’s highest priority requirements.
2. Compose a 1–2 sentence statement capturing these matches and
   conveying the candidate’s unique value, using confident and
   impactful language.
3. Insert placeholders for missing specifics if needed.
4. Prefix the line with `UVP:` and output it.
