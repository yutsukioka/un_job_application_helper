---
name: apex-bullet-enhancer
description: Enhance 2–3 existing job history bullet points by rewriting them using strong action verbs, quantifiable outcomes, and integration of high-priority JD terms (from TERM_EXTRACTOR). Use this skill only when the user requests example bullet enhancements (Phase 2.3). Do not generate the final Admin Profile or CV directly.
---

# apex-bullet-enhancer

## Purpose

This skill rewrites a small set of existing bullets or sentences from the candidate's 
`USER_JOB_HISTORY_TEXT` into stronger, result‑oriented versions.

It is an **example generator** used to demonstrate how to:
- strengthen verbs,
- clarify scope,
add metrics (or placeholders),
integrate high-priority JD terminology naturally.

Important:
- TERM_EXTRACTOR star ratings are an internal prioritization signal.
- When this skill says "use a high-priority term," use the **term text** (do not paste star symbols into the enhanced bullet).

## Shared definitions

Apply the expert lens, collaboration rules, guardrails, quality loop
protocol, and guiding principles defined in `apex-guardrails`.

Use `strategy_markdown` formatting (headings and bullets allowed). This output is not intended to be pasted directly into strict application fields.

## Inputs

Required:

- `USER_JOB_HISTORY_TEXT`: the full job history with bullets or descriptive sentences.
- `TERM_EXTRACTOR`: 5 high‑priority terms with star ratings and synonyms.

Optional:

- `JOB_DESCRIPTION_TEXT`: to align vocabulary with the target role.
- `apex-keyword-insertion-map` output: to focus on must-use phrases.

## Output format

Return:

## Bullet Enhancement Examples

Provide 2-3 entries. For each entry:

- **Role** (if identifiable from the user history; otherwise use [Role]):
- **Original:** "<verbatim excerpt>"
- **Enhanced:** "<rewritten bullet>"
- **High-priority term(s) integrated:** term1; term2

Guidance:
- Enhanced bullet should be **one sentence** whenever possible.
- Use placeholders for missing specifics (e.g., [User to Insert Metric], [Tool], [Donor], [Country]).
- Prefer “action + scope + result + metric” structure.

## Rules

- Do not invent responsibilities or achievements, employers, dates, tools, budgets, or metrics. absent from the original text.
- If a metric is missing, add a placeholder rather than guessing.
- Avoid vague starts like “Responsible for…”. Prefer strong verbs (Led, Coordinated, Delivered, Analysed, Negotiated, Streamlined).
- Do not include star symbols (★) inside the Enhanced bullet.

## Steps

1. Select 2–3 bullets/sentences from USER_JOB_HISTORY_TEXT that best align with high-priority JD terms.
2. Rewrite each bullet:
   - Start with a strong action verb.
   - Clarify scope (what, who, where).
   - Add an outcome and metric if present; otherwise add a placeholder.
   - Integrate 1–2 high-priority term(s) naturally (term text only).
3. Output entries in the required format.
