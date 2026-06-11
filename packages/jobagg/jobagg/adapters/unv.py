"""UNV Unified Volunteering Platform adapter."""

from __future__ import annotations

import copy
from typing import Any

from jobagg.adapters.base import JobAdapter, register_adapter
from jobagg.models import JobRecord
from jobagg.normalize import build_job
from jobagg.utils import as_bool as _as_bool
from jobagg.utils import as_int as _as_int


@register_adapter
class UNVAdapter(JobAdapter):
    family = "unv"

    def fetch_jobs(self) -> list[JobRecord]:
        api_url = self.source.extra.get("api_url")
        if not api_url:
            raise ValueError(f"{self.source.id} requires extra.api_url for UNV")
        page_size = _as_int(self.source.extra.get("page_size"), default=10)
        max_pages = _as_int(self.source.extra.get("max_pages"), default=25)
        fetch_details = _as_bool(self.source.extra.get("fetch_details"), default=False)
        base_payload = copy.deepcopy(self.source.extra.get("search_payload") or {})
        base_payload.setdefault("take", page_size)

        jobs: list[JobRecord] = []
        seen_keys: set[str] = set()
        total: int | None = None
        for page in range(max_pages):
            payload = copy.deepcopy(base_payload)
            payload["take"] = page_size
            payload["skip"] = page * page_size
            response_payload = self.post_json(str(api_url), payload, headers=self._headers())
            page_jobs = self.parse_jobs(response_payload)
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
            total = _total(response_payload) or total
            if total is not None and len(jobs) >= total:
                break
        return jobs

    def parse_jobs(self, payload: Any) -> list[JobRecord]:
        jobs: list[JobRecord] = []
        for item in _rows(payload):
            external_id = item.get("id") or item.get("doaRequestNo")
            jobs.append(
                build_job(
                    self.source,
                    title=item.get("name"),
                    external_id=external_id,
                    location=_label(item.get("country")),
                    department=_host_entity(item),
                    employment_type=_label(item.get("volunteerType"))
                    or _label(item.get("workArrangement"))
                    or _label(item.get("categoryName")),
                    posted_at=item.get("publishDate"),
                    closes_at=item.get("sourcingEndDate"),
                    apply_url=self._apply_url(external_id),
                    source_url=self._detail_api_url(external_id),
                    description=_description(item),
                    raw=item,
                )
            )
        return jobs

    def fetch_detail_for_listing_item(self, item: dict[str, Any]) -> JobRecord | None:
        external_id = item.get("id") or item.get("doaRequestNo")
        detail_url = self._detail_api_url(external_id)
        if not detail_url:
            return None
        return next(iter(self.parse_jobs(self.fetch_json(detail_url))), None)

    def _detail_api_url(self, external_id: Any) -> str | None:
        if external_id is None:
            return None
        template = self.source.extra.get("detail_api_url_template")
        if template:
            return str(template).format(job_id=external_id)
        return f"{self.source.base_url.rstrip('/')}/api/doa/doa/{external_id}"

    def _apply_url(self, external_id: Any) -> str:
        template = self.source.extra.get("detail_url_template") or self.source.extra.get(
            "apply_url_template"
        )
        if template and external_id is not None:
            return str(template).format(job_id=external_id)
        return self.source.base_url

    def _headers(self) -> dict[str, str]:
        return {
            "Accept": "application/json",
            "Referer": self.source.base_url,
        }


def _rows(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if not isinstance(payload, dict):
        return []
    value = payload.get("value")
    if isinstance(value, dict):
        result = value.get("result")
        if isinstance(result, list):
            return [item for item in result if isinstance(item, dict)]
        if value.get("name"):
            return [value]
    for key in ("result", "results", "data"):
        rows = payload.get(key)
        if isinstance(rows, list):
            return [item for item in rows if isinstance(item, dict)]
    return []


def _total(payload: Any) -> int | None:
    if not isinstance(payload, dict):
        return None
    value = payload.get("value")
    total = value.get("total") if isinstance(value, dict) else payload.get("total")
    try:
        return int(total)
    except (TypeError, ValueError):
        return None


def _label(value: Any) -> str | None:
    if isinstance(value, dict):
        return value.get("label") or value.get("shortDescription") or value.get("longDescription")
    if value:
        return str(value)
    return None


def _host_entity(item: dict[str, Any]) -> str | None:
    host = item.get("hostEntity")
    if isinstance(host, dict):
        return host.get("name") or _label(host.get("institution"))
    return _label(host)


def _description(item: dict[str, Any]) -> str | None:
    parts = [
        item.get("organizationMission"),
        item.get("context"),
        item.get("taskDescription"),
        item.get("requiredSkillExperience"),
        item.get("livingConditions"),
    ]
    return "\n\n".join(str(part) for part in parts if part)
