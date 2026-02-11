---
name: capel-fit
description: Normalize and fit text to a strict character limit and target band using deterministic scripts. Use this utility skill for per-entry CAPEL length control in Admin Profiles and qualification answers.
---

# capel-fit

## Purpose

This skill provides deterministic character counting and automatic
adjustment utilities to ensure that text fits within specified
character limits (with spaces) while meeting target bands. It is
script‑backed to guarantee precise counts and reproducible behavior.

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
