---
name: apex-jd-core-requirements
description: >-
  Identify the top 5–7 core requirements from the job description and requirements text,
  and separately list any explicit knockout/eligibility criteria (degree, years, languages, licenses).
  Use this skill during Phase 1.2 or when the user asks to extract critical requirements.
  Do not generate other documents.
---

# apex-jd-core-requirements

## Purpose

This skill analyzes the job description and requirements to extract
the most critical skills, experiences and qualifications. These
requirements form the foundation for evidence mapping, keyword strategy, and tailoring writing.

This skill also flags explicit knockout/eligibility criteria (when present), since these are
often used as automated screening requirements.

Apply the expert lens, collaboration rules, guardrails, quality loop
protocol, and guiding principles defined in `apex-guardrails`.

## Inputs

Required:

- `JOB_DESCRIPTION_TEXT`: the role’s duties and responsibilities.

Optional:

- `JOB_REQUIREMENT_TEXT`: any additional listed qualifications.
- `TERM_EXTRACTOR`: to note star ratings for important terms.

## Output format

Return the following sections in a Markdown

## Knockout / Eligibility Criteria (if explicitly stated)
- Education:
- Minimum years of experience:
- Languages:
- Other (licenses, certifications, nationality, security clearance, etc.):

If a category is not stated, write "Not explicitly stated."

## Core Requirements (Top 5–7)
For each requirement include:
- **Requirement:** A concise phrasing of the requirement.
- **Why it is core:** A brief explanation referencing where it appears in the JD (no long quotes).
- **Star weight:** From `TERM_EXTRACTOR` if applicable; otherwise indicate “not starred / unknown”.
- **Evidence signals to look for:** 2–4 examples of what would count as strong evidence (e.g., “authored donor reports”, “managed USD budget”, “supervised X staff”)

## Supervision / Management Signals (if stated or strongly implied)
Summarize whether the JD emphasizes:
- supervisory responsibilities,
- team leadership,
- direct reports,
- budget/resource accountability,
- inter-agency coordination.

If not present, write "Not emphasized in JD."

## Selection rules

- Prioritize required/essential language and repeated duties which are central to the role title and top duties.
- Include gatekeeping qualifications (e.g., degree, years of experience, certifications) when explicitly stated.
- Include supervision/management responsibility as a core requirement if emphasized.
- Exclude generic behavioral competencies unless they are explicitly central and repeated in the JD.
- Focus on actual work and screening criteria, not generic soft skills,
  unless the JD clearly emphasizes them.

## Steps

1. Scan the JD and requirements to extract 10-15 candidate requirements.
2. Identify any explicit knockout criteria (education, years, languages, etc.).
3. Score each candidate requirement internally (do not print) for:
   a. **Gatekeeping?** Is it stated as "required" or "essential"?
   b. **Repetition?** How many times does it appear across JD sections?
   c. **Role centrality?** Is it directly tied to the role title or
      top-listed duties?
   d. **Star weight?** Does it allign with `TERM_EXTRACTOR` high-priority terms (if available)
3. Select the top 5–7 requirements, ensuring coverage across
   - technical/functional domain,
   - skills
   - key deliverables,
   - stakeholder coordination,
   - tools/systems (if central),
   supervision/management (if relevant),
   - compliance/policy (if relevant),
   - UN frameworks,
   - high‑impact duties.
4. Output only JD-grounded requirements and knockout criteria; do not invent missing requirements.
