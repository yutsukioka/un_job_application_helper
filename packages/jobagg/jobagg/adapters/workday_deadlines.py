"""Resolve only recognized public Workday deadline headings.

The CXS ``endDate`` is retained as a separate provider claim. A calendar date
cannot supply an exact UTC deadline, and stale prose must not silently override
a materially different API date.
"""

from __future__ import annotations

import re
from datetime import UTC, date, datetime, timedelta, timezone
from typing import Any

from jobagg.models import JobRecord
from jobagg.normalize import clean_text

PUBLIC_DEADLINE_SOURCES = frozenset({"wfp_workday", "unhcr_workday", "wto_workday"})
_MONTHS = {name.lower(): number for number, name in enumerate((
    "January", "February", "March", "April", "May", "June", "July", "August",
    "September", "October", "November", "December",
), 1)}
_HEADINGS = {
    "wfp_workday": r"DEADLINE FOR APPLICATIONS\b",
    "unhcr_workday": r"Deadline for Applications\b",
    "wto_workday": r"Application Deadline\s*:",
}


def _date_claim(source_id: str, text: str) -> tuple[date, str, str] | None:
    if source_id == "wfp_workday":
        match = re.match(r"(\d{1,2})\s+([A-Za-z]+)\s+(\d{4})\b", text)
        if match:
            value = date(int(match[3]), _MONTHS[match[2].lower()], int(match[1]))
    elif source_id == "unhcr_workday":
        match = re.match(r"([A-Za-z]+)\s+(\d{1,2}),\s*(\d{4})\b", text)
        if match:
            value = date(int(match[3]), _MONTHS[match[1].lower()], int(match[2]))
    else:
        match = re.match(r"(\d{2})-(\d{2})-(\d{4})\b", text)
        if match:
            value = date(int(match[3]), int(match[2]), int(match[1]))
    if not match:
        return None
    return value, match[0], text[match.end():].lstrip()


def public_deadline_resolution(source_id: str, info: dict[str, Any]) -> dict[str, Any] | None:
    """Return public claims with explicit precision; never infer a local cutoff."""
    if source_id not in PUBLIC_DEADLINE_SOURCES:
        return None
    result: dict[str, Any] = {
        "record_kind": "detail", "kind": "missing_public_label", "utc_resolved": False,
        "closes_at": None, "closes_at_local": None, "closes_tz": None,
        "api_end_date": info.get("endDate"),
        "public_field": "jobPostingInfo.jobDescription",
    }
    text = clean_text(info.get("jobDescription") or info.get("description")) or ""
    heading = re.match(_HEADINGS[source_id], text, re.I)
    if not heading:
        return result
    result.update(kind="unparsed_public_label", public_label=heading[0])
    try:
        claim = _date_claim(source_id, text[heading.end():].lstrip())
    except (ValueError, KeyError):
        return result
    if claim is None:
        return result
    calendar, label, rest = claim
    result.update(kind="public_calendar_date_only", closes_at_local=calendar.isoformat(),
                  public_date=label, public_calendar_date=calendar.isoformat())
    utc_value = None
    if source_id == "wfp_workday":
        clock = re.match(r"[-–]\s*(\d{2}):(\d{2})\b", rest)
        if clock:
            try:
                local = datetime.combine(calendar, datetime.min.time()).replace(
                    hour=int(clock[1]), minute=int(clock[2]))
            except ValueError:
                result["kind"] = "unparsed_public_clock"
                return result
            result.update(kind="unknown_public_timezone", closes_at_local=local.isoformat(timespec="minutes"),
                          public_clock=clock[0].lstrip("-– "))
            zone_text = rest[clock.end():].lstrip()
            numeric = re.match(r"[-–]\s*GMT([+-])(\d{2}):(\d{2})(?![\d:])", zone_text)
            greenwich = re.match(r"[-–]\s*GMT\s+Greenwich Mean Time\b", zone_text, re.I)
            offset = None
            if numeric and int(numeric[2]) < 24 and int(numeric[3]) < 60:
                offset = timedelta(hours=int(numeric[2]), minutes=int(numeric[3]))
                if numeric[1] == "-":
                    offset = -offset
                result["closes_tz"] = "UTC" + numeric[1] + numeric[2] + ":" + numeric[3]
                result["public_timezone"] = numeric[0].lstrip("-– ")
            elif greenwich:
                offset = timedelta(0)
                result.update(closes_tz="UTC", public_timezone=greenwich[0].lstrip("-– "))
            else:
                # In September the observed "GMT United Kingdom Time (London)"
                # label contradicts civil time. Retain it, without guessing DST.
                result["public_timezone_unresolved"] = zone_text[:120]
            if offset is not None:
                utc_value = local.replace(tzinfo=timezone(offset)).astimezone(UTC)
                result.update(kind="explicit_public_clock_and_offset", utc_resolved=True,
                              closes_at=utc_value.isoformat(), public_claimed_utc=utc_value.isoformat())
    # Workday's date-only endDate commonly denotes the following calendar day.
    # That boundary is compatible with the label, but larger discrepancies are
    # competing provider claims (including stale text and malformed API years).
    api_date = info.get("endDate")
    if isinstance(api_date, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", api_date):
        try:
            delta = (date.fromisoformat(api_date) - (utc_value.date() if utc_value else calendar)).days
        except ValueError:
            result["api_date_invalid"] = True
        else:
            result["api_date_minus_public_date_days"] = delta
            if delta not in (0, 1):
                result.update(kind="conflicting_public_deadline_claims", utc_resolved=False, closes_at=None,
                              conflict_reason="API endDate differs from the public deadline beyond the next-day boundary")
    return result


def apply_public_deadline(job: JobRecord, info: dict[str, Any]) -> JobRecord:
    resolution = public_deadline_resolution(job.source_id, info)
    if resolution is None:
        return job
    job.raw = {**job.raw, "_workday_deadline_resolution": resolution}
    job.closes_at = datetime.fromisoformat(resolution["closes_at"]) if resolution["utc_resolved"] else None
    job.closes_at_local = resolution["closes_at_local"]
    job.closes_tz = resolution["closes_tz"]
    return job
