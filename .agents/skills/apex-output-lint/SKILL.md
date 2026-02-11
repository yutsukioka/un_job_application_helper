---
name: apex-output-lint
description: Perform a final compliance check and minimal formatting fixes on application outputs to meet e‑recruitment field constraints (single spaces, ASCII punctuation, no bullets/tabs, correct newline separation). Use this skill only when the user asks to validate or lint output formatting. Do not generate new content.
---

# apex-output-lint

## Purpose

This skill cleans and validates text outputs to ensure they comply
with formatting rules typical of online application fields. It can
remove bullets or tabs, convert fancy punctuation to ASCII, ensure
single spaces, and enforce the correct number of newlines between
items. It may call `capel-fit` for deterministic character counting
where necessary.

## Inputs

Provide the text to be linted. If character limits apply, specify
`CHAR_LIMIT`, `TARGET_LOW`, `TARGET_HIGH` so the skill can delegate to
`capel-fit` for length validation. Otherwise, length validation is not
performed.

## Behavior

1. Normalize punctuation to ASCII (e.g., curly quotes → straight
   quotes, em dash → hyphen, ellipsis → "...").
2. Collapse multiple spaces or tabs into single spaces.
3. Remove bullet or numbering characters at the start of lines where
   not allowed.
4. Ensure that there is exactly one blank line between items when
   required (e.g., between job entries).
5. If character limits are provided, call `capel-fit` to validate and
   adjust the text accordingly.
6. Return the cleaned text. If the user requested a validation report,
   include a short summary of issues found and actions taken.

## When to use

Use this skill as the final step before delivering application
materials when strict formatting constraints apply. Do not use it to
generate new content or change substantive wording.
