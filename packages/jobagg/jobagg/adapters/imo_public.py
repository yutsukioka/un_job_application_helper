"""Source-bound date-only precision for the public IMO vacancy API."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from dataclasses import asdict
import hashlib
import json
import re
from typing import Any

from jobagg.normalize import clean_text
from jobagg.models import JobRecord

MARKER = "_imo_public_date_precision"
DATE_KEYS = (
    "dateofissue",
    "deadlineforapplications",
    "jobCloseDateExternal",
    "jobCloseDateInternal",
)

BODY_KEYS = (
    "jobDescription", "purposeforthepost", "maindutiesandresponsibilities",
    "requiredcompetencies", "professionalexperience", "education",
    "languageskills", "otherskills", "contractInformation", "salaryinformation",
    "essentialCompetencies", "desiredCompetencies", "salary",
    "competencyQuestions", "backgroundQuestions",
)
PUBLIC_KEYS = DATE_KEYS + BODY_KEYS


def public_claims(raw):
    """Snapshot the finite API inputs used by this source-bound date marker."""
    return deepcopy({key: raw[key] for key in PUBLIC_KEYS if key in raw})


def _calendar(value):
    if not isinstance(value, str):
        return None
    text = value.strip()
    if re.fullmatch(r"\d{2}/\d{2}/\d{4}", text):
        return datetime.strptime(text, "%d/%m/%Y").date().isoformat()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        return datetime.strptime(text, "%Y-%m-%d").date().isoformat()
    return None


def _binding(raw: dict[str, Any], row: dict[str, Any]):
    from jobagg.adapters.imo import _description

    identity = str(raw.get("jobVacancyId", ""))
    if (
        row.get("source_id") != "imo_api"
        or row.get("ats_family") != "imo_api"
        or not re.fullmatch(r"[1-9][0-9]*", identity)
        or str(row.get("external_id")) != identity
        or row.get("source_url") != f"https://recruit.imo.org/vacancies/{identity}"
        or row.get("apply_url") != row.get("source_url")
    ):
        raise ValueError("IMO public calendar source/identity/URL mismatch")
    description = clean_text(_description(raw))
    if not description or description != row.get("description"):
        raise ValueError("IMO public calendar full API body mismatch")
    fields, claims = {}, {}
    posted = _calendar(raw.get("dateofissue"))
    if posted:
        fields["posted_at"] = None
        claims["posting"] = {
            "literal": raw["dateofissue"],
            "calendar": posted,
            "precision": "date",
            "timezone": None,
        }
    closing_key = next(
        (
            key
            for key in DATE_KEYS[1:]
            if raw.get(key) and not str(raw[key]).startswith("0001-01-01")
        ),
        None,
    )
    closing = _calendar(raw[closing_key]) if closing_key else None
    if closing:
        fields.update(closes_at=None, closes_at_local=closing, closes_tz=None)
        claims["closing"] = {
            "field": closing_key,
            "literal": raw[closing_key],
            "calendar": closing,
            "precision": "date",
            "timezone": None,
        }
    return {
        "schema_version": 1,
        "source_id": "imo_api",
        "external_id": identity,
        "source_url": row["source_url"],
        "apply_url": row["apply_url"],
        "description_sha256": hashlib.sha256(description.encode()).hexdigest(),
        "raw_date_claims": {key: raw[key] for key in DATE_KEYS if key in raw},
        "source_public_claims_sha256": hashlib.sha256(
            json.dumps(public_claims(raw), sort_keys=True, ensure_ascii=False).encode()
        ).hexdigest(),
        "public_calendar_claims": claims,
        "owned_fields": fields,
        "whole_job_certified": False,
    }


def apply_public_date_precision(job):
    if job.source_id != "imo_api" or not job.description:
        # A source row without public prose must remain in the listing inventory.
        # It cannot receive this full-body-bound precision marker; the worker's
        # separate detail-quality gate will hold it until fresh detail exists.
        return job
    resolution = _binding(job.raw, asdict(job))
    if resolution["owned_fields"]:
        for key, value in resolution["owned_fields"].items():
            setattr(job, key, value)
        job.raw = {**job.raw, MARKER: resolution}
    return job


def bound_public_date_fields(raw, row):
    """Return explicit unknowns only after re-rendering their exact source body."""
    if MARKER not in raw:
        return {}
    row = asdict(row) if isinstance(row, JobRecord) else dict(row)
    expected = _binding(raw, row)
    if raw[MARKER] != expected or not expected["owned_fields"]:
        raise ValueError("IMO public calendar marker differs from source claims")
    if any(row[key] != value for key, value in expected["owned_fields"].items()):
        raise ValueError("IMO public calendar normalized fields differ")
    return dict(expected["owned_fields"])


def restore_raw_public_claims(target, claims):
    """Restore validated current body/date inputs after a previous-raw merge."""
    for key in PUBLIC_KEYS:
        if key in claims:
            target[key] = deepcopy(claims[key])
        else:
            target.pop(key, None)
