---
name: apex-keyword-insertion-map
description: Create a list of 5–10 must‑use phrases (from term extractor and job  description) and specify where to insert them in the Admin Profile entries. Use this skill during Phase 2.2 or when the user asks for keyword placement guidance.
---

# apex-keyword-insertion-map

## Purpose

This skill identifies critical keywords and phrases from the term
extractor and job description, then advises where to place them in
each Admin Profile entry to maximize ATS matching and relevance
without keyword stuffing.

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

- `TERM_EXTRACTOR`: a list of keywords with star ratings.
- `JOB_DESCRIPTION_TEXT`: the role description to derive context and
  additional key phrases.

Optional:

- `USER_ADMIN_PROFILE_TEXT` or `USER_JOB_HISTORY_TEXT`: to identify
  which job entries the keywords should be inserted into.

## Output format

Return a section titled `## Must‑Use Phrases (5–10)` with one
entry per phrase. For each phrase include:

* **Phrase:** the exact wording to use.
* **Why:** a brief rationale tied to the JD or star rating.
* **Where to place:** the relevant job entry name(s) and a suggested
  position within the paragraph (e.g., “opening clause”, “final
  sentence”).
* **Natural insertion suggestion:** a short sentence fragment showing
  how to insert the phrase naturally (not a full rewrite).

## Example (for pattern reference; do not copy verbatim)

* **Phrase:** "results-based management"
* **Why:** High-star term; appears 3 times in JD duties section.
* **Where to place:** Programme Manager role entry, opening clause.
* **Natural insertion suggestion:** "...applied results-based
  management principles to design a monitoring framework that..."

## Selection rules

- Blend high‑star terms (especially ★★★ and above) from the term
  extractor with key role‑specific terms from the job description.
- Avoid near‑duplicate phrases; choose unique high‑value ones.
- Limit the list to 5–10 phrases.

## Steps

1. Extract candidate phrases from the term extractor and JD.
2. Score them based on star rating and role importance.
3. Select 5–10 phrases that maximize coverage of critical domains.
4. For each phrase, determine the most relevant job entry (from the
   user’s history) and suggest a logical place to insert it.
5. Output the structured list.
