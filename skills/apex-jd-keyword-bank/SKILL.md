---
name: apex-jd-keyword-bank
description: >-
  Extract an expanded 20–40 phrase keyword bank from JOB_DESCRIPTION_TEXT and JOB_REQUIREMENT_TEXT,
  grouped by category (technical/functional, deliverables, stakeholders, tools/systems, compliance/frameworks).
  Use this skill to complement term_extractor when deeper ATS phrase coverage is needed.
---

# apex-jd-keyword-bank

## Purpose

This skill produces a larger, structured "JD Keyword Bank" (20–40 phrases) to support:
- ATS semantic matching (more coverage than 5-term extraction),
- keyword insertion mapping across multiple documents/fields,
- consistent terminology across Admin Profile, CV, cover letter, and system-specific fields.

This is not a resume/CV generator. It does not rewrite user content; it only extracts
phrases grounded in the JD and suggests safe variants.

Apply the expert lens, collaboration rules, guardrails, quality loop protocol,
and guiding principles defined in `apex-guardrails`.

## Inputs

Required:
- `JOB_DESCRIPTION_TEXT`

Optional:
- `JOB_REQUIREMENT_TEXT` (recommended)
- `TERM_EXTRACTOR` (if available, use it to ensure consistency but do not repeat star symbols here)

## Output format

Return exactly this structure in Markdown:

## JD Keyword Bank (20–40 phrases)

### A) Technical / Functional terms (10–15)
- <phrase>
- ...

### B) Deliverables / Outputs (5–10)
- <phrase>
- ...

### C) Stakeholders / Coordination (5–10)
- <phrase>
- ...

### D) Tools / Systems (3–8)
- <phrase>
- ...

### E) Compliance / Frameworks / Policy language (3–8)
- <phrase>
- ...

### Notes for use (do not paste into application fields)
- 2–4 bullets on how to use the bank without keyword stuffing.

Rules:
- Each phrase must be JD-grounded (explicitly present or clearly implied by repeated language).
- Prefer exact JD phrases when possible.
- Add variants only when common and safe (e.g., acronyms and spelled-out versions if the JD uses them).
- Do not include star symbols in this output.
- Do not invent tools or frameworks not in the JD.

## Steps

1. Parse JD for repeated technical nouns, process phrases, and deliverables.
2. Extract candidate phrases (aim for 40–60), then de-duplicate and select the best 20–40.
3. Group into the five categories above.
4. Add short “Notes for use” focusing on natural placement (opening clause, mid-sentence, closing clause) and avoiding repetition.
