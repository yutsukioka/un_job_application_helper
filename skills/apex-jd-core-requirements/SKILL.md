---
name: apex-jd-core-requirements
description: Identify the top 5–7 core requirements from the job description and requirements text, incorporating star weights if available. Use this skill during Phase 1.2 or when the user asks to extract critical requirements. Do not generate other documents.
---

# apex-jd-core-requirements

## Purpose

This skill analyzes the job description and requirements to extract
the most critical skills, experiences and qualifications. These
requirements form the foundation for mapping evidence and tailoring
application materials.

## Shared definitions

Apply the expert lens, collaboration rules, guardrails, quality loop
protocol, and guiding principles defined in `apex-guardrails`.

## Inputs

Required:

- `JOB_DESCRIPTION_TEXT`: the role’s duties and responsibilities.

Optional:

- `JOB_REQUIREMENT_TEXT`: any additional listed qualifications.
- `TERM_EXTRACTOR`: to note star ratings for important terms.

## Output format

Return a Markdown section titled `## Core Requirements (Top 5–7)`.
For each requirement, include:

* **Requirement:** A concise phrasing of the requirement.
* **Why it is core:** A brief explanation referencing where it appears
  in the JD (no long quotes).
* **Star weight:** The star rating (e.g. ★★★★) if the term appears in
  the term extractor; otherwise indicate “not starred / unknown”.

## Selection rules

- Look for responsibilities or qualifications described as “required,”
  “essential” or repeated across sections.
- Prioritize requirements central to the role title and top duties.
- Include gatekeeping qualifications (e.g., degree, years of
  experience, certifications) if present.
- Exclude generic behavioral competencies unless explicitly central to
  this role.

## Steps

1. Scan the job description and requirements for candidate
   requirements (10–15 items).
2. For each candidate requirement, internally assess (do not print):
   a. **Gatekeeping?** Is it stated as "required" or "essential"?
   b. **Repetition?** How many times does it appear across JD sections?
   c. **Star weight?** Does it appear in the term extractor at
      ★★★ or above?
   d. **Role centrality?** Is it directly tied to the role title or
      top-listed duties?
   Assign an internal priority score based on these four factors.
3. Select the top 5–7 requirements, ensuring coverage across domains
   (technical skills, tools, UN frameworks, high‑impact duties,
   essential qualifications).
4. Output each requirement with its justification and star weight.
