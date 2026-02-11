---
name: apex-bullet-enhancer
description: Enhance 2–3 existing job history bullet points by rewriting them using strong action verbs, quantifiable outcomes, and integration of high‑star keywords. Use this skill only when the user requests example bullet enhancements (Phase 2.3 of the Exceptional Candidate workflow). Do not generate the final Admin Profile or CV directly.
---

# apex-bullet-enhancer

## Purpose

This skill rewrites a handful of existing bullet points from the
`USER_JOB_HISTORY_TEXT` into stronger, result‑oriented versions.
Each original bullet is paired with its enhanced version, illustrating
how to incorporate action verbs, quantifiable metrics (or placeholders)
and ★★☆ or ★★★ keywords from the `TERM_EXTRACTOR`.

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

- `USER_JOB_HISTORY_TEXT`: the full job history with bullet points or
  descriptions.
- `TERM_EXTRACTOR`: list of high‑priority keywords with star ratings.

Optional:

- `JOB_DESCRIPTION_TEXT`: to align the tone and vocabulary of the
  enhancements with the target role.

## Output format

Produce a section titled `## Bullet Enhancement Examples` with 2–3
entries. For each entry:

1. **Original:** the original bullet text excerpt from the user’s
   history.
2. **Enhanced:** the rewritten version, using strong verbs and
   quantifiable outcomes. Include at least one ★★☆ or ★★★ keyword. If
   the original bullet lacks specific metrics, insert a placeholder such
   as `[User to Insert Metric]`.

## Example (for pattern reference; do not copy verbatim)

**Original:** "Responsible for managing the project budget and
reporting to donors."

**Enhanced:** "Managed a USD [X]M project budget across [N] funding
streams, producing quarterly donor reports that achieved a 100%
compliance rate and strengthened partnerships with [Donor Name]."

## Rules

- Do not invent responsibilities or achievements absent from the
  original text. If context is insufficient to produce a strong result,
  shorten the original bullet and add a placeholder instead of
  guessing.
- Keep each enhanced bullet concise—ideally one sentence.
- Maintain professional tone consistent with UN/international vacancy
  bullet style (e.g. “led”, “oversaw”, “delivered”).

## Steps

1. Parse the job history to identify 2–3 bullets or sentences that map
   to high‑priority keywords (★★☆ or ★★★).
2. For each selected bullet, draft an enhanced version with:
   - A strong action verb
   - A quantifiable outcome (use placeholder if needed)
   - Integration of at least one high‑star keyword
   - Formal tone
3. Output the originals and enhanced versions as described.
