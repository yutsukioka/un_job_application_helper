"""Lightweight relevance scoring for collected jobs.

The scorer takes a "strategy signals" object (loadable from a markdown
strategy report or a JSON file) describing the terms and CCOG codes a user
is targeting, and produces a per-job score plus the reasons that
contributed to it. It is intentionally simple and dependency-free so it
can be invoked from the CLI without bringing in extra packages.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# CCOG codes look like "1.A.06.04" or just "1.A.06"; we accept either.
_CCOG_FULL_RE = re.compile(r"\b\d\.[A-Za-z0-9]\.\d{2}\.\d{2}\b")
_CCOG_FAMILY_RE = re.compile(r"\b\d\.[A-Za-z0-9]\.\d{2}\b")
# term-extractor lines look like "1. Programme Management ⭐⭐⭐⭐⭐"
_TERM_LINE_RE = re.compile(r"^\s*\d+\.\s*([^⭐\n]+?)\s*(?:[⭐]+)?\s*$")
_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9\-]+")


@dataclass(slots=True)
class StrategySignals:
    """Bag of terms / CCOG codes used to score a job's fit."""

    terms: list[str] = field(default_factory=list)
    ccog_codes: list[str] = field(default_factory=list)
    ccog_families: list[str] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not (self.terms or self.ccog_codes or self.ccog_families)


def load_strategy_signals(path: str | Path) -> StrategySignals:
    """Load strategy signals from a JSON or Markdown file.

    JSON shape (optional fields)::

        {
          "terms": ["Programme Management", "Monitoring & Evaluation"],
          "ccog_codes": ["1.A.06.04"],
          "ccog_families": ["1.A.06"]
        }

    Markdown shape: the function scans for term-extractor numbered lines
    (e.g. ``1. Programme Management ⭐⭐⭐⭐⭐``) and any inline CCOG codes.
    """

    raw = Path(path).read_text(encoding="utf-8")
    text = raw.strip()
    if text.startswith("{"):
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            data = {}
        return StrategySignals(
            terms=[str(t).strip() for t in data.get("terms", []) if str(t).strip()],
            ccog_codes=[str(c).strip() for c in data.get("ccog_codes", []) if str(c).strip()],
            ccog_families=[
                str(c).strip() for c in data.get("ccog_families", []) if str(c).strip()
            ],
        )

    terms: list[str] = []
    for line in raw.splitlines():
        match = _TERM_LINE_RE.match(line)
        if match:
            term = match.group(1).strip(" -·:")
            if term and term.lower() not in {t.lower() for t in terms}:
                terms.append(term)
    ccog_codes = sorted({m.group(0) for m in _CCOG_FULL_RE.finditer(raw)})
    ccog_families = sorted({m.group(0) for m in _CCOG_FAMILY_RE.finditer(raw)})
    # Any "full" code implies its family; drop families already covered.
    covered = {".".join(code.split(".")[:3]) for code in ccog_codes}
    ccog_families = [fam for fam in ccog_families if fam not in covered]
    return StrategySignals(terms=terms, ccog_codes=ccog_codes, ccog_families=ccog_families)


def score_job(job: dict[str, Any], signals: StrategySignals) -> dict[str, Any]:
    """Score a single job result against strategy signals.

    Returns a dict with ``score`` (0..1) and ``reasons`` (list[str]).
    """

    if signals.is_empty:
        return {"score": 0.0, "reasons": []}

    reasons: list[str] = []
    raw_score = 0.0

    title = str(job.get("title") or "")
    description = str(job.get("description") or "")
    haystack_title = title.lower()
    haystack_desc = description.lower()

    primary_code = str(job.get("ccog_primary_code") or "")
    family_code = str(job.get("ccog_family_code") or "")
    if primary_code and primary_code in signals.ccog_codes:
        raw_score += 3.0
        reasons.append(f"CCOG primary match: {primary_code}")
    elif family_code and (
        family_code in signals.ccog_families
        or family_code in {".".join(c.split(".")[:3]) for c in signals.ccog_codes}
    ):
        raw_score += 1.5
        reasons.append(f"CCOG family match: {family_code}")

    desc_hits = 0
    for term in signals.terms:
        term_lower = term.lower().strip()
        if not term_lower:
            continue
        if term_lower in haystack_title:
            raw_score += 1.0
            reasons.append(f"Term in title: {term}")
            continue
        if term_lower in haystack_desc:
            desc_hits += 1
            if desc_hits <= 5:
                raw_score += 0.5
                reasons.append(f"Term in description: {term}")

    # Normalize: 3 (CCOG primary) + 5 * 1.0 (top 5 title hits) is a strong
    # ceiling for a typical signals set, so we cap at 8.0 then clamp.
    normalized = min(1.0, raw_score / 8.0)
    return {"score": round(normalized, 4), "reasons": reasons}


def score_jobs(
    jobs: Iterable[dict[str, Any]],
    signals: StrategySignals,
) -> list[dict[str, Any]]:
    """Annotate each job dict with ``score`` and ``score_reasons``."""

    out: list[dict[str, Any]] = []
    for job in jobs:
        result = score_job(job, signals)
        annotated = dict(job)
        annotated["score"] = result["score"]
        annotated["score_reasons"] = result["reasons"]
        out.append(annotated)
    return out
