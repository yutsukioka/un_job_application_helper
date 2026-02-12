---
name: apex-generate-competency-mapping
description: Create a competency mapping document listing skills per job with relevance scores and calculating total experience per skill across roles, also assigning skill types from the provided taxonomy. Use this skill only when the user selects Option 6 of Phase 8 or explicitly requests a competency mapping document.
---

# apex-generate-competency-mapping

## Purpose

This skill analyzes the candidate’s job history and skills taxonomy to
produce two sections: a per‑job mapping of skills with relevance scores,
and an aggregated summary of total experience and skill type for each
skill.

## Shared definitions

Apply the expert lens, collaboration rules, guardrails, quality loop
protocol, internal CAPEL generation technique, guiding principles, and
error handling patterns defined in `apex-guardrails`.

## Inputs

Required:

- `USER_JOB_HISTORY_TEXT`: including roles, duties and achievements
  along with dates for each job.
- `SKILLS_TAXONOMY`: the list of skills the user wants to use for
  mapping.

Optional:

- `JOB_DESCRIPTION_TEXT`: to help interpret which skills may be
  relevant.

## Output format

Divide the document into two sections with exactly these headings:

1. `## Job title and skills`
   - For each job, output two lines:
     * **Job Title:** `Job Title — Organization — Dates` (include
       organization and dates if provided).
     * **Skills:** a comma‑separated list of skills used in that role
       with relevance score ≥ 1, ordered by relevance (all 3’s first,
       then 2’s, then 1’s). Limit to the top 10–12 skills per role.
   - Insert a blank line between each job entry.

2. `## Total years of experiences per skills with Skill Type`
   - For each unique skill identified (score ≥ 1 across any job), output
     three lines:
     * **Skills:** `<SkillName>`
     * **Total years of experiences:** `<X years Y months [Z days]>`
     * **Skill Type:** `<CategoryName>` (choose from Adaptation,
       Communication, Hard Skill, Leadership, Persuasion, Problem
       Solving, Soft Skill, Teamwork, Time Management).
   - Insert a blank line between each skill entry.

Conclude with a note prompting the user to review and adjust details if
needed.

- **Initial acknowledgment:** Begin with "Understood. Generating your
  competency mapping document based on your job history and provided
  skills taxonomy. This may take a moment..."
- **Conclusion:** After presenting all jobs and the aggregated skills,
  add: "Here is your competency mapping document. Please review the
  skills and experience durations for accuracy, and adjust any details
  as needed to ensure it reflects your actual experience."

## Rules

- Only list skills from the provided taxonomy. Do not infer skills not
  listed.
- Assign relevance scores strictly:
  - 3: Core skill central to the role or repeated often.
  - 2: Regular skill used frequently but not the primary focus.
  - 1: Occasional skill used in a supporting manner.
  - 0: Not used (do not list).
- Determine relevance based on explicit evidence in the job description
  and the candidate’s role description; if uncertain, err on the side
  of lower scores.
- When calculating total experience for each skill, use role dates to
  sum durations; do not double‑count overlapping periods. If dates are
  imprecise (year or year and month only), provide conservative
  estimates. Express durations in years and months; include days only
  when precise data is available.
- Choose the most appropriate Skill Type from the taxonomy categories
  for each skill.
- Do not include explanatory text or commentary beyond the specified
  headings and lists.

## Recursive self-evaluation (internal only; do not print)

Apply the recursive self-evaluation loop protocol from `apex-guardrails`.

**Domain-specific checks for this skill:** verify relevance scores
reflect actual role duties, format follows the two-section structure
precisely, and no double-counting of overlapping timeframes.

## Steps

1. Parse the job history to extract roles with their timeframes and
   responsibilities/achievements.
2. For each role, match skills from the taxonomy to evidence in the
   duties and achievements. Assign relevance scores according to the
   rubric and list the skills with score ≥ 1.
3. Compile the per‑job list under `Job title and skills`.
4. Aggregate the durations for each skill across roles, avoiding
   double‑counting overlapping timeframes. Estimate durations when
   dates are approximate.
5. Assign a Skill Type to each skill using the provided categories.
6. Output the aggregated summary under the second heading.
7. Add a concluding note reminding the user to review for accuracy and
   completeness.
