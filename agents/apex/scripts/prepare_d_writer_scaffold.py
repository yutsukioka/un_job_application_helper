#!/usr/bin/env python3
"""Create empty D-writer draft targets and reject pre-populated drafts."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path


DEFAULT_OPTIONS = (
    "option1_admin_profile.md",
    "option2_cv.md",
    "option3_cover_letter.md",
    "option4_qualification_answers.md",
    "option7_motivation_statement.md",
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--outdir", type=Path, required=True)
    parser.add_argument("--role", required=True, choices=("screening-lead", "technical-lead", "ats-format-lead"))
    parser.add_argument("--option", action="append", default=list(DEFAULT_OPTIONS))
    args = parser.parse_args()

    role_dir = args.outdir / args.role
    role_dir.mkdir(parents=True, exist_ok=True)
    failures: list[str] = []
    created: list[str] = []

    for option_name in args.option:
        target = role_dir / option_name
        if target.exists() and target.stat().st_size > 0:
            failures.append(str(target))
            continue
        if not target.exists():
            target.touch()
            created.append(str(target))

    if failures:
        print("D_WRITER_PREFLIGHT_FAILURE: non-empty draft target exists before IMPLEMENT pass 1", file=sys.stderr)
        for path in failures:
            print(f"- {path}", file=sys.stderr)
        return 2

    discussion_dir = args.outdir / "_discussion"
    discussion_dir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "role": args.role,
        "created_or_verified_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "draft_targets": [str(role_dir / option_name) for option_name in args.option],
        "created_empty_targets": created,
        "status": "READY_FOR_INDEPENDENT_DRAFTING",
    }
    manifest_path = discussion_dir / f"d_writer_scaffold_{args.role}.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"D_WRITER_PREFLIGHT_PASS: {manifest_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
