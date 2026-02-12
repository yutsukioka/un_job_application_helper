#!/usr/bin/env python3
"""
fit_entry.py

Validate and optionally auto‑adjust a single paragraph against
character limits and target bands. In report mode, it prints the
status and character counts. In auto mode, it applies conservative
compression or expansion heuristics and outputs the adjusted text.

Important: automatic adjustments are conservative and avoid altering
meaning. However, manual revision is recommended for the highest
quality.

Usage examples:

    echo "text" | python3 fit_entry.py --char-limit 750 --target-low 700 --target-high 750
    python3 fit_entry.py --file draft.txt --char-limit 1000 --target-low 900 --target-high 1000 --mode auto
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Tuple

from normalize_text import normalize_text


# Phrase replacements that shorten without altering meaning
PHRASE_REPLACEMENTS = [
    (r"\bin order to\b", "to"),
    (r"\bwith regard to\b", "about"),
    (r"\bas well as\b", "and"),
    (r"\bis responsible for\b", "leads"),
    (r"\bwas responsible for\b", "led"),
    (r"\bdue to the fact that\b", "because"),
    (r"\bat this point in time\b", "now"),
    (r"\butilize\b", "use"),
]

FILLER_WORDS = [
    "very",
    "highly",
    "really",
    "clearly",
    "actually",
    "basically",
    "significantly",
]


@dataclass(frozen=True)
class Limits:
    char_limit: int
    target_low: int
    target_high: int


def read_text(args: argparse.Namespace) -> str:
    if args.file:
        return Path(args.file).read_text(encoding="utf-8")
    return sys.stdin.read()


def apply_phrase_replacements(text: str) -> str:
    for pattern, repl in PHRASE_REPLACEMENTS:
        text = re.sub(pattern, repl, text, flags=re.IGNORECASE)
    return text


def remove_filler_words(text: str) -> str:
    for w in FILLER_WORDS:
        text = re.sub(rf"\b{re.escape(w)}\b\s+", "", text, flags=re.IGNORECASE)
    return text


def drop_trailing_parenthetical(text: str) -> str:
    return re.sub(r"\s*\([^)]*\)\s*$", "", text).strip()


def smart_truncate_to_limit(text: str, limit: int) -> str:
    """Last resort: truncate to limit and cut at a sentence boundary."""
    if len(text) <= limit:
        return text
    cut = text[:limit].rstrip()
    m = re.search(r"[.!?]\s", cut[::-1])
    if m:
        idx_from_end = m.start()
        forward_idx = len(cut) - idx_from_end - 1
        cut = cut[: forward_idx + 1].rstrip()
    if cut and cut[-1] not in ".!?":
        cut = cut.rstrip(" ,;:") + "."
    return cut


def status_for_count(count: int, limits: Limits) -> str:
    if count > limits.char_limit:
        return "TOO_LONG"
    if count < limits.target_low:
        return "TOO_SHORT"
    if count > limits.target_high:
        return "ABOVE_BAND_OK_IF_ALLOWED"
    return "OK"


def report(text: str, limits: Limits) -> str:
    count = len(text)
    status = status_for_count(count, limits)
    parts = [
        f"STATUS: {status}",
        f"CHARS: {count}",
        f"CHAR_LIMIT: {limits.char_limit}",
        f"TARGET_BAND: {limits.target_low}-{limits.target_high}",
    ]
    if count > limits.char_limit:
        parts.append(f"OVER_BY: {count - limits.char_limit}")
    if count < limits.target_low:
        parts.append(f"UNDER_BY: {limits.target_low - count}")
    if limits.target_high < count <= limits.char_limit:
        parts.append(f"ABOVE_TARGET_HIGH_BY: {count - limits.target_high}")
    return "\n".join(parts)


def auto_adjust(text: str, limits: Limits, max_passes: int = 5) -> Tuple[str, str]:
    notes = []
    t = text
    t = normalize_text(t)
    notes.append("normalized")
    for _ in range(max_passes):
        count = len(t)
        status = status_for_count(count, limits)
        if status == "OK" or status == "ABOVE_BAND_OK_IF_ALLOWED":
            return t, "; ".join(notes)
        if status == "TOO_LONG":
            before = t
            t = apply_phrase_replacements(t)
            if t != before:
                notes.append("phrase_replacements")
            before = t
            t = remove_filler_words(t)
            if t != before:
                notes.append("filler_trim")
            before = t
            t = drop_trailing_parenthetical(t)
            if t != before:
                notes.append("dropped_trailing_parenthetical")
            t = normalize_text(t)
            if len(t) > limits.char_limit:
                t = smart_truncate_to_limit(t, limits.char_limit)
                notes.append("last_resort_truncate")
                return t, "; ".join(notes)
        if status == "TOO_SHORT":
            needed = limits.target_low - len(t)
            if needed <= 0:
                return t, "; ".join(notes)
            addon = " [User to Insert Specific Metric]"
            if len(addon) <= needed:
                t = normalize_text(t + addon)
                notes.append("added_placeholder_metric")
            else:
                notes.append("too_short_report_only")
                return t, "; ".join(notes)
    return t, "; ".join(notes)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--file", type=str, default=None, help="Input file (otherwise stdin).")
    parser.add_argument("--char-limit", type=int, required=True)
    parser.add_argument("--target-low", type=int, required=True)
    parser.add_argument("--target-high", type=int, required=True)
    parser.add_argument(
        "--mode", choices=["report", "auto"], default="report", help="Mode: report or auto"
    )
    parser.add_argument("--print-report", action="store_true", help="Print report in auto mode.")
    args = parser.parse_args()
    limits = Limits(
        char_limit=args.char_limit, target_low=args.target_low, target_high=args.target_high
    )
    raw = read_text(args)
    norm = normalize_text(raw)
    if args.mode == "report":
        print(report(norm, limits))
        print("\n---\nTEXT:\n" + norm)
        return 0
    fitted, notes = auto_adjust(norm, limits)
    if args.print_report:
        print(report(fitted, limits), file=sys.stderr)
        print(f"NOTES: {notes}", file=sys.stderr)
    print(fitted)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
