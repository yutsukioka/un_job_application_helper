---
name: apex-generate-admin-profile-ra-split
description: >-
  Generate an alternate Admin Profile where each job entry is split into three
  lines: Job Title, Responsibilities, and Achievements. Use this skill only
  when the user selects Option 5 of Phase 8 or explicitly asks for a split
  responsibilities/achievements profile. Do not generate CVs or other documents
  in this skill.
---

# apex-generate-admin-profile-ra-split

## Purpose

This skill produces an alternate version of the Admin Profile
in which each job entry is divided into separate lines for
responsibilities and achievements, while maintaining the
no‑invention rule and respecting character limits for each line.

## Shared definitions

Apply the expert lens, collaboration rules, guardrails, quality loop
protocol, internal CAPEL generation technique, guiding principles, and
error handling patterns defined in `apex-guardrails`.

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
2. **Responsibilities:** A concise narrative written as a single continuous
   line (no line breaks) describing the key duties and scope of the role.Integrate critical keywords where relevant. Ensure
   this line meets the character limits.
3. **Achievements:** A concise narrative written as a single continuous
   line (no line breaks) detailing the main accomplishments and results, using quantifiable outcomes where
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

## Recursive self-evaluation (internal only; do not print)

Apply the recursive self-evaluation loop protocol from `apex-guardrails`.

**Domain-specific checks for this skill:** verify character limits for
both Responsibilities and Achievements lines and the three-line format
per job entry.

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
