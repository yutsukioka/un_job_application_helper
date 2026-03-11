---
name: place-holder-checker
description: >-
  Scan generated application documents and strategy reports for unresolved
  placeholders, group findings by file, and write a deterministic
  consolidated report to `0x_place_holders.txt`. Use this skill after one
  or more outputs are generated or whenever the user wants a missing-information sweep.
---

# place-holder-checker

## Purpose

This skill performs a deterministic placeholder sweep across generated application materials.

It identifies unresolved placeholder markers such as:
- `[TBD]`
- `[INSERT ...]`
- `[ADD ...]`
- `[MISSING ...]`
- `[Confirm ...]`
- `[If applicable]`
- `<<EXAMPLE_REQUIRED>>`

and produces a single review artifact named:

`0x_place_holders.txt`

This skill is a QA/reporting utility.
It does not rewrite source files, fill placeholders, infer missing facts, or edit application content.

It complements:
- `apex-candidate-evidence-bank` by consolidating unresolved missing-information markers across downstream outputs
- `apex-cross-doc-consistency` by focusing on unresolved placeholders rather than factual mismatches across documents

## Shared definitions

Apply the expert lens, collaboration rules, guardrails, quality loop protocol, and guiding principles defined in `apex-guardrails`.

If a short user-facing summary is returned in chat, it may use `strategy_markdown`.
The output artifact itself must be plain text.

## When to use

Use this skill when:
- one or more generated outputs already exist and need a placeholder sweep;
- the user asks to find missing information or unresolved placeholders;
- a `phase1_7_strategy_report*.md` file exists and the user wants the `## Metrics & Specifics Needed` items surfaced together with generated-document placeholders;
- the workflow is preparing for final manual review, cross-document QA, or submission.

Recommended run position:
- After Phase 8 document generation
- Before final human review
- Before or alongside `apex-cross-doc-consistency`

## Inputs

Required:
- Repository root or current workspace root

Optional:
- Explicit file list
- Additional scan roots
- Additional filename globs
- Override output path (default: repo root `0x_place_holders.txt`)

## Default scan scope

Unless the caller explicitly overrides the scan scope, inspect these locations in this order:

1. `output/` (recursive)
2. `generated/` (recursive)
3. `applications/` (recursive)
4. repository-root files matching `phase1_7_strategy_report*.md`

Default file extensions to inspect:
- `.md`
- `.txt`
- `.markdown`

Do not scan these locations unless explicitly requested:
- `.agents/`
- `inputs/`
- `.git/`
- `node_modules/`
- `.venv/`
- `__pycache__/`
- hidden OS metadata files
- binary files

## Deterministic scanning logic

1. Build the candidate file list from the default scan scope plus any explicit caller-provided files or globs.
2. Normalize all file paths to repository-relative form.
3. De-duplicate file paths.
4. Sort candidate files deterministically within each scan root.
5. Read each candidate file as plain text.
6. Scan line by line using the placeholder patterns defined below.
7. For each regex match:
   - capture the matched placeholder token;
   - capture short same-line context after the token, trimmed to a concise review string;
   - build a normalized entry:
     - `<placeholder>`
     - or `<placeholder> <same-line context>` when contextual text exists;
   - store the first line number where that normalized entry appears in that file.
8. De-duplicate entries per file by normalized entry text while preserving first-seen order.
9. Write the grouped report to `0x_place_holders.txt`.
10. Do not modify source files.

## Placeholder detection patterns

Detection must be pattern-based only.
Do not rely on semantic inference.

### Primary patterns

Use case-sensitive regex matching for these placeholder classes:

- `$begin:math:display$TBD$end:math:display$`
- `$begin:math:display$INSERT\[\^$end:math:display$\r\n]*\]`
- `$begin:math:display$ADD\[\^$end:math:display$\r\n]*\]`
- `$begin:math:display$MISSING\[\^$end:math:display$\r\n]*\]`
- `$begin:math:display$Confirm\[\^$end:math:display$\r\n]*\]`
- `$begin:math:display$If applicable$end:math:display$`
- `$begin:math:display$User to Insert\[\^$end:math:display$\r\n]*\]`
- `$begin:math:display$PASTE HERE$end:math:display$`
- `$begin:math:display$Select one\[\^$end:math:display$\r\n]*\]`
- `<<[A-Z0-9_ -]+>>`

### Generic fallback

Also treat a standalone square-bracket token as a placeholder when it matches:

- `$begin:math:display$\[\^$end:math:display$\r\n]{1,80}\]`

Apply these exclusion rules to the generic fallback:
- ignore a match immediately followed by `(` because it is likely Markdown link text;
- ignore a match immediately preceded by `!` because it is likely Markdown image syntax;
- ignore matches whose inner content is numeric only, for example `[1]`.

The generic fallback exists to catch unresolved user-facing fields such as:
- `[Full Name]`
- `[Org Name]`
- `[Dates]`
- `[Country]`
- `[Hiring Manager Name]`

## Output artifact

Create or overwrite:

`0x_place_holders.txt`

Default location:
- repository root

## Output format

Write plain text using exactly this structure:

```text
PLACEHOLDER REPORT
Generated: <ISO timestamp>
Scanned roots: <comma-separated roots/globs>
Scanned files: <N>
Files with placeholders: <M>

FILE: <repo-relative-path>
--------------------------------
- L<line> | <placeholder entry>
- L<line> | <placeholder entry>

FILE: <repo-relative-path>
--------------------------------
- L<line> | <placeholder entry>

SUMMARY
--------------------------------
Unique files with placeholders: <M>
Unique placeholder entries: <K>
