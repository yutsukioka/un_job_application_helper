---
name: apex-generate-cover-letter
description: Draft a tailored cover letter for the target role using business‑letter formatting, UVP, STAR stories and gap addressing. Use this skill only when the user selects Option 3 of Phase 8 or explicitly requests a cover letter. Do not generate CVs or Admin Profiles in this skill.
---

# apex-generate-cover-letter

## Purpose

This skill writes a complete cover letter that aligns the candidate’s
experience and motivation with the job description and organization.
It follows a standard business letter format and integrates insights
from earlier phases: UVP, STAR stories, gap mitigation and cultural
fit.

## Shared definitions

Apply the expert lens, collaboration rules, guardrails, quality loop
protocol, internal CAPEL generation technique, guiding principles, and
error handling patterns defined in `apex-guardrails`.

## Inputs

Required:

- `JOB_DESCRIPTION_TEXT`: the full job description to understand role
  requirements and organizational mission.
- `USER_JOB_HISTORY_TEXT`: to pull specific examples and evidence.

Optional:

- `apex-uvp-statement` output: for the opening pitch.
- `apex-star-story-blueprints` output: to select examples for body
  paragraphs.
- `apex-candidate-evidence-bank` output: for gap identification and
  mitigation.

## Output format

Return the cover letter as plain text using a standard business letter
format:

1. Date line (e.g., “February 11, 2026”).
2. Hiring manager’s name/title and organization (use placeholders if
   unknown), each on separate lines.
3. Salutation (e.g., “Dear [Hiring Manager Name],”).
4. Opening paragraph: state the role applied for and convey the UVP or
   a compelling one‑line pitch. Mention motivation linked to the
   organization’s mission.
5. Body paragraphs (2–3):
   - Paragraph 1: Address a key requirement with a STAR story,
     highlighting Situation, Task, Action and Result in narrative form.
   - Paragraph 2: Address another requirement or related set of
     requirements, incorporating another example or combining two
     smaller ones. Also explain motivation and alignment with the
     organization’s values or mission.
   - Paragraph 3 (optional): Address any notable gap by framing
     transferable skills or a commitment to develop the missing
     competency.
6. Closing paragraph: Reiterate enthusiasm for the role and the
   contributions the candidate can bring. Invite further discussion and
   thank the reader.
7. Sign‑off: Professional closing (e.g., “Sincerely,”) followed by the
   candidate’s name.

## Rules

- Do not fabricate names, dates, employer details or specifics not
  present in user inputs; use placeholders instead (e.g., “[Hiring
  Manager Name]”).
- Keep paragraphs concise and focused. Maintain a professional and
  enthusiastic tone consistent with the organization’s sector.
- Integrate at least one STAR story aligned to high‑priority
  requirements; insert placeholders for missing metrics or specific
  outcomes.
- Do not repeat bullet points verbatim; provide narrative context.
- **Sign-off:** Use a professional closing ("Sincerely," or "Best
  regards,") followed by the candidate's name. If applicable, indicate
  any enclosures (e.g., "Enclosure: Resume").
- **Initial acknowledgment:** Begin with "Understood. Generating your
  tailored cover letter based on our strategy. This may take a
  moment..."
- **Conclusion:** After the letter text, advise: "Here is the draft of
  your cover letter. Please review it thoroughly, fill in any
  placeholders (e.g., names, dates), and adjust any wording as needed
  to ensure it reflects your voice and enthusiasm."

## Recursive self-evaluation (internal only; do not print)

Apply the recursive self-evaluation loop protocol from `apex-guardrails`.

**Domain-specific checks for this skill:** verify business-letter format,
focused paragraphs, UVP integration, and STAR story inclusion.

## Steps

1. Determine key requirements and select relevant STAR stories.
2. Draft each section of the letter following the format above.
3. Insert UVP in the opening paragraph.
4. Address gaps if present, turning them into positive commitments.
5. Ensure all placeholders are clearly bracketed.
6. Output the complete letter.
