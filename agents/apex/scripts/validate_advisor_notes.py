#!/usr/bin/env python3
"""Validate that advisor notes contain substantive per-advisor review content."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path


RUBBER_STAMP_PHRASES = {
    "no blocker",
    "no blockers",
    "looks good",
    "ready for",
    "ready to",
    "pass",
    "passes",
    "approved",
    "adequate",
    "complete",
    "nothing to add",
}

SUGGESTION_OR_ISSUE_TERMS = {
    "add",
    "avoid",
    "cap",
    "check",
    "clarify",
    "compare",
    "confirm",
    "correct",
    "flag",
    "fix",
    "include",
    "preserve",
    "quantify",
    "remove",
    "replace",
    "revise",
    "risk",
    "shorten",
    "specify",
    "tighten",
    "validate",
    "verify",
}

DOMAIN_TERMS = {
    "ats",
    "capel",
    "ccog",
    "character",
    "claim",
    "competency",
    "coverage",
    "draft",
    "evidence",
    "format",
    "grounding",
    "jd",
    "keyword",
    "ledger",
    "metric",
    "placeholder",
    "qualification",
    "register",
    "requirement",
    "scope",
    "source",
    "technical",
}

WORD_RE = re.compile(r"[A-Za-z][A-Za-z0-9_-]*")


def extract_advisor_text(raw: str, advisor: str) -> str:
    chunks: list[str] = []
    heading = re.compile(
        rf"(?ms)^###\s+{re.escape(advisor)}\s*$\n(?P<body>.*?)(?=^###\s+|\n##\s+|\Z)"
    )
    chunks.extend(match.group("body").strip() for match in heading.finditer(raw))

    bullet = re.compile(rf"(?im)^\s*[-*]\s*{re.escape(advisor)}\s*:\s*(?P<body>.+)$")
    chunks.extend(match.group("body").strip() for match in bullet.finditer(raw))

    tagged = re.compile(rf"(?im)^.*\bfrom['\"]?:\s*['\"]?{re.escape(advisor)}['\"]?.*$")
    chunks.extend(match.group(0).strip() for match in tagged.finditer(raw))

    return "\n".join(chunk for chunk in chunks if chunk)


def substantive_score(text: str) -> dict[str, object]:
    lowered = text.lower()
    words = [w.lower() for w in WORD_RE.findall(lowered)]
    meaningful_words = [
        w for w in words if len(w) >= 4 and w not in {"this", "that", "with", "from", "into", "file"}
    ]
    suggestion_hits = sorted({term for term in SUGGESTION_OR_ISSUE_TERMS if term in words})
    domain_hits = sorted({term for term in DOMAIN_TERMS if term in words})
    rubber_hits = sorted({phrase for phrase in RUBBER_STAMP_PHRASES if phrase in lowered})
    has_specific_anchor = bool(
        re.search(r"\b(option\d|phase\d|metric_ledger|ccog|capel|inspira|unicef|iom|[A-Za-z0-9_-]+\.md)\b", lowered)
    )
    rubber_only = bool(rubber_hits) and not suggestion_hits and len(domain_hits) < 2
    substantive = (
        len(meaningful_words) >= 12
        and (suggestion_hits or has_specific_anchor)
        and len(domain_hits) >= 1
        and not rubber_only
    )
    return {
        "substantive": substantive,
        "meaningful_word_count": len(meaningful_words),
        "suggestion_or_issue_terms": suggestion_hits,
        "domain_terms": domain_hits,
        "rubber_stamp_phrases": rubber_hits,
        "has_specific_anchor": has_specific_anchor,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--advisor-notes", type=Path, required=True)
    parser.add_argument("--advisors", required=True, help="Comma-separated advisor names")
    parser.add_argument("--json", action="store_true", help="Print machine-readable result")
    args = parser.parse_args()

    if not args.advisor_notes.exists() or args.advisor_notes.stat().st_size == 0:
        print(f"FAIL: advisor notes missing or empty: {args.advisor_notes}", file=sys.stderr)
        return 2

    raw = args.advisor_notes.read_text(encoding="utf-8", errors="replace")
    result: dict[str, object] = {
        "advisor_notes": str(args.advisor_notes),
        "ok": True,
        "advisors": {},
    }
    failures: list[str] = []

    for advisor in [a.strip() for a in args.advisors.split(",") if a.strip()]:
        text = extract_advisor_text(raw, advisor)
        score = substantive_score(text) if text else {"substantive": False, "reason": "advisor not found"}
        result["advisors"][advisor] = score
        if not score.get("substantive"):
            result["ok"] = False
            failures.append(advisor)

    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    elif failures:
        print("SUBSTANTIVE_REVIEW_FAILURE: " + ", ".join(failures), file=sys.stderr)
    else:
        print("SUBSTANTIVE_REVIEW_PASS")

    return 0 if result["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
