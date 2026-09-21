"""Detail-content quality checks shared by sync and consolidation."""

from __future__ import annotations

import hashlib
import re
from typing import Any


DETAIL_QUALITY_COMPLETE = "complete"
DETAIL_QUALITY_EMPTY = "empty"
DETAIL_QUALITY_PLACEHOLDER_ONLY = "placeholder_only"
DETAIL_QUALITY_TOO_SHORT = "too_short"
DETAIL_QUALITY_LIST_ONLY = "list_only"
DETAIL_QUALITY_DETAIL_MISSING = "detail_missing"

INCOMPLETE_DETAIL_QUALITY_STATUSES = {
    DETAIL_QUALITY_EMPTY,
    DETAIL_QUALITY_PLACEHOLDER_ONLY,
    DETAIL_QUALITY_TOO_SHORT,
    DETAIL_QUALITY_LIST_ONLY,
    DETAIL_QUALITY_DETAIL_MISSING,
}

_SPACE_RE = re.compile(r"\s+")
_PLACEHOLDER_TEXT = {
    ".",
    "duties and responsibilities",
    "same as external",
}
_DETAIL_SECTION_MARKERS = (
    "responsibilities",
    "qualifications",
    "requirements",
    "work experience",
    "duties",
    "education",
    "competencies",
    "languages",
    "organizational setting",
    "org. setting",
    "terms of reference",
    "scope of work",
    "deliverables",
)


def detail_quality_status(
    *,
    title: object | None,
    description: object | None,
    raw: dict[str, Any] | None = None,
    detail_status: object | None = None,
    min_detail_chars: int = 80,
) -> str:
    """Classify whether a stored description is useful detail content.

    ``detail_status`` is accepted for caller compatibility, but content quality
    is deliberately independent from queue state. A pending backlog item can
    still contain useful list/detail text, and a complete backlog item can still
    contain only a placeholder.
    """
    del detail_status

    text = _normalize_text(description)
    if not text:
        return DETAIL_QUALITY_EMPTY

    # World Bank's listing API returns only the introduction, even when it is
    # thousands of characters long. Duties/criteria require the public page.
    if (raw or {}).get("_worldbank_record_kind") == "listing":
        return DETAIL_QUALITY_LIST_ONLY

    # A captured introduction can be long while explicitly deferring duties to
    # an unavailable notice. Bind that negative finding to the exact normalized
    # body so a later changed/full description is evaluated normally.
    main_verification = (raw or {}).get("_jobagg_main_text_verification")
    if (
        isinstance(main_verification, dict)
        and main_verification.get("complete") is False
        and main_verification.get("normalized_description_sha256")
        == hashlib.sha256(text.encode("utf-8")).hexdigest()
    ):
        return DETAIL_QUALITY_DETAIL_MISSING

    lowered = text.casefold()
    if (raw or {}).get("parser") == "successfactors_xml" and re.search(r"\[\[[^\]]+\]\]", text):
        # A long XML template can omit populated public fields throughout the
        # notice. Its length cannot establish that the public detail was read.
        return DETAIL_QUALITY_DETAIL_MISSING
    if lowered in _PLACEHOLDER_TEXT:
        return DETAIL_QUALITY_PLACEHOLDER_ONLY
    if _normalize_text(title).casefold() == lowered:
        return DETAIL_QUALITY_PLACEHOLDER_ONLY
    is_oracle_raw = raw is not None and _is_oracle_raw(raw)
    if raw is not None and _oracle_heading_only_detail(raw, text):
        return DETAIL_QUALITY_PLACEHOLDER_ONLY
    if raw is not None and _oracle_listing_summary_only(raw, text):
        return DETAIL_QUALITY_LIST_ONLY
    if raw is not None and _pageup_listing_only(raw):
        return DETAIL_QUALITY_LIST_ONLY
    if not is_oracle_raw and _list_summary_only(text):
        return DETAIL_QUALITY_LIST_ONLY
    if len(text) < min_detail_chars:
        return DETAIL_QUALITY_TOO_SHORT
    return DETAIL_QUALITY_COMPLETE


