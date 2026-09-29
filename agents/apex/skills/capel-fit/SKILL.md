---
name: capel-fit
description: Validate exact text or normalize and fit legacy fields to numeric character limits. Use for CAPEL control in application fields; preserve native text and bibliographic metadata with the exact validation path.
---

# capel-fit

## Purpose

This skill provides **deterministic, post-generation** character counting
and adjustment utilities. It is the second half of the CAPEL length
control system:

1. **Internal CAPEL (LLM-side):** During drafting, the LLM uses a word
   budget countdown to approximate the target length. This technique is
   defined in `apex-guardrails` under "Internal CAPEL Generation
   Technique."
2. **capel-fit scripts (this skill):** After the LLM finishes drafting,
   these Python scripts validate the exact character count and apply
   deterministic compression or expansion to fit the text within the
   target band. Scripts guarantee precise counts and reproducible
   behavior.

Generation skills should: *"Draft using internal CAPEL word budget, then
validate with `capel-fit` scripts."*

## Inputs

Provide:

- `CHAR_LIMIT`: maximum characters allowed (with spaces).
- `TARGET_LOW`: lower bound of the desired character band.
- `TARGET_HIGH`: upper bound of the band (≤ CHAR_LIMIT).
- `WORD_TARGET` (optional): internal word budget hint.
- The text to be fitted (via stdin).

## Default behavior

Run the scripts to normalize punctuation and whitespace, count
characters and, if needed, apply conservative compression or expansion
heuristics. The scripts aim to fit the text within the target band and
never exceed the character limit. They use placeholders to expand
under‑length text when needed.

If `CHAR_LIMIT` is missing or `UNLIMITED`, do not fit the text unless the
user explicitly requests normalization only.

## Scripts

This skill includes a `scripts/` directory with:

* `validate_text.py`: validates the exact UTF-8 input without rewriting,
  trimming, punctuation changes, newline conversion or placeholder expansion.
  Reports code-point and UTF-16 counts; the selected unit controls the check.

* `normalize_text.py`: converts fancy punctuation to ASCII,
  collapses whitespace and strips leading/trailing spaces.
* `charcount.py`: counts characters (with spaces) after optional
  normalization.
* `fit_entry.py`: validates and optionally auto‑adjusts text to fit
  the specified limits. In auto mode, it applies conservative
  compression (phrase replacements, filler word removal) or expansion
  (adding placeholders) until the text fits.

## Usage

### Native text and bibliographic metadata

For publication titles, Unicode/native field text, or any instruction to
preserve the exact string, use `validate_text.py` directly. Skip normalization
and automatic fitting. Revise editable narrative deliberately if needed, then
validate again. An over-limit title or identifier must be reported for a
permitted representation; do not truncate it or strengthen contribution verbs.
Keep review counts out of the delivered field value.

```bash
python3 agents/apex/skills/capel-fit/scripts/validate_text.py \
  --file field.txt --char-limit 4000 --unit utf16 --json
```

The default band is 0–CHAR_LIMIT; a maximum creates no minimum writing target.
Use `--target-low`/`--target-high` only for an actual requested band. Exit 0
means inside the band, 1 means outside, and 2 means invalid input/limits.
`--unit codepoints` is the default; choose `utf16` explicitly for an HTML
maxlength check. Report that counting convention without claiming untested
server equivalence. Both count whitespace and trailing newlines as supplied;
prepare the exact final field value rather than counting a decorated report.

### Legacy normalized fields

Invoke this skill when a text block must be fitted to strict
character limits, such as Admin Profile entries or qualification
answers. After manual drafting in another skill, call `capel-fit` to
validate and adjust the text. Do not use it to generate new
substantive content.

Example CLI usage:

```bash
python3 skills/capel-fit/scripts/normalize_text.py < input.txt
python3 skills/capel-fit/scripts/charcount.py < input.txt
python3 skills/capel-fit/scripts/fit_entry.py \
  --char-limit 1000 \
  --target-low 900 \
  --target-high 1000 \
  --mode auto \
  --print-report < input.txt
```

## Rules

1. If numeric limits exist, select the appropriate path. Native/exact text
   uses `validate_text.py` directly. Legacy fields requiring normalized ASCII
   use `normalize_text.py`, `charcount.py`, then `fit_entry.py`. Existing legacy
   CLI defaults are unchanged.
2. Do not add substantive content; only apply safe normalization,
   conservative compression, or placeholder-based expansion where permitted.
   Automatic fitting can change claims or remove qualifications: inspect every
   transformation before accepting it, and never use it on bibliographic data.
3. If `CHAR_LIMIT` is missing or `UNLIMITED`, skip fitting unless the
   user explicitly asks for normalization-only output.
