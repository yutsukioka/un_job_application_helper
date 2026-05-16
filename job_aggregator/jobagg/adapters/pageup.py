"""PageUp adapter."""

from __future__ import annotations

import html
import json
import re
from typing import Any
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

from jobagg.adapters.base import JobAdapter, register_adapter
from jobagg.models import JobRecord
from jobagg.normalize import build_job
from jobagg.utils import as_bool as _as_bool
from jobagg.utils import as_int as _as_int
from jobagg.utils import clean_html

_ITEM_RE = re.compile(
    r'<div class="list-view--item">(?P<html>.*?)(?=<div class="list-view--item">|$)',
    re.IGNORECASE | re.DOTALL,
)
_JOB_LINK_RE = re.compile(
    r'<a[^>]+class="[^"]*\bjob-link\b[^"]*"[^>]+href="(?P<href>[^"]+)"[^>]*>'
    r"(?P<title>.*?)</a>",
    re.IGNORECASE | re.DOTALL,
)
def _rows(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        for key in ("jobs", "JobList", "results", "data"):
            value = payload.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
    return []


@register_adapter
class PageUpAdapter(JobAdapter):
    family = "pageup"

    def fetch_jobs(self) -> list[JobRecord]:
        filter_url = self.source.extra.get("filter_url")
        if filter_url:
            return self._fetch_filter_jobs(str(filter_url))
        api_url = self.source.extra.get("api_url")
        if not api_url:
            raise ValueError(f"{self.source.id} requires extra.filter_url or extra.api_url for PageUp")
        return self.parse_jobs(self.fetch_json(str(api_url)))

    def parse_jobs(self, payload: Any) -> list[JobRecord]:
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except json.JSONDecodeError:
                return self.parse_listing_html(payload)
        if isinstance(payload, dict) and isinstance(payload.get("results"), str):
            return self.parse_listing_html(payload["results"])

        jobs = []
        for item in _rows(payload):
            external_id = item.get("jobId") or item.get("id") or item.get("reference")
            jobs.append(
                build_job(
                    self.source,
                    title=item.get("title") or item.get("jobTitle"),
                    external_id=external_id,
                    location=item.get("location") or item.get("workTypeLocation"),
                    department=item.get("category") or item.get("department"),
                    employment_type=item.get("workType") or item.get("employmentType"),
                    posted_at=item.get("postedDate"),
                    closes_at=item.get("closingDate"),
                    apply_url=item.get("url") or item.get("jobUrl") or item.get("applyUrl") or str(external_id),
                    description=item.get("summary") or item.get("description"),
                    raw=item,
                )
            )
        return jobs

    def _fetch_filter_jobs(self, filter_url: str) -> list[JobRecord]:
        page_size = _as_int(self.source.extra.get("page_size"), default=20)
        max_pages = _as_int(self.source.extra.get("max_pages"), default=25)
        fetch_details = _as_bool(self.source.extra.get("fetch_details"), default=False)
        base_params = dict(self.source.extra.get("query") or {})
        base_params.setdefault("search-keyword", "")

        jobs: list[JobRecord] = []
        seen_keys: set[str] = set()
        total_count: int | None = None
        for page in range(1, max_pages + 1):
            params = {**base_params, "page": page, "page-items": page_size}
            payload = self._post_pageup_json(self._url_with_query(filter_url, params))
            if not isinstance(payload, dict):
                break
            page_jobs = self.parse_listing_html(str(payload.get("results") or ""))
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
            total_count = _as_int(payload.get("count"), default=total_count or 0) or total_count
            page_items = _as_int(payload.get("pageitems"), default=page_size)
            current_page = _as_int(payload.get("page"), default=page)
            if page_new == 0 or (total_count is not None and current_page * page_items >= total_count):
                break
        return jobs

    def parse_listing_html(self, html_text: str) -> list[JobRecord]:
        jobs = []
        for match in _ITEM_RE.finditer(html_text):
            item_html = match.group("html")
            link = _JOB_LINK_RE.search(item_html)
            if not link:
                continue
            href = html.unescape(link.group("href"))
            detail_url = urljoin(self.source.base_url, href)
            external_id = self._job_id_from_url(detail_url) or self._extract_hash_id(link.group("title"))
            jobs.append(
                build_job(
                    self.source,
                    title=self._clean_html(link.group("title")),
                    external_id=external_id,
                    location=self._extract_class_text(item_html, "location"),
                    closes_at=self._extract_time_datetime(item_html) or self._extract_labeled_value(
                        item_html, "Deadline"
                    ),
                    apply_url=detail_url,
                    source_url=detail_url,
                    description=self._extract_teaser(item_html),
                    raw={"listing_html": item_html, "_pageup_detail_url": detail_url},
                )
            )
        return jobs

    def fetch_detail_for_listing_item(self, item: dict[str, Any]) -> JobRecord | None:
        detail_url = item.get("_pageup_detail_url")
        if not detail_url:
            return None
        detail_text = self.fetch_text(str(detail_url))
        try:
            payload = json.loads(detail_text)
        except json.JSONDecodeError:
            detail_html = detail_text
        else:
            if isinstance(payload, dict) and isinstance(payload.get("results"), str):
                detail_html = payload["results"]
            else:
                return None
        return self.parse_detail_html(detail_html, str(detail_url))

    def parse_detail_html(self, detail_html: str, detail_url: str) -> JobRecord:
        title = self._extract_heading(detail_html) or self._extract_title_from_detail_url(detail_url)
        external_id = self._extract_class_text(detail_html, "job-externalJobNo") or self._job_id_from_url(detail_url)
        apply_url = self._extract_apply_url(detail_html) or detail_url
        return build_job(
            self.source,
            title=title,
            external_id=external_id,
            location=self._extract_class_text(detail_html, "location"),
            department=self._extract_class_text(detail_html, "categories"),
            employment_type=self._extract_labeled_value(detail_html, "Contract type"),
            closes_at=self._extract_time_datetime(detail_html) or self._extract_labeled_value(
                detail_html, "Deadline"
            ),
            apply_url=apply_url,
            source_url=detail_url,
            description=self._clean_html(detail_html),
            raw={"detail_html": detail_html, "_pageup_detail_url": detail_url},
        )

    def _post_pageup_json(self, url: str) -> Any:
        text = self.post_form_text(url, headers=self._headers())
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return text

    def _headers(self) -> dict[str, str]:
        parts = urlsplit(self.source.base_url)
        origin = f"{parts.scheme}://{parts.netloc}" if parts.scheme and parts.netloc else None
        headers = {
            "Accept": "*/*",
            "Referer": str(self.source.extra.get("listing_url") or self.source.base_url),
            "X-Requested-With": "XMLHttpRequest",
        }
        if origin:
            headers["Origin"] = origin
        return headers

    def _url_with_query(self, url: str, params: dict[str, Any]) -> str:
        parts = urlsplit(url)
        existing = dict(parse_qsl(parts.query, keep_blank_values=True))
        existing.update({key: str(value) for key, value in params.items() if value is not None})
        return urlunsplit(
            (
                parts.scheme,
                parts.netloc,
                parts.path,
                urlencode(existing, doseq=True),
                "",
            )
        )

    def _clean_html(self, html_text: str | None) -> str | None:
        return clean_html(html_text)

    def _extract_class_text(self, html_text: str, class_name: str) -> str | None:
        pattern = re.compile(
            rf'<span[^>]+class="[^"]*\b{re.escape(class_name)}\b[^"]*"[^>]*>(?P<value>.*?)</span>',
            re.IGNORECASE | re.DOTALL,
        )
        match = pattern.search(html_text)
        return self._clean_html(match.group("value")) if match else None

    def _extract_time_datetime(self, html_text: str) -> str | None:
        match = re.search(r"<time[^>]+datetime=\"(?P<value>[^\"]+)\"", html_text, re.IGNORECASE)
        return html.unescape(match.group("value")) if match else None

    def _extract_labeled_value(self, html_text: str, label: str) -> str | None:
        pattern = re.compile(
            rf"<b>\s*{re.escape(label)}\s*:\s*</b>\s*(?P<value>.*?)(?:<br>|</p>|$)",
            re.IGNORECASE | re.DOTALL,
        )
        match = pattern.search(html_text)
        return self._clean_html(match.group("value")) if match else None

    def _extract_teaser(self, item_html: str) -> str | None:
        match = re.search(
            r'<div class="row--teaser">(?P<value>.*?)<p><b>Location:</b>',
            item_html,
            re.IGNORECASE | re.DOTALL,
        )
        return self._clean_html(match.group("value")) if match else None

    def _extract_heading(self, detail_html: str) -> str | None:
        match = re.search(r"<h2[^>]*>(?P<value>.*?)</h2>", detail_html, re.IGNORECASE | re.DOTALL)
        return self._clean_html(match.group("value")) if match else None

    def _extract_apply_url(self, detail_html: str) -> str | None:
        match = re.search(
            r'<a[^>]+class="[^"]*\bapply-link\b[^"]*"[^>]+href="(?P<href>[^"]+)"',
            detail_html,
            re.IGNORECASE | re.DOTALL,
        )
        if not match:
            return None
        return urljoin(self.source.base_url, html.unescape(match.group("href")))

    def _job_id_from_url(self, url: str) -> str | None:
        match = re.search(r"/job/(?P<job_id>\d+)(?:/|$)", urlsplit(url).path)
        return match.group("job_id") if match else None

    def _extract_hash_id(self, text: str) -> str | None:
        match = re.search(r"#\s*(?P<job_id>\d{4,})", self._clean_html(text) or "")
        return match.group("job_id") if match else None

    def _extract_title_from_detail_url(self, url: str) -> str:
        slug = urlsplit(url).path.rstrip("/").split("/")[-1]
        return slug.replace("-", " ").title() if slug else "Untitled role"
