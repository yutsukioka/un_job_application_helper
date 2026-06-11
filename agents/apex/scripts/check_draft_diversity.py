#!/usr/bin/env python3
"""Check that D-server option drafts are independently generated."""

from __future__ import annotations

import argparse
import difflib
import json
import re
import sys
from itertools import combinations
from pathlib import Path


DEFAULT_ROLES = ("screening-lead", "technical-lead", "ats-format-lead")


def normalize_for_similarity(text: str) -> str:
    text = re.sub(r"\s+", " ", text.strip())
    return text


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--outdir", type=Path, required=True)
    parser.add_argument("--option", action="append", default=["option1_admin_profile.md"])
    parser.add_argument("--roles", default=",".join(DEFAULT_ROLES))
    parser.add_argument("--threshold", type=float, default=0.95)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    roles = [role.strip() for role in args.roles.split(",") if role.strip()]
    result: dict[str, object] = {"ok": True, "threshold": args.threshold, "comparisons": []}

    for option_name in args.option:
        files: dict[str, Path] = {role: args.outdir / role / option_name for role in roles}
        missing = [str(path) for path in files.values() if not path.exists()]
        if missing:
            result["ok"] = False
            result["comparisons"].append({"option": option_name, "missing": missing})
            continue

        contents = {
            role: normalize_for_similarity(path.read_text(encoding="utf-8", errors="replace"))
            for role, path in files.items()
        }
        for left, right in combinations(roles, 2):
            ratio = difflib.SequenceMatcher(None, contents[left], contents[right], autojunk=False).ratio()
            failure = ratio > args.threshold
            result["comparisons"].append(
                {
                    "option": option_name,
                    "left": left,
                    "right": right,
                    "similarity": round(ratio, 6),
                    "status": "DIVERSITY_FAILURE" if failure else "OK",
                }
            )
            if failure:
                result["ok"] = False

    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    elif result["ok"]:
        print("DRAFT_DIVERSITY_PASS")
    else:
        print("DIVERSITY_FAILURE", file=sys.stderr)

    return 0 if result["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
