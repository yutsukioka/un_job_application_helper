---
name: capel-fit
description: Normalize and fit text to a strict character limit and target band using deterministic scripts. Use this utility skill for per-entry CAPEL length control in Admin Profiles and qualification answers.
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

* `normalize_text.py`: converts fancy punctuation to ASCII,
  collapses whitespace and strips leading/trailing spaces.
* `charcount.py`: counts characters (with spaces) after optional
  normalization.
* `fit_entry.py`: validates and optionally auto‑adjusts text to fit
  the specified limits. In auto mode, it applies conservative
  compression (phrase replacements, filler word removal) or expansion
  (adding placeholders) until the text fits.

## Usage

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

1. If numeric limits exist, use the deterministic scripts in this order:
   `normalize_text.py`, `charcount.py`, then `fit_entry.py`.
2. Do not add substantive content; only apply safe normalization,
   conservative compression, or placeholder-based expansion.
3. If `CHAR_LIMIT` is missing or `UNLIMITED`, skip fitting unless the
   user explicitly asks for normalization-only output.
