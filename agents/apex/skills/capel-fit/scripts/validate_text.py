#!/usr/bin/env python3
"""Validate the exact input string without normalization or rewriting.

Exit 0 means within the requested band, 1 outside, and 2 invalid arguments/input.
Use utf16 for an explicitly chosen HTML maxlength check; codepoints otherwise.
Neither counting mode establishes a recruitment server's validation behavior.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys


def validate_text(text: str, char_limit: int, target_low: int = 0,
                  target_high: int | None = None, unit: str = "codepoints") -> dict:
    if not isinstance(text, str):
        raise ValueError("text must be a string")
    high = char_limit if target_high is None else target_high
    if any(type(n) is not int for n in (char_limit, target_low, high)):
        raise ValueError("limits must be integers")
    if not 0 <= target_low <= high <= char_limit:
        raise ValueError("require 0 <= target_low <= target_high <= char_limit")
    if unit not in {"codepoints", "utf16"}:
        raise ValueError("unit must be codepoints or utf16")
    counts = {"codepoints": len(text), "utf16": len(text.encode("utf-16-le")) // 2}
    count = counts[unit]
    status = ("TOO_LONG" if count > char_limit else "TOO_SHORT" if count < target_low
              else "ABOVE_TARGET_HIGH" if count > high else "OK")
    return {"status": status, "within_band": status == "OK", "count": count,
            "unit": unit, "counts": counts, "char_limit": char_limit,
            "target_low": target_low, "target_high": high,
            "normalization": "none", "input_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest()}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--file", type=Path)
    parser.add_argument("--char-limit", type=int, required=True)
    parser.add_argument("--target-low", type=int, default=0)
    parser.add_argument("--target-high", type=int)
    parser.add_argument("--unit", choices=["codepoints", "utf16"], default="codepoints")
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args()
    try:
        # Byte reads preserve CRLF and terminal newlines; read_text would translate.
        raw = args.file.read_bytes() if args.file else sys.stdin.buffer.read()
        result = validate_text(raw.decode("utf-8"), args.char_limit, args.target_low,
                               args.target_high, args.unit)
    except (OSError, ValueError, UnicodeError) as exc:
        print("Invalid text or limits: " + (str(exc) if type(exc) is ValueError else
              "input must be readable UTF-8 text"), file=sys.stderr)
        return 2
    if args.as_json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"STATUS: {result['status']}\nCHARS: {result['count']}\nUNIT: {result['unit']}"
              f"\nCHAR_LIMIT: {result['char_limit']}\nTARGET_BAND: {result['target_low']}-{result['target_high']}"
              "\nNORMALIZATION: none")
    return 0 if result["within_band"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
