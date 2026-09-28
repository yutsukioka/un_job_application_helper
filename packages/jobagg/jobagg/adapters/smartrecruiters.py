"""SmartRecruiters adapter."""

from __future__ import annotations

from typing import Any
import hashlib
import json
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from jobagg.adapters.base import JobAdapter, register_adapter
from jobagg.models import JobRecord
from jobagg.normalize import build_job, clean_text
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
        grouped: dict[str, list[JobRecord]] = {}
        seen_postings: set[str] = set()
        totals: set[int] = set()
        pages_fetched = 0
        for page in range(max_pages):
            offset = page * page_size
            payload = self.fetch_json(self._page_url(api_url, limit=page_size, offset=offset))
            pages_fetched += 1
            if isinstance(payload, dict) and isinstance(payload.get("totalFound"), int):
                totals.add(payload["totalFound"])
            page_jobs = self.parse_jobs(payload)
            new_postings = 0
            for job in page_jobs:
                posting_id = str(job.raw.get("id") or "")
                if not posting_id:
                    raise ValueError("SmartRecruiters listing has no public posting ID")
                if posting_id in seen_postings:
                    continue
                seen_postings.add(posting_id)
                new_postings += 1
                grouped.setdefault(job.identity_key(), []).append(job)
            if not new_postings or (len(totals) == 1 and len(seen_postings) >= next(iter(totals))):
                break
        self.run_diagnostics.pages_fetched = pages_fetched
        # totalFound counts language postings, while jobs use canonical refNumber.
        self.run_diagnostics.total_reported_by_source = next(iter(totals)) if len(totals) == 1 else None
        self.run_diagnostics.pagination_complete = len(totals) == 1 and len(seen_postings) == next(iter(totals))
        jobs: list[JobRecord] = []
        for variants in grouped.values():
            job = variants[0]
            if len(variants) > 1:
                job.raw = {**job.raw, "_smartrecruiters_listing_variants": [dict(v.raw) for v in variants]}
            if fetch_details:
                job = self.fetch_detail_for_listing_item(job.raw) or job
            jobs.append(job)
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
        variants = item.get("_smartrecruiters_listing_variants")
        variants = variants if isinstance(variants, list) and variants else [item]
        details: list[dict[str, Any]] = []
        expected_ref = str(item.get("refNumber") or item.get("id") or "")
        seen_ids: set[str] = set()
        for variant in variants:
            posting_id = str(variant.get("id") or "")
            if not posting_id or posting_id in seen_ids:
                if posting_id in seen_ids:
                    continue
                raise ValueError("SmartRecruiters variant has no public posting ID")
            seen_ids.add(posting_id)
            detail_url = variant.get("ref")
            if not detail_url:
                company = self.source.extra.get("company")
                if company:
                    detail_url = f"https://api.smartrecruiters.com/v1/companies/{company}/postings/{posting_id}"
            if not detail_url:
                return None
            payload = self.fetch_json(str(detail_url))
            if not isinstance(payload, dict) or str(payload.get("id") or "") != posting_id:
                raise ValueError(f"SmartRecruiters detail identity mismatch for posting {posting_id}")
            if str(payload.get("refNumber") or payload.get("id") or "") != expected_ref:
                raise ValueError(f"SmartRecruiters canonical vacancy mismatch for posting {posting_id}")
            if not clean_text(_description(payload)):
                raise ValueError(f"SmartRecruiters posting {posting_id} has no substantive job-ad sections")
            details.append(payload)
        raw = dict(details[0])
        if len(details) > 1:
            raw["_smartrecruiters_listing_variants"] = variants
            raw["_smartrecruiters_public_variants"] = details
            raw["_smartrecruiters_variant_manifest_sha256"] = variant_manifest_hash(variants)
        job = next(iter(self.parse_jobs({"content": [raw]})), None)
        if job is not None and len(details) > 1:
            bodies = []
            for payload in details:
                language = payload.get("language") or {}
                label = language.get("labelNative") or language.get("label") or language.get("code") or "Public variant"
                bodies.append(f"{label}: {payload.get('name') or ''}\n\n{_description(payload)}")
            job.description = clean_text("\n\n".join(bodies))
        return job

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


def variant_manifest_hash(variants: list[dict[str, Any]]) -> str:
    """Bind cached full variants to the current public inventory and metadata."""
    fields = ("id", "refNumber", "name", "language", "releasedDate", "ref", "jobAdId", "defaultJobAd")
    rows = [{key: item.get(key) for key in fields} for item in variants]
    rows.sort(key=lambda row: str(row.get("id") or ""))
    return hashlib.sha256(json.dumps(rows, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
