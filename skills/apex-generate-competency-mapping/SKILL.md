---
name: apex-generate-competency-mapping
description: >-
  Create a competency mapping document: skills per job with relevance scores, and total experience per skill across roles( no double-counting overlaps), including skill type/category from SKILL_TAXONOMY where available. Option 6 of Phase 8.
---

# apex-generate-competency-mapping

## Purpose

This skill maps the candidate’s job history to a provided `SKILLS_TAXONOMY` and outputs:
1) Skills used in each role with relevance scores (3/2/1)
2) Aggregated total experience per skill (years/months), avoiding double-counting overlaps
3) A skill type/category for each skill (from SKILLS_TAXONOMY when available)

This output is commonly useful for IOM/Oracle Fusion Cloud Recruiting systems and internal consistency checks.

## Shared definitions

Apply the expert lens, collaboration rules, guardrails, quality loop protocol, guiding principles, and error handling patterns defined in `apex-guardrails`.

## Inputs

Required:
- `USER_JOB_HISTORY_TEXT` (must include role dates)
- `SKILLS_TAXONOMY` (the authoritative list of skills; do not invent new skills)

Optional:
- `JOB_DESCRIPTION_TEXT` (to interpret relevance, but relevance must still be grounded in role evidence)

## Output format

Divide the document into two sections with exactly these headings:

1. `## Job title and skills`
   - For each job, output two lines:
     * **Job Title:** `Job Title — Organization — Dates` (use placeholders if missing).
     * **Skills:** a comma‑separated list oin descending relevance, formatted as `SkillName (3)` / `SkillName (2)` / `SkillName (1)`
   - Insert a blank line between each job entry.

2. `## Total years of experiences per skills with Skill Type`
   - For each unique skill with score ≥1 in any job, output exactly three lines:
     * **Skills:** `<SkillName>`
     * **Total years of experiences:** `<X years Y months [Z days]>` (use placeholders if dates are missing)
     * **Skill Type:** `<CategoryName>` (choose from Adaptation, Communication, Hard Skill, Leadership, Persuasion, Problem Solving, Soft Skill, Teamwork, Time Management).
   - Insert a blank line between each skill entry.

Do not add any other headings or narrative text.

## Relevance scoring rubric

- Assign per-role relevance scores based on explicit evidence in that role:
  - 3 = Core skill central to the role; repeatedly used; key outputs depend on it
  - 2 = Frequently used skill; important but not the primary focus.
  - 1 = Occasional skill used; supporting skill.
  - 0 = Not used (do not list).
- Determine relevance based on explicit evidence in the job description
  and the candidate’s role description; if uncertain, err on the side
  of lower scores.

## Total experience calculation rules

- Use role dates to compute durations (years/months).
- Do not double-count overlapping calendar time:
  - If two roles overlap, a calendar month counts once per skill even if the skill appears in both roles.
- If dates are incomplete or missing:
  - Use placeholders for totals (e.g., `[Confirm dates to calculate total]`) rather than guessing.


## Skill Type / Category rules

- Use the category/type provided in SKILLS_TAXONOMY when available.
- If SKILLS_TAXONOMY does not include categories, use one of:
  Adaptation; Communication; Hard Skill; Leadership; Persuasion; Problem Solving; Soft Skill; Teamwork; Time Management.

## Rules

- Only list skills present in SKILLS_TAXONOMY.
- Do not invent dates or durations.
- Do not include commentary outside the two required sections.

## Recursive self-evaluation (internal only; do not print)

Apply the recursive self-evaluation loop protocol from `apex-guardrails`.

**Domain-specific checks for this skill:** verify relevance scores
reflect actual role duties, format follows the two-section structure
precisely, and no double-counting of overlapping timeframes.

## Steps

1. Parse USER_JOB_HISTORY_TEXT to extract roles with their timeframes and
   responsibilities/achievements.
2. For each role:
   - Identify which taxonomy skills are explicitly evidenced in duties/achievements.
   - Assign relevance scores (3/2/1) and list the skills.
3. Aggregate totals 
   - For each skill, sum role durations where that skill appears (score ≥1), avoiding double counting overlaps.
4. Assign Skill Type per skill using SKILLS_TAXONOMY when possible, if SKILLS_TAXONOMY does not include categories, use one of:
 Adaptation; Communication; Hard Skill; Leadership; Persuasion; Problem Solving; Soft Skill; Teamwork; Time Management.
5. Output in the required two-section format only.
