"""Normalization helpers for cross-platform job data."""

from __future__ import annotations

import html
import re
from datetime import UTC, datetime
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

from jobagg.models import JobRecord, OrganizationSource

_SPACE_RE = re.compile(r"\s+")
_TAG_RE = re.compile(r"<[^>]+>")
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


def parse_datetime(value: object | None) -> datetime | None:
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
    for date_format in (
        "%B %d, %Y",
        "%b %d, %Y",
        "%d %B %Y",
        "%d %b %Y",
        "%m/%d/%Y",
        "%m/%d/%y",
        "%d/%m/%Y",
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
    return JobRecord(
        source_id=source.id,
        org_id=source.id,
        ats_family=source.ats_family,
        title=require_text(title),
        external_id=clean_text(external_id),
        location=clean_text(location),
        department=clean_text(department),
        employment_type=clean_text(employment_type),
        posted_at=parse_datetime(posted_at),
        closes_at=parse_datetime(closes_at),
        apply_url=canonical_url(apply_url, source.base_url),
        source_url=canonical_url(source_url or apply_url, source.base_url),
        description=clean_text(description),
        status=(clean_text(status) or "open").lower(),
        raw=dict(raw or {}),
    )
