"""iCIMS adapter."""

from __future__ import annotations

from typing import Any

from jobagg.adapters.base import JobAdapter, register_adapter
from jobagg.models import JobRecord
from jobagg.normalize import build_job


@register_adapter
class ICIMSAdapter(JobAdapter):
    family = "icims"

    def fetch_jobs(self) -> list[JobRecord]:
        search_url = self.source.extra.get("api_url") or self.source.extra.get("search_url")
        if not search_url:
            raise ValueError(f"{self.source.id} requires extra.search_url or extra.api_url")
        return self.parse_jobs(self.fetch_json(str(search_url)))

    def parse_jobs(self, payload: Any) -> list[JobRecord]:
        rows = []
        if isinstance(payload, dict):
            rows = payload.get("jobs") or payload.get("searchResults") or payload.get("items") or []
        elif isinstance(payload, list):
            rows = payload
        jobs = []
        for item in rows if isinstance(rows, list) else []:
            if not isinstance(item, dict):
                continue
            external_id = item.get("id") or item.get("jobId") or item.get("reqId")
            jobs.append(
                build_job(
                    self.source,
                    title=item.get("title") or item.get("jobtitle"),
                    external_id=external_id,
                    location=item.get("location") or item.get("primaryLocation"),
                    department=item.get("department"),
                    employment_type=item.get("employmentType"),
                    posted_at=item.get("postedDate"),
                    apply_url=item.get("url") or item.get("jobUrl") or str(external_id),
                    description=item.get("description"),
                    raw=item,
                )
            )
        return jobs

