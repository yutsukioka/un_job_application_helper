"""Public Oracle text projection shared by parsing and retained-detail merges."""
from __future__ import annotations

import re
from typing import Any

from jobagg.normalize import clean_text


def public_description_parts(summary: Any, *full_parts: Any) -> list[Any]:
    """Keep a distinct teaser, but do not repeat one already in the full body."""
    parts = [part for part in full_parts if part]
    summary_text = re.sub(r"\s", "", clean_text(str(summary or "")) or "")
    full_text = re.sub(r"\s", "", clean_text("\n\n".join(str(p) for p in parts)) or "")
    # These complete teaser values are unfilled source templates, not vacancy
    # prose. Mixed factual/template text is deliberately retained for review.
    # The original response remains in raw; only the public body is projected.
    template_summary = bool(full_text) and summary_text.casefold() in {
        "applyby:dd/mm/yyyy",
        "provideashortsummaryofthejobvacancy",
        "applyby:dd/mm/yyyyprovideashortsummaryofthejobvacancy",
    }
    if summary and not template_summary and not (summary_text and summary_text in full_text):
        parts.insert(0, summary)
    return parts
