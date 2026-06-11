"""USAJobs adapter."""

from __future__ import annotations

from typing import Any

from jobagg.adapters.base import JobAdapter, register_adapter
from jobagg.models import JobRecord
from jobagg.normalize import build_job


@register_adapter
class USAJobsAdapter(JobAdapter):
    family = "usajobs"

    def fetch_jobs(self) -> list[JobRecord]:
        api_url = self.source.extra.get("api_url") or "https://data.usajobs.gov/api/search"
        payload = self.fetch_json(str(api_url))
        return self.parse_jobs(payload)

    def parse_jobs(self, payload: Any) -> list[JobRecord]:
        rows = []
        if isinstance(payload, dict):
            rows = payload.get("SearchResult", {}).get("SearchResultItems", [])
        jobs = []
        for row in rows:
            item = row.get("MatchedObjectDescriptor", {}) if isinstance(row, dict) else {}
            if not item:
                continue
            external_id = item.get("PositionID")
            locations = item.get("PositionLocation") or []
            location_text = "; ".join(
                loc.get("LocationName", "") for loc in locations if isinstance(loc, dict)
            )
            jobs.append(
                build_job(
                    self.source,
                    title=item.get("PositionTitle"),
                    external_id=external_id,
                    location=location_text,
                    department=item.get("OrganizationName") or item.get("DepartmentName"),
                    employment_type=_first_named_value(item.get("PositionSchedule")),
                    posted_at=item.get("PublicationStartDate"),
                    closes_at=item.get("ApplicationCloseDate"),
                    apply_url=item.get("PositionURI") or str(external_id),
                    description=item.get("UserArea", {}).get("Details", {}).get("JobSummary"),
                    raw=item,
                )
            )
        return jobs


def _first_named_value(value: object) -> str | None:
    if not isinstance(value, list):
        return None
    for item in value:
        if isinstance(item, dict) and item.get("Name"):
            return str(item["Name"])
    return None
