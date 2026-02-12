---
name: apex-generate-qualification-answers
description: Draft detailed answers to each job qualification question using the two-part format, with strict 1000-character limits. Use this skill only when the user selects Option 4 of Phase 8 or explicitly requests qualification answers. Do not generate other documents in this skill.
---

# apex-generate-qualification-answers

## Purpose

This skill generates concise yet comprehensive answers to each
application qualification question, following the specific structure
and length constraints defined in the original prompt.

## Shared definitions

Apply the expert lens, collaboration rules, guardrails, quality loop
protocol, internal CAPEL generation technique, guiding principles, and
error handling patterns defined in `apex-guardrails`.

## Inputs

Required:

- `JOB_QUALIFICATION_QUESTIONS`: a list of long‑form application
  questions requiring written answers.
- `USER_JOB_HISTORY_TEXT`: to source relevant experiences.

Optional:

- `apex-candidate-evidence-bank` output for quick reference to
  experience.
- `TERM_EXTRACTOR` or JD to align with keyword requirements.

## Output format

Return each question followed by its answer in plain text. Answers
must follow the two‑part single‑paragraph format:

1. **Part 1:** Start with parentheses indicating the period and
   organization where the experience was gained (e.g., “(2018–2021,
   XYZ Company) – ”). If multiple experiences apply, mention up to two
   separated by semicolons.
2. **Part 2:** Immediately continue with a detailed description of
   how that experience meets the requirement, including what you did,
   how it demonstrates the skill or knowledge asked about, and the
   outcome. Use concise narrative and include metrics where available
   (insert placeholders for missing numbers or specifics).

Each answer must not exceed **1000 characters (with spaces)**. Apply
CAPEL‑style length control internally: aim for ~800–950 characters by
planning an internal word budget (e.g. 150 words) and using placeholders
when necessary. If the answer is too short (below ~800 characters), add
one or two specific details (e.g., tool used, scale, metric) relevant to
the requirement. If it exceeds 1000 characters, tighten the text by
removing filler phrases and combining clauses.

## Example (for pattern reference; do not copy verbatim)

**Question:** Do you have experience in results-based management
and programme monitoring?

**Answer:** (2019-2023, [Organization], [Country]) - Led the design
and implementation of a results-based monitoring framework for a
USD [X]M multi-year programme spanning [N] field locations;
developed programme logic models, indicator tracking matrices, and
quarterly reporting templates aligned with [Framework]; trained [N]
national staff on data collection protocols and quality assurance
procedures, achieving [X]% data completeness across all programme
indicators; introduced real-time dashboards using [Tool] that
reduced reporting cycle time by [X]% and enabled evidence-based
programme adjustments, contributing to a [X]% improvement in
beneficiary targeting accuracy.

## Rules

- Do not start answers with “Yes, I have…” or restate the question.
- Do not refer the reader to other documents. Each answer must stand
  alone.
- Follow the two‑part format strictly—no line breaks or bullet points.
- Use semicolons or concise conjunctions to join sentences when needed.
- Use placeholders like `[User to Insert Specific Metric]` for missing
  data instead of inventing details.
- Under no circumstances should an answer exceed 1000 characters.
- **Required qualifications:** If the question asks for a required
  qualification (e.g., a certain number of years of experience or a
  specific degree), explicitly confirm the candidate meets it and
  provide evidence. For example: "I have over 5 years of experience in
  [field], as demonstrated by [specific role or project]."
- **Desirable qualifications:** If the question asks for desirable
  qualifications like publications or certifications, mention them
  specifically. Provide titles of publications with year and where
  published, or certification names with the granting institution and
  date. For example: "I hold the PMP certification (Project Management
  Professional, 2022, PMI)."
- **Multi-part questions:** If a question allows multiple examples
  (e.g., "describe your experience in A, B, and C"), structure the
  answer to address each part clearly using sentences that signpost
  each area ("In area A, I did XYZ...; in area B, my role was...;
  etc."). Maintain the single-paragraph rule.

## Recursive self-evaluation (internal only; do not print)

Apply the recursive self-evaluation loop protocol from `apex-guardrails`.

**Domain-specific checks for this skill:** verify strict adherence to the
1000-character limit per answer and the two-part format.

## Steps

1. For each question, determine the relevant experience(s) in the
   user’s history.
2. Begin with: "Understood. Drafting targeted answers for your job
   qualification questions. This may take a moment..."
3. Plan the answer with an internal word budget (~150 words). Use
   CAPEL-style countdown to manage length and ensure the most
   important details appear early.
4. Write the two-part answer following the format and rules above.
5. Count characters (with spaces). If exceeding 1000, compress the
   answer by removing filler and combining sentences; if below ~800
   characters, enrich with additional specific details.
6. Present each question with its answer, separated by a blank line
   between pairs.
7. After all answers, add a note: "Here are your drafted responses.
   Please double-check that all dates, employer names, and specifics
   match your actual experience, and edit as necessary."
