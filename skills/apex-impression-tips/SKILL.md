---
name: apex-impression-tips
description: Provide tone and language recommendations and final review advice to maximize the impression of the candidate’s application materials (Phase 6). Use this skill only when the user asks for impression maximizer tips or when composing the full strategy report.
---

# apex-impression-tips

## Purpose

This skill offers guidance on the overall tone, language and final
polishing steps to ensure that all application materials leave a strong
impression. It covers Phase 6 of the original prompt.

## Shared definitions

Apply the expert lens, collaboration rules, guardrails, quality loop
protocol, and guiding principles defined in `apex-guardrails`.

## Inputs

Required:

- `JOB_DESCRIPTION_TEXT`: to adjust tone recommendations based on the
  organization's sector (public sector, non-profit, tech, etc.).

Recommended (for higher quality):

- `apex-candidate-evidence-bank` output: to reference specific
  strengths and gaps in the tone advice.
- `apex-uvp-statement` output: to ensure tone reinforces the
  candidate's positioning.
- `apex-star-story-blueprints` output: to connect final review
  advice to the candidate's key stories.

## Output format

Return a section titled `## Impression Maximizer Tips` with two
subsections:

1. **Tone & Language:** A paragraph describing the recommended tone
   (e.g. confident and proactive, diplomatic and detail‑oriented) and
   specific language guidance to match the organization type.
2. **Final Review:** A paragraph advising the user to perform a
   thorough final pass for consistency, accuracy and narrative
   cohesion across all documents.

## Rules

- Tailor the tone recommendation to the type of organization (e.g.,
  humanitarian, policy, tech) based on cues from the job description.
- Emphasize the importance of a final review to catch errors and
  maintain coherence.

## Steps

1. Determine organization type or values from the job description
   (e.g., humanitarian agency, development bank, technical body,
   peacekeeping operation).
2. Internally map the organization type to a tone profile
   (do not print):
   - **Humanitarian/field agency**: empathetic, action-oriented,
     evidence of resilience and adaptability.
   - **Policy/HQ body**: diplomatic, analytical, evidence of
     strategic thinking and stakeholder management.
   - **Technical/data role**: precise, methodical, evidence of
     innovation and system-building.
3. Propose the recommended tone and specific language guidance
   matching the profile (e.g., "Use collaborative verbs like
   'facilitated' and 'convened' over directive language").
4. Draft a final review checklist covering:
   - Consistency of names, dates, and job titles across all documents.
   - Narrative cohesion: does the UVP thread through the CV, cover
     letter, and Admin Profile?
   - Gap coverage: are mitigations mentioned where needed?
   - Keyword saturation: are high-star terms present without
     over-repetition?
   - Placeholder sweep: are all `[brackets]` filled or flagged?
5. Output both subsections under the specified heading.
