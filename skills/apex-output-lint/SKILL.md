---
name: apex-output-lint
description: >-
   Perform a final compliance check and minimal formatting fixes on application outputs. Supports multiple link profiles (INSPIRA_FIELD, UNICEF_FIELD, IOM_RA) to meet e‑recruitment system constraints. Use this skill only when the user asks to validate or lint output formatting. Do not generate new substantive content.
---

# apex-output-lint

## Purpose

This skill cleans and validates text outputs to ensure they comply
with the formatting rules of online application fields. It normalizes punctuation/whitespace, removes or converts bullets as needed, and can delegate terministic character counting/fitting to `capel-fit` when numeric limits are provided.

This skill is formatting-only. It must not add new accomplishments, claims, employers, or metrics. If critical details are missing, it may preserve or insert bracketed placeholders only when strictly necessary to avoid implying invented facts.

## Inputs

Required:
- Text to lint (the paste-ready field content).

Optional:
- `LINT_PROFILE` (recommended):
  - `INSPIRA_FIELD` (strict single-paragraph field; typically ~1000 chars)
  - `UNICEF_FIELD` (strict field; typically longer e.g., ~2500 chars)
  - `IOM_RA` (Responsibilities/Achievements sections; bullets allowed)
  - `AUTO` (default): infer from the text structure; if uncertain, behave like INSPIRA_FIELD.
- If numeric character limits apply:
  - `CHAR_LIMIT`, `TARGET_LOW`, `TARGET_HIGH`, `WORD_TARGET` (optional)

## Lint Profiles and Constraints

### Profile: INSPIRA_FIELD
Use for strict single text fields.
Hard constraints:
1. **Single paragraph:** Do not use internal line breaks.
2. **ASCII punctuation only:** No curly quotes, em/en dashes, fancy ellipsis, or non-ASCII punctuation. Use straight quotes (`"`/`'`), hyphens (`-`), and three dots (`...`). (straight quotes, hyphens, "..." for ellipsis).
3. **No bullets or tabs:** Do not use lines beginning with "-", "*", or "•".
4. **Single spaces only:** No leading/trailing spaces, no double spaces.
5. **No decorative headings/labels:** The output should be the field content only.

### Profile: UNICEF_FIELD
Default to the same hard constraints as INSPIRA_FIELD unless the user explicitly allows multiple paragraphs.
Hard constraints (default):
1. **Single paragraph:**
2. **ASCII punctuation only:**
3. **No bullets or tabs:**
4. **Single spaces only:**
5. **No decorative headings/labels:**

### Profile: IOM_RA
Use when the system expects separate Responsibilities and Achievements sections.
Allowed:
- **Headings:** of "Responsibilities:" and "Achievements:"
- **Bullets:** Hyphen bullets using "- " only
- **Blank lines:** Blank line between sections
Still required:
- **ASCII punctuation only:**

## Behavior

1. Normalize punctuation:
   - Ccurly quotes to straight quotes
   - Em/en dash to "-"
   - Ellipsis to "...".
2. Normalize whitespace:
   - Convert tabs to single spaces
   - Collapse repeated spaces to a single space
   - Trim leading/trailing spaces
3. Handle line breaks and bullets based on `LINT_PROFILE`:
   - INSPIRA_FIELD / UNICEF_FIELD:
     - Remove internal line breaks (convert to spaces) to enforce one paragraph.
     - Remove leading bullet markers ("-", "*", "•") at line starts.
   - IOM_RA:
     - Preserve line breaks.
     - Convert any bullet glyphs ("•") or "*" bullets into "- " bullets.
4. If numeric limits are provided (CHAR_LIMIT/TARGET_*):
   - Delegate counting/fitting to `capel-fit` for deterministic compliance.
   - If the text cannot be fitted without removing substantive meaning, return best-fit and flag.
5. Validation report (always internal, but may be printed if requested):
   - Run the profile’s checklist and report any remaining violations.
6. Output:
   - Default: return only the cleaned text.
   - If user asks for a report: return cleaned text followed by a short checklist PASS/FAIL summary.

## **Hard Rules**

- Do not invent facts. Do not add new content.
- Do not rewrite substantive content beyond minimal formatting fixes.
- Preserve placeholders of [User to Insert Metric] or [Confirm detail].
- If the user wants to lint CV or cover letter outputs, require them to specify a profile; otherwise recommend not linting document outputs with strict-field rules.

## When to use

Use this skill as the final step before pasting into strict e-recruitment fields(INSPIRA/UNICEF) or before submitting an IOM RA split section.
Do not use it to generate new content, change substantive wording, or improve content quality; use the relevant generation skills for that.