def detail_quality_requeue_reason(status: str) -> str | None:
    if status in INCOMPLETE_DETAIL_QUALITY_STATUSES:
        return f"detail_quality_{status}"
    return None


def oracle_detail_payload_has_substantive_content(item: dict[str, Any]) -> bool:
    """Return true when an Oracle CE detail item has more than placeholder text."""

    short = _normalize_text(item.get("ShortDescription") or item.get("ShortDescriptionStr"))
    responsibilities = _normalize_text(item.get("ExternalResponsibilitiesStr"))
    qualifications = _normalize_text(item.get("ExternalQualificationsStr"))
    external_description = _normalize_text(
        item.get("ExternalDescriptionStr") or item.get("Description")
    )
    if responsibilities or qualifications:
        return True
    if (
        external_description
        and external_description.casefold() not in _PLACEHOLDER_TEXT
        and not _list_summary_only(external_description)
    ):
        return True
    if _substantive_short_oracle_detail(short):
        return True
    return False


def _oracle_heading_only_detail(raw: dict[str, Any], description: str) -> bool:
    if description.casefold() != "duties and responsibilities":
        return False
    return not (
        _normalize_text(raw.get("ExternalResponsibilitiesStr"))
        or _normalize_text(raw.get("ExternalQualificationsStr"))
        or _normalize_text(raw.get("ExternalDescriptionStr") or raw.get("Description"))
    )


def _oracle_listing_summary_only(raw: dict[str, Any], description: str) -> bool:
    if not _is_oracle_raw(raw):
        return False
    if oracle_detail_payload_has_substantive_content(raw):
        return False
    text = _normalize_text(description)
    lowered = text.casefold()
    short = _normalize_text(raw.get("ShortDescription") or raw.get("ShortDescriptionStr"))
    if _template_summary_text(text):
        return True
    if short and lowered == short.casefold():
        return len(text) < 80
    return False


def _is_oracle_raw(raw: dict[str, Any]) -> bool:
    return (
        any(
            raw.get(key) not in (None, "")
            for key in (
                "oracle_site_number",
                "oracle_site_name",
                "oracle_expected_site_name",
                "source_priority",
                "requisitionFlexFields",
            )
        )
        and str(raw.get("source_priority") or "oracle_hcm_ce") == "oracle_hcm_ce"
    )


def _pageup_listing_only(raw: dict[str, Any]) -> bool:
    return (
        raw.get("listing_html") not in (None, "")
        and raw.get("_pageup_detail_url") not in (None, "")
        and raw.get("detail_html") in (None, "")
    )


def _list_summary_only(description: str) -> bool:
    text = _normalize_text(description)
    lowered = text.casefold()
    if "provide a short summary of the job vacancy" in lowered:
        return True
    if lowered.startswith("apply by: dd/mm/yyyy"):
        return True
    if lowered.startswith("apply by:") and not _has_detail_section_marker(text):
        return True
    return False


def _substantive_short_oracle_detail(value: str) -> bool:
    text = _normalize_text(value)
    if not text:
        return False
    lowered = text.casefold()
    if lowered in _PLACEHOLDER_TEXT or _template_summary_text(text):
        return False
    return len(text) >= 80


def _has_detail_section_marker(value: str) -> bool:
    lowered = _normalize_text(value).casefold()
    return any(marker in lowered for marker in _DETAIL_SECTION_MARKERS)


def _template_summary_text(value: str) -> bool:
    lowered = _normalize_text(value).casefold()
    return "provide a short summary of the job vacancy" in lowered or lowered.startswith(
        "apply by: dd/mm/yyyy"
    )


def _normalize_text(value: object | None) -> str:
    if value is None:
        return ""
    return _SPACE_RE.sub(" ", str(value)).strip()
