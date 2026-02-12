---
name: apex-generate-cv
description: Generate a comprehensive CV (résumé) in plain text, including header, summary statement, experience, education, and skills/certifications, based on user inputs. Use this skill only when the user selects Option 2 of Phase 8 or explicitly requests a CV. Do not generate other documents in this skill.
---

# apex-generate-cv

## Purpose

This skill constructs a full CV using the candidate’s history and the
recommendations from earlier analysis phases. It follows best
practices for international organizations: clear sections, action
oriented bullets and appropriate keyword embedding.

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

- `USER_JOB_HISTORY_TEXT`: the user’s work experience.
- `JOB_DESCRIPTION_TEXT`: the target role description.

Optional:

- `USER_ADMIN_PROFILE_TEXT`: to align entries with the Admin Profile.
- `TERM_EXTRACTOR`: for keyword insertion.
- `apex-keyword-insertion-map` output.
- `apex-bullet-enhancer` output.
- `apex-uvp-statement` output for the summary statement.

## Output format

Produce plain text with the following sections:

1. **Header:** Candidate’s name and contact details (use placeholders such
   as `[Full Name]`, `[Email]`, `[Phone]` if absent).
2. **Summary Statement:** A concise 1–2 sentence summary derived from
   the UVP (or create one aligned with the role if not available).
3. **Experience:** List roles in reverse‑chronological order. For each
   role:
   - Start with `Job Title, Organization, Location, Dates` on one
     line (use placeholders if information is missing).
   - Follow with 2–4 bullet points highlighting achievements and
     responsibilities, using strong action verbs and quantifiable
     outcomes. Incorporate high‑priority keywords naturally. Each bullet
     should be one concise sentence. Use the enhanced bullets where
     available.
4. **Education:** If provided in the inputs, list degrees, institutions,
   and graduation years. Use one line per degree.
5. **Skills/Certifications:** Include technical skills, languages, tools
   and certifications relevant to the role. List them in categories if
   helpful. Only include skills present in the user’s inputs or the
   approved taxonomy.

## Rules

- Do not invent job titles, employers, dates or qualifications. Use
  placeholders instead.
- Keep section headings simple (e.g., `Experience`, `Education`, `Skills`).
  Do not include any Markdown headings (e.g., `#`, `##`) in the final
  CV text; the output should be ready to copy-paste into a document.
- Use a dash `-` or bullet point to start each CV bullet.
- Ensure verb tense consistency (past tense for past roles; present for
  current roles).
- Integrate keywords identified in Phase 2 naturally; avoid obvious
  keyword stuffing.
- Maintain a professional and factual tone throughout.
- **Initial acknowledgment:** Begin with "Understood. Generating your
  updated CV based on our strategy. This may take a moment..."
- **Conclusion:** Finish by saying: "Here is the draft of your
  updated CV. Please review it carefully, fill in any placeholders,
  and make any further personal adjustments to ensure it perfectly
  represents you."

## Internal recursive self-evaluation loop (internal only; do not print)

For the generated CV, run a recursive quality loop:

- **Minimum cycles:** 2
- **Maximum cycles:** 5
- **Stopping rule:** You may stop after any cycle >= 2 if all constraints are met and no material improvements remain. Never exceed 5 cycles.

**Each cycle:**

1. Draft the CV content.
2. Verify **factual grounding**: remove anything not supported by inputs; add placeholders where needed.
3. Verify **alignment**: ensure each section explicitly maps to JD requirements and three-star-and-above terms.
4. Verify **format/length constraints**: ensure clear headings and consistent formatting (no markdown headers in output).
5. Revise and tighten for clarity, specificity, and UN-style professionalism.

Do not output the loop, rubrics, or scores.

## Steps

1. Compile the candidate’s roles from `USER_JOB_HISTORY_TEXT` and
   `USER_ADMIN_PROFILE_TEXT` (if provided).
2. Draft the summary statement using the UVP or derive one from the
   strongest matches between the candidate’s experience and the job’s
   needs.
3. For each role, create bullet points using existing descriptions and
   enhanced bullets where available; integrate keywords appropriately.
4. Build the education and skills/certifications sections from
   available data.
5. Output the CV with clear headings and consistent formatting.
