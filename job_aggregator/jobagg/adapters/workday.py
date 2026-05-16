"""Workday CXS adapter."""

from __future__ import annotations

from typing import Any

from jobagg.adapters.base import JobAdapter, register_adapter
from jobagg.models import JobRecord
from jobagg.normalize import build_job
from jobagg.utils import as_bool as _as_bool
from jobagg.utils import as_int as _as_int


def _jobs_from_payload(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, dict):
        for key in ("jobPostings", "jobs", "postings"):
            value = payload.get(key)
            if isinstance(value, list):
                return value
        if isinstance(payload.get("data"), list):
            return payload["data"]
    if isinstance(payload, list):
        return payload
    return []


@register_adapter
class WorkdayAdapter(JobAdapter):
    family = "workday"

    def fetch_jobs(self) -> list[JobRecord]:
        jobs_url = self._jobs_url()
        page_size = _as_int(self.source.extra.get("page_size"), default=20)
        max_pages = _as_int(self.source.extra.get("max_pages"), default=25)
        search_text = str(self.source.extra.get("search_text") or "")
        applied_facets = dict(
            self.source.extra.get("applied_facets")
            or self.source.extra.get("facets")
            or {}
        )
        fetch_details = _as_bool(self.source.extra.get("fetch_details"), default=False)

        jobs: list[JobRecord] = []
        seen_keys: set[str] = set()
        offset = 0
        pages = 0
        expected_total: int | None = None
        # When ``expected_total`` shrinks between pages by more than one page
        # the listing is being mutated under us; older pages we already
        # consumed may be duplicates re-shifted into the next window.
        # We tolerate small fluctuations (vendor-side caching) but break out
        # if a page comes back fully duplicate of what we already have.
        while pages < max_pages:
            payload = self.post_json(
                jobs_url,
                {
                    "appliedFacets": applied_facets,
                    "limit": page_size,
                    "offset": offset,
                    "searchText": search_text,
                },
            )
            rows = _jobs_from_payload(payload)
            if not rows:
                break
            new_in_page = 0
            for item in rows:
                if fetch_details:
                    detail = self._fetch_detail(item)
                    if detail is not None:
                        for parsed in self.parse_jobs(detail):
                            key = parsed.identity_key()
                            if key in seen_keys:
                                continue
                            seen_keys.add(key)
                            jobs.append(parsed)
                            new_in_page += 1
                        continue
                parsed = self.parse_listing_item(item)
                key = parsed.identity_key()
                if key in seen_keys:
                    continue
                seen_keys.add(key)
                jobs.append(parsed)
                new_in_page += 1

            page_total = _as_int(
                payload.get("total") if isinstance(payload, dict) else None,
                default=0,
            )
            if page_total > 0 and (expected_total is None or page_total > expected_total):
                expected_total = page_total
            offset += page_size
            pages += 1
            if new_in_page == 0:
                # Page was fully duplicate of jobs we already collected;
                # the listing is either looping or shrinking under us.
                break
            if expected_total is not None and offset >= expected_total:
                break
            if expected_total is None and len(rows) < page_size:
                break
        return jobs

    def parse_jobs(self, payload: Any) -> list[JobRecord]:
        if isinstance(payload, dict) and isinstance(payload.get("jobPostingInfo"), dict):
            return [self.parse_detail(payload)]

        jobs: list[JobRecord] = []
        for item in _jobs_from_payload(payload):
            jobs.append(self.parse_listing_item(item))
        return jobs

    def parse_listing_item(self, item: dict[str, Any]) -> JobRecord:
        external_path = item.get("externalPath") or item.get("url") or item.get("jobPostingUrl")
        bullet_fields = item.get("bulletFields") if isinstance(item.get("bulletFields"), list) else []
        external_id = (
            item.get("jobReqId")
            or (bullet_fields[0] if bullet_fields else None)
            or item.get("id")
            or external_path
        )
        apply_url = (
            item.get("externalUrl")
            or item.get("applyUrl")
            or item.get("jobPostingUrl")
            or self._public_job_url(str(external_path or external_id or ""))
        )
        location = item.get("locationsText") or item.get("location") or item.get("primaryLocation")
        return build_job(
            self.source,
            title=item.get("title"),
            external_id=external_id,
            location=location,
            department=item.get("jobFamily") or item.get("department"),
            employment_type=item.get("timeType") or item.get("workerSubType"),
            posted_at=item.get("postedOn") or item.get("startDate"),
            apply_url=str(apply_url),
            source_url=str(apply_url),
            description=item.get("description") or item.get("jobDescription"),
            raw=item,
        )

    def parse_detail(self, payload: dict[str, Any]) -> JobRecord:
        info = payload.get("jobPostingInfo") or payload
        external_id = info.get("jobReqId") or info.get("jobPostingId") or info.get("id")
        apply_url = info.get("externalUrl") or self._public_job_url(str(info.get("jobPostingId") or external_id))
        return build_job(
            self.source,
            title=info.get("title"),
            external_id=external_id,
            location=info.get("location") or (info.get("country") or {}).get("descriptor"),
            department=info.get("jobFamily") or info.get("department"),
            employment_type=info.get("timeType"),
            posted_at=info.get("startDate") or info.get("postedOn"),
            closes_at=info.get("endDate"),
            apply_url=str(apply_url),
            source_url=str(apply_url),
            description=info.get("jobDescription") or info.get("description"),
            status="open" if info.get("posted", True) else "closed",
            raw=payload,
        )

    def _jobs_url(self) -> str:
        if self.source.extra.get("jobs_url"):
            return str(self.source.extra["jobs_url"])
        if self.source.extra.get("cxs_base_url"):
            return f"{str(self.source.extra['cxs_base_url']).rstrip('/')}/jobs"
        if self.source.extra.get("api_url"):
            return str(self.source.extra["api_url"])
        raise ValueError(
            f"{self.source.id} requires extra.cxs_base_url, extra.jobs_url, or extra.api_url for Workday"
        )

    def _cxs_base_url(self) -> str | None:
        if self.source.extra.get("cxs_base_url"):
            return str(self.source.extra["cxs_base_url"]).rstrip("/")
        jobs_url = self.source.extra.get("jobs_url") or self.source.extra.get("api_url")
        if jobs_url and str(jobs_url).rstrip("/").endswith("/jobs"):
            return str(jobs_url).rstrip("/")[: -len("/jobs")]
        return None

    def _fetch_detail(self, item: dict[str, Any]) -> dict[str, Any] | None:
        detail_url = self._detail_url(item)
        if detail_url is None:
            return None
        return self.fetch_json(detail_url)

    def fetch_detail_for_listing_item(self, item: dict[str, Any]) -> JobRecord | None:
        detail = self._fetch_detail(item)
        if detail is None:
            return None
        return self.parse_detail(detail)

    def _detail_url(self, item: dict[str, Any]) -> str | None:
        cxs_base_url = self._cxs_base_url()
        external_path = item.get("externalPath")
        if not cxs_base_url or not external_path:
            return None
        return f"{cxs_base_url}{_workday_job_path(str(external_path))}"

    def _public_job_url(self, value: str) -> str:
        if value.startswith(("http://", "https://")):
            return value
        return f"{self.source.base_url.rstrip('/')}{_workday_job_path(value)}"


def _workday_job_path(value: str) -> str:
    path = value.strip().lstrip("/")
    if path.startswith("job/"):
        return f"/{path}"
    return f"/job/{path}"
