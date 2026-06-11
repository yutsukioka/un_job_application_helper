"""Shared small utility helpers used across adapters."""

from __future__ import annotations

import re
from typing import Any

from jobagg.normalize import clean_text

_TAG_RE = re.compile(r"<[^>]+>")
_SCRIPT_STYLE_RE = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.IGNORECASE | re.DOTALL)


def as_bool(value: Any, *, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def as_int(value: Any, *, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def clean_html(html_text: str | None) -> str | None:
    if not html_text:
        return None
    text = _SCRIPT_STYLE_RE.sub(" ", html_text)
    text = _TAG_RE.sub(" ", text)
    return clean_text(text)
