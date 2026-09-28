"""UN Careers / Inspira public job openings adapter."""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

from jobagg.adapters.base import JobAdapter, register_adapter
from jobagg.models import JobRecord
from jobagg.normalize import build_job, clean_text
from jobagg.utils import as_bool as _as_bool
from jobagg.utils import as_int as _as_int


def _rows_from_payload(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, dict):
        data = payload.get("data")
        if isinstance(data, dict):
            rows = data.get("list")
            if isinstance(rows, list):
                return [row for row in rows if isinstance(row, dict)]
        rows = payload.get("list")
        if isinstance(rows, list):
            return [row for row in rows if isinstance(row, dict)]
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    return []


def _count_from_payload(payload: Any) -> int | None:
    if not isinstance(payload, dict):
        return None
    data = payload.get("data")
    if isinstance(data, dict) and data.get("count") is not None:
        return _as_int(data.get("count"), default=0)
    if payload.get("count") is not None:
        return _as_int(payload.get("count"), default=0)
    return None


@register_adapter
class InspiraAdapter(JobAdapter):
    family = "inspira"

    def fetch_jobs(self) -> list[JobRecord]:
        list_url = str(
            self.source.extra.get("list_url")
            or "https://careers.un.org/api/public/opening/jo/list/filteredV2/en"
        )
        page_size = _as_int(self.source.extra.get("page_size"), default=50)
        max_pages = _as_int(self.source.extra.get("max_pages"), default=25)
        sort_by = str(self.source.extra.get("sort_by") or "startDate")
        sort_direction = _as_int(self.source.extra.get("sort_direction"), default=-1)
        filter_config = dict(self.source.extra.get("filter_config") or {})
        fetch_details = _as_bool(self.source.extra.get("fetch_details"), default=False)

        jobs: list[JobRecord] = []
        seen_keys: set[str] = set()
        totals: set[int] = set()
        pages_fetched = 0
        terminal = False
        for page in range(max_pages):
            payload = {
                "filterConfig": filter_config,
                "pagination": {
                    "page": page,
                    "itemPerPage": page_size,
                    "sortBy": sort_by,
                    "sortDirection": sort_direction,
                },
            }
            response = self.post_json(list_url, payload, headers=self._headers())
            pages_fetched += 1
            total = _count_from_payload(response)
            if total is not None:
                totals.add(total)
            rows = _rows_from_payload(response)
            if not rows:
                terminal = True
                break
            for item in rows:
                job = self.parse_listing_item(item)
                if fetch_details:
                    detail_job = self.fetch_detail_for_listing_item(job.raw)
                    if detail_job is not None:
                        job = detail_job
                key = job.identity_key()
                if key in seen_keys:
                    continue
                seen_keys.add(key)
                jobs.append(job)
            if total is not None and (page + 1) * page_size >= total:
                terminal = True
                break
            if len(rows) < page_size:
                terminal = True
                break
        # Equal startDate values can shift across offset pages. If the unique
        # inventory disagrees with a stable advertised total, request that
        # observed total in one bounded response, preserving the exact filters.
        # The bound is explicit; larger/changed inventories remain uncertified.
        expected = next(iter(totals)) if len(totals) == 1 else None
        reconciliation_limit = _as_int(
            self.source.extra.get("single_page_reconciliation_max_rows"), default=1000
        )
        if (expected is not None and 0 < expected <= reconciliation_limit
                and len(jobs) != expected):
            response = self.post_json(list_url, {
                "filterConfig": filter_config,
                "pagination": {"page": 0, "itemPerPage": expected,
                    "sortBy": sort_by, "sortDirection": sort_direction},
            }, headers=self._headers())
            pages_fetched += 1
            final_total = _count_from_payload(response)
            candidates = {}
            for item in _rows_from_payload(response):
                candidate = self.parse_listing_item(item)
                candidates[candidate.identity_key()] = candidate
            if final_total == expected and len(candidates) == expected:
                jobs = list(candidates.values())
                terminal = True
            else:
                if final_total is not None:
                    totals.add(final_total)
                # Retain all discoveries, but an inconsistent observation does
                # not justify absence transitions or a completeness claim.
                prior = {job.identity_key(): job for job in jobs}
                prior.update(candidates)
                jobs = list(prior.values())
                terminal = False
        self.run_diagnostics.pages_fetched = pages_fetched
        self.run_diagnostics.total_reported_by_source = expected
        self.run_diagnostics.pagination_complete = (
            terminal and len(totals) == 1 and expected is not None and len(jobs) == expected
        )
        if expected == 0 and not jobs and len(totals) == 1:
            self.run_diagnostics.health_status = "ok_empty"
            self.run_diagnostics.empty_reason = "verified_total_zero"
            self.run_diagnostics.zero_fetched_evidence = {"total_reported_by_source": 0}
        return jobs

    def parse_jobs(self, payload: Any) -> list[JobRecord]:
        jobs = []
        for item in _rows_from_payload(payload):
            jobs.append(self.parse_listing_item(item))
        return jobs

    def parse_listing_item(self, item: dict[str, Any]) -> JobRecord:
        job_id = item.get("jobId")
        source_url = self._public_detail_url(job_id)
        return build_job(
            self.source,
            title=item.get("postingTitle") or item.get("jobTitle") or item.get("jobCodeTitle"),
            external_id=job_id,
            location=self._duty_station_text(item.get("dutyStation")),
            department=self._dict_name(item.get("dept")),
            employment_type=self._employment_type(item),
            posted_at=item.get("startDate"),
            closes_at=item.get("endDate"),
            apply_url=item.get("inspiraURL") or source_url,
            source_url=source_url,
            description=item.get("jobDescription"),
            raw={**item, "_inspira_detail_url": self._api_detail_url(job_id)},
        )

    def fetch_detail_for_listing_item(self, item: dict[str, Any]) -> JobRecord | None:
        detail_url = item.get("_inspira_detail_url")
        if not detail_url:
            detail_url = self._api_detail_url(item.get("jobId"))
        if not detail_url:
            return None
        payload = self.fetch_json(str(detail_url))
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, dict):
            return None
        return self.parse_detail_item(data)

    def parse_detail_item(self, item: dict[str, Any]) -> JobRecord:
        return self.parse_listing_item(item)

    def _headers(self) -> dict[str, str]:
        return {
            "Accept": "application/json",
            "Origin": "https://careers.un.org",
            "Referer": str(self.source.extra.get("search_url") or self.source.base_url),
        }

    def _api_detail_url(self, job_id: Any) -> str | None:
        if job_id in (None, ""):
            return None
        template = str(
            self.source.extra.get("detail_api_url_template")
            or "https://careers.un.org/api/public/opening/joV2/{job_id}/en"
        )
        return template.format(job_id=quote(str(job_id), safe=""))

    def _public_detail_url(self, job_id: Any) -> str:
        if job_id in (None, ""):
            return self.source.base_url
        template = str(
            self.source.extra.get("detail_url_template")
            or "https://careers.un.org/jobSearchDescription/{job_id}?language=en"
        )
        return template.format(job_id=quote(str(job_id), safe=""))

    def _duty_station_text(self, value: Any) -> str | None:
        if not isinstance(value, list):
            return clean_text(value)
        names = []
        for item in value:
            if isinstance(item, dict):
                name = item.get("description") or item.get("name") or item.get("code")
                if name:
                    names.append(str(name))
            elif item:
                names.append(str(item))
        return "; ".join(names) or None

    def _dict_name(self, value: Any) -> str | None:
        if not isinstance(value, dict):
            return clean_text(value)
        return clean_text(value.get("name") or value.get("Name") or value.get("description") or value.get("code"))

    def _employment_type(self, item: dict[str, Any]) -> str | None:
        parts = []
        for key in ("jc", "jl", "jf"):
            name = self._dict_name(item.get(key))
            if name:
                parts.append(name)
        return " / ".join(parts) or clean_text(item.get("categoryCode") or item.get("jobLevel"))
