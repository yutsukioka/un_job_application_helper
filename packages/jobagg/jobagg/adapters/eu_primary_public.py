"""Public fields from the observed EUIPO/EUDA primary vacancy PDF layouts."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import re
import unicodedata
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from jobagg.models import JobRecord, OrganizationSource
from jobagg.normalize import build_job


def _text(value):
    return unicodedata.normalize("NFC", " ".join(str(value).split()))


def _sha(value):
    return hashlib.sha256(value.encode()).hexdigest()


def _one(pattern, text):
    values = re.findall(pattern, text, re.I | re.S)
    if len(values) != 1:
        raise ValueError("Primary PDF public field missing or ambiguous: " + pattern)
    return _text(values[0])


def _url(value, host):
    parsed = urlsplit(value)
    if parsed.scheme != "https" or parsed.netloc != host or parsed.query or parsed.fragment:
        raise ValueError("Primary PDF/source URL has an unreviewed host or suffix")
    return parsed


def render_public_notice(source: OrganizationSource, *, external_id: str, summary_url: str,
                         primary_url: str, page_units: list[dict], document_proof: dict,
                         required_attachment_urls: list[str], source_conflicts: list | None = None) -> JobRecord:
    if source.id != "eu_careers_static":
        raise ValueError("Primary PDF public field renderer requires EU source")
    summary = _url(summary_url, "eu-careers.europa.eu")
    if not re.fullmatch(r"/en/job-opportunities/[^/]+/" + re.escape(external_id), summary.path):
        raise ValueError("EU summary does not bind this reference")
    if (not isinstance(page_units, list) or len(page_units) < 2
            or any(not isinstance(unit, dict) or not isinstance(unit.get("text"), str) for unit in page_units)
            or [unit.get("page") for unit in page_units] != list(range(1, len(page_units) + 1))
            or document_proof.get("job_key") != source.id + ":" + external_id
            or document_proof.get("url") != primary_url
            or document_proof.get("page_count") != len(page_units)
            or any(not re.fullmatch(r"[a-f0-9]{64}", str(document_proof.get(key, ""))) for key in (
                "content_sha256", "extracted_text_sha256", "ordered_page_text_sha256"))
            or document_proof.get("ordered_page_text_sha256") != _sha("\n".join(unit["text"] for unit in page_units))):
        raise ValueError("Primary PDF pages/reference/document proof differ")
    body = _text("\n".join(unit["text"] for unit in page_units))
    if len(body) < 3000 or primary_url not in required_attachment_urls:
        raise ValueError("Full primary PDF body and required primary link are required")
    local, utc, zone, posting = None, None, None, None
    if source_conflicts is not None and (not isinstance(source_conflicts, list)
                                        or any(not isinstance(item, dict) for item in source_conflicts)):
        raise ValueError("Source conflicts must retain explicit reviewed claim objects")
    conflicts = list(source_conflicts or [])
    first = _text(page_units[0]["text"])
    if external_id.startswith("ext-"):
        primary = _url(primary_url, "euipo.europa.eu")
        identity = re.fullmatch(r"ext-(\d{2})-(\d{2})-ad-(\d+)-(cpd|boa)", external_id)
        if not identity or not primary.path.startswith("/tunnel-web/secure/webdav/guest/document_library/contentPdfs/about_euipo/vacancies/"):
            raise ValueError("Unreviewed EUIPO PDF path/reference")
        if not primary.path.rsplit("/", 1)[-1].lower().startswith(
                f"ext-{identity[1]}-{identity[2]}-ad-{identity[3]}-") or not primary.path.lower().endswith(".pdf"):
            raise ValueError("EUIPO primary filename/reference differs")
        labels = ["Job title", "Function group/grade", "Type of contract", "Reference", "Deadline for applications",
                  "Place of employment", "Reserve list (RL) valid until", "Number of candidates on RL"]
        values = {}
        for label, following in zip(labels, labels[1:] + ["The European Union Intellectual Property Office"]):
            values[label] = _one(re.escape(label) + r"\s+(.+?)\s+" + re.escape(following), first)
        reference = re.sub(r"[^a-z0-9]", "", values["Reference"].lower())
        if reference != re.sub(r"[^a-z0-9]", "", external_id):
            raise ValueError("Public PDF reference differs from board reference")
        title, contract, grade, location = (values[key] for key in (
            "Job title", "Type of contract", "Function group/grade", "Place of employment"))
        if contract != "Temporary Agent" or grade != "AD " + identity[3] or location != "Alicante, SPAIN":
            raise ValueError("Unreviewed EUIPO public contract/grade/location")
        department = _one(r"in the (Cooperation and Partnerships Department|Boards of Appeal Operations Area) of the EUIPO", first)
        closing = re.fullmatch(r"(\d{2}/\d{2}/\d{4}) (\d{2}:\d{2}) Alicante time \(CET\)", values["Deadline for applications"])
        if not closing:
            raise ValueError("EUIPO public closing clock requires review")
        naive = datetime.strptime(closing[1] + " " + closing[2], "%d/%m/%Y %H:%M")
        zone = "Europe/Madrid"
        local = naive.isoformat()
        offset = naive.replace(tzinfo=ZoneInfo(zone)).utcoffset()
        if offset is not None and offset.total_seconds() == 3600:
            utc = naive.replace(tzinfo=ZoneInfo(zone)).astimezone(timezone.utc)
            local = naive.replace(tzinfo=ZoneInfo(zone)).isoformat()
        else:
            conflicts.append({"field": "closing_time", "public_label": values["Deadline for applications"],
                              "reason": "Alicante named-place summer offset differs from the printed CET abbreviation; UTC instant unresolved."})
        for section in ("BACKGROUND", "DUTIES", "ELIGIBILITY CRITERIA"):
            if section not in body:
                raise ValueError("EUIPO full public section missing: " + section)
        provider = "euipo_reviewed_primary_pdf"
    elif re.fullmatch(r"ca\d{6}", external_id):
        primary = _url(primary_url, "www.euda.europa.eu")
        reference = f"CA.{external_id[2:6]}.{external_id[6:8]}"
        if not re.fullmatch(r"/system/files/documents/\d{4}-\d{2}/" + re.escape(reference.lower()) + r"-call-for-applications-[^/]+\.pdf", primary.path):
            raise ValueError("EUDA primary filename/reference differs")
        title = _one(r"No\s+" + re.escape(reference) + r"\s+[—–-]\s+(.+?)\s+Contract agent FG", first)
        contract_match = re.search(r"Contract agent (FG [IVX]+) [—–-] (\d+-year contract)", first)
        if not contract_match:
            raise ValueError("EUDA public contract/function-group missing")
        contract, grade = "Contract agent", contract_match[1]
        department = _one(r"within the (.+?\(SHR\) unit),", first)
        sector = _one(r"Head of the (.+?\(DHAB\) sector)\.", first)
        location = "Lisbon, Portugal"
        if "Based in Lisbon" not in first or "Lisbon, Portugal" not in first:
            raise ValueError("EUDA public location absent")
        closing_label = _one(r"The closing date for the submission of applications is (\d{2}/\d{2}/\d{4} at \d{2}\.\d{2}, Lisbon time)\.", body)
        closing = re.fullmatch(r"(\d{2}/\d{2}/\d{4}) at (\d{2})\.(\d{2}), Lisbon time", closing_label)
        zone = "Europe/Lisbon"
        zoned = datetime.strptime(closing[1] + " " + closing[2] + ":" + closing[3], "%d/%m/%Y %H:%M").replace(tzinfo=ZoneInfo(zone))
        local, utc = zoned.isoformat(), zoned.astimezone(timezone.utc)
        publication = _one(r"Date of publication:\s*(\d{2}/\d{2}/\d{4})", body)
        posting = datetime.strptime(publication, "%d/%m/%Y").date().isoformat()
        values = {"Reference": reference, "Job title": title, "Type of contract": contract,
                  "Function group/grade": grade, "Contract duration": contract_match[2], "Unit": department,
                  "Sector": sector, "Place of employment": location, "Deadline for applications": closing_label,
                  "Date of publication": publication}
        for section in ("Main duties", "Eligibility criteria", "Selection procedure"):
            if section.casefold() not in body.casefold():
                raise ValueError("EUDA full public section missing: " + section)
        provider = "euda_reviewed_primary_pdf"
    else:
        raise ValueError("Primary PDF public field layout not reviewed")
    proof = {"record_kind": "detail", "provider": provider, "source_id": source.id,
             "external_id": external_id, "summary_url": summary_url, "primary_url": primary_url,
             "public_fields": values, "body_sha256": _sha(body), "page_count": len(page_units),
             "document_proof_sha256": _sha(json.dumps(document_proof, sort_keys=True, ensure_ascii=False)),
             "posting_time_resolved": False, "public_posting_calendar_date": posting,
             "utc_resolved": utc is not None, "closes_at": utc.isoformat() if utc else None,
             "closes_at_local": local, "closes_tz": zone, "source_content_conflicts": conflicts}
    job = build_job(source, title=title, external_id=external_id, description=body, source_url=summary_url,
                    apply_url=primary_url, location=location, department=department, employment_type=contract,
                    raw={"parser": "eu_official_detail", "detail_url": summary_url,
                         "detail_fetch_url": primary_url, "official_vacancy_url": primary_url,
                         "official_notice_text": body, "required_attachment_urls": list(dict.fromkeys(required_attachment_urls)),
                         "identity_verification": "official_link_and_title_or_reference", "grade": grade,
                         "_eu_official_field_resolution": proof, "public_primary_page_units": page_units,
                         "public_primary_document_proof": document_proof,
                         "reviewed_source_content_conflicts": list(source_conflicts or []),
                         "source_content_conflicts": conflicts})
    job.description = body
    job.posted_at = None
    job.closes_at, job.closes_at_local, job.closes_tz = utc, local, zone
    return job
