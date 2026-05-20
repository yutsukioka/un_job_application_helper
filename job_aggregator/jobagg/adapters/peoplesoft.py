"""PeopleSoft Careers listing adapter."""

from __future__ import annotations

import re
from urllib.parse import urlencode

from jobagg.adapters.base import JobAdapter, register_adapter
from jobagg.models import JobRecord
from jobagg.normalize import build_job
from jobagg.utils import clean_html


@register_adapter
class PeopleSoftAdapter(JobAdapter):
    family = "peoplesoft"

    def fetch_jobs(self) -> list[JobRecord]:
        listing_url = str(self.source.extra.get("listing_url") or self.source.base_url)
        return self.parse_listing_html(self.fetch_text(listing_url), listing_url=listing_url)

    def fetch_detail_for_listing_item(self, item: dict[str, str]) -> JobRecord | None:
        detail_url = item.get("detail_url") or item.get("source_url") or item.get("apply_url")
        if not detail_url:
            return None
        detail_url = str(detail_url)
        self.ensure_allowed(detail_url)
        html_text = self.fetch_text(detail_url)
        description = _detail_description(html_text)
        if not description:
            return None
        job_id = item.get("job_id")
        return build_job(
            self.source,
            title=_detail_title(html_text) or item.get("title") or job_id,
            external_id=job_id,
            apply_url=detail_url,
            source_url=detail_url,
            description=description,
            raw={**item, "detail_url": detail_url, "parser": "peoplesoft_detail"},
        )

    def parse_listing_html(self, html_text: str, *, listing_url: str | None = None) -> list[JobRecord]:
        listing_url = listing_url or str(self.source.extra.get("listing_url") or self.source.base_url)
        jobs = []
        for row_html in _search_rows(html_text):
            title = _span_value(row_html, "SCH_JOB_TITLE")
            job_id = _span_value(row_html, "HRS_JOB_OPENING_ID")
            if not title or not job_id:
                continue
            detail_url = _url_with_job_id(listing_url, job_id)
            jobs.append(
                build_job(
                    self.source,
                    title=title,
                    external_id=job_id,
                    location=_span_value(row_html, "LOCATION"),
                    department=_span_value(row_html, "HRS_DEPT_DESCR"),
                    posted_at=_span_value(row_html, "SCH_OPENED"),
                    closes_at=_span_value(row_html, "HRS_JO_PST_CLS_DT"),
                    apply_url=detail_url,
                    source_url=detail_url,
                    raw={
                        "job_id": job_id,
                        "title": title,
                        "detail_url": detail_url,
                        "close_date_text": _span_value(row_html, "HRS_CLS_DT_DESCR"),
                        "parser": "peoplesoft_listing",
                    },
                )
            )
        return _dedupe(jobs)


def _search_rows(html_text: str) -> list[str]:
    return re.findall(
        r"<li\b(?=[^>]*id=[\"']HRS_AGNT_RSLT_I\$0_row_\d+[\"'])[^>]*>(?P<body>.*?)</li>",
        html_text,
        flags=re.IGNORECASE | re.DOTALL,
    )


def _span_value(row_html: str, field_id_contains: str) -> str | None:
    value_pattern = re.compile(
        rf"<span\b"
        rf"(?=[^>]*class=[\"'][^\"']*\bps_box-value\b[^\"']*[\"'])"
        rf"(?=[^>]*id=[\"'][^\"']*{re.escape(field_id_contains)}[^\"']*[\"'])"
        rf"[^>]*>(?P<value>.*?)</span>",
        flags=re.IGNORECASE | re.DOTALL,
    )
    label_pattern = re.compile(
        rf"<span\b"
        rf"(?=[^>]*id=[\"'][^\"']*{re.escape(field_id_contains)}[^\"']*[\"'])"
        rf"[^>]*>(?P<value>.*?)</span>",
        flags=re.IGNORECASE | re.DOTALL,
    )
    matches = [clean_html(match.group("value")) for match in value_pattern.finditer(row_html)]
    if not matches:
        matches = [
            clean_html(match.group("value"))
            for match in label_pattern.finditer(row_html)
            if "lbl" not in match.group(0).casefold()
        ]
    return next((value for value in matches if value), None)


def _url_with_job_id(listing_url: str, job_id: str) -> str:
    separator = "&" if "?" in listing_url else "?"
    return f"{listing_url}{separator}{urlencode({'job_id': job_id})}"


def _detail_title(html_text: str) -> str | None:
    return _first_match(
        html_text,
        (
            r"<h1\b[^>]*>(?P<value>.*?)</h1>",
            r"<title\b[^>]*>(?P<value>.*?)</title>",
        ),
    )


def _detail_description(html_text: str) -> str | None:
    body = _first_match(
        html_text,
        (
            r"<div\b(?=[^>]*id=[\"'][^\"']*HRS_JO_DSCR_DESCRLONG[^\"']*[\"'])[^>]*>(?P<value>.*?)</div>",
            r"<span\b(?=[^>]*id=[\"'][^\"']*HRS_JO_DSCR_DESCRLONG[^\"']*[\"'])[^>]*>(?P<value>.*?)</span>",
            r"<main\b[^>]*>(?P<value>.*?)</main>",
            r"<body\b[^>]*>(?P<value>.*?)</body>",
        ),
        raw=True,
    )
    return clean_html(body) if body else None


def _first_match(
    html_text: str,
    patterns: tuple[str, ...],
    *,
    raw: bool = False,
) -> str | None:
    for pattern in patterns:
        match = re.search(pattern, html_text, flags=re.IGNORECASE | re.DOTALL)
        if not match:
            continue
        value = match.group("value")
        if raw:
            return value
        cleaned = clean_html(value)
        if cleaned:
            return cleaned
    return None


def _dedupe(jobs: list[JobRecord]) -> list[JobRecord]:
    seen = set()
    deduped = []
    for job in jobs:
        key = job.identity_key()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(job)
    return deduped
