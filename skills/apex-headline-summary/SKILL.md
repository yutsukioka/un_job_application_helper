---
name: apex-headline-summary
description: >-
  Generate a one-line role-targeted headline/summary for the Admin Profile
  and CV, optimized for ATS parsing and human screening. This skill
  implements Phase 2.1 of the Exceptional Candidate Creator methodology.
  Use this skill when the orchestrator reaches Phase 2.1 or when the user
  explicitly asks for a headline optimization.
---

# apex-headline-summary

## Purpose

This skill produces a single, compelling headline that summarizes the
candidate's professional identity and alignment with the target role.
The headline is used at the top of the Admin Profile and as the CV
summary seed. It integrates high-priority keywords naturally and
positions the candidate for both ATS parsing and human screening.

Apply the expert lens, collaboration rules, guardrails, quality loop
protocol, and guiding principles defined in `apex-guardrails`.

## Inputs

Required:

- `USER_JOB_HISTORY_TEXT`: to identify the candidate's core strengths
  and career trajectory.
- `JOB_DESCRIPTION_TEXT`: to align the headline with the target role's
  requirements and language.

Optional:

- `TERM_EXTRACTOR`: to prioritize high-star keywords in the headline.
- `apex-jd-core-requirements` output: to ensure the headline addresses
  the most critical requirements.
- `apex-candidate-evidence-bank` output: to anchor the headline in the
  candidate's strongest evidence.

## Output format

Return a single line in the format:

```
Headline: <one-line role-targeted summary>
```

The headline should be 10-20 words, written as a noun phrase or a
concise statement (not a full sentence). It must:

- Name the candidate's primary professional domain.
- Reference the target role's key requirement or sector.
- Include at least one high-priority keyword from the term extractor.
- Avoid generic language; be specific to this candidate and this role.

## Example (for pattern reference; do not copy verbatim)

```
Headline: Senior Programme Management Specialist with 12+ years leading multi-sector UN development initiatives across [N] countries
```

## Rules

- Do not invent qualifications or experience; use placeholders for
  missing specifics (e.g., `[N] years`, `[sector]`).
- Keep the headline to a single continuous line (no line breaks).
- Use professional, confident language appropriate for international
  organizations.
- Integrate keywords naturally; do not stuff.

## Recursive self-evaluation (internal only; do not print)

Apply the recursive self-evaluation loop protocol from `apex-guardrails`.

**Domain-specific checks for this skill:** verify the headline is
10-20 words, includes at least one high-priority keyword, and aligns
with the target role's primary requirements.

## Steps

1. Review the candidate's strongest qualifications and career theme
   from `USER_JOB_HISTORY_TEXT`.
2. Identify the target role's primary domain and top requirements from
   `JOB_DESCRIPTION_TEXT` and `TERM_EXTRACTOR`.
3. Draft a headline that bridges the candidate's strengths with the
   role's needs.
4. Integrate at least one high-star keyword naturally.
5. Verify length (10-20 words) and specificity.
6. Output the headline in the required format.
