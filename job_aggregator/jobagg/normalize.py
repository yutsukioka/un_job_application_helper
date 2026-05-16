"""Normalization helpers for cross-platform job data."""

from __future__ import annotations

import html
import logging
import re
from datetime import UTC, datetime
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

from jobagg.models import JobRecord, OrganizationSource

_LOGGER = logging.getLogger(__name__)

_SPACE_RE = re.compile(r"\s+")
_TAG_RE = re.compile(r"<[^>]+>")
_NUMERIC_DATE_RE = re.compile(r"^(\d{1,2})[/-](\d{1,2})[/-](\d{2,4})$")
_VALID_DATE_LOCALES = {None, "ISO", "US", "EU"}
_TRACKING_PARAMS = {
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_term",
    "utm_content",
    "iis",
    "iisn",
}


def clean_text(value: object | None) -> str | None:
    if value is None:
        return None
    text = html.unescape(str(value))
    text = _TAG_RE.sub(" ", text)
    text = _SPACE_RE.sub(" ", text).strip()
    return text or None


def require_text(value: object | None, fallback: str = "Untitled role") -> str:
    return clean_text(value) or fallback


def canonical_url(url: str, base_url: str | None = None) -> str:
    joined = urljoin(base_url or "", url)
    parts = urlsplit(joined)
    query = urlencode(
        [
            (key, value)
            for key, value in parse_qsl(parts.query, keep_blank_values=True)
            if key.lower() not in _TRACKING_PARAMS
        ],
        doseq=True,
    )
    scheme = parts.scheme.lower() or "https"
    netloc = parts.netloc.lower()
    path = parts.path or "/"
    return urlunsplit((scheme, netloc, path, query, ""))


def parse_datetime(
    value: object | None,
    *,
    date_locale: str | None = None,
) -> datetime | None:
    """Parse a datetime/date string.

    ``date_locale`` controls how purely numeric ``X/Y/Z`` strings are interpreted:

    - ``None`` or ``"ISO"`` (default): only unambiguous numeric dates are
      accepted. If both ``m/d/Y`` and ``d/m/Y`` are valid readings (e.g.
      ``01/02/2026``) the value is rejected and a warning is logged rather
      than silently guessing.
    - ``"US"``: prefer ``m/d/Y`` for ambiguous values.
    - ``"EU"``: prefer ``d/m/Y`` for ambiguous values.

    ISO-8601 and named-month formats are always parsed.
    """

    if date_locale not in _VALID_DATE_LOCALES:
        raise ValueError(f"Unsupported date_locale: {date_locale!r}")

    text = clean_text(value)
    if not text:
        return None
    candidates = [
        text,
        text.replace("Z", "+00:00"),
    ]
    for candidate in candidates:
        try:
            parsed = datetime.fromisoformat(candidate)
        except ValueError:
            continue
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC)

    numeric_match = _NUMERIC_DATE_RE.match(text)
    if numeric_match is not None:
        return _parse_numeric_date(text, numeric_match, date_locale)

    for date_format in (
        "%B %d, %Y",
        "%b %d, %Y",
        "%d %B %Y",
        "%d %b %Y",
        "%b %d, %Y, %I:%M:%S %p",
        "%d-%b-%Y, %I:%M:%S %p",
        "%Y-%m-%d, %I:%M:%S %p",
        "%d/%b/%Y, %I:%M:%S %p",
        "%d/%b/%Y",
        "%d-%b-%Y",
        "%d-%b-%y",
        "%Y-%m-%d",
    ):
        try:
            return datetime.strptime(text, date_format).replace(tzinfo=UTC)
        except ValueError:
            continue
    return None


def _parse_numeric_date(
    text: str,
    match: re.Match[str],
    date_locale: str | None,
) -> datetime | None:
    """Parse a purely numeric date, refusing to guess on ambiguity."""

    a, b, year_text = match.group(1), match.group(2), match.group(3)
    a_int, b_int = int(a), int(b)
    year = int(year_text)
    if len(year_text) == 2:
        year += 2000 if year < 70 else 1900

    md_valid = _is_valid_ymd(year, a_int, b_int)
    dm_valid = _is_valid_ymd(year, b_int, a_int)

    if md_valid and dm_valid and a_int != b_int:
        # Truly ambiguous (e.g. 01/02/2026). Refuse to guess unless caller
        # opted in via date_locale.
        if date_locale == "US":
            return datetime(year, a_int, b_int, tzinfo=UTC)
        if date_locale == "EU":
            return datetime(year, b_int, a_int, tzinfo=UTC)
        _LOGGER.warning(
            "Refusing to parse ambiguous numeric date %r without date_locale; returning None",
            text,
        )
        return None

    if md_valid:
        return datetime(year, a_int, b_int, tzinfo=UTC)
    if dm_valid:
        return datetime(year, b_int, a_int, tzinfo=UTC)
    return None


def _is_valid_ymd(year: int, month: int, day: int) -> bool:
    if not (1 <= month <= 12):
        return False
    if not (1 <= day <= 31):
        return False
    try:
        datetime(year, month, day)
    except ValueError:
        return False
    return True



def build_job(
    source: OrganizationSource,
    *,
    title: object,
    apply_url: str,
    external_id: object | None = None,
    location: object | None = None,
    department: object | None = None,
    employment_type: object | None = None,
    posted_at: object | None = None,
    closes_at: object | None = None,
    source_url: str | None = None,
    description: object | None = None,
    status: object | None = "open",
    raw: dict | None = None,
) -> JobRecord:
    date_locale = source.extra.get("date_locale") if source.extra else None
    return JobRecord(
        source_id=source.id,
        org_id=source.id,
        ats_family=source.ats_family,
        title=require_text(title),
        external_id=clean_text(external_id),
        location=clean_text(location),
        department=clean_text(department),
        employment_type=clean_text(employment_type),
        posted_at=parse_datetime(posted_at, date_locale=date_locale),
        closes_at=parse_datetime(closes_at, date_locale=date_locale),
        apply_url=canonical_url(apply_url, source.base_url),
        source_url=canonical_url(source_url or apply_url, source.base_url),
        description=clean_text(description),
        status=(clean_text(status) or "open").lower(),
        raw=dict(raw or {}),
    )
