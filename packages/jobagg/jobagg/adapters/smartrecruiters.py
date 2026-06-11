"""SmartRecruiters adapter."""

from __future__ import annotations

from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from jobagg.adapters.base import JobAdapter, register_adapter
from jobagg.models import JobRecord
from jobagg.normalize import build_job
from jobagg.utils import as_bool as _as_bool
from jobagg.utils import as_int as _as_int


@register_adapter
class SmartRecruitersAdapter(JobAdapter):
    family = "smartrecruiters"

    def fetch_jobs(self) -> list[JobRecord]:
        company = self.source.extra.get("company")
        api_url = self.source.extra.get("api_url")
        if not api_url:
            if not company:
                raise ValueError(f"{self.source.id} requires extra.company or extra.api_url")
            api_url = f"https://api.smartrecruiters.com/v1/companies/{company}/postings"
        return self._fetch_paginated_jobs(str(api_url))

    def _fetch_paginated_jobs(self, api_url: str) -> list[JobRecord]:
        page_size = _as_int(self.source.extra.get("page_size"), default=100)
        max_pages = _as_int(self.source.extra.get("max_pages"), default=10)
        fetch_details = _as_bool(self.source.extra.get("fetch_details"), default=False)
        jobs: list[JobRecord] = []
        seen_keys: set[str] = set()
        total_found: int | None = None
        for page in range(max_pages):
            offset = page * page_size
            payload = self.fetch_json(self._page_url(api_url, limit=page_size, offset=offset))
            page_jobs = self.parse_jobs(payload)
            if not page_jobs:
                break
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
            if isinstance(payload, dict):
                total_found = _as_int(payload.get("totalFound"), default=total_found or 0)
            if total_found is not None and offset + page_size >= total_found:
                break
        return jobs

    def parse_jobs(self, payload: Any) -> list[JobRecord]:
        rows = payload.get("content", []) if isinstance(payload, dict) else payload
        jobs = []
        for item in rows if isinstance(rows, list) else []:
            if not isinstance(item, dict):
                continue
            location = item.get("location") or {}
            jobs.append(
                build_job(
                    self.source,
                    title=item.get("name"),
                    external_id=item.get("refNumber") or item.get("id"),
                    location=location.get("fullLocation") or location.get("city") if isinstance(location, dict) else location,
                    department=item.get("department", {}).get("label") if isinstance(item.get("department"), dict) else item.get("department"),
                    employment_type=item.get("typeOfEmployment", {}).get("label") if isinstance(item.get("typeOfEmployment"), dict) else None,
                    posted_at=item.get("releasedDate"),
                    apply_url=item.get("applyUrl")
                    or item.get("postingUrl")
                    or _public_posting_url(self.source.extra.get("company"), item)
                    or item.get("ref")
                    or item.get("id"),
                    description=_description(item),
                    raw=item,
                )
            )
        return jobs

    def fetch_detail_for_listing_item(self, item: dict[str, Any]) -> JobRecord | None:
        detail_url = item.get("ref")
        if not detail_url:
            company = self.source.extra.get("company")
            item_id = item.get("id")
            if company and item_id:
                detail_url = f"https://api.smartrecruiters.com/v1/companies/{company}/postings/{item_id}"
        if not detail_url:
            return None
        return next(iter(self.parse_jobs({"content": [self.fetch_json(str(detail_url))]})), None)

    def _page_url(self, api_url: str, *, limit: int, offset: int) -> str:
        parts = urlsplit(api_url)
        query = dict(parse_qsl(parts.query, keep_blank_values=True))
        query["limit"] = str(limit)
        if offset:
            query["offset"] = str(offset)
        elif "offset" in query:
            query["offset"] = "0"
        return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), ""))


def _description(item: dict[str, Any]) -> str | None:
    job_ad = item.get("jobAd")
    if not isinstance(job_ad, dict):
        return None
    sections = job_ad.get("sections")
    if not isinstance(sections, dict):
        return None
    parts = []
    for value in sections.values():
        if isinstance(value, dict) and value.get("text"):
            parts.append(str(value["text"]))
        elif isinstance(value, str):
            parts.append(value)
    return "\n\n".join(parts) or None


def _public_posting_url(company: Any, item: dict[str, Any]) -> str | None:
    item_id = item.get("id")
    if not company or not item_id:
        return None
    return f"https://jobs.smartrecruiters.com/{company}/{item_id}"
