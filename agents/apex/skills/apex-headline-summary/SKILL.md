---
name: apex-headline-summary
description: >-
  Generate a one-line role-targeted headline/summary for the Admin Profile
  and CV, optimized for ATS and human screening. Used in Phase 2.1. Output is one line only.
---

# apex-headline-summary

## Purpose

This skill produces a single, compelling headline that summarizes the
candidate's professional identity and alignment with the target role.

It is used as:
- the header headline in Admin Profile (where applicable), and/or
- the seed for a CV summary statement.

Important:
- `TERM_EXTRACTOR` star ratings are internal: use the term text, not star symbols, in the headline.

## Shared definitions

Apply the expert lens, collaboration rules, guardrails, quality loop
protocol, and guiding principles defined in `apex-guardrails`.

Default format profile: `strategy_markdown` (but the final output is a
single plain line).

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

## Output format (STRICT)

Return exactly one line containing the headline text only. Do not add
any label prefix.

Constraints:
- 10-20 words
- noun phrase or concise statement (not a full paragraph)
- includes at least one high-priority JD term (term text only)
- no line breaks
- no extra commentary before or after the line

Example (pattern reference only; do not copy):
Senior Programme Management Specialist | Results-Based Management, Partner Coordination Field Operations with 12+ years leading multi-sector UN development initiatives across [N] countries

## Rules

- Do not invent years of experiences, grades, or credentials; use placeholders if needed (e.g., `[N] years`, `[sector]`).
- Avoid generic phrases (“hardworking professional”).
- Use plain ASCII punctuation.

## Steps

1. Identify the candidate’s strongest professional theme from `USER_JOB_HISTORY_TEXT`.
2. Identify the target role’s primary domain and top requirements from `JOB_DESCRIPTION_TEXT` (and `TERM_EXTRACTOR` if provided).
3. Draft a single-line headline bridging the candidate’s strengths to the role’s needs.
4. Verify 10–20 words and no invented facts.
5. Output exactly one line.
