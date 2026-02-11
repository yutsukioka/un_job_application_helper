---
name: apex-generate-admin-profile-ra-split
description: Generate an alternate Admin Profile where each job entry is split into three lines: Job Title, Responsibilities, and Achievements. Use this skill only when the user selects Option 5 of Phase 8 or explicitly asks for a split responsibilities/achievements profile. Do not generate CVs or other documents in this skill.
---

# apex-generate-admin-profile-ra-split

## Purpose

This skill produces an alternate version of the Admin Profile
in which each job entry is divided into separate lines for
responsibilities and achievements, while maintaining the
no‑invention rule and respecting character limits for each line.

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

- `USER_JOB_HISTORY_TEXT`: including duties and achievements for each
  role.
- `JOB_DESCRIPTION_TEXT`: for aligning keywords and tone.

Optional:

- `TERM_EXTRACTOR` and keyword insertion guidance.
- Character limits: `CHAR_LIMIT`, `TARGET_LOW`, `TARGET_HIGH`,
  `WORD_TARGET` for per‑line CAPEL management.

## Output format

For each job, output exactly three lines:

1. **Job Title:** `<Role Title> — <Organization> (<Dates>)` (include
   organization and dates only if provided; otherwise use the role
   title alone).
2. **Responsibilities:** A single sentence describing the key duties and
   scope of the role. Integrate critical keywords where relevant. Ensure
   this line meets the character limits.
3. **Achievements:** A single sentence detailing the main
   accomplishments and results, using quantifiable outcomes where
   possible and placeholders otherwise. Also respect the character
   limits.

Insert exactly one blank line between each job entry for readability.

## Rules

* Do not invent organization names, dates or results; use placeholders
  such as `[Org Name]`, `[Dates]`, `[User to Insert Metric]` as needed.
* **Coverage:** Include every job from USER_JOB_HISTORY_TEXT and/or
  USER_ADMIN_PROFILE_TEXT; do not omit, merge, or skip any roles or
  contracts. Preserve the source chronology (default: newest to oldest,
  unless specified otherwise).
* Use CAPEL-style internal countdown for each Responsibilities and
  Achievements line to fit within the provided character limits. Aim
  for the target band; use placeholders for expansion if too short and
  conservative compression if too long.
* Maintain professional tone and integrate high-priority keywords
  naturally.
* **Initial acknowledgment:** Begin with "Understood. Generating your
  additional Admin Profile with separate responsibilities and
  achievements sections. This may take a moment..."
* **Conclusion:** After listing all roles, add: "Here is the draft of
  your additional Admin Profile with responsibilities and achievements
  separated for each role. Please review each entry carefully, fill in
  any placeholders, and adjust any details to ensure accuracy and
  completeness."

## Internal recursive self-evaluation loop (internal only; do not print)

For the generated split profile, run a recursive quality loop:

- **Minimum cycles:** 2
- **Maximum cycles:** 5
- **Stopping rule:** You may stop after any cycle >= 2 if all constraints are met and no material improvements remain. Never exceed 5 cycles.

**Each cycle:**

1. Draft the split profile entries.
2. Verify **factual grounding**: remove anything not supported by inputs; add placeholders where needed.
3. Verify **alignment**: ensure each section explicitly maps to JD requirements and three-star-and-above terms.
4. Verify **format/length constraints**: ensure strict adherence to character limits for both Responsibilities and Achievements fields.
5. Revise and tighten for clarity, specificity, and UN-style professionalism.

Do not output the loop, rubrics, or scores.

## Steps

1. For each job in the user’s history, extract the title, duties and
   achievements.
2. Draft the Responsibilities line: focus on the main duties, tools and
   scope; integrate keywords; apply `capel-fit` to meet character
   limits.
3. Draft the Achievements line: emphasize outcomes and quantifiable
   results; insert placeholders for missing numbers; apply
   `capel-fit` as needed.
4. Assemble each job entry with the three lines separated by newlines
   and a blank line between jobs.
5. Output the complete split profile.
