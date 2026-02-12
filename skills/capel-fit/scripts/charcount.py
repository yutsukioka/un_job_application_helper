#!/usr/bin/env python3
"""
charcount.py

Count characters (with spaces) in the provided text after optional
normalization. This script can read from stdin or a file.

Usage examples:

    echo "Hello world" | python3 charcount.py
    python3 charcount.py --file path/to/text.txt
    python3 charcount.py --no-normalize --file path/to/text.txt
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from normalize_text import normalize_text


def read_text_from_stdin() -> str:
    return sys.stdin.read()


def read_text_from_file(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--file", type=str, default=None, help="Read input from file instead of stdin."
    )
    parser.add_argument(
        "--no-normalize", action="store_true", help="Do not normalize before counting."
    )
    args = parser.parse_args()

    if args.file:
        text = read_text_from_file(Path(args.file))
    else:
        text = read_text_from_stdin()

    if not args.no_normalize:
        text = normalize_text(text)

    # Word's "Characters (with spaces)" metric is simply the length of the string
    count = len(text)
    print(count)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
