---
name: apex-build-context-pack
description: Create or update `inputs/application_context.md` containing all raw inputs (job history, admin profile, job description, requirements, qualification questions, term extractor, skills taxonomy and limits) for the Exceptional Candidate workflow. Use this skill to assemble or refresh the context pack before analysis or generation tasks.
---

# apex-build-context-pack

## Purpose

This skill consolidates all required user inputs into a single
Markdown file (`inputs/application_context.md`) that other skills
reference. It does not perform any analysis or generate documents; it
simply organizes the raw data in a standard structure.

## Inputs

Provide the following data when invoking this skill (either by
pasting directly or referencing existing files in the repo):

1. `USER_JOB_HISTORY_TEXT`
2. `USER_ADMIN_PROFILE_TEXT`
3. `JOB_DESCRIPTION_TEXT`
4. `JOB_REQUIREMENT_TEXT`
5. `JOB_QUALIFICATION_QUESTIONS`
6. `TERM_EXTRACTOR`
7. `SKILLS_TAXONOMY`
8. `LIMITS` (CHAR_LIMIT, TARGET_LOW, TARGET_HIGH, WORD_TARGET)

If any section is missing, use a placeholder block with `[PASTE HERE]` to
indicate that the user must supply it later.

## Output behavior

1. Ensure an `inputs/` directory exists; create it if necessary.
2. Create or overwrite `inputs/application_context.md` with the following
   structure and headings:

   ```
   # Application Context Pack

   ## USER_JOB_HISTORY_TEXT
   ```
   <pasted text or [PASTE HERE]>
   ```

   ## USER_ADMIN_PROFILE_TEXT
   ```
   <pasted text or [PASTE HERE]>
   ```

   ## JOB_DESCRIPTION_TEXT
   ```
   <pasted text or [PASTE HERE]>
   ```

   ## JOB_REQUIREMENT_TEXT
   ```
   <pasted text or [PASTE HERE]>
   ```

   ## JOB_QUALIFICATION_QUESTIONS
   ```
   <pasted text or [PASTE HERE]>
   ```

   ## TERM_EXTRACTOR
   ```
   <pasted text or [PASTE HERE]>
   ```

   ## SKILLS_TAXONOMY
   ```
   <pasted text or [PASTE HERE]>
   ```

   ## LIMITS
   CHAR_LIMIT: <value or [PASTE HERE]>
   TARGET_LOW: <value or [PASTE HERE]>
   TARGET_HIGH: <value or [PASTE HERE]>
   WORD_TARGET: <value or [PASTE HERE]>
   ```

3. After writing the file, run the **Pre-flight Validation** below.

## Pre-flight Validation

Before any downstream skill consumes `inputs/application_context.md`,
verify that all 11 required sections are populated (i.e., contain
substantive text, not just `[PASTE HERE]`). Check each section and
report status:

| # | Section | Status |
|---|---------|--------|
| 1 | `USER_JOB_HISTORY_TEXT` | populated / missing |
| 2 | `USER_ADMIN_PROFILE_TEXT` | populated / missing |
| 3 | `JOB_DESCRIPTION_TEXT` | populated / missing |
| 4 | `JOB_REQUIREMENT_TEXT` | populated / missing |
| 5 | `JOB_QUALIFICATION_QUESTIONS` | populated / missing |
| 6 | `TERM_EXTRACTOR` | populated / missing |
| 7 | `SKILLS_TAXONOMY` | populated / missing |
| 8 | `CHAR_LIMIT` | populated / missing |
| 9 | `TARGET_LOW` | populated / missing |
| 10 | `TARGET_HIGH` | populated / missing |
| 11 | `WORD_TARGET` | populated / missing |

If any section is missing, list it and recommend the user supply the
data before proceeding. Do not allow the orchestrator to run with
missing critical sections (`USER_JOB_HISTORY_TEXT`,
`JOB_DESCRIPTION_TEXT`).

## When to use

Use this skill whenever the raw context needs to be assembled or
refreshed. It should be run before other analytical or generation
skills if the context file is missing or outdated.
