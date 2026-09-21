"""Source-bound Workday date precision without manufacturing a cutoff clock."""
from __future__ import annotations

import hashlib
from html.parser import HTMLParser
import json
import re
from datetime import UTC, date, datetime
from typing import Any
from urllib.parse import urlsplit

from jobagg.adapters.workday_deadlines import PUBLIC_DEADLINE_SOURCES, public_deadline_resolution
from jobagg.models import JobRecord
from jobagg.normalize import clean_text

MARKER = "_workday_public_date_precision"
# PAHO has its own public-field resolver, including explicit civil cutoff clocks.
SOURCES = {
    "globalfund_workday": "https://theglobalfund.wd1.myworkdayjobs.com/External",
    "imf_workday": "https://imf.wd5.myworkdayjobs.com/IMF",
    "tbi_workday": "https://tbinstitute.wd3.myworkdayjobs.com/TBI",
    "wef_workday": "https://weforum.wd3.myworkdayjobs.com/Forum_Careers",
    "wfp_workday": "https://wd3.myworkdaysite.com/recruiting/wfp/job_openings",
    "unhcr_workday": "https://unhcr.wd3.myworkdayjobs.com/External",
    "wto_workday": "https://wto.wd103.myworkdayjobs.com/External",
}
DATE_FIELDS = ("posted_at", "closes_at", "closes_at_local", "closes_tz")


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def instant(value: Any) -> datetime | None:
    """Only an explicit ISO clock AND numeric timezone supplies an instant."""
    if not isinstance(value, str) or not re.fullmatch(
            r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})", value):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)
    except ValueError:
        return None


class _TerminalField(HTMLParser):
    def __init__(self, label: str):
        super().__init__(convert_charrefs=True)
        self.label, self.paragraph, self.tail = label, None, []
        self.matches = 0
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style"}:
            self.hidden += 1
        if tag == "p":
            self.paragraph = []

    def handle_endtag(self, tag):
        if tag in {"script", "style"}:
            self.hidden = max(0, self.hidden - 1)
        if tag == "p" and self.paragraph is not None:
            if " ".join("".join(self.paragraph).split()) == self.label:
                self.matches += 1
                self.tail = []
            self.paragraph = None

    def handle_data(self, data):
        if self.hidden:
            return
        if self.paragraph is not None:
            self.paragraph.append(data)
        if self.matches:
            self.tail.append(data)


def _calendar(value: str, fmt: str) -> str | None:
    try:
        return datetime.strptime(value, fmt).date().isoformat()
    except ValueError:
        return None


def _deadline(source_id: str, info: dict[str, Any]) -> dict[str, Any]:
    if source_id in PUBLIC_DEADLINE_SOURCES:
        return public_deadline_resolution(source_id, info)
    result = {"record_kind": "detail", "kind": "missing_public_deadline", "utc_resolved": False,
              "closes_at": None, "closes_at_local": None, "closes_tz": None,
              "api_end_date": info.get("endDate"), "public_value": None}
    if source_id in {"globalfund_workday", "tbi_workday"}:
        label = "Job Posting End Date" if source_id == "globalfund_workday" else "Closing Date:"
        parser = _TerminalField(label)
        parser.feed(str(info.get("jobDescription") or info.get("description") or ""))
        result.update(public_field="jobPostingInfo.jobDescription", public_label=label)
        if parser.matches != 1:
            result["kind"] = "missing_or_ambiguous_public_label"
            return result
        value = " ".join("".join(parser.tail).split())
        fmt = "%d %B %Y" if source_id == "globalfund_workday" else "%Y-%m-%d"
    else:
        value = info.get("jobPostingEndDateAsText")
        result["public_field"] = "jobPostingInfo.jobPostingEndDateAsText"
        if not value:
            return result
        if not isinstance(value, str) or not value.startswith("End Date: "):
            result.update(kind="unparsed_public_label", public_value=value)
            return result
        value, fmt = value.removeprefix("End Date: "), "%B %d, %Y"
        result["public_label"] = "End Date:"
    result["public_value"] = value
    if not value:
        result["kind"] = "empty_public_deadline"
        return result
    calendar = _calendar(value, fmt)
    if calendar is None:
        result["kind"] = "unparsed_public_deadline"
        return result
    result.update(kind="public_calendar_date_only", closes_at_local=calendar, public_calendar_date=calendar)
    api = info.get("endDate")
    if isinstance(api, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", api):
        try:
            delta = (date.fromisoformat(api) - date.fromisoformat(calendar)).days
        except ValueError:
            result["api_date_invalid"] = True
        else:
            result["api_date_minus_public_date_days"] = delta
            result["triage_note"] = "A 0/1-day difference is not proof of API cutoff semantics."
            if delta not in (0, 1):
                result.update(kind="conflicting_public_deadline_claims",
                              conflict_reason="API calendar differs beyond the possible next-day boundary")
    return result


def precision_resolution(source_id: str, info: dict[str, Any]) -> dict[str, Any] | None:
    if source_id not in SOURCES:
        return None
    body = info.get("jobDescription") or info.get("description")
    external_id = str(info.get("jobReqId") or info.get("jobPostingId") or info.get("id") or "")
    url, base = urlsplit(str(info.get("externalUrl") or "")), urlsplit(SOURCES[source_id])
    if (not isinstance(body, str) or not body.strip() or not external_id
            or url.scheme != "https" or url.netloc != base.netloc or url.query or url.fragment
            or not url.path.startswith(base.path + "/job/")
            or url.path.rsplit("/", 1)[-1] != info.get("jobPostingId")
            or not re.search("_" + re.escape(external_id) + r"(?:-\d+)?$", url.path)):
        raise ValueError("Workday date precision requires the exact public source/job payload")
    api_start = info.get("startDate")
    timestamp = instant(api_start)
    posting = {"public_field": "jobPostingInfo.startDate", "api_value": api_start,
               "public_relative_label": info.get("postedOn"),
               "kind": "explicit_provider_instant" if timestamp else "unknown_public_posting_time",
               "posted_at": timestamp.isoformat() if timestamp else None}
    if isinstance(api_start, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", api_start):
        try:
            posting.update(kind="provider_calendar_date_only", calendar_date=date.fromisoformat(api_start).isoformat())
        except ValueError:
            posting["kind"] = "invalid_provider_calendar_date"
    return {"record_kind": "detail", "provider": "workday_public_date_precision", "source_id": source_id,
            "external_id": external_id, "source_url": url.geturl(),
            "job_posting_info_sha256": digest(info),
            "retained_description_sha256": hashlib.sha256((clean_text(body) or "").encode()).hexdigest(),
            "posting": posting, "deadline": _deadline(source_id, info),
            "scope": "posting_and_deadline_precision_only", "whole_job_complete": False}


def apply_public_date_precision(job: JobRecord, info: dict[str, Any]) -> JobRecord:
    resolution = precision_resolution(job.source_id, info)
    if resolution is None:
        return job
    if (job.external_id != resolution["external_id"] or job.source_url != resolution["source_url"]
            or job.apply_url != resolution["source_url"]):
        raise ValueError("Workday row identity differs from its date evidence")
    job.raw = {**job.raw, MARKER: resolution}
    job.posted_at = instant(resolution["posting"]["posted_at"])
    deadline = resolution["deadline"]
    job.closes_at = instant(deadline["closes_at"])
    job.closes_at_local, job.closes_tz = deadline["closes_at_local"], deadline["closes_tz"]
    return job
