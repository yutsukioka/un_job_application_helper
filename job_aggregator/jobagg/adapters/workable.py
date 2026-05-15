"""Workable adapter."""

from __future__ import annotations

from typing import Any

from jobagg.adapters.base import JobAdapter, register_adapter
from jobagg.models import JobRecord
from jobagg.normalize import build_job
from jobagg.utils import as_bool as _as_bool


@register_adapter
class WorkableAdapter(JobAdapter):
    family = "workable"

    def fetch_jobs(self) -> list[JobRecord]:
        account = self.source.extra.get("account")
        api_url = self.source.extra.get("api_url")
        if not api_url:
            if not account:
                raise ValueError(f"{self.source.id} requires extra.account or extra.api_url")
            api_url = f"https://apply.workable.com/api/v3/accounts/{account}/jobs"
        method = str(self.source.extra.get("method") or "POST").upper()
        if method == "GET":
            jobs = self.parse_jobs(self.fetch_json(str(api_url)))
        else:
            payload = self.source.extra.get("search_payload") or {
                "query": "",
                "department": [],
                "location": [],
                "workplace": [],
                "worktype": [],
            }
            jobs = self.parse_jobs(self.post_json(str(api_url), payload, headers=self._headers()))
        if not _as_bool(self.source.extra.get("fetch_details"), default=False):
            return jobs
        detailed_jobs = []
        for job in jobs:
            detailed_jobs.append(self.fetch_detail_for_listing_item(job.raw) or job)
        return detailed_jobs

    def parse_jobs(self, payload: Any) -> list[JobRecord]:
        rows = _rows(payload)
        jobs = []
        account = self.source.extra.get("account")
        for item in rows:
            external_id = item.get("shortcode") or item.get("id")
            jobs.append(
                build_job(
                    self.source,
                    title=item.get("title"),
                    external_id=external_id,
                    location=_location_text(item.get("location")) or _locations_text(item.get("locations")),
                    department=_list_or_text(item.get("department")),
                    employment_type=item.get("employment_type") or item.get("type") or item.get("workplace"),
                    posted_at=item.get("published"),
                    apply_url=item.get("url")
                    or item.get("application_url")
                    or item.get("shortlink")
                    or _apply_url(account, external_id)
                    or str(external_id),
                    description=_description(item),
                    raw=item,
                )
            )
        return jobs

    def fetch_detail_for_listing_item(self, item: dict[str, Any]) -> JobRecord | None:
        account = self.source.extra.get("account")
        shortcode = item.get("shortcode") or item.get("id")
        if not account or not shortcode:
            return None
        url = f"https://apply.workable.com/api/v2/accounts/{account}/jobs/{shortcode}"
        return next(iter(self.parse_jobs(self.fetch_json(url))), None)

    def _headers(self) -> dict[str, str]:
        return {
            "Accept": "application/json",
            "Referer": self.source.base_url,
        }


def _rows(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        for key in ("jobs", "results", "data"):
            value = payload.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
        if payload.get("title"):
            return [payload]
    return []


def _location_text(value: Any) -> str | None:
    if isinstance(value, dict):
        if value.get("location_str"):
            return str(value["location_str"])
        parts = [value.get("city"), value.get("region"), value.get("country")]
        return ", ".join(str(part) for part in parts if part) or None
    if value:
        return str(value)
    return None


def _locations_text(value: Any) -> str | None:
    if not isinstance(value, list):
        return None
    locations = [_location_text(item) for item in value]
    return "; ".join(dict.fromkeys(location for location in locations if location)) or None


def _list_or_text(value: Any) -> str | None:
    if isinstance(value, list):
        labels = []
        for item in value:
            if isinstance(item, dict):
                labels.append(item.get("name") or item.get("label") or item.get("title"))
            elif item:
                labels.append(item)
        return "; ".join(str(item) for item in labels if item) or None
    if isinstance(value, dict):
        return str(value.get("name") or value.get("label") or value.get("title") or "")
    if value:
        return str(value)
    return None


def _description(item: dict[str, Any]) -> str | None:
    parts = [item.get("description"), item.get("requirements"), item.get("benefits")]
    return "\n\n".join(str(part) for part in parts if part)


def _apply_url(account: Any, external_id: Any) -> str | None:
    if not account or not external_id:
        return None
    return f"https://apply.workable.com/{account}/j/{external_id}/"
