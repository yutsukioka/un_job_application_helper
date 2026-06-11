#!/usr/bin/env python3
"""
normalize_text.py

Utilities to normalize text for strict character counting:

* Convert curly quotes and dashes to ASCII equivalents
* Collapse all whitespace (including newlines and tabs) into single spaces
* Strip leading and trailing whitespace

The script is dependency‑free and uses only Python's standard library.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Map common Unicode punctuation to ASCII
UNICODE_TO_ASCII = {
    "\u2018": "'",  # left single quote
    "\u2019": "'",  # right single quote
    "\u201C": '"',  # left double quote
    "\u201D": '"',  # right double quote
    "\u2013": "-",  # en dash
    "\u2014": "-",  # em dash
    "\u2026": "...",  # ellipsis
    "\u00A0": " ",  # non‑breaking space
}


@dataclass(frozen=True)
class NormalizeOptions:
    collapse_whitespace: bool = True
    strip: bool = True
    ascii_punctuation: bool = True


def to_ascii_punctuation(text: str) -> str:
    """Replace Unicode punctuation characters with ASCII equivalents."""
    for src, dst in UNICODE_TO_ASCII.items():
        text = text.replace(src, dst)
    return text


def collapse_whitespace(text: str) -> str:
    """Collapse runs of any whitespace into a single space."""
    return re.sub(r"\s+", " ", text)


def normalize_text(text: str, opts: NormalizeOptions = NormalizeOptions()) -> str:
    """Normalize text according to the provided options."""
    if opts.ascii_punctuation:
        text = to_ascii_punctuation(text)
    if opts.collapse_whitespace:
        text = collapse_whitespace(text)
    if opts.strip:
        text = text.strip()
    return text


if __name__ == "__main__":
    import sys
    content = sys.stdin.read()
    print(normalize_text(content))
