---
name: apex-keyword-insertion-map
description: Create a list of must‑use JD phrases and specify where to insert them across Phase 8 outputs (Admin Profile fields, IOM Responsibilities/Achievements, Qualification Answers, CV, Cover Letter). Use this skill during Phase 2.2 or when the user asks for keyword placement guidance. Do not rewrite full documents.
---

# apex-keyword-insertion-map

## Purpose

This skill identifies critical keywords and phrases from:
- `TERM_EXTRACTOR`(5 high-priority terms with variants), and
- the job description(and optional `JD_KEYWORD_BANK`),

then provides a concrete placement plan across the candidate's application outputs.

Key goal:
- Maximize ATS and human screening alignment **without keyword stuffing**.

Important:
- Star symbols (★) may appear in `TERM_EXTRACTOR`, but this skill must not instruct inserting star symbols into any final application text. Use the term text only.

## Shared definitions

Apply the expert lens, collaboration rules, and quality loop protocol defined in `apex-guardrails`.
Output format profile: `strategy_markdown` (headings and bullets allowed).

## Inputs

Required:

- `TERM_EXTRACTOR`: a list of keywords with star ratings.
- `JOB_DESCRIPTION_TEXT`: the role description to derive context and
  additional key phrases.

Optional:

- `JD_KEYWORD_BANK`: a list of an expanded 20-40 phrase keyword bank from the job description.
- `USER_ADMIN_PROFILE_TEXT` or `USER_JOB_HISTORY_TEXT`: to identify which job entries the keywords should be inserted into.
- `LIMITS`: a guidance on `TARGET_SYSTEM`.

## Output format

Return exactly these sections: 

## Must‑Use Phrases (8–12)

For each phrase include:

- **Phrase:** the exact wording to use.
- **Why it matters:** a brief rationale tied to the JD.
- **Safe variants:** optional; only if common and JD-consistent.
- **Where to place:** by output type:
  - Option 1 (INSPIRA/UNICEF Duties/Responsibilities field): <opening / middle / closing clause> 
  - Option 2 (CV): <summary / experience bullets / skills section>
  - Option 3 (Cover Letter): <which paragraph / sentence>
  - Option 4 (Qualification Answers): <which answer(s) and where>
  - Option 5 (IOM Responsibilities bullets): <which bullet theme>
  - Option 5 (IOM Achievements bullets): <which bullet theme>
  - Option 7 (Motivation Statement): <which paragraph / clause>
  - Option 8 (ATS DRA): <duties / responsibilities / achievements>
  - **Natural insertion suggestion:** a short sentence fragment showing how to insert the phrase naturally (not a full rewrite).

## Distribution guidance (avoid keyword stuffing)
- 3-6 bullets on how often to reuse phrases across documents/fields and how to avoid repetition in a single paragraph/field.

## Selection rules

- Prefer exact phrases used in the JD (especially recurring nouns/verbs and deliverable language).
- Combine high-priority terms from TERM_EXTRACTOR with additional JD phrases (deliverables, stakeholders, systems/tools, compliance language).
- Avoid near duplicates (e.g., pick either “results-based management” or “RBM framework design” unless the JD treats them as distinct).
- Do not exceed 12 phrases.

## Rules

- Do not generate full documents or paragraphs; only placement guidance.
- Do not invent tools, frameworks, donors, locations, or systems not present in the JD or user history.
- Do not include star symbols (★) in any phrase or example fragment.
- Avoid near duplicates and keyword stuffing.

## Steps

1. Parse TERM_EXTRACTOR and extract the core term text (ignore star symbols for writing outputs).
2. If JD_KEYWORD_BANK exists, use it to expand phrase coverage; otherwise extract additional phrases directly from the JD.
3. Select 8–12 phrases that maximize coverage of critical domains and minimal redundancy.
4. Map each phrase across the relevant Phase 8 outputs rather than only the Admin Profile family.
5. Provide a short insertion fragment for each phrase.
6. Provide distribution guidance to prevent keyword stuffing.
