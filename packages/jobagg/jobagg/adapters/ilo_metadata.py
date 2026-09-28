"""ILO's English, French and Spanish public metadata header."""

from __future__ import annotations

import re
from datetime import UTC, date, datetime
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from typing import Any

from jobagg.models import JobRecord

_LABELS = {
    "Grade": "grade", "Grado": "grade",
    "Job ID": "public_job_id",
    "Vacancy no.": "vacancy_number", "No. du poste": "vacancy_number", "Número de la vacante": "vacancy_number",
    "Department": "department", "Départment": "department", "Département": "department", "Departamento": "department",
    "Organization Unit": "organization_unit", "Unité": "organization_unit", "Unidad": "organization_unit",
    "Location": "location", "Lieu d'affectation": "location", "Lugar de destino": "location",
    "Contract type": "contract_type", "Type de contrat": "contract_type", "Tipo de contrato": "contract_type",
    "Contract duration": "contract_duration", "Durée du contract": "contract_duration",
    "Durée du contrat": "contract_duration", "Duración del contrato": "contract_duration",
    "Publication date": "publication_date", "Date de publication": "publication_date", "Fecha de publicación": "publication_date",
}
_MONTHS = {name: number for number, group in enumerate((
    "january janvier enero", "february février febrero", "march mars marzo", "april avril abril",
    "may mai mayo", "june juin junio", "july juillet julio", "august août agosto",
    "september septembre septiembre", "october octobre octubre", "november novembre noviembre",
    "december décembre diciembre",
), 1) for name in group.split()}


class _HeaderLines(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.lines: list[str] = []
        self.started = False
        self.finished = False
        self.ignored = 0

    def flush(self) -> None:
        line = re.sub(r"\s+", " ", "".join(self.parts)).strip()
        self.parts = []
        if re.match(r"^(?:Grade|Grado)\s*:", line):
            self.started = True
        if self.started and line and not self.finished:
            self.lines.append(line)
        if len(self.lines) > 30:
            self.finished = True

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style"}:
            self.ignored += 1
        if self.ignored:
            return
        if tag in {"br", "p", "div", "hr"}:
            self.flush()
        if tag == "hr" and self.started:
            self.finished = True

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style"}:
            self.ignored = max(0, self.ignored - 1)
        if not self.ignored and tag in {"p", "div"}:
            self.flush()

    def handle_data(self, data: str) -> None:
        if not self.ignored and not self.finished:
            self.parts.append(data)


def public_metadata(html_text: str) -> dict[str, Any] | None:
    parser = _HeaderLines()
    parser.feed(html_text)
    parser.close()
    parser.flush()
    values, labels = {}, {}
    for line in parser.lines:
        label, separator, value = line.partition(":")
        canonical = _LABELS.get(label.strip())
        if not separator or canonical is None or not value.strip():
            continue
        value = value.strip()
        if canonical in values and values[canonical] != value:
            raise ValueError("Conflicting ILO public metadata: " + canonical)
        values[canonical] = value
        labels[canonical] = label.strip()
    if not values.get("grade") or not str(values.get("public_job_id") or "").isdigit():
        return None
    local_date = None
    public_date = values.get("publication_date", "")
    parts = public_date.lower().replace(" de ", " ").split()
    if len(parts) == 3 and parts[1] in _MONTHS:
        try:
            local_date = date(int(parts[2]), _MONTHS[parts[1]], int(parts[0])).isoformat()
        except ValueError:
            pass
    return {"values": values, "labels": labels, "publication_date_local": local_date,
            "publication_date_precision": "public_calendar_date" if local_date else "unparsed_or_missing_public_date",
            "posted_at_utc_resolved": False}


def apply_public_metadata(job: JobRecord, html_text: str, *, detail: bool) -> JobRecord:
    if job.source_id != "ilo_successfactors":
        return job
    metadata = public_metadata(html_text)
    if metadata is None:
        return job
    values = metadata["values"]
    for column, field in (("location", "location"), ("department", "department"), ("employment_type", "contract_type")):
        if values.get(field):
            setattr(job, column, values[field])
    job.raw = {**job.raw, "ilo_public_fields": {metadata["labels"][key]: value for key, value in values.items()},
               "grade": values["grade"], "_ilo_field_resolution": {**metadata, "record_kind": "detail" if detail else "listing"}}
    if values.get("contract_type"):
        job.raw["contract_type"] = values["contract_type"]
    # Keep the explicit publisher RSS timestamp separately from the less
    # precise job-page label. Do not derive midnight from the calendar date.
    publication = None
    raw_timestamp = job.raw.get("pubDate")
    if isinstance(raw_timestamp, str):
        try:
            publication = datetime.fromisoformat(raw_timestamp.replace("Z", "+00:00"))
        except ValueError:
            try:
                publication = parsedate_to_datetime(raw_timestamp)
            except (ValueError, TypeError, OverflowError):
                pass
    if publication and publication.tzinfo is not None:
        job.raw["_ilo_field_resolution"].update(rss_publication_timestamp=raw_timestamp,
                                                rss_claimed_publication_utc=publication.astimezone(UTC).isoformat())
        if metadata["publication_date_local"] in (None, publication.date().isoformat()):
            job.posted_at = publication.astimezone(UTC)
            job.raw["_ilo_field_resolution"].update(posted_at_utc_resolved=True, posted_at_precision="explicit_rss_timestamp")
        else:
            job.posted_at = None
            job.raw["_ilo_field_resolution"]["publication_claims_conflict"] = True
    elif metadata["publication_date_local"]:
        job.posted_at = None
    return job
