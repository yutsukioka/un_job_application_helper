"""Avature portal adapter for public HTML job listings."""

from __future__ import annotations

import html
import re
from typing import Any
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

from jobagg.adapters.base import JobAdapter, register_adapter
from jobagg.models import JobRecord
from jobagg.normalize import build_job
from jobagg.utils import as_bool as _as_bool
from jobagg.utils import as_int as _as_int
from jobagg.utils import clean_html as _clean_html

_ARTICLE_RE = re.compile(
    r'<article[^>]+class="[^"]*\barticle--result\b[^"]*"[^>]*>(?P<html>.*?)(?=</article>)',
    re.IGNORECASE | re.DOTALL,
)
_DETAIL_LINK_RE = re.compile(
    r'<a[^>]+href="(?P<href>[^"]*?/JobDetail/[^"]+)"[^>]*>(?P<title>.*?)</a>',
    re.IGNORECASE | re.DOTALL,
)
_SUBTITLE_RE = re.compile(
    r'<div[^>]+class="[^"]*\barticle__header__text__subtitle\b[^"]*"[^>]*>(?P<body>.*?)</div>',
    re.IGNORECASE | re.DOTALL,
)
_SUMMARY_RE = re.compile(
    r'<div[^>]+class="[^"]*\barticle__content\b[^"]*"[^>]*>(?P<body>.*?)</div>',
    re.IGNORECASE | re.DOTALL,
)
_FIELD_RE = re.compile(
    r'<div[^>]+class="[^"]*\barticle__content__view__field\b[^"]*"[^>]*>'
    r"(?P<body>.*?)</div>\s*</div>",
    re.IGNORECASE | re.DOTALL,
)
@register_adapter
class AvatureAdapter(JobAdapter):
    family = "avature"

    def fetch_jobs(self) -> list[JobRecord]:
        listing_url = str(self.source.extra.get("listing_url") or self.source.base_url)
        page_size = _as_int(self.source.extra.get("page_size"), default=25)
        max_pages = _as_int(self.source.extra.get("max_pages"), default=25)
        fetch_details = _as_bool(self.source.extra.get("fetch_details"), default=False)

        jobs: list[JobRecord] = []
        seen_keys: set[str] = set()
        for page in range(max_pages):
            page_url = _url_with_query(
                listing_url,
                {
                    "jobRecordsPerPage": page_size,
                    "jobOffset": page * page_size,
                },
            )
            page_jobs = self.parse_listing_html(self.fetch_text(page_url))
            if not page_jobs:
                break
            page_new = 0
            for job in page_jobs:
                if fetch_details:
                    detail_job = self.fetch_detail_for_listing_item(job.raw)
                    if detail_job is not None:
                        job = detail_job
                key = job.identity_key()
                if key in seen_keys:
                    continue
                seen_keys.add(key)
                jobs.append(job)
                page_new += 1
            if page_new == 0 or len(page_jobs) < page_size:
                break
        return jobs

    def parse_listing_html(self, html_text: str) -> list[JobRecord]:
        jobs: list[JobRecord] = []
        for match in _ARTICLE_RE.finditer(html_text):
            item_html = match.group("html")
            link = _DETAIL_LINK_RE.search(item_html)
            if not link:
                continue
            detail_url = urljoin(self.source.base_url, html.unescape(link.group("href")))
            subtitle_parts = _subtitle_parts(item_html)
            jobs.append(
                build_job(
                    self.source,
                    title=_clean_html(link.group("title")),
                    external_id=_job_id_from_url(detail_url),
                    location=subtitle_parts[0] if subtitle_parts else None,
                    department=subtitle_parts[1] if len(subtitle_parts) > 1 else None,
                    posted_at=subtitle_parts[2] if len(subtitle_parts) > 2 else None,
                    apply_url=detail_url,
                    source_url=detail_url,
                    description=_summary(item_html),
                    raw={"listing_html": item_html, "_detail_url": detail_url},
                )
            )
        return jobs

    def fetch_detail_for_listing_item(self, item: dict[str, Any]) -> JobRecord | None:
        detail_url = item.get("_detail_url")
        if not detail_url:
            return None
        return self.parse_detail_html(self.fetch_text(str(detail_url)), str(detail_url))

    def parse_detail_html(self, html_text: str, detail_url: str) -> JobRecord:
        fields = _detail_fields(html_text)
        title = _meta_content(html_text, "og:title") or _title_from_url(detail_url)
        apply_url = fields.get("Apply URL") or detail_url
        return build_job(
            self.source,
            title=title,
            external_id=_job_id_from_url(detail_url),
            location=fields.get("Duty Station") or fields.get("Location"),
            department=fields.get("Seniority Level") or fields.get("Level"),
            employment_type=fields.get("Contract type") or fields.get("Contract Type"),
            posted_at=fields.get("Posted") or fields.get("Posting Start Date"),
            closes_at=fields.get("Posting End Date"),
            apply_url=apply_url,
            source_url=detail_url,
            description=_clean_html(_main_content(html_text)),
            raw={"detail_html": html_text, "_detail_url": detail_url},
        )


def _url_with_query(url: str, params: dict[str, Any]) -> str:
    parts = urlsplit(url)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query.update({key: str(value) for key, value in params.items() if value is not None})
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), ""))


def _subtitle_parts(item_html: str) -> list[str]:
    match = _SUBTITLE_RE.search(item_html)
    if not match:
        return []
    text = _clean_html(match.group("body")) or ""
    return [part.strip() for part in text.split("•") if part.strip()]


def _summary(item_html: str) -> str | None:
    match = _SUMMARY_RE.search(item_html)
    return _clean_html(match.group("body")) if match else None


def _job_id_from_url(url: str) -> str | None:
    match = re.search(r"/JobDetail/[^/]+/(?P<job_id>\d+)", urlsplit(url).path)
    return match.group("job_id") if match else None


def _detail_fields(html_text: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    for match in _FIELD_RE.finditer(html_text):
        body = match.group("body")
        label_match = re.search(
            r'<div[^>]+class="[^"]*\barticle__content__view__field__label\b[^"]*"[^>]*>'
            r"(?P<value>.*?)</div>",
            body,
            re.IGNORECASE | re.DOTALL,
        )
        value_match = re.search(
            r'<div[^>]+class="[^"]*\barticle__content__view__field__value\b[^"]*"[^>]*>'
            r"(?P<value>.*?)</div>",
            body,
            re.IGNORECASE | re.DOTALL,
        )
        label = _clean_html(label_match.group("value")) if label_match else None
        value = _clean_html(value_match.group("value")) if value_match else None
        if label and value:
            fields[label] = value
    return fields


def _meta_content(html_text: str, property_name: str) -> str | None:
    match = re.search(
        rf'<meta[^>]+property="{re.escape(property_name)}"[^>]+content="(?P<value>[^"]+)"',
        html_text,
        re.IGNORECASE,
    )
    return html.unescape(match.group("value")) if match else None


def _main_content(html_text: str) -> str:
    match = re.search(
        r'<div[^>]+class="[^"]*\barticle__content__view\b[^"]*"[^>]*>(?P<body>.*?)</main>',
        html_text,
        re.IGNORECASE | re.DOTALL,
    )
    return match.group("body") if match else html_text


def _title_from_url(url: str) -> str:
    parts = [part for part in urlsplit(url).path.rstrip("/").split("/") if part]
    if len(parts) >= 2:
        return parts[-2].replace("-", " ")
    return "Untitled role"
