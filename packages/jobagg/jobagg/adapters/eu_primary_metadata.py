"""Explicit metadata from observed EU agency primary-notice layouts.

The notice remains verbatim in raw. This projection does not certify linked
attachments, wrapper amendments, or PDF extraction/visual review completeness.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import re
import unicodedata
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from jobagg.models import JobRecord

MARKER = "_eu_primary_metadata_resolution"
PREVIOUS = "_eu_primary_previous_text_spacing_resolution"
FIELDS = ("title", "location", "department", "employment_type", "posted_at", "closes_at",
          "closes_at_local", "closes_tz")
HOSTS = {"eu-lisa-": "erecruitment.eulisa.europa.eu", "euosha-": "euosha.gestmax.eu",
         "etf-": "www.etf.europa.eu", "euaa-": "careers.euaa.europa.eu", "f4e-": "f4e-jobs.gestmax.eu"}


def _sha(value):
    return hashlib.sha256(value.encode()).hexdigest()


def _text(value):
    return unicodedata.normalize("NFC", " ".join(value.split()))


def _one(pattern, body):
    matches = list(re.finditer(pattern, body, re.I))
    if len(matches) != 1:
        raise ValueError("Primary public label missing or ambiguous: " + pattern)
    return matches[0]


def supports_reference(identity):
    return any(str(identity).startswith(prefix) for prefix in HOSTS)


def _clock(date, clock, zone):
    local = datetime.strptime(date + " " + clock, "%d %B %Y %H:%M").replace(tzinfo=ZoneInfo(zone))
    return local.astimezone(timezone.utc), local.isoformat(), zone


def apply_public_fields(job: JobRecord, notice_text: str) -> JobRecord:
    """Project only source-labelled fields; reject unknown reference/layouts."""
    source, identity = job.source_id, str(job.external_id)
    summary, official = urlsplit(job.source_url or ""), urlsplit(job.apply_url or "")
    if (source != "eu_careers_static" or summary.scheme != "https" or summary.netloc != "eu-careers.europa.eu"
            or summary.query or summary.fragment
            or not re.fullmatch(r"/en/job-opportunities/[^/]+/" + re.escape(identity), summary.path)
            or official.scheme != "https" or official.netloc != official.hostname or official.fragment
            or not any(identity.startswith(prefix) and official.netloc == host for prefix, host in HOSTS.items())
            or (identity.startswith("euaa-") and (official.path != "/" or official.query))
            or (not identity.startswith("euaa-") and not official.path.lower().endswith(".pdf"))
            or job.raw.get("official_notice_text") != notice_text or notice_text not in (job.description or "")
            or job.raw.get("official_vacancy_url") != job.apply_url
            or job.raw.get("detail_fetch_url") != job.apply_url
            or str(job.raw.get("external_id")) != identity
            or job.raw.get("detail_url") != job.source_url
            or job.raw.get("parser") != "eu_official_detail"
            or job.raw.get("identity_verification") != "official_link_and_title_or_reference"):
        raise ValueError("Primary notice source, URL, identity, or full body mismatch")
    body = _text(notice_text)
    if len(body) < 3000:
        raise ValueError("Full primary notice required")
    claims, conflicts = {}, []
    posting, department, unit, grade, title, location = None, None, None, None, None, None
    if identity.startswith("eu-lisa-"):
        heading = _one(r"VACANCY NOTICE [–—-] (.+?) \((AD\d+)\) Ref\. (eu-LISA/\d+/TA/AD\d+/\d+\.\d+)", body)
        title, grade, reference = heading.groups()
        if re.sub(r"[^a-z0-9]", "", reference.lower()) != re.sub(r"[^a-z0-9]", "", identity):
            raise ValueError("eu-LISA public reference differs")
        header = _one(r"Date of publication: (\d{2} \w+ \d{4}) (Unit(?: and Department)?) (.+?) Contract Duration (.+?) Function Group/Grade (AD\d+) \((Temporary Staff)\) Place of Employment (.+?) Working model", body)
        publication, org_label, org_value, duration, header_grade, contract, location = header.groups()
        if header_grade != grade or location != "Tallinn (Estonia)":
            raise ValueError("eu-LISA public grade/location differs")
        if org_label == "Unit and Department":
            parts = org_value.split(" / ")
            if len(parts) != 2 or not parts[0].endswith(" Unit") or not parts[1].endswith(" Department"):
                raise ValueError("eu-LISA unit/department labels ambiguous")
            unit, department = parts
        else:
            unit = department = org_value
        posting = datetime.strptime(publication, "%d %B %Y").date().isoformat()
        deadline = _one(r"Deadline for Application (\d{2} \w+ \d{4})(1)? (\d{1,2}:\d{2} [ap]m) Tallinn time / (\d{1,2}:\d{2} [ap]m) Strasbourg time Validity", body)
        date, footnote, tallinn, strasbourg = deadline.groups()
        utc, local, zone = _clock(date, datetime.strptime(tallinn, "%I:%M %p").strftime("%H:%M"), "Europe/Tallinn")
        second = _clock(date, datetime.strptime(strasbourg, "%I:%M %p").strftime("%H:%M"), "Europe/Paris")[0]
        if second != utc:
            utc, local = None, datetime.fromisoformat(local).replace(tzinfo=None).isoformat()
            conflicts.append({"field": "closing_time", "public_claim": deadline[0], "reason": "The two explicit public place clocks disagree."})
        claims.update(reference=reference, title=heading[0], organization_label=org_label, organization_value=org_value,
                      contract_and_grade=header[0], contract_duration=duration, publication=publication,
                      deadline=deadline[0], deadline_footnote_marker=footnote)
    elif identity.startswith("euosha-"):
        header = _one(r"(EUOSHA/TA/\d{2}/\d{2}) [–—-] (.+?) \((AST\d+)\)3 1 JOB FRAMEWORK", body)
        reference, title, grade = header.groups()
        if reference.lower().replace("/", "-") != identity:
            raise ValueError("EU-OSHA public reference differs")
        engagement = _one(r"The contract of employment is pursuant to Article 2\(f\).+?for a long-term contract of (.+?) as (Temporary Agent) Function Group (AST), grade (\d+)\s*,", body)
        if grade != engagement[3] + engagement[4]:
            raise ValueError("EU-OSHA grade conflicts with engagement section")
        contract = engagement[2]
        organization = _one(r"Procurement team within the (Resource and Service Centre Unit \(RSC\))\.", body)
        department = unit = organization[1]
        if "city of Bilbao, Spain" not in body:
            raise ValueError("EU-OSHA public location missing")
        location = "Bilbao, Spain"
        deadline = _one(r"The application must be submitted in the eRecruitment tool by no later than \w+ (\d{1,2} \w+ \d{4}) at (\d{2})h(\d{2}) Bilbao Time\.", body)
        utc, local, zone = _clock(deadline[1], deadline[2] + ":" + deadline[3], "Europe/Madrid")
        claims.update(reference=reference, title=header[0], organization=organization[0], main_contract=engagement[0],
                      deadline=deadline[0], alternate_contract_offer="Contract Agent, Function Group III" if "(Contract Agent, Function Group III)" in body else None)
    elif identity.startswith("etf-"):
        header = _one(r"Location: (.+?) Contract: (temporary agent), function group (AD) grade (\d+) Deadline: (\d{1,2} \w+ \d{4}) Ref: (ETF/REC/\d{2}/\d{2})", body)
        location, contract, group, number, calendar, reference = header.groups()
        if reference.lower().replace("/", "-") != identity:
            raise ValueError("ETF public reference differs")
        title = _one(r"#JOINTHEETF 01 (.+?) Join the European Training Foundation", body)[1]
        organization = _one(r"The digital specialist, as well as members of the digital team, is part of the (Corporate Services Unit) and reports directly to the Head of Unit\.", body)
        department = unit = organization[1]
        grade, contract = group + number, "Temporary Agent"
        deadline = _one(r"closing date of (\d{1,2} \w+ \d{4}) at (\d{2})\.(\d{2}) \(Turin time\)\.", body)
        if deadline[1] != calendar or location != "Turin, Italy":
            raise ValueError("ETF primary dates/location disagree")
        utc, local, zone = _clock(deadline[1], deadline[2] + ":" + deadline[3], "Europe/Rome")
        claims.update(reference=reference, header=header[0], organization=organization[0], deadline=deadline[0])
    elif identity.startswith("euaa-"):
        header = _one(r"Reference: (EUAA/\d{4}/(?:TA|CA)/\d{3}) Publication: External Title of function: (.+?) Category and grade: ((?:Temporary|Contract) Agent)\* [–—-] ((?:AD|AST|FG) (?:\d+|[IVX]+)) 1\. European Union Agency", body)
        reference, title, contract, grade = header.groups()
        if reference.lower().replace("/", "-") != identity or (("/TA/" in reference) != (contract == "Temporary Agent")):
            raise ValueError("EUAA public reference/contract differs")
        places = list(re.finditer(r"The place of employment is (Malta)([.,])", body))
        if len(places) != 1:
            raise ValueError("EUAA actual place of employment missing")
        location = places[0][1]
        place_claim = body[places[0].start():body.index(".", places[0].end() - 1) + 1]
        if title == "Internal Control and Risk Management Assistant":
            organization = _one(r"Under the supervision and line management of the Head of (Internal Control and Compliance Unit),", body)
        elif title == "Head of Corporate Security Unit":
            organization = _one(r"Lead and manage the (Corporate Security Unit) and advise the DED on all security matters", body)
            variant = "Head of Corporate and Security Unit (CSU)"
            if variant in body:
                conflicts.append({"field": "unit_name", "public_claim": variant, "reason": "Job description includes 'and'; public title and explicit task use Corporate Security Unit."})
        elif title == "Operations Officer":
            organization = _one(r"will be working in the (Operational and Technical Assistance Unit \(OTAU\)) within the (Operational Support Centre \(C1\))\.", body)
            claims["centre"] = organization[2]
        else:
            raise ValueError("EUAA organizational scope requires current public label review")
        department = unit = organization[1]
        deadline = _one(r"The closing date for the submission of applications is (\d{1,2} \w+ \d{4}) at (12:00) pm \(noon - Malta time\)\.", body)
        utc, local, zone = _clock(deadline[1], deadline[2], "Europe/Malta")
        claims.update(reference=reference, header=header[0], organization=organization[0], location=place_claim, deadline=deadline[0])
    elif identity.startswith("f4e-"):
        header = _one(r"Reference Grade Location Closing date (F4E/TA/(AD\d+)/\d{4}/\d+) (Temporary Agent) (AD\d+) (Barcelona, Spain)i (\d{2}/\d{2}/\d{4}) [‐–—-] (\d{2}:\d{2}) \(CET\)", body)
        reference, grade, contract, grade2, location, date, clock = header.groups()
        if reference.lower().replace("/", "-") != identity or grade != grade2:
            raise ValueError("F4E public reference/grade differs")
        organization = _one(r"Apply to become (.+?) within our (Administration Department \(ADMIN\)) of 'Fusion for Energy'\. As a member of the (People & Culture Unit),", body)
        title, department, unit = organization.groups()
        deadline = _one(r"No later than (\d{2}/\d{2}/\d{4}) at (\d{2})h(\d{2}) Barcelona time\.", body)
        if deadline[1] != date or deadline[2] + ":" + deadline[3] != clock:
            raise ValueError("F4E public calendar/clock claims disagree")
        naive = datetime.strptime(date + " " + clock, "%d/%m/%Y %H:%M")
        zone = "Europe/Madrid"
        zoned = naive.replace(tzinfo=ZoneInfo(zone))
        utc, local = zoned.astimezone(timezone.utc), zoned.isoformat()
        if zoned.utcoffset().total_seconds() != 3600:
            utc, local = None, naive.isoformat()
            conflicts.append({"field": "closing_time", "public_claims": [header[0], deadline[0]], "reason": "Printed CET differs from named Barcelona seasonal offset; UTC instant unresolved."})
        publication = _one(r"Vacancy published on F4E website on (\d{2}/\d{2}/\d{4})\.", body)[1]
        posting = datetime.strptime(publication, "%d/%m/%Y").date().isoformat()
        claims.update(reference=reference, header=header[0], organization=organization[0], deadline=deadline[0], publication=publication)
    else:
        raise ValueError("Unreviewed primary metadata layout")
    job.title, job.location, job.department, job.employment_type = title, location, department, contract
    job.posted_at, job.closes_at, job.closes_at_local, job.closes_tz = None, utc, local, zone
    job.normalized_hash = None
    previous = job.raw.get(PREVIOUS)
    job.raw[MARKER] = {
        "record_kind": "detail", "provider": "eu_labelled_primary_notice", "source_id": source,
        "external_id": identity, "source_url": job.source_url, "apply_url": job.apply_url,
        "official_notice_text_sha256": _sha(notice_text), "description_sha256": _sha(job.description),
        "canonical_fields": {name: (getattr(job, name).isoformat() if isinstance(getattr(job, name), datetime)
                                    else getattr(job, name)) for name in FIELDS},
        "public_grade": grade, "public_unit": unit, "public_claims": claims,
        "public_posting_calendar_date": posting, "posting_time_resolved": False,
        "utc_resolved": utc is not None, "source_conflicts": conflicts,
        "previous_text_proof_sha256": _sha(json.dumps(previous, sort_keys=True, ensure_ascii=False)) if previous else None,
        "scope": "Explicit primary-notice title, contract, organizational unit, location and date precision; remaining text stays verbatim.",
        "whole_job_complete": False, "wrapper_text_metadata_reconciled": False,
    }
    return job
