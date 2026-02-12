---
name: apex-cover-letter-pointers
description: Provide 2–3 strategic recommendations for crafting a cover letter tailored to the target role. This covers Phase 5 of the Exceptional Candidate workflow. Use this skill only when the user asks for cover letter pointers or when building the complete strategy report.
---

# apex-cover-letter-pointers

## Purpose

This skill generates succinct guidance for the cover letter that builds
on the analysis from earlier phases. Rather than drafting the letter,
it offers high‑level recommendations on narrative theme, gap
addressing, and complementing the CV and Admin Profile.

## Shared definitions

Apply the expert lens, collaboration rules, guardrails, quality loop
protocol, and guiding principles defined in `apex-guardrails`.

## Inputs

Required:

- `JOB_DESCRIPTION_TEXT`: to align the cover letter with the role's
  mission and requirements.
- `USER_JOB_HISTORY_TEXT`: to identify standout experiences and
  personal motivations.

Recommended (for higher quality):

- `apex-candidate-evidence-bank` output: to address gaps
  strategically.
- `apex-uvp-statement` output: for thematic unity across documents.

## Output format

Return a section titled `## Cover Letter Integration Pointers` with 2–3
paragraphs. Each recommendation should focus on one of the following
areas:

1. **Core Narrative Theme:** Suggest a unifying story or theme that
   ties together motivation, UVP and alignment with the job and
   organization’s mission.
2. **Gap Addressing Strategy:** Provide advice on how to proactively
   address any critical gaps identified (e.g. lacking direct
   experience) using transferable skills or a commitment to learn.
3. **Complement, Don’t Repeat:** Remind the user that the cover letter
   should add context and personality, rather than re‑listing CV
   bullet points. Recommend highlighting motivation, cultural fit, or
   values alignment.

## Rules

- Do not draft the cover letter itself. Keep each recommendation
  concise but specific, ideally one short paragraph.
- Ensure suggestions align with the organization’s tone and the
  analysis from earlier phases.

## Steps

1. Identify the candidate’s most compelling motivations and relevant
   experiences.
2. Note any gaps and propose ways to address them positively.
3. Draft 2–3 recommendations following the structure above.
