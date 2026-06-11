#!/usr/bin/env python3
"""Write a sanitized input bundle for independent evaluation servers."""

from __future__ import annotations

import argparse
from pathlib import Path


def extract_section(markdown: str, heading: str) -> str:
    lines = markdown.splitlines()
    capture = False
    block: list[str] = []
    target = f"## {heading}".strip()
    for line in lines:
        if line.strip().startswith("## "):
            if capture:
                break
            capture = line.strip() == target
            continue
        if capture:
            block.append(line)
    return "\n".join(block).strip()


def extract_target_system(markdown: str) -> str:
    limits = extract_section(markdown, "LIMITS")
    for line in limits.splitlines():
        if line.strip().startswith("TARGET_SYSTEM:"):
            return line.split(":", 1)[1].strip().upper()
    return ""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--context-pack", type=Path, required=True)
    parser.add_argument("--output-file", type=Path, required=True)
    args = parser.parse_args()

    raw = args.context_pack.read_text(encoding="utf-8", errors="replace")
    jd_text = extract_section(raw, "JOB_DESCRIPTION_TEXT")
    qualification_questions = extract_section(raw, "JOB_QUALIFICATION_QUESTIONS")
    target_system = extract_target_system(raw)

    if not jd_text:
        raise SystemExit("JOB_DESCRIPTION_TEXT is empty; cannot build independent evaluation input")

    lines = [
        "# Independent Evaluation Input",
        "",
        "This file is the only application-context input permitted for E1/E2.",
        "It intentionally excludes candidate history, strategy reports, metrics, and advisor notes.",
        "",
        "## JOB_DESCRIPTION_TEXT",
        jd_text,
        "",
    ]
    if target_system == "INSPIRA" and qualification_questions:
        lines.extend(["## JOB_QUALIFICATION_QUESTIONS", qualification_questions, ""])

    args.output_file.parent.mkdir(parents=True, exist_ok=True)
    args.output_file.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    print(f"INDEPENDENT_EVAL_INPUT_READY: {args.output_file}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
