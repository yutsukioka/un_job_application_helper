"""IMO custom vacancy portal adapter."""

from __future__ import annotations

from typing import Any

from jobagg.adapters.base import JobAdapter, register_adapter
from jobagg.adapters.imo_public import apply_public_date_precision
from jobagg.models import JobRecord
from jobagg.normalize import build_job
from jobagg.utils import clean_html


# The public IMO vacancy frontend resolves /vacancies/:id by retrieving
# CurrentJobVacancies and finding the matching jobVacancyId. These are the
# fields rendered by that frontend's full vacancy template.
_PUBLIC_DETAIL_FIELDS = (
    "contractInformation",
    "salaryinformation",
    "purposeforthepost",
    "maindutiesandresponsibilities",
    "requiredcompetencies",
    "professionalexperience",
    "education",
    "languageskills",
    "otherskills",
)


@register_adapter
class IMOAPIAdapter(JobAdapter):
    family = "imo_api"

    def _api_url(self) -> str:
        return str(
            self.source.extra.get("api_url")
            or f"{self.source.base_url.rstrip('/')}/api/CurrentJobVacancies"
        )

    def fetch_jobs(self) -> list[JobRecord]:
        return self.parse_jobs(self.fetch_json(self._api_url()))

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
                apply_public_date_precision(
                    build_job(
                        self.source,
                        title=title,
                        external_id=job_id,
                        location=item.get("location"),
                        department=item.get("department") or item.get("location"),
                        employment_type=item.get("contractType")
                        or item.get("contractHours")
                        or item.get("role"),
                        posted_at=item.get("dateofissue"),
                        closes_at=_closing_date(item),
                        apply_url=self._vacancy_url(job_id),
                        source_url=self._vacancy_url(job_id),
                        description=_description(item),
                        raw=item,
                    )
                )
            )
        return jobs

    def fetch_detail_for_listing_item(self, item: dict[str, Any]) -> JobRecord | None:
        """Refresh the same public endpoint the frontend uses, selecting one ID.

        The supplied listing is an identity hint only. A detail task must observe
        a fresh response through the existing robots/HTTP/capture transport.
        """
        job_id = item.get("jobVacancyId")
        if type(job_id) not in (str, int) or not str(job_id).strip():
            return None
        payload = self.fetch_json(self._api_url())
        if not isinstance(payload, list):
            raise ValueError("IMO public vacancy response is not a vacancy list")
        matches = [
            row
            for row in payload
            if isinstance(row, dict)
            and type(row.get("jobVacancyId")) in (str, int)
            and str(row["jobVacancyId"]) == str(job_id)
        ]
        if len(matches) > 1:
            raise ValueError("IMO public vacancy identity is duplicated")
        if not matches:
            return None
        item = matches[0]
        if not all(key in item for key in _PUBLIC_DETAIL_FIELDS):
            return None
        if not any(
            clean_html(item.get(key))
            for key in ("purposeforthepost", "maindutiesandresponsibilities")
        ):
            return None
        if not any(
            clean_html(item.get(key))
            for key in ("requiredcompetencies", "professionalexperience", "education")
        ):
            return None
        jobs = self.parse_jobs([item])
        if len(jobs) != 1 or str(jobs[0].external_id) != str(item.get("jobVacancyId")):
            return None
        job = jobs[0]
        job.raw = {
            **job.raw,
            "imo_detail_source": "CurrentJobVacancies",
            "imo_detail_response_verified": True,
            "imo_detail_identity_field": "jobVacancyId",
            "imo_detail_fetch_kind": "fresh_current_vacancies_exact_id",
            "imo_detail_api_url": self._api_url(),
        }
        return job

    def _vacancy_url(self, job_id: object) -> str:
        template = self.source.extra.get("detail_url_template")
        if template:
            return str(template).format(job_id=job_id)
        return f"{self.source.base_url.rstrip('/')}/vacancies/{job_id}"


def _closing_date(item: dict[str, Any]) -> Any:
    for key in (
        "deadlineforapplications",
        "jobCloseDateExternal",
        "jobCloseDateInternal",
    ):
        value = item.get(key)
        if value and not str(value).startswith("0001-01-01"):
            return value
    return None


def _description(item: dict[str, Any]) -> str | None:
    parts = []
    seen = set()
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
        "essentialCompetencies",
        "desiredCompetencies",
        "salary",
    ):
        text = clean_html(item.get(key))
        if text and text not in seen:
            parts.append(text)
            seen.add(text)
    # These questions are also returned publicly by CurrentJobVacancies and
    # used in the candidate application form. Preserve their wording alongside
    # the full raw response rather than omit them from searchable job text.
    for key in ("competencyQuestions", "backgroundQuestions"):
        values = item.get(key)
        if not isinstance(values, list):
            continue
        questions = []
        for value in values:
            text = clean_html(value) if isinstance(value, str) else None
            if text and text not in seen:
                questions.append(text)
                seen.add(text)
        if questions:
            parts.append("\n".join(questions))
    return "\n\n".join(parts) or None
