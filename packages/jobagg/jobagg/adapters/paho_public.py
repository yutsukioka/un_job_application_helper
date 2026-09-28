"""Project PAHO's labelled public notice fields without inventing date precision."""

from __future__ import annotations

from datetime import UTC, date, datetime
import re
from typing import Any
from zoneinfo import ZoneInfo

from jobagg.models import JobRecord
from jobagg.normalize import clean_text

_MONTHS = {
    name: number
    for number, names in enumerate((
        ("january", "enero"), ("february", "febrero"), ("march", "marzo"),
        ("april", "abril"), ("may", "mayo"), ("june", "junio"),
        ("july", "julio"), ("august", "agosto"),
        ("september", "septiembre", "setiembre"), ("october", "octubre"),
        ("november", "noviembre"), ("december", "diciembre"),
    ), 1)
    for name in names
}
_LABELS = (
    "Contractual Agreement:", "Job Posting:", "Closing Date:",
    "Primary Location:", "Organization:", "Schedule:",
)
_ZONES = {
    "Eastern Time": "America/New_York",
    "Venezuela Time": "America/Caracas",
    "Uruguay Standard Time": "America/Montevideo",
    "Suriname Time": "America/Paramaribo",
}


def _calendar(value: str) -> date:
    match = re.fullmatch(r"([A-Za-z]+)\s+(\d{1,2}),\s*(\d{4})", value)
    if not match:
        raise ValueError("Unrecognized public PAHO calendar date")
    return date(int(match[3]), _MONTHS[match[1].lower()], int(match[2]))


def public_fields(info: dict[str, Any]) -> dict[str, Any]:
    """Read the single ordered metadata header, retaining competing API claims."""
    text = clean_text(info.get("jobDescription") or info.get("description")) or ""
    result: dict[str, Any] = {
        "record_kind": "detail", "provider": "paho_labelled_public_notice",
        "external_id": info.get("jobReqId") or info.get("jobPostingId") or info.get("id"),
        "public_fields": {}, "posting_time_resolved": False, "utc_resolved": False,
        "posted_at": None, "closes_at": None, "closes_at_local": None, "closes_tz": None,
        "api_start_date": info.get("startDate"), "api_end_date": info.get("endDate"),
        "api_time_type": info.get("timeType"), "api_location": info.get("location"),
    }
    header = text[:2200]
    positions = [header.find(label) for label in _LABELS]
    if min(positions) < 0 or positions != sorted(set(positions)):
        result["kind"] = "missing_or_unordered_public_header"
        return result
    fields = {
        label[:-1]: header[start + len(label):stop].strip()
        for label, start, stop in zip(_LABELS[:-1], positions[:-1], positions[1:])
    }
    schedule = re.match(r"\s*(Full time|Part time)\b", header[positions[-1] + len(_LABELS[-1]):])
    fields["Schedule"] = schedule[1] if schedule else None
    prefix = header[:positions[0]]
    grade = re.search(r"\bGrade:\s*([^:]+?)(?=\s+Salary\s*-|$)", prefix)
    result.update(kind="public_header", public_fields=fields,
                  public_grade=grade[1].strip() if grade else None)
    try:
        result["public_posting_calendar_date"] = _calendar(fields["Job Posting"]).isoformat()
    except (ValueError, KeyError):
        result["posting_date_unparsed"] = True
    claim = re.fullmatch(
        r"([A-Za-z]+\s+\d{1,2},\s*\d{4}),\s*(\d{1,2}):(\d{2})\s*(AM|PM)\s+(.+)",
        fields["Closing Date"],
    )
    if not claim:
        result["kind"] = "unparsed_public_deadline"
        return result
    try:
        calendar = _calendar(claim[1])
        hour, minute = int(claim[2]), int(claim[3])
        if not 1 <= hour <= 12 or not 0 <= minute < 60:
            raise ValueError("Invalid public clock")
        hour = hour % 12 + (12 if claim[4] == "PM" else 0)
        local = datetime.combine(calendar, datetime.min.time()).replace(hour=hour, minute=minute)
    except (ValueError, KeyError):
        result["kind"] = "unparsed_public_deadline"
        return result
    label = claim[5]
    zone = _ZONES.get(label)
    if label == "Central Standard Time" and "Honduras" in fields["Primary Location"]:
        zone = "America/Tegucigalpa"
    result.update(public_calendar_date=calendar.isoformat(), public_timezone_label=label,
                  closes_at_local=local.isoformat(timespec="minutes"), closes_tz=zone)
    if zone is None:
        result["kind"] = "unresolved_public_timezone"
        return result
    # Named civil time is interpreted in PAHO's Americas recruitment context;
    # retain the literal source label and mapping rather than claiming an
    # explicit numeric source offset. Unknown labels never receive this map.
    aware = local.replace(tzinfo=ZoneInfo(zone))
    utc = aware.astimezone(UTC)
    if aware.astimezone(UTC).astimezone(ZoneInfo(zone)).replace(tzinfo=None) != local:
        result["kind"] = "nonexistent_public_local_time"
        return result
    if local.replace(tzinfo=ZoneInfo(zone), fold=1).utcoffset() != aware.utcoffset():
        result["kind"] = "ambiguous_public_local_time"
        return result
    result.update(kind="public_clock_and_named_civil_timezone", utc_resolved=True,
                  closes_at=utc.isoformat(), interpreted_iana_timezone=zone,
                  timezone_mapping_basis="PAHO Americas public notice timezone label")
    api = info.get("endDate")
    if isinstance(api, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", api):
        try:
            delta = (date.fromisoformat(api) - calendar).days
        except ValueError:
            result["api_end_date_invalid"] = True
        else:
            result["api_end_date_minus_public_date_days"] = delta
            if delta not in (0, 1):
                result.update(kind="conflicting_public_deadline_claims", utc_resolved=False, closes_at=None)
    return result


def apply_public_fields(job: JobRecord, info: dict[str, Any]) -> JobRecord:
    if job.source_id != "paho_workday":
        return job
    resolution = public_fields(info)
    fields = resolution["public_fields"]
    job.raw = {**job.raw, "_paho_public_field_resolution": resolution}
    job.employment_type = fields.get("Contractual Agreement")
    job.department = fields.get("Organization")
    job.location = fields.get("Primary Location") or clean_text(info.get("location"))
    job.posted_at = None
    job.closes_at = datetime.fromisoformat(resolution["closes_at"]) if resolution["utc_resolved"] else None
    job.closes_at_local, job.closes_tz = resolution["closes_at_local"], resolution["closes_tz"]
    return job
