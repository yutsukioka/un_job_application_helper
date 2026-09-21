"""Public browser regression: IOM notice categories are not contracts."""
import json
import re
from pathlib import Path
from types import SimpleNamespace

from jobagg.adapters.base import AdapterContext
from jobagg.adapters.oracle_hcm import OracleHCMAdapter, _contract_type
from jobagg.db import JobDatabase
from jobagg.models import OrganizationSource


def test_iom_full_public_body_contract_and_repeat_listing_merge(tmp_path):
    payload = json.loads((Path(__file__).parent / "fixtures/oracle/iom_public_22916_20260913.json").read_text())
    source = OrganizationSource(id="iom_oracle_hcm", name="IOM", ats_family="oracle_hcm",
                                base_url="https://example.org", extra={"site_number": "CX_1001"})
    adapter = OracleHCMAdapter(AdapterContext(source=source, http=SimpleNamespace()))
    job = adapter.parse_jobs({"items": [payload]})[0]
    assert job.employment_type == "Fixed-term (1 year with possibility of extension)"
    text = re.sub(r"\s", "", job.description)
    assert len(text) == 4559  # Independently read full public browser sections.
    fnv = 2166136261
    for char in text:
        fnv = ((fnv ^ ord(char)) * 16777619) & 0xffffffff
    assert fnv == 0x62ca597e
    assert "three (2) years" in job.description  # Retain the source contradiction.
    db = JobDatabase(tmp_path / "jobs.sqlite3")
    db.initialize()
    db.upsert_job(job)
    listing = {key: value for key, value in payload.items() if key not in (
        "ExternalDescriptionStr", "ExternalResponsibilitiesStr", "ExternalQualificationsStr")}
    listing["requisitionFlexFields"] = [{"Prompt": "Recruiting Type", "Value": "General Service"}]
    for _ in range(2):
        db.upsert_job(adapter.parse_jobs({"items": [listing]})[0])
        stored = db.get_job(job.identity_key())
        assert stored["employment_type"] == job.employment_type
        assert stored["description"] == job.description


def test_iom_without_public_contract_stays_unknown():
    assert _contract_type({"requisitionFlexFields": [
        {"Prompt": "Vacancy Type", "Value": "Vacancy Notice"},
        {"Prompt": "Recruiting Type", "Value": "General Service"},
    ]}, source_id="iom_oracle_hcm") is None
    assert _contract_type({"requisitionFlexFields": [
        {"Prompt": "Vacancy Type", "Value": "Fixed Term"},
    ]}, source_id="undp_oracle_hcm") == "Fixed Term"


def test_iom_contract_resolution_distinguishes_empty_listing_keys_from_observed_detail():
    source=OrganizationSource(id="iom_oracle_hcm",name="IOM",ats_family="oracle_hcm",base_url="https://example.org")
    adapter=OracleHCMAdapter(AdapterContext(source,SimpleNamespace()))
    listing={"Id":"22910","Title":"Public missing body","ExternalResponsibilitiesStr":None,"ExternalQualificationsStr":None,
             "requisitionFlexFields":[{"Prompt":"Vacancy Type","Value":"Vacancy Notice"}]}
    assert "_oracle_contract_resolution"not in adapter.parse_jobs({"items":[listing]})[0].raw
    detail=adapter.parse_jobs({"items":[listing]},public_detail_observed=True)[0]
    assert detail.raw["_oracle_contract_resolution"] == {"record_kind":"detail","public_contract_type":None,"resolved":False,"source_field":None}
    assert detail.description is None
    assert detail.employment_type is None
    full={**listing,"ExternalDescriptionStr":"Duties and requirements are actually present.",
          "requisitionFlexFields":[{"Prompt":"Contract Type","Value":"Special Short Term Graded (Up to 9 months)"}]}
    marker=adapter.parse_jobs({"items":[full]})[0].raw["_oracle_contract_resolution"]
    assert marker["public_contract_type"] == "Special Short Term Graded (Up to 9 months)"
    assert marker["source_field"] == "requisitionFlexFields.Contract Type"
    assert marker["resolved"] is True


def test_teaser_suppression_preserves_distinct_public_text_and_section_order():
    from jobagg.oracle_public import public_description_parts
    assert public_description_parts("<p>Shared introduction.</p>","<div>Shared introduction.</div><p>Full details.</p>")==["<div>Shared introduction.</div><p>Full details.</p>"]
    assert public_description_parts("Distinct introduction.","Duties.","Qualifications.")==["Distinct introduction.","Duties.","Qualifications."]
    assert public_description_parts(None,"Duties.",None,"Qualifications.")==["Duties.","Qualifications."]


def test_only_whole_unfilled_teasers_are_suppressed_with_a_real_body():
    from jobagg.oracle_public import public_description_parts
    placeholder = "Apply by: DD/MM/YYYY\nPROVIDE A SHORT SUMMARY OF THE JOB VACANCY"
    assert public_description_parts(placeholder, "Actual duties.", "Actual requirements.") == [
        "Actual duties.", "Actual requirements."]
    assert public_description_parts(placeholder) == [placeholder]
    for summary in ("Apply by: 12/09/2026\nPROVIDE A SHORT SUMMARY OF THE JOB VACANCY",
                    "Apply by: DD/MM/YYYY\nActual additional eligibility requirement."):
        assert public_description_parts(summary, "Actual duties.") == [summary, "Actual duties."]


def test_actual_iom_22776_browser_body_and_raw_template_survive_listing_refresh(tmp_path):
    import hashlib
    import unicodedata
    payload = json.loads((Path(__file__).parent / "fixtures/oracle/iom_template_teaser_22776_20260913.json").read_text())
    source = OrganizationSource(id="iom_oracle_hcm", name="IOM", ats_family="oracle_hcm",
                                base_url="https://example.org", extra={"site_number": "CX_1001"})
    adapter = OracleHCMAdapter(AdapterContext(source, SimpleNamespace()))
    job = adapter.parse_jobs({"items": [payload]})[0]
    body = "".join(unicodedata.normalize("NFKC", job.description).split())
    assert len(body) == 16769
    assert hashlib.sha256(body.encode()).hexdigest() == "d44c2750f94a4bc1884172b99f457d00c3f4c55df309e2863293c738b5bcef74"
    assert job.raw["ShortDescriptionStr"] == payload["ShortDescriptionStr"]
    db = JobDatabase(tmp_path / "jobs.sqlite3")
    db.initialize()
    db.upsert_job(job)
    listing = {k: v for k, v in payload.items() if k not in (
        "ExternalDescriptionStr", "ExternalResponsibilitiesStr", "ExternalQualificationsStr")}
    for _ in range(2):
        db.upsert_job(adapter.parse_jobs({"items": [listing]})[0])
        stored = db.get_job(job.identity_key())
        assert stored["description"] == job.description
        assert stored["raw"]["ShortDescriptionStr"] == payload["ShortDescriptionStr"]
