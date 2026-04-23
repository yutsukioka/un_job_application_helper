---
name: apex-uvp-statement
description: Produce a concise 1–2 sentence Unique Value Proposition (UVP) statement tailored to the role and organization using only the candidate’s evidence. Designed to be copy/paste ready for a CV summary or cover letter opening. Use this skill during Phase 4 or when the user asks for a professional summary or branding line.
---

# apex-uvp-statement

## Purpose

This skill synthesizes the candidate’s strongest matches to the job
requirements into a concise, high-impact UVP statement. It highlights
unique strengths and alignment with the organization’s mission. The
statement should sound specific, evidence-based, and aligned to the
target role’s mission and deliverables.

## Shared definitions

Apply the expert lens, collaboration rules, guardrails, and quality loop
protocol defined in `apex-guardrails`.
Output format profile: `strategy_markdown` (but output is one paragraph only).

## Inputs

Required:

- `USER_JOB_HISTORY_TEXT`: for evidence of the candidate's skills and
  achievements.
- `JOB_DESCRIPTION_TEXT`: to understand what the role needs.

Recommended (for higher quality):

- `apex-jd-core-requirements` output: to anchor the UVP in the most
  critical requirements.
- `TERM_EXTRACTOR`: to ensure high-star terms appear naturally.
- `JD_KEYWORD_BANK`: an expanded 20–40 phrase keyword bank.

## Output format (STRICT)

Return exactly one paragraph (1–2 sentences), with no prefix label.
Constraints:
- 35–60 words (guideline): Describing why the candidate is an exceptional fit.
- Include 1–2 high-priority JD terms (term text only): Use JD language naturally without keyword stuffing. If a key detail (metric, technology, scope) is missing, insert a placeholder.
- Plain ASCII punctuation
- No line breaks
- No extra commentary before or after the paragraph

## Example (for pattern reference; do not copy verbatim)

A results-driven programme management specialist with [X]+
years leading multi-stakeholder humanitarian and development
initiatives across [N] countries, combining deep expertise in
[Domain A] and [Domain B] with a proven record of delivering
measurable outcomes in complex, resource-constrained environments.

## Rules

- Do not invent years of experiences, degrees, or metrics; base the statement on actual evidence. Use placeholders if needed.
- Keep it concise—no more than two sentences.
- Clearly tie the candidate’s strengths to the role’s critical needs.
- Avoid generic phrases (“hardworking professional”).
- Keep it specific to the target role’s needs (deliverables + context).

## Steps

1. Identify 2–3 strongest matches between the candidate evidence and
   the JD's core requirements.
2. Compose a 1–2 sentence UVP that capturing:
   - names the candidate’s domain,
   - signals the types of deliverables they produce,
   - includes 1–2 JD terms,
   - references scope (geography, stakeholders, budgets) only if supported.
3. Check for invented facts; insert placeholders for missing specifics if needed.
4. Output exactly one paragraph.
