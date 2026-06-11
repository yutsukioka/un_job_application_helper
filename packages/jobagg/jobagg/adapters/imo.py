"""IMO custom vacancy portal adapter."""

from __future__ import annotations

from typing import Any

from jobagg.adapters.base import JobAdapter, register_adapter
from jobagg.models import JobRecord
from jobagg.normalize import build_job
from jobagg.utils import clean_html


@register_adapter
class IMOAPIAdapter(JobAdapter):
    family = "imo_api"

    def fetch_jobs(self) -> list[JobRecord]:
        api_url = str(
            self.source.extra.get("api_url")
            or f"{self.source.base_url.rstrip('/')}/api/CurrentJobVacancies"
        )
        return self.parse_jobs(self.fetch_json(api_url))

    def parse_jobs(self, payload: Any) -> list[JobRecord]:
        rows = payload if isinstance(payload, list) else []
        jobs = []
        for item in rows:
            if not isinstance(item, dict):
                continue
            job_id = item.get("jobVacancyId")
            title = item.get("title")
            if not job_id or not title:
                continue
            jobs.append(
                build_job(
                    self.source,
                    title=title,
                    external_id=job_id,
                    location=item.get("location"),
                    department=item.get("department") or item.get("location"),
                    employment_type=item.get("contractType") or item.get("contractHours") or item.get("role"),
                    posted_at=item.get("dateofissue"),
                    closes_at=_closing_date(item),
                    apply_url=self._vacancy_url(job_id),
                    source_url=self._vacancy_url(job_id),
                    description=_description(item),
                    raw=item,
                )
            )
        return jobs

    def _vacancy_url(self, job_id: object) -> str:
        template = self.source.extra.get("detail_url_template")
        if template:
            return str(template).format(job_id=job_id)
        return f"{self.source.base_url.rstrip('/')}/vacancies/{job_id}"


def _closing_date(item: dict[str, Any]) -> Any:
    for key in ("deadlineforapplications", "jobCloseDateExternal", "jobCloseDateInternal"):
        value = item.get(key)
        if value and not str(value).startswith("0001-01-01"):
            return value
    return None


def _description(item: dict[str, Any]) -> str | None:
    parts = []
    for key in (
        "jobDescription",
        "purposeforthepost",
        "maindutiesandresponsibilities",
        "requiredcompetencies",
        "professionalexperience",
        "education",
        "languageskills",
        "otherskills",
        "contractInformation",
        "salaryinformation",
    ):
        text = clean_html(item.get(key))
        if text:
            parts.append(text)
    return "\n\n".join(parts) or None
