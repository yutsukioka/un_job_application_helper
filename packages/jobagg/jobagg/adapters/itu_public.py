"""Read ITU's English/French public vacancy header, retaining source claims."""

from __future__ import annotations

from datetime import UTC, date, datetime
from html.parser import HTMLParser
import re
from typing import Any

from jobagg.models import JobRecord

_LABELS = {
    "Vacancy notice no": "notice_number", "Numéro de l'avis de vacance": "notice_number",
    "Sector": "sector", "Secteur": "sector",
    "Department": "department", "Département": "department", "Départment": "department",
    "Country of contract": "country", "Pays du contrat": "country",
    "Duty station": "duty_station", "Lieu d'affectation": "duty_station",
    "Position number": "position_number", "Numéro de poste": "position_number",
    "Grade": "grade", "Type of contract": "contract_type", "Type de contrat": "contract_type",
    "Duration of contract": "contract_duration", "Durée du contrat": "contract_duration",
    "Recruitment open to": "recruitment_scope", "Type de publication": "recruitment_scope",
    "Application deadline (Midnight Geneva Time)": "deadline",
    "Date limite de candidature (Minuit heure de Genève)": "deadline",
}
_MONTHS = {name: number for number, group in enumerate((
    "january janvier", "february février", "march mars", "april avril", "may mai", "june juin",
    "july juillet", "august août", "september septembre", "october octobre",
    "november novembre", "december décembre",
), 1) for name in group.split()}


class _Header(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.lines: list[str] = []
        self.meta: dict[str, str] = {}
        self.started = False
        self.finished = False
        self.ignored = 0

    def flush(self) -> None:
        text = re.sub(r"\s+", " ", "".join(self.parts)).strip()
        self.parts.clear()
        if text.startswith(("Vacancy notice no:", "Numéro de l'avis de vacance:")):
            self.started = True
        if self.started and text and not self.finished:
            if text in {"ORGANIZATIONAL UNIT", "UNITE ORGANISATIONNELLE"}:
                self.finished = True
            else:
                self.lines.append(text)
        if len(self.lines) > 30:
            self.finished = True

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if tag == "meta" and attributes.get("itemprop") in {"datePosted", "validThrough"}:
            key, value = attributes["itemprop"], attributes.get("content") or ""
            if key in self.meta and self.meta[key] != value:
                raise ValueError("Conflicting ITU publisher metadata")
            self.meta[key] = value
        if tag in {"script", "style"}:
            self.ignored += 1
        if not self.ignored and tag in {"br", "p", "div", "h2"}:
            self.flush()

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style"}:
            self.ignored = max(0, self.ignored - 1)
        if not self.ignored and tag in {"p", "div", "h2"}:
            self.flush()

    def handle_data(self, text: str) -> None:
        if not self.ignored and not self.finished:
            self.parts.append(text)


def _value(value: str) -> str | None:
    return value if value and not re.search(r"\[\[[^\]]+\]\]", value) else None


def _explicit_utc(value: str | None) -> datetime | None:
    if not value or not re.fullmatch(r"[A-Z][a-z]{2} [A-Z][a-z]{2} \d{1,2} \d{2}:\d{2}:\d{2} UTC \d{4}", value):
        return None
    try:
        return datetime.strptime(value, "%a %b %d %H:%M:%S UTC %Y").replace(tzinfo=UTC)
    except ValueError:
        return None


def public_fields(html_text: str) -> dict[str, Any] | None:
    parser = _Header()
    parser.feed(html_text)
    parser.close()
    parser.flush()
    values, labels = {}, {}
    for line in parser.lines:
        for label, canonical in _LABELS.items():
            if line == label or line.startswith(label + ":") or (canonical == "deadline" and line.startswith(label + " ")):
                value = line[len(label):].lstrip(": ").strip()
                if canonical in values and values[canonical] != value:
                    raise ValueError("Conflicting ITU public header: " + canonical)
                values[canonical], labels[canonical] = value, label
                break
    if not str(values.get("notice_number") or "").isdigit() or set(values) != set(_LABELS.values()):
        return None
    deadline_date = None
    match = re.fullmatch(r"(\d{1,2}) ([^ ]+) (\d{4})", values["deadline"])
    if match and match[2].lower() in _MONTHS:
        try:
            deadline_date = date(int(match[3]), _MONTHS[match[2].lower()], int(match[1])).isoformat()
        except ValueError:
            pass
    posted = _explicit_utc(parser.meta.get("datePosted"))
    expires = _explicit_utc(parser.meta.get("validThrough"))
    return {
        "record_kind": "detail", "provider": "itu_labelled_public_notice",
        "values": values, "labels": labels,
        "unknown_or_placeholder_fields": sorted(key for key, value in values.items() if _value(value) is None),
        "publisher_metadata": parser.meta,
        "publisher_posted_at_utc": posted.isoformat() if posted else None,
        "publisher_closes_at_utc": expires.isoformat() if expires else None,
        "posting_time_resolved": posted is not None,
        "public_deadline_calendar_date": deadline_date,
        "public_deadline_timezone": "Europe/Zurich",
        "public_deadline_clock": "Midnight",
        "utc_resolved": False,
        "unknown_deadline_reason": "public_midnight_day_boundary_not_explicit",
    }


def apply_public_fields(job: JobRecord, html_text: str) -> JobRecord:
    if job.source_id != "itu_successfactors":
        return job
    resolution = public_fields(html_text)
    if resolution is None:
        return job
    values = resolution["values"]
    job.department = _value(values["department"])
    job.employment_type = _value(values["contract_type"])
    location = list(dict.fromkeys(value for key in ("duty_station", "country") if (value := _value(values[key]))))
    job.location = ", ".join(location) or None
    job.posted_at = datetime.fromisoformat(resolution["publisher_posted_at_utc"]) if resolution["posting_time_resolved"] else None
    job.closes_at = None
    job.closes_at_local = resolution["public_deadline_calendar_date"]
    job.closes_tz = resolution["public_deadline_timezone"]
    job.raw = {
        **job.raw, "detail_html": html_text, "parser": "successfactors_detail",
        "itu_public_fields": {resolution["labels"][key]: value for key, value in values.items()},
        "grade": _value(values["grade"]), "position_number": _value(values["position_number"]),
        "contract_type": _value(values["contract_type"]), "_itu_public_field_resolution": resolution,
    }
    return job
