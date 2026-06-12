import sqlite3

import pytest

import jobagg.classification.pipeline as classification_pipeline
from jobagg.classification import classify_database
from jobagg.classification.classifiers import ccog as ccog_classifier
from jobagg.classification.classifiers.ccog import (
    _full_markdown_path,
    _keyword_score,
    ccog_tree,
    collapse_ccog_to_medium,
    get_ccog_family,
    get_ccog_medium,
    normalize_ccog_code,
)
from jobagg.classification.classifiers.contract import classify_contract
from jobagg.classification.grade_mapping import grade_mapping_rows
from jobagg.classification.models import ContractCategory, FeatureBundle, GradeResult
from jobagg.db import JobDatabase
from jobagg.filters.explain import explain_job_match
from jobagg.filters.facets import facet_counts
from jobagg.filters.query import search_collected_jobs, search_vacancies
from jobagg.filters.schemas import VacancyFilters, VacancySearchRequest
from jobagg.models import OrganizationSource
from jobagg.normalize import build_job


def test_classifies_wfp_logistics_assistant(tmp_path):
    db = JobDatabase(tmp_path / "jobs.sqlite3")
    db.initialize()
    source = OrganizationSource(
        id="wfp_workday",
        name="World Food Programme",
        ats_family="workday",
        base_url="https://example.org",
    )
    db.upsert_job(
        build_job(
            source,
            title="Logistics Assistant - G5 (Multiple locations)",
            external_id="JR1",
            location="Multiple locations",
            description="Support warehouse, inventory, transport and shipping operations.",
            apply_url="https://example.org/jobs/JR1",
            raw={"locationsText": "Multiple locations", "externalPath": "/jobs/JR1"},
        )
    )

    assert classify_database(db, source_id="wfp_workday") == 1

    row = next(db.iter_jobs_with_classification(source_id="wfp_workday"))
    assert row["grade_family"] == "G"
    assert row["grade_code"] == "G5"
    assert row["grade_mapping_organization"] == "World Food Programme"
    assert row["grade_mapping_raw_grade_code"] == "G-5"
    assert row["standard_grade_family"] == "UN General Service"
    assert row["standard_seniority_tier"] == "T2_JUNIOR_PROFESSIONAL"
    assert row["standard_scope"] == "Local"
    assert row["standard_un_equivalent"] == "G-5"
    assert row["contract_category"] == "staff_other"
    assert row["national_international"] == "local"
    assert row["work_modality"] == "multiple_locations"
    assert row["ccog_primary_code"] == "2.2.06"
    assert row["needs_review"] is False
    assert search_collected_jobs(db, VacancySearchRequest(ccog_families=["2.2.06"])).total == 1
    assert len(search_vacancies(db, VacancyFilters(ccog_family="2.2.06"))) == 1
    explanation = explain_job_match(
        db,
        "wfp_workday:JR1",
        VacancySearchRequest(ccog_families=["2.2.06"]),
    )
    assert explanation["matched"] is True


def test_city_only_location_recomputes_onsite_modality(tmp_path):
    db = JobDatabase(tmp_path / "jobs.sqlite3")
    db.initialize()
    source = OrganizationSource(
        id="unicef_pageup",
        name="UNICEF",
        ats_family="pageup",
        base_url="https://jobs.unicef.org",
    )
    db.upsert_job(
        build_job(
            source,
            title="Programme Officer, P-3",
            external_id="city-only",
            location="Nairobi",
            apply_url="https://jobs.unicef.org/jobs/city-only",
        )
    )

    classify_database(db)
    row = next(db.iter_jobs_with_classification(source_id="unicef_pageup"))

    assert row["city"] == "Nairobi"
    assert row["country_iso3"] == "KEN"
    assert row["work_modality"] == "onsite"


def test_classification_recomputes_when_rule_digest_changes(tmp_path, monkeypatch):
    db = JobDatabase(tmp_path / "jobs.sqlite3")
    db.initialize()
    source = OrganizationSource(
        id="unicef_pageup",
        name="UNICEF",
        ats_family="pageup",
        base_url="https://jobs.unicef.org",
    )
    db.upsert_job(
        build_job(
            source,
            title="Programme Officer, P-3, Nairobi, Kenya",
            external_id="rule-cache",
            location="Nairobi, Kenya",
            apply_url="https://jobs.unicef.org/jobs/rule-cache",
        )
    )

    monkeypatch.setattr(classification_pipeline, "_classification_rules_digest", lambda: "rules-a")
    assert classify_database(db) == 1
    assert classify_database(db) == 0

    monkeypatch.setattr(classification_pipeline, "_classification_rules_digest", lambda: "rules-b")
    assert classify_database(db) == 1


def test_classifies_unv_specialist_and_filters_facets(tmp_path):
    db = JobDatabase(tmp_path / "jobs.sqlite3")
    db.initialize()
    source = OrganizationSource(
        id="unv_uvp",
        name="UN Volunteers",
        ats_family="unv",
        base_url="https://app.unv.org",
    )
    db.upsert_job(
        build_job(
            source,
            title="Access to Finance Specialist",
            external_id="100",
            location="Rwanda",
            apply_url="https://app.unv.org/opportunities/100",
            raw={
                "name": "Access to Finance Specialist",
                "country": {"longDescription": "Rwanda", "props": {"codeISO2": "RW"}},
                "dutyStations": [{"longDescription": "Kigali"}],
                "categoryName": {
                    "value": {"code": "SPECIALIST"},
                    "longDescription": "Specialist",
                },
                "volunteerType": {"longDescription": "International"},
                "workLocation": {"longDescription": "On UN premises"},
                "workArrangement": {"longDescription": "Full time"},
                "assignmentDuration": {"longDescription": "12 months"},
                "hoursWeek": {"longDescription": "40"},
                "hostEntity": {"institution": {"longDescription": "UNDP"}},
                "sdgType": {"longDescription": "No poverty"},
                "expertiseAreas": [{"longDescription": "Economics and finance"}],
                "isOnsite": True,
            },
        )
    )

    classify_database(db)
    rows = search_vacancies(
        db,
        VacancyFilters(
            contract_category="volunteering_unv",
            national_international="unv_international",
            region="Africa",
        ),
    )
    assert len(rows) == 1
    row = rows[0]
    assert row["unv_category"] == "un_volunteer_specialist"
    assert row["grade_mapping_organization"] == "United Nations Volunteers"
    assert row["grade_mapping_raw_grade_code"] == "International UNV Specialist"
    assert row["standard_seniority_tier"] == "T2_JUNIOR_PROFESSIONAL"
    assert row["standard_un_equivalent"] == "~P-2 functional only"
    assert row["work_modality"] == "onsite"
    assert row["country_iso3"] == "RWA"
    assert row["city"] == "Kigali"
    assert row["ccog_primary_code"] == "1.L.09"

    facets = facet_counts(db, VacancyFilters(region="Africa"))
    assert facets["contract_categories"]["volunteering_unv"] == 1
    assert facets["unv_categories"]["un_volunteer_specialist"] == 1


def test_spanish_con_does_not_imply_consultant_grade_or_contract(tmp_path):
    db = JobDatabase(tmp_path / "jobs.sqlite3")
    db.initialize()
    source = OrganizationSource(
        id="wfp_workday",
        name="World Food Programme",
        ats_family="workday",
        base_url="https://example.org",
    )
    db.upsert_job(
        build_job(
            source,
            title="Asistente de logística con experiencia - G5",
            external_id="spanish-con",
            location="Panama City, Panama",
            description="Trabaja con equipos de logística y almacén.",
            apply_url="https://example.org/jobs/spanish-con",
        )
    )

    classify_database(db)
    row = next(db.iter_jobs_with_classification(source_id="wfp_workday"))

    assert row["grade_code"] == "G5"
    assert row["contract_category"] == "staff_other"


def test_classification_schema_and_manual_override(tmp_path):
    db = JobDatabase(tmp_path / "jobs.sqlite3")
    db.initialize()
    source = OrganizationSource(
        id="unicef_pageup",
        name="UNICEF",
        ats_family="pageup",
        base_url="https://jobs.unicef.org",
    )
    job = build_job(
        source,
        title="Fund Raising Officer (Acquisition lead) NO-B, Fixed-Term",
        external_id="200",
        location="Bangkok, Thailand",
        apply_url="https://jobs.unicef.org/jobs/200",
    )
    db.upsert_job(job)
    classify_database(db)
    stored = next(db.iter_jobs_with_classification(source_id="unicef_pageup"))
    assert stored["grade_code"] == "NOB"
    assert stored["contract_category"] == "fixed_term_appointment_staff"
    assert stored["ccog_primary_code"] == "1.A.10.c"
    assert stored["ccog_family_code"] == "1.A"
    assert stored["ccog_family_label"] == "Administrative specialists"

    db.upsert_classification_override(
        vacancy_id=job.identity_key(),
        field_name="ccog_primary_code",
        override_value="1.A.01",
        reason="test correction",
    )
    classify_database(db)
    overridden = next(db.iter_jobs_with_classification(source_id="unicef_pageup"))
    assert overridden["ccog_primary_code"] == "1.A.01"
    assert overridden["ccog_primary_label"] == "Financial management specialists"
    assert overridden["ccog_family_code"] == "1.A"
    assert overridden["ccog_family_label"] == "Administrative specialists"
    assert overridden["ccog_confidence"] == 1.0

    db.upsert_classification_override(
        vacancy_id=job.identity_key(),
        field_name="city",
        override_value="Nairobi",
        reason="test location correction",
    )
    db.upsert_classification_override(
        vacancy_id=job.identity_key(),
        field_name="country_iso3",
        override_value="KEN",
        reason="test location correction",
    )
    classify_database(db)
    locations = list(db.iter_vacancy_locations(job.identity_key()))
    response = search_collected_jobs(
        db,
        VacancySearchRequest(cities=["Nairobi"], countries_iso3=["KEN"]),
    )
    assert len(locations) == 1
    assert locations[0]["source_field"] == "manual_override"
    assert locations[0]["city_key"] == "nairobi"
    assert locations[0]["country_iso3"] == "KEN"
    assert response.total == 1

    with sqlite3.connect(db.path) as conn:
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name LIKE 'vacancy_%'"
            )
        }
    assert {"vacancy_source_features", "vacancy_classifications", "vacancy_locations"} <= tables


def test_search_taxonomy_fields_are_classified_and_stored(tmp_path):
    db = JobDatabase(tmp_path / "jobs.sqlite3")
    db.initialize()
    source = OrganizationSource(
        id="unicef_pageup",
        name="UNICEF",
        ats_family="pageup",
        base_url="https://jobs.unicef.org",
    )
    db.upsert_job(
        build_job(
            source,
            title="Finance Officer, P-3, Nairobi, Kenya",
            external_id="taxonomy-1",
            location="Nairobi, Kenya",
            description="Lead budgeting, accounting and project financial control work.",
            apply_url="https://jobs.unicef.org/jobs/taxonomy-1",
        )
    )

    classify_database(db)
    row = next(db.iter_jobs_with_classification(source_id="unicef_pageup"))

    assert row["occupational_family_code"] == "1.A"
    assert row["occupational_medium_code"] == "1.A.01"
    assert row["mandate_network_code"] == "MAGNET"
    assert row["mandate_family_label"] == "Finance"
    assert {"budgeting", "accounting", "project_financial_control"} <= set(row["capability_tags"])
    assert row["contract_group"] == "staff"
    assert row["seniority_group"] == "mid"


def test_skills_catalog_docx_phrases_map_to_capability_tags(tmp_path):
    db = JobDatabase(tmp_path / "jobs.sqlite3")
    db.initialize()
    source = OrganizationSource(
        id="unicef_pageup",
        name="UNICEF",
        ats_family="pageup",
        base_url="https://jobs.unicef.org",
    )
    db.upsert_job(
        build_job(
            source,
            title="Emergency Programme Officer, P-3",
            external_id="skills-docx-1",
            location="Geneva, Switzerland",
            description=(
                "Support Cash-Based Programming, Security Sector Reform (SSR), "
                "Early Recovery from Disaster/ Conflict, and Out of Country Voting (OCV)."
            ),
            apply_url="https://jobs.unicef.org/jobs/skills-docx-1",
        )
    )

    classify_database(db)
    row = next(db.iter_jobs_with_classification(source_id="unicef_pageup"))

    assert {
        "cash_based_programming",
        "security_sector_reform",
        "early_recovery_disaster_conflict",
        "out_of_country_voting",
    } <= set(row["capability_tags"])


def test_grade_mapping_table_is_seeded_in_database(tmp_path):
    db = JobDatabase(tmp_path / "jobs.sqlite3")
    db.initialize()

    with db.connect() as conn:
        count = conn.execute("SELECT COUNT(*) FROM grade_mappings").fetchone()[0]
        wfp_g5 = conn.execute(
            """
            SELECT normalized_seniority_tier, approximate_un_equivalent
            FROM grade_mappings
            WHERE organization = ? AND raw_grade_code = ?
            """,
            ("World Food Programme", "G-5"),
        ).fetchone()

    assert count == len(grade_mapping_rows()) >= 700
    assert tuple(wfp_g5) == ("T2_JUNIOR_PROFESSIONAL", "G-5")


def test_adb_ti2_position_level_standardizes_from_taleo_flat_payload(tmp_path):
    db = JobDatabase(tmp_path / "jobs.sqlite3")
    db.initialize()
    source = OrganizationSource(
        id="adb_taleo",
        name="Asian Development Bank",
        ats_family="taleo",
        base_url="https://adb.taleo.net/careersection/1/jobsearch.ftl",
    )
    db.upsert_job(
        build_job(
            source,
            title="Senior Social Protection Specialist",
            external_id="260497",
            location="Asian Development Bank-India Resident Mission-India-New Delhi",
            employment_type="Technical International (Field Office)",
            description="This is a fixed term staff appointment.",
            apply_url="https://adb.taleo.net/careersection/1/jobdetail.ftl?job=260497",
            raw={
                "_taleo_flat": {
                    "JOB_LEVEL": "TI2",
                    "POSITION_LEVEL_LABEL": "Technical International (Field Office)",
                }
            },
        )
    )

    classify_database(db)
    row = next(db.iter_jobs_with_classification(source_id="adb_taleo"))

    assert row["grade_code"] == "TI2"
    assert row["grade_mapping_organization"] == "Asian Development Bank"
    assert row["grade_mapping_raw_grade_code"] == "TI2"
    assert row["standard_seniority_tier"] == "T4_SENIOR_PROFESSIONAL"
    assert row["standard_scope"] == "International"
    assert row["standard_un_equivalent"] == "~P4"
    assert row["standard_employment_category"] == "Staff"


def test_adb_tl1_position_level_uses_adb_grade_json_mapping(tmp_path):
    db = JobDatabase(tmp_path / "jobs.sqlite3")
    db.initialize()
    source = OrganizationSource(
        id="adb_taleo",
        name="Asian Development Bank",
        ats_family="taleo",
        base_url="https://adb.taleo.net/careersection/1/jobsearch.ftl",
    )
    db.upsert_job(
        build_job(
            source,
            title="Human Resource Assistant",
            external_id="260510",
            location="Asian Development Bank-Asian Development Bank Headquarters-Philippines-Manila",
            employment_type="Technical Local - HQ",
            description="This is a fixed term staff appointment.",
            apply_url="https://adb.taleo.net/careersection/1/jobdetail.ftl?job=260510",
            raw={
                "_taleo_flat": {
                    "JOB_LEVEL": "TL1",
                    "STAFF_CATEGORY": "Technical Local - HQ",
                    "POSITION_LEVEL_LABEL": "Technical Local - HQ",
                }
            },
        )
    )

    classify_database(db)
    row = next(db.iter_jobs_with_classification(source_id="adb_taleo"))

    assert row["grade_code"] == "TL1"
    assert row["grade_family"] == "TL"
    assert row["grade_mapping_raw_grade_code"] == "TL1"
    assert row["standard_seniority_tier"] == "T2_JUNIOR_PROFESSIONAL"
    assert row["standard_scope"] == "National / Local"
    assert row["standard_un_equivalent"] == "~NO-A/NO-B"
    assert row["standard_employment_category"] == "Staff"


def test_adb_m1_managerial_position_level_standardizes(tmp_path):
    db = JobDatabase(tmp_path / "jobs.sqlite3")
    db.initialize()
    source = OrganizationSource(
        id="adb_taleo",
        name="Asian Development Bank",
        ats_family="taleo",
        base_url="https://adb.taleo.net/careersection/1/jobsearch.ftl",
    )
    db.upsert_job(
        build_job(
            source,
            title="Director",
            external_id="260558",
            location="Asian Development Bank-Asian Development Bank Headquarters-Philippines-Manila",
            employment_type="Managerial International (HQ)",
            description="This is a managerial international appointment.",
            apply_url="https://adb.taleo.net/careersection/1/jobdetail.ftl?job=260558",
            raw={
                "_taleo_flat": {
                    "JOB_LEVEL": "M1",
                    "STAFF_CATEGORY": "Managerial International (HQ)",
                    "POSITION_LEVEL_LABEL": "Managerial International (HQ)",
                }
            },
        )
    )

    classify_database(db)
    row = next(db.iter_jobs_with_classification(source_id="adb_taleo"))

    assert row["grade_code"] == "M1"
    assert row["grade_mapping_raw_grade_code"] == "M1"
    assert row["standard_seniority_tier"] == "T6_DIRECTOR"
    assert row["standard_scope"] == "International"
    assert row["standard_un_equivalent"] == "~D1"


def test_fao_p_grade_from_detail_standardizes_with_mapping(tmp_path):
    db = JobDatabase(tmp_path / "jobs.sqlite3")
    db.initialize()
    source = OrganizationSource(
        id="fao_taleo",
        name="Food and Agriculture Organization",
        ats_family="taleo",
        base_url="https://jobs.fao.org/careersection/fao_external/jobsearch.ftl",
    )
    db.upsert_job(
        build_job(
            source,
            title="Food Standards Officer",
            external_id="2600978",
            location="Italy-Rome",
            employment_type="Professional",
            description="Seven years of relevant experience.",
            apply_url="https://jobs.fao.org/careersection/fao_external/jobdetail.ftl?job=2600978",
            raw={
                "_taleo_flat": {
                    "JOB_LEVEL": "P-4",
                    "Grade Level": "P-4",
                    "TYPE_OF_REQUISITION": "Professional",
                    "Type of Requisition": "Professional",
                }
            },
        )
    )

    classify_database(db)
    row = next(db.iter_jobs_with_classification(source_id="fao_taleo"))

    assert row["grade_code"] == "P4"
    assert row["grade_mapping_raw_grade_code"] == "P-4"
    assert row["standard_seniority_tier"] == "T4_SENIOR_PROFESSIONAL"
    assert row["standard_employment_category"] == "Staff"


def test_fao_n_dash_grade_standardizes_to_national_officer(tmp_path):
    db = JobDatabase(tmp_path / "jobs.sqlite3")
    db.initialize()
    source = OrganizationSource(
        id="fao_taleo",
        name="Food and Agriculture Organization",
        ats_family="taleo",
        base_url="https://jobs.fao.org/careersection/fao_external/jobsearch.ftl",
    )
    db.upsert_job(
        build_job(
            source,
            title="Assistant FAO Representative (Programme)",
            external_id="2601181",
            location="Bangladesh-Dhaka",
            employment_type="National Professional Officer",
            description="Grade Level: N-2",
            apply_url="https://jobs.fao.org/careersection/fao_external/jobdetail.ftl?job=2601181",
            raw={"_taleo_flat": {"JOB_LEVEL": "N-2", "Grade Level": "N-2"}},
        )
    )

    classify_database(db)
    row = next(db.iter_jobs_with_classification(source_id="fao_taleo"))

    assert row["grade_code"] == "NOB"
    assert row["grade_mapping_raw_grade_code"] == "NO-B"
    assert row["standard_seniority_tier"] == "T2_JUNIOR_PROFESSIONAL"
    assert row["standard_un_equivalent"] == "~P-2"


def test_fao_npp_requisition_standardizes_as_national_consultant(tmp_path):
    db = JobDatabase(tmp_path / "jobs.sqlite3")
    db.initialize()
    source = OrganizationSource(
        id="fao_taleo",
        name="Food and Agriculture Organization",
        ats_family="taleo",
        base_url="https://jobs.fao.org/careersection/fao_external/jobsearch.ftl",
    )
    db.upsert_job(
        build_job(
            source,
            title="Junior Programme Support Assistant",
            external_id="2601032",
            location="Bangladesh",
            employment_type="NPP (National Project Personnel)",
            description="National project personnel assignment supporting an international consultant.",
            apply_url="https://jobs.fao.org/careersection/fao_external/jobdetail.ftl?job=2601032",
            raw={"_taleo_flat": {"TYPE_OF_REQUISITION": "NPP (National Project Personnel)"}},
        )
    )

    classify_database(db)
    row = next(db.iter_jobs_with_classification(source_id="fao_taleo"))

    assert row["contract_category"] == "consultant"
    assert row["national_international"] == "national"
    assert row["grade_code"] is None
    assert row["grade_mapping_raw_grade_code"] == "NPP"
    assert row["standard_seniority_tier"] == "T0_NONSTAFF_UNGRADED"
    assert row["standard_employment_category"] == "National Consultant"
    assert row["standard_scope"] == "National / Local"


def test_fao_psa_requisition_standardizes_as_international_consultant(tmp_path):
    db = JobDatabase(tmp_path / "jobs.sqlite3")
    db.initialize()
    source = OrganizationSource(
        id="fao_taleo",
        name="Food and Agriculture Organization",
        ats_family="taleo",
        base_url="https://jobs.fao.org/careersection/fao_external/jobsearch.ftl",
    )
    db.upsert_job(
        build_job(
            source,
            title="National Planning and Implementation Specialist",
            external_id="2600808",
            location="Guyana",
            employment_type="PSA (Personal Services Agreement)",
            description="Personal services agreement assignment.",
            apply_url="https://jobs.fao.org/careersection/fao_external/jobdetail.ftl?job=2600808",
            raw={"_taleo_flat": {"TYPE_OF_REQUISITION": "PSA (Personal Services Agreement)"}},
        )
    )

    classify_database(db)
    row = next(db.iter_jobs_with_classification(source_id="fao_taleo"))

    assert row["contract_category"] == "consultant"
    assert row["national_international"] == "international"
    assert row["grade_code"] is None
    assert row["grade_mapping_raw_grade_code"] == "PSA"
    assert row["standard_seniority_tier"] == "T0_NONSTAFF_UNGRADED"
    assert row["standard_employment_category"] == "International Consultant"
    assert row["standard_scope"] == "International"


def test_fao_fellows_programme_standardizes_as_other_pathway(tmp_path):
    db = JobDatabase(tmp_path / "jobs.sqlite3")
    db.initialize()
    source = OrganizationSource(
        id="fao_taleo",
        name="Food and Agriculture Organization",
        ats_family="taleo",
        base_url="https://jobs.fao.org/careersection/fao_external/jobsearch.ftl",
    )
    db.upsert_job(
        build_job(
            source,
            title="Call for Expression of Interest - Fellows Programme",
            external_id="2600010",
            employment_type="Fellows Programme",
            description="Grade Level: N/A. Type of Requisition: Fellows Programme.",
            apply_url="https://jobs.fao.org/careersection/fao_external/jobdetail.ftl?job=2600010",
            raw={
                "_taleo_flat": {
                    "TYPE_OF_REQUISITION": "Fellows Programme",
                    "Type of Requisition": "Fellows Programme",
                }
            },
        )
    )

    classify_database(db)
    row = next(db.iter_jobs_with_classification(source_id="fao_taleo"))

    assert row["contract_category"] == "other"
    assert row["grade_code"] is None
    assert row["grade_mapping_raw_grade_code"] == "Fellows Programme"
    assert row["standard_seniority_tier"] == "T0_PATHWAY_OUTSIDE_SYSTEM"


def test_fao_fellows_programme_standardizes_from_title_when_taleo_field_absent(tmp_path):
    db = JobDatabase(tmp_path / "jobs.sqlite3")
    db.initialize()
    source = OrganizationSource(
        id="fao_taleo",
        name="Food and Agriculture Organization",
        ats_family="taleo",
        base_url="https://jobs.fao.org/careersection/fao_external/jobsearch.ftl",
    )
    db.upsert_job(
        build_job(
            source,
            title="Call for Expression of Interest - Fellows Programme for FAO Offices",
            external_id="2600010",
            description="The Fellows Programme is designed to attract fellows.",
            apply_url="https://jobs.fao.org/careersection/fao_external/jobdetail.ftl?job=2600010",
            raw={"_taleo_flat": {}},
        )
    )

    classify_database(db)
    row = next(db.iter_jobs_with_classification(source_id="fao_taleo"))

    assert row["contract_category"] == "other"
    assert row["grade_mapping_raw_grade_code"] == "Fellows Programme"
    assert row["standard_seniority_tier"] == "T0_PATHWAY_OUTSIDE_SYSTEM"


def test_fao_volunteer_programme_standardizes_as_other_volunteer(tmp_path):
    db = JobDatabase(tmp_path / "jobs.sqlite3")
    db.initialize()
    source = OrganizationSource(
        id="fao_taleo",
        name="Food and Agriculture Organization",
        ats_family="taleo",
        base_url="https://jobs.fao.org/careersection/fao_external/jobsearch.ftl",
    )
    db.upsert_job(
        build_job(
            source,
            title="Call for Expression of Interest - FAO Regular Volunteer Programme",
            external_id="2600018",
            employment_type="Volunteer Programme",
            description="Type of Requisition: Volunteer Programme.",
            apply_url="https://jobs.fao.org/careersection/fao_external/jobdetail.ftl?job=2600018",
            raw={
                "_taleo_flat": {
                    "TYPE_OF_REQUISITION": "Volunteer Programme",
                    "Type of Requisition": "Volunteer Programme",
                }
            },
        )
    )

    classify_database(db)
    row = next(db.iter_jobs_with_classification(source_id="fao_taleo"))

    assert row["contract_category"] == "other"
    assert row["grade_code"] is None
    assert row["grade_mapping_raw_grade_code"] == "Volunteer Programme"
    assert row["standard_employment_category"] == "Volunteer / other; not staff"


def test_unv_category_can_be_read_from_nested_category_details(tmp_path):
    db = JobDatabase(tmp_path / "jobs.sqlite3")
    db.initialize()
    source = OrganizationSource(
        id="unv_uvp",
        name="United Nations Volunteers",
        ats_family="unv",
        base_url="https://app.unv.org",
    )
    db.upsert_job(
        build_job(
            source,
            title="Research Specialist",
            external_id="nested-specialist",
            location="Rwanda",
            apply_url="https://app.unv.org/opportunities/nested-specialist",
            raw={
                "name": "Research Specialist",
                "country": {"longDescription": "Rwanda", "props": {"codeISO2": "RW"}},
                "dutyStations": [{"longDescription": "Kigali"}],
                "volunteersCategoryDetails": {
                    "categoryName": {
                        "value": {"code": "SPECIALIST"},
                        "longDescription": "Specialist",
                    },
                    "categoryType": {"longDescription": "International"},
                    "workArrangement": {"longDescription": "Full time"},
                    "assignmentDuration": {"longDescription": "12 months"},
                },
            },
        )
    )

    classify_database(db)
    row = next(db.iter_jobs_with_classification(source_id="unv_uvp"))

    assert row["contract_category"] == "volunteering_unv"
    assert row["unv_category"] == "un_volunteer_specialist"
    assert row["unv_volunteer_type"] == "unv_international"
    assert row["grade_mapping_organization"] == "United Nations Volunteers"
    assert row["grade_mapping_raw_grade_code"] == "International UNV Specialist"
    assert row["standard_seniority_tier"] == "T2_JUNIOR_PROFESSIONAL"


def test_fao_volunteer_programme_standardizes_from_title_when_taleo_field_absent(tmp_path):
    db = JobDatabase(tmp_path / "jobs.sqlite3")
    db.initialize()
    source = OrganizationSource(
        id="fao_taleo",
        name="Food and Agriculture Organization",
        ats_family="taleo",
        base_url="https://jobs.fao.org/careersection/fao_external/jobsearch.ftl",
    )
    db.upsert_job(
        build_job(
            source,
            title="Call for Expression of Interest - FAO Regular Volunteer Programme for Africa",
            external_id="2600018",
            employment_type=None,
            description="The volunteer will report to an assigned supervisor.",
            apply_url="https://jobs.fao.org/careersection/fao_external/jobdetail.ftl?job=2600018",
            raw={"_taleo_flat": {}},
        )
    )

    classify_database(db)
    row = next(db.iter_jobs_with_classification(source_id="fao_taleo"))

    assert row["contract_category"] == "other"
    assert row["grade_mapping_raw_grade_code"] == "FAO Regular Volunteer Programme"
    assert row["standard_grade_family"] == "Volunteer assignment - outside staff grade system"


def test_internship_standardizes_as_t0_pathway_even_when_grade_signal_exists(tmp_path):
    db = JobDatabase(tmp_path / "jobs.sqlite3")
    db.initialize()
    source = OrganizationSource(
        id="unicef_pageup",
        name="UNICEF",
        ats_family="pageup",
        base_url="https://jobs.unicef.org",
    )
    db.upsert_job(
        build_job(
            source,
            title="Internship - Programme Support P-2",
            external_id="intern-p2",
            location="Geneva, Switzerland",
            description="This internship is for learning and exposure.",
            apply_url="https://jobs.unicef.org/jobs/intern-p2",
        )
    )

    classify_database(db)
    row = next(db.iter_jobs_with_classification(source_id="unicef_pageup"))

    assert row["contract_category"] == "internship_unknown"
    assert row["grade_code"] == "P2"
    assert row["grade_mapping_raw_grade_code"] == "Internship"
    assert row["standard_seniority_tier"] == "T0_PATHWAY_OUTSIDE_SYSTEM"
    assert row["standard_employment_category"] == "Internship / other; outside formal staff grade system"


def test_cern_studentship_standardizes_as_t0_pathway(tmp_path):
    db = JobDatabase(tmp_path / "jobs.sqlite3")
    db.initialize()
    source = OrganizationSource(
        id="cern_custom_html",
        name="CERN",
        ats_family="custom_html",
        base_url="https://careers.cern/jobs/",
    )
    db.upsert_job(
        build_job(
            source,
            title="Technical Studentship (Applied Physics)",
            external_id="tsc-ap",
            location="Geneva, Switzerland",
            description="Studentship with a monthly net allowance in Geneva.",
            apply_url="https://careers.cern/jobs/tsc-ap/",
        )
    )

    classify_database(db)
    row = next(db.iter_jobs_with_classification(source_id="cern_custom_html"))

    assert row["contract_category"] == "internship_unknown"
    assert row["grade_mapping_raw_grade_code"] == "Studentship"
    assert row["standard_seniority_tier"] == "T0_PATHWAY_OUTSIDE_SYSTEM"
    assert row["standard_employment_category"] == "Internship / other; outside formal staff grade system"


def test_cern_explicit_grade_range_standardizes(tmp_path):
    db = JobDatabase(tmp_path / "jobs.sqlite3")
    db.initialize()
    source = OrganizationSource(
        id="cern_custom_html",
        name="CERN",
        ats_family="custom_html",
        base_url="https://careers.cern/jobs/",
    )
    db.upsert_job(
        build_job(
            source,
            title="Ceph Software Engineer",
            external_id="it-sd-gss-2026-96-ld",
            location="Geneva, Switzerland",
            description="Contract duration (in months): 60 Grade range: 6 Benchmark job: 200020 - Computing Engineer",
            apply_url="https://careers.cern/jobs/it-sd-gss-2026-96-ld/",
        )
    )

    classify_database(db)
    row = next(db.iter_jobs_with_classification(source_id="cern_custom_html"))

    assert row["grade_code"] == "Grade 6"
    assert row["grade_mapping_raw_grade_code"] == "Grade 6"
    assert row["standard_seniority_tier"] == "T4_SENIOR_PROFESSIONAL"
    assert row["standard_un_equivalent"] == "~P-3/P-4"


def test_cern_grap_experience_text_infers_grade4(tmp_path):
    db = JobDatabase(tmp_path / "jobs.sqlite3")
    db.initialize()
    source = OrganizationSource(
        id="cern_custom_html",
        name="CERN",
        ats_family="custom_html",
        base_url="https://careers.cern/jobs/",
    )
    db.upsert_job(
        build_job(
            source,
            title="Applied Physicist",
            external_id="te-mpe-pe-2026-85-grap",
            location="Geneva, Switzerland",
            description=(
                "By the application deadline, you have a master's degree with 2 to 6 years "
                "of professional experience since graduation or a PhD with a maximum of "
                "3 years of professional experience since graduation."
            ),
            apply_url="https://careers.cern/jobs/te-mpe-pe-2026-85-grap/",
        )
    )

    classify_database(db)
    row = next(db.iter_jobs_with_classification(source_id="cern_custom_html"))

    assert row["grade_code"] == "Grade 4"
    assert row["grade_mapping_raw_grade_code"] == "Grade 4"
    assert row["standard_seniority_tier"] == "T2_JUNIOR_PROFESSIONAL"
    assert row["standard_un_equivalent"] == "~P-1/P-2"


def test_cern_grae_experience_text_infers_grade2(tmp_path):
    db = JobDatabase(tmp_path / "jobs.sqlite3")
    db.initialize()
    source = OrganizationSource(
        id="cern_custom_html",
        name="CERN",
        ats_family="custom_html",
        base_url="https://careers.cern/jobs/",
    )
    db.upsert_job(
        build_job(
            source,
            title="Computational Physicist",
            external_id="be-op-ps-2026-139-grae",
            location="Geneva, Switzerland",
            description=(
                "By the application deadline, you have a maximum of 2 years of professional "
                "experience since graduation in the respective field and your highest educational "
                "qualification is either a bachelor's or master's degree. You must have a "
                "university degree and can't hold a PhD."
            ),
            apply_url="https://careers.cern/jobs/be-op-ps-2026-139-grae/",
        )
    )

    classify_database(db)
    row = next(db.iter_jobs_with_classification(source_id="cern_custom_html"))

    assert row["grade_code"] == "Grade 2"
    assert row["grade_mapping_raw_grade_code"] == "Grade 2"
    assert row["standard_seniority_tier"] == "T1_ENTRY_SUPPORT"
    assert row["standard_un_equivalent"] == "~G-3/G-4"


def test_cern_general_secondary_technician_track_infers_grade2(tmp_path):
    db = JobDatabase(tmp_path / "jobs.sqlite3")
    db.initialize()
    source = OrganizationSource(
        id="cern_custom_html",
        name="CERN",
        ats_family="custom_html",
        base_url="https://careers.cern/jobs/",
    )
    db.upsert_job(
        build_job(
            source,
            title="CMS DSS Electronics Technician",
            external_id="ep-cmx-2025-276-grae",
            location="Geneva, Switzerland",
            description=(
                "This position offers an excellent opportunity for a young professional. "
                "By the application deadline, you have a maximum of 2 years of professional "
                "experience since graduation in a technical or administrative field and your "
                "highest educational qualification is a general secondary education diploma "
                "or a shorter non-university degree. You can't hold a bachelor's degree, "
                "master's degree or PhD."
            ),
            apply_url="https://careers.cern/jobs/ep-cmx-2025-276-grae/",
        )
    )

    classify_database(db)
    row = next(db.iter_jobs_with_classification(source_id="cern_custom_html"))

    assert row["grade_code"] == "Grade 2"
    assert row["grade_mapping_raw_grade_code"] == "Grade 2"
    assert row["standard_seniority_tier"] == "T1_ENTRY_SUPPORT"


def test_cern_grade8_range_standardizes(tmp_path):
    db = JobDatabase(tmp_path / "jobs.sqlite3")
    db.initialize()
    source = OrganizationSource(
        id="cern_custom_html",
        name="CERN",
        ats_family="custom_html",
        base_url="https://careers.cern/jobs/",
    )
    db.upsert_job(
        build_job(
            source,
            title="Governance, Risk and Compliance Lead",
            external_id="cio-2026-108-ld",
            location="Geneva, Switzerland",
            description="Contract duration (in months): 60 Grade range: 8 Benchmark job: 200020 - Computing Engineer",
            apply_url="https://careers.cern/jobs/cio-2026-108-ld/",
            raw={"grade": "8"},
        )
    )

    classify_database(db)
    row = next(db.iter_jobs_with_classification(source_id="cern_custom_html"))

    assert row["grade_code"] == "Grade 8"
    assert row["grade_mapping_raw_grade_code"] == "Grade 8"
    assert row["standard_seniority_tier"] == "T6_DIRECTOR"
    assert row["standard_un_equivalent"] == "~P-5/D-1"


def test_cern_staff_work_described_as_consultancy_is_not_consultant(tmp_path):
    db = JobDatabase(tmp_path / "jobs.sqlite3")
    db.initialize()
    source = OrganizationSource(
        id="cern_custom_html",
        name="CERN",
        ats_family="custom_html",
        base_url="https://careers.cern/jobs/",
    )
    db.upsert_job(
        build_job(
            source,
            title="Data Analytics Infrastructure Engineer",
            external_id="it-da-asm-2026-95-ld",
            location="Geneva, Switzerland",
            description=(
                "Contract duration (in months): 36 Grade range: 6 Benchmark job: 200020 - "
                "Computing Engineer. Consultancy, assistance and advice to end users."
            ),
            apply_url="https://careers.cern/jobs/it-da-asm-2026-95-ld/",
        )
    )

    classify_database(db)
    row = next(db.iter_jobs_with_classification(source_id="cern_custom_html"))

    assert row["contract_category"] != "consultant"
    assert row["grade_mapping_raw_grade_code"] == "Grade 6"


def test_generic_consultant_standardizes_only_as_t0_nonstaff(tmp_path):
    db = JobDatabase(tmp_path / "jobs.sqlite3")
    db.initialize()
    source = OrganizationSource(
        id="unfpa_oracle_hcm",
        name="UNFPA",
        ats_family="oracle_hcm",
        base_url="https://example.org",
    )
    db.upsert_job(
        build_job(
            source,
            title="International Consultant P-3",
            external_id="consultant-p3",
            location="Nairobi, Kenya",
            description="International consultant assignment.",
            apply_url="https://example.org/jobs/consultant-p3",
        )
    )

    classify_database(db)
    row = next(db.iter_jobs_with_classification(source_id="unfpa_oracle_hcm"))

    assert row["contract_category"] == "consultant"
    assert row["grade_code"] == "P3"
    assert row["grade_mapping_raw_grade_code"] == "Consultant"
    assert row["standard_seniority_tier"] == "T0_NONSTAFF_UNGRADED"
    assert row["standard_employment_category"] == "Consultant / contractor"
    assert row["standard_un_equivalent"] == "No staff-grade equivalent"


def test_fixed_term_without_grade_standardizes_as_t0_staff(tmp_path):
    db = JobDatabase(tmp_path / "jobs.sqlite3")
    db.initialize()
    source = OrganizationSource(
        id="unfpa_oracle_hcm",
        name="UNFPA",
        ats_family="oracle_hcm",
        base_url="https://example.org",
    )
    db.upsert_job(
        build_job(
            source,
            title="Programme Associate",
            external_id="fixed-term-ungraded",
            location="Nairobi, Kenya",
            employment_type="Fixed Term",
            description="Fixed term staff appointment without exposed grade.",
            apply_url="https://example.org/jobs/fixed-term-ungraded",
        )
    )

    classify_database(db)
    row = next(db.iter_jobs_with_classification(source_id="unfpa_oracle_hcm"))

    assert row["contract_category"] == "fixed_term_appointment_staff"
    assert row["grade_code"] is None
    assert row["grade_mapping_raw_grade_code"] == "Fixed Term"
    assert row["standard_seniority_tier"] == "T0_STAFF_UNGRADED"
    assert row["standard_employment_category"] == "Staff"


def test_level_bearing_consultant_code_can_be_standardized(tmp_path):
    db = JobDatabase(tmp_path / "jobs.sqlite3")
    db.initialize()
    source = OrganizationSource(
        id="unops_avature",
        name="UNOPS",
        ats_family="avature",
        base_url="https://jobs.unops.org",
    )
    db.upsert_job(
        build_job(
            source,
            title="International Consultant IICA-1",
            external_id="iica-1",
            location="Copenhagen, Denmark",
            description="International consultant assignment with IICA-1 level.",
            apply_url="https://jobs.unops.org/jobs/iica-1",
        )
    )

    classify_database(db)
    row = next(db.iter_jobs_with_classification(source_id="unops_avature"))

    assert row["contract_category"] == "consultant"
    assert row["grade_code"] == "IICA1"
    assert row["grade_mapping_raw_grade_code"] == "IICA-1"
    assert row["standard_seniority_tier"] == "T2_JUNIOR_PROFESSIONAL"
    assert row["standard_employment_category"] == "Contractor"


def test_level_bearing_ssa_consultant_code_can_be_standardized(tmp_path):
    db = JobDatabase(tmp_path / "jobs.sqlite3")
    db.initialize()
    source = OrganizationSource(
        id="wfp_workday",
        name="World Food Programme",
        ats_family="workday",
        base_url="https://example.org",
    )
    db.upsert_job(
        build_job(
            source,
            title="Driver - Special Services Agreement SSA-2",
            external_id="ssa-2",
            location="Dakar, Senegal",
            description="Special Services Agreement roster.",
            apply_url="https://example.org/jobs/ssa-2",
        )
    )

    classify_database(db)
    row = next(db.iter_jobs_with_classification(source_id="wfp_workday"))

    assert row["contract_category"] == "consultant"
    assert row["grade_family"] == "SSA"
    assert row["grade_code"] == "SSA2"
    assert row["grade_mapping_raw_grade_code"] == "SSA1..SSA7"
    assert row["standard_seniority_tier"] == "T1_ENTRY_SUPPORT"


def test_imf_hiring_for_a03_a04_standardizes_from_workday_description(tmp_path):
    db = JobDatabase(tmp_path / "jobs.sqlite3")
    db.initialize()
    source = OrganizationSource(
        id="imf_workday",
        name="International Monetary Fund",
        ats_family="workday",
        base_url="https://imf.wd5.myworkdayjobs.com/IMF",
    )
    db.upsert_job(
        build_job(
            source,
            title="Administrative Coordinator (Contractual) - FADRM",
            external_id="26-R9292",
            location="USA-Washington, DC",
            description="Department: FADRM Fiscal Affairs Department Resource & Info. Management Hiring For: A03, A04",
            apply_url="https://imf.wd5.myworkdayjobs.com/IMF/job/USA-Washington-DC/Administrative-Coordinator--Contractual----FADRM_26-R9292",
            raw={},
        )
    )

    classify_database(db)
    row = next(db.iter_jobs_with_classification(source_id="imf_workday"))

    assert row["grade_code"] == "A03"
    assert row["grade_mapping_raw_grade_code"] == "A03..A04"
    assert row["standard_seniority_tier"] == "T1_ENTRY_SUPPORT"
    assert row["standard_un_equivalent"] == "~G-3/G-4"


def test_imf_a11_grade_overrides_weak_consultant_text(tmp_path):
    db = JobDatabase(tmp_path / "jobs.sqlite3")
    db.initialize()
    source = OrganizationSource(
        id="imf_workday",
        name="International Monetary Fund",
        ats_family="workday",
        base_url="https://imf.wd5.myworkdayjobs.com/IMF",
    )
    db.upsert_job(
        build_job(
            source,
            title="Economist/Sr Economist (FADT2)",
            external_id="26-R9305",
            location="Washington, DC",
            description=(
                "Hiring For: A11, A12, A13, A14. The department may coordinate "
                "with external consultants, but this is an IMF graded vacancy."
            ),
            apply_url="https://imf.wd5.myworkdayjobs.com/IMF/job/USA-Washington-DC/Economist-Sr-Economist--FADT2-_26-R9305",
            raw={},
        )
    )

    classify_database(db)
    row = next(db.iter_jobs_with_classification(source_id="imf_workday"))

    assert row["contract_category"] == "staff_other"
    assert row["grade_code"] == "A11"
    assert row["grade_mapping_raw_grade_code"] == "A11"
    assert row["standard_seniority_tier"] == "T3_MID_PROFESSIONAL"


def test_imf_b_band_standardizes_as_broad_low_confidence_band(tmp_path):
    db = JobDatabase(tmp_path / "jobs.sqlite3")
    db.initialize()
    source = OrganizationSource(
        id="imf_workday",
        name="International Monetary Fund",
        ats_family="workday",
        base_url="https://imf.wd5.myworkdayjobs.com/IMF",
    )
    db.upsert_job(
        build_job(
            source,
            title="Economist (Local) - Regional Office for Asia and the Pacific",
            external_id="26-R9163",
            location="Tokyo",
            description="Department: OAP Regional Off. for Asia & the Pacific Hiring For: B",
            apply_url="https://imf.wd5.myworkdayjobs.com/IMF/job/Tokyo/Economist--Local_26-R9163",
            raw={},
        )
    )

    classify_database(db)
    row = next(db.iter_jobs_with_classification(source_id="imf_workday"))

    assert row["grade_code"] == "B"
    assert row["grade_mapping_raw_grade_code"] == "B"
    assert row["standard_seniority_tier"] == "T6_T7"
    assert row["grade_mapping_confidence"] == "LOW"


def test_imo_classification_grade_from_api_standardizes(tmp_path):
    db = JobDatabase(tmp_path / "jobs.sqlite3")
    db.initialize()
    source = OrganizationSource(
        id="imo_api",
        name="International Maritime Organization",
        ats_family="imo_api",
        base_url="https://recruit.imo.org",
    )
    db.upsert_job(
        build_job(
            source,
            title="Finance Officer",
            external_id="1039",
            location="ADMINISTRATIVE DIVISION",
            employment_type="Fixed Term",
            description="Classification Grade: P3. The role manages finance operations.",
            apply_url="https://recruit.imo.org/vacancies/1039",
            raw={"classification": "P3", "contractType": "Fixed Term"},
        )
    )
    db.upsert_job(
        build_job(
            source,
            title="Administrative Assistant (Roster)",
            external_id="1033",
            location="CROSS-DIVISIONAL",
            employment_type="Temporary",
            description="At least four years' experience may count as 2 years for degree holders.",
            apply_url="https://recruit.imo.org/vacancies/1033",
            raw={"classification": "G4", "contractType": "Temporary"},
        )
    )

    classify_database(db, source_id="imo_api")
    rows = {
        row["external_id"]: row
        for row in db.iter_jobs_with_classification(source_id="imo_api")
    }

    assert rows["1039"]["grade_code"] == "P3"
    assert rows["1039"]["grade_mapping_raw_grade_code"] == "P-3"
    assert rows["1039"]["standard_seniority_tier"] == "T3_MID_PROFESSIONAL"
    assert rows["1033"]["grade_code"] == "G4"
    assert rows["1033"]["grade_mapping_raw_grade_code"] == "G-4"
    assert rows["1033"]["standard_seniority_tier"] == "T2_JUNIOR_PROFESSIONAL"


def test_icrc_title_maps_to_ifrc_functional_proxy(tmp_path):
    db = JobDatabase(tmp_path / "jobs.sqlite3")
    db.initialize()
    source = OrganizationSource(
        id="icrc_successfactors",
        name="International Committee of the Red Cross",
        ats_family="successfactors_rmk",
        base_url="https://careers.icrc.org",
    )
    db.upsert_job(
        build_job(
            source,
            title="Security Operations Center (SOC) Coordinator",
            external_id="1393433333",
            location="Geneva",
            employment_type="Open-ended contract",
            description="Coordinates security operations support.",
            apply_url="https://careers.icrc.org/job/1393433333",
            raw={},
        )
    )

    classify_database(db)
    row = next(db.iter_jobs_with_classification(source_id="icrc_successfactors"))

    assert row["grade_mapping_raw_grade_code"] == "Coordinator / Manager / Supervisor / Lead"
    assert row["standard_grade_family"] == "IFRC job classification functional proxy"
    assert row["standard_seniority_tier"] == "T4_SENIOR_PROFESSIONAL"
    assert row["standard_un_equivalent"] == "~P-4/P-5"


def test_globalfund_gl_e_standardizes_from_title_and_experience(tmp_path):
    db = JobDatabase(tmp_path / "jobs.sqlite3")
    db.initialize()
    source = OrganizationSource(
        id="globalfund_workday",
        name="The Global Fund",
        ats_family="workday",
        base_url="https://theglobalfund.wd1.myworkdayjobs.com/External",
    )
    db.upsert_job(
        build_job(
            source,
            title="Manager, OIG Strategy, Planning and Delivery - GL E",
            external_id="JR4595-1",
            location="Geneva",
            description="10 years of demonstrated progressive experience in strategy development and monitoring in an oversight function.",
            apply_url="https://theglobalfund.wd1.myworkdayjobs.com/External/job/Geneva/Manager_JR4595-1",
            raw={"jobDescription": "10 years of demonstrated progressive experience"},
        )
    )

    classify_database(db)
    row = next(db.iter_jobs_with_classification(source_id="globalfund_workday"))

    assert row["grade_code"] == "GL E"
    assert row["grade_mapping_raw_grade_code"] == "GL E"
    assert row["standard_seniority_tier"] == "T4_SENIOR_PROFESSIONAL"


def test_experience_requirement_fallback_uses_longest_required_years(tmp_path):
    db = JobDatabase(tmp_path / "jobs.sqlite3")
    db.initialize()
    source = OrganizationSource(
        id="unicef_pageup",
        name="UNICEF",
        ats_family="pageup",
        base_url="https://jobs.unicef.org",
    )
    db.upsert_job(
        build_job(
            source,
            title="Manager, Strategy Planning and Delivery",
            external_id="experience-only",
            location="Geneva",
            description=(
                "Qualifications include 7 years of experience in oversight operations. "
                "Desirable: 10 years of experience in strategy development and monitoring."
            ),
            apply_url="https://example.org/experience-only",
            raw={},
        )
    )

    classify_database(db)
    row = next(db.iter_jobs_with_classification(source_id="unicef_pageup"))

    assert row["grade_code"] == "EXP10"
    assert row["min_years_experience"] == 10
    assert row["grade_mapping_raw_grade_code"] == "Experience >= 10 years"
    assert row["standard_grade_family"] == "Experience-inferred functional proxy"
    assert row["standard_seniority_tier"] == "T4_SENIOR_PROFESSIONAL"


def test_aiib_global_recruitment_cfa_requirement_is_not_consultant(tmp_path):
    db = JobDatabase(tmp_path / "jobs.sqlite3")
    db.initialize()
    source = OrganizationSource(
        id="aiib_successfactors_legacy",
        name="Asian Infrastructure Investment Bank",
        ats_family="successfactors_legacy",
        base_url="https://career5.successfactors.eu/career?company=AIIB",
    )
    db.upsert_job(
        build_job(
            source,
            title="Finance Analyst/Associate, Loan Management",
            external_id="25255",
            location="Beijing",
            employment_type="Global Recruitment",
            description=(
                "Requirements include at least 3 years of experience in loan management. "
                "CFA or other relevant finance certification is preferred."
            ),
            apply_url="https://career5.successfactors.eu/sfcareer/jobreqcareer?jobId=6421&company=AIIB",
            raw={"parser": "aiib_official_detail"},
        )
    )

    classify_database(db, source_id="aiib_successfactors_legacy")
    row = next(db.iter_jobs_with_classification(source_id="aiib_successfactors_legacy"))

    assert row["contract_category"] != "consultant"
    assert row["grade_code"] == "EXP3"
    assert row["min_years_experience"] == 3
    assert row["grade_mapping_raw_grade_code"] == "Experience >= 3 years"
    assert row["standard_seniority_tier"] == "T2_JUNIOR_PROFESSIONAL"


def test_undp_ipsa_grade_from_oracle_flex_standardizes(tmp_path):
    db = JobDatabase(tmp_path / "jobs.sqlite3")
    db.initialize()
    source = OrganizationSource(
        id="undp_oracle_hcm",
        name="UNDP",
        ats_family="oracle_hcm",
        base_url="https://estm.fa.em2.oraclecloud.com",
    )
    db.upsert_job(
        build_job(
            source,
            title="Project Manager",
            external_id="34063",
            location="Home Based",
            description="Grade IPSA-9. National Personnel Service Agreement.",
            apply_url="https://estm.fa.em2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1/job/34063",
            raw={
                "requisitionFlexFields": [
                    {"Prompt": "Grade", "Value": "IPSA-9"},
                    {"Prompt": "Vacancy Type", "Value": "National Personnel Service Agreement"},
                ]
            },
        )
    )

    classify_database(db)
    row = next(db.iter_jobs_with_classification(source_id="undp_oracle_hcm"))

    assert row["grade_code"] == "IPSA-9"
    assert row["grade_mapping_raw_grade_code"] == "IPSA-9"
    assert row["standard_seniority_tier"] == "T2_JUNIOR_PROFESSIONAL"


def test_undp_npsa_agency_un_volunteers_is_not_unv_volunteer_assignment(tmp_path):
    db = JobDatabase(tmp_path / "jobs.sqlite3")
    db.initialize()
    source = OrganizationSource(
        id="undp_oracle_hcm",
        name="UNDP",
        ats_family="oracle_hcm",
        base_url="https://estm.fa.em2.oraclecloud.com",
    )
    db.upsert_job(
        build_job(
            source,
            title="Administrative Associate",
            external_id="34078",
            location="Kampala, Uganda",
            description="This vacancy is under Agency UN Volunteers but uses an NPSA contract.",
            apply_url="https://estm.fa.em2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1/job/34078",
            raw={
                "requisitionFlexFields": [
                    {"Prompt": "Grade", "Value": "NPSA-6"},
                    {"Prompt": "Vacancy Type", "Value": "National Personnel Service Agreement"},
                    {"Prompt": "Agency", "Value": "UN Volunteers"},
                ]
            },
        )
    )

    classify_database(db)
    row = next(db.iter_jobs_with_classification(source_id="undp_oracle_hcm"))

    assert row["contract_category"] == "consultant"
    assert row["grade_code"] == "NPSA-6"
    assert row["grade_mapping_raw_grade_code"] == "NPSA-5..7"
    assert row["standard_scope"] == "National / Local"
    assert row["unv_category"] != "un_volunteer_specialist"


def test_undp_nb_grade_from_oracle_flex_standardizes(tmp_path):
    db = JobDatabase(tmp_path / "jobs.sqlite3")
    db.initialize()
    source = OrganizationSource(
        id="undp_oracle_hcm",
        name="UNDP",
        ats_family="oracle_hcm",
        base_url="https://estm.fa.em2.oraclecloud.com",
    )
    db.upsert_job(
        build_job(
            source,
            title="Cleaning Support",
            external_id="32027",
            location="Santo Domingo, Dominican Republic",
            description="National Personnel Service Agreement with local support duties.",
            apply_url="https://estm.fa.em2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1/job/32027",
            raw={
                "requisitionFlexFields": [
                    {"Prompt": "Grade", "Value": "NB1"},
                    {"Prompt": "Vacancy Type", "Value": "National Personnel Service Agreement"},
                ]
            },
        )
    )

    classify_database(db)
    row = next(db.iter_jobs_with_classification(source_id="undp_oracle_hcm"))

    assert row["contract_category"] == "consultant"
    assert row["grade_code"] == "NB1"
    assert row["grade_mapping_raw_grade_code"] == "NB1"
    assert row["standard_seniority_tier"] == "T1_ENTRY_SUPPORT"
    assert row["standard_scope"] == "National / Local"


def test_unfpa_sb3_individual_consultancy_standardizes_as_level_bearing_nonstaff(tmp_path):
    db = JobDatabase(tmp_path / "jobs.sqlite3")
    db.initialize()
    source = OrganizationSource(
        id="unfpa_oracle_hcm",
        name="UNFPA",
        ats_family="oracle_hcm",
        base_url="https://estm.fa.em2.oraclecloud.com",
    )
    db.upsert_job(
        build_job(
            source,
            title="Consultants Nationaux pour l'evaluation du Programme Pays",
            external_id="34679",
            description="Vacancy Type Individual Consultancy. Grade SB3.",
            apply_url="https://estm.fa.em2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_2003/job/34679",
            raw={
                "requisitionFlexFields": [
                    {"Prompt": "Grade", "Value": "SB3"},
                    {"Prompt": "Vacancy Type", "Value": "Individual Consultancy"},
                ]
            },
        )
    )

    classify_database(db)
    row = next(db.iter_jobs_with_classification(source_id="unfpa_oracle_hcm"))

    assert row["contract_category"] == "consultant"
    assert row["grade_code"] == "SB3"
    assert row["grade_mapping_raw_grade_code"] == "SB3"
    assert row["standard_seniority_tier"] == "T2_JUNIOR_PROFESSIONAL"
    assert row["standard_employment_category"] == "National Consultant / contractor"


def test_unfpa_nob_from_oracle_flex_standardizes_as_national_officer(tmp_path):
    db = JobDatabase(tmp_path / "jobs.sqlite3")
    db.initialize()
    source = OrganizationSource(
        id="unfpa_oracle_hcm",
        name="UNFPA",
        ats_family="oracle_hcm",
        base_url="https://estm.fa.em2.oraclecloud.com",
    )
    db.upsert_job(
        build_job(
            source,
            title="Programme Analyst",
            external_id="34465",
            description="Grade NOB.",
            apply_url="https://estm.fa.em2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_2003/job/34465",
            raw={"requisitionFlexFields": [{"Prompt": "Grade", "Value": "NOB"}]},
        )
    )

    classify_database(db)
    row = next(db.iter_jobs_with_classification(source_id="unfpa_oracle_hcm"))

    assert row["grade_code"] == "NOB"
    assert row["grade_mapping_raw_grade_code"] == "NO-B"
    assert row["standard_seniority_tier"] == "T2_JUNIOR_PROFESSIONAL"


def test_unfpa_ic_title_without_flex_fields_standardizes_as_consultancy(tmp_path):
    db = JobDatabase(tmp_path / "jobs.sqlite3")
    db.initialize()
    source = OrganizationSource(
        id="unfpa_oracle_hcm",
        name="UNFPA",
        ats_family="oracle_hcm",
        base_url="https://estm.fa.em2.oraclecloud.com",
    )
    db.upsert_job(
        build_job(
            source,
            title="IC - Development of the 4Ps Parent Leaders Handbook",
            external_id="34465",
            description="Purpose of consultancy: develop handbook materials.",
            apply_url="https://estm.fa.em2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_2003/job/34465",
            raw={"requisitionFlexFields": []},
        )
    )

    classify_database(db)
    row = next(db.iter_jobs_with_classification(source_id="unfpa_oracle_hcm"))

    assert row["contract_category"] == "consultant"
    assert row["grade_mapping_raw_grade_code"] == "Consultancy"
    assert row["standard_seniority_tier"] == "T0_NONSTAFF_UNGRADED"


def test_icao_consulting_post_standardizes_as_consultant_pathway(tmp_path):
    db = JobDatabase(tmp_path / "jobs.sqlite3")
    db.initialize()
    source = OrganizationSource(
        id="icao_oracle_hcm",
        name="International Civil Aviation Organization",
        ats_family="oracle_hcm",
        base_url="https://estm.fa.em2.oraclecloud.com",
    )
    db.upsert_job(
        build_job(
            source,
            title="FRA2601 - FAF/26/020 - Auditeur ANS/ATM",
            external_id="34126",
            location="Paris",
            description="Consulting assignment for aviation audit services.",
            apply_url="https://estm.fa.em2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_3001/job/34126",
            raw={},
        )
    )

    classify_database(db)
    row = next(db.iter_jobs_with_classification(source_id="icao_oracle_hcm"))

    assert row["contract_category"] == "consultant"
    assert row["grade_mapping_raw_grade_code"] == "Consulting"
    assert row["standard_seniority_tier"] == "T0_NONSTAFF_UNGRADED"


def test_oracle_special_service_agreement_text_standardizes_as_consultant(tmp_path):
    db = JobDatabase(tmp_path / "jobs.sqlite3")
    db.initialize()
    source = OrganizationSource(
        id="icao_oracle_hcm",
        name="International Civil Aviation Organization",
        ats_family="oracle_hcm",
        base_url="https://estm.fa.em2.oraclecloud.com",
    )
    db.upsert_job(
        build_job(
            source,
            title="Auditeur ANS/CNS",
            external_id="34126",
            description="Rémunération: Special Service Agreement (SSA) for five months.",
            apply_url="https://estm.fa.em2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_3001/job/34126",
            raw={},
        )
    )

    classify_database(db)
    row = next(db.iter_jobs_with_classification(source_id="icao_oracle_hcm"))

    assert row["contract_category"] == "consultant"
    assert row["contract_subtype"] == "Special Service Agreement (SSA)"
    assert row["grade_mapping_raw_grade_code"] == "Consultant"
    assert row["standard_seniority_tier"] == "T0_NONSTAFF_UNGRADED"


def test_iaea_pip_taleo_post_standardizes_as_consultant(tmp_path):
    db = JobDatabase(tmp_path / "jobs.sqlite3")
    db.initialize()
    source = OrganizationSource(
        id="iaea_taleo",
        name="International Atomic Energy Agency",
        ats_family="taleo",
        base_url="https://iaea.taleo.net/careersection/ex/jobsearch.ftl",
    )
    db.upsert_job(
        build_job(
            source,
            title="Consultant - Technical Cooperation",
            external_id="PIP-TC20141224-001",
            description="Consultant position supporting technical cooperation assignments.",
            apply_url="https://iaea.taleo.net/careersection/ex/jobdetail.ftl?job=PIP-TC20141224-001",
            raw={},
        )
    )

    classify_database(db)
    row = next(db.iter_jobs_with_classification(source_id="iaea_taleo"))

    assert row["contract_category"] == "consultant"
    assert row["grade_mapping_raw_grade_code"] == "Consultant"
    assert row["standard_seniority_tier"] == "T0_NONSTAFF_UNGRADED"


def test_icc_visiting_professional_standardizes_as_pathway(tmp_path):
    db = JobDatabase(tmp_path / "jobs.sqlite3")
    db.initialize()
    source = OrganizationSource(
        id="icc_successfactors_legacy",
        name="International Criminal Court",
        ats_family="successfactors_legacy",
        base_url="https://career5.successfactors.eu/career?company=1657261P",
    )
    db.upsert_job(
        build_job(
            source,
            title="Visiting Professional - Chambers (funded by the EC grant)",
            external_id="3861",
            apply_url="https://career5.successfactors.eu/career?company=1657261P&career_job_req_id=3861",
            raw={},
        )
    )

    classify_database(db)
    row = next(db.iter_jobs_with_classification(source_id="icc_successfactors_legacy"))

    assert row["contract_category"] == "other"
    assert row["grade_mapping_raw_grade_code"] == "Visiting Professional"
    assert row["standard_seniority_tier"] == "T0_PATHWAY_OUTSIDE_SYSTEM"


def test_icc_p2_staff_grade_overrides_weak_ssa_text(tmp_path):
    db = JobDatabase(tmp_path / "jobs.sqlite3")
    db.initialize()
    source = OrganizationSource(
        id="icc_successfactors_legacy",
        name="International Criminal Court",
        ats_family="successfactors_legacy",
        base_url="https://career5.successfactors.eu/career?company=1657261P",
    )
    db.upsert_job(
        build_job(
            source,
            title="Associate Human Resources Officer (P-2) - JPO - Republic of Korea",
            external_id="24421",
            description="This staff vacancy text also contains an unrelated standalone SSA token.",
            apply_url="https://career5.successfactors.eu/career?company=1657261P&career_job_req_id=24421",
            raw={},
        )
    )

    classify_database(db)
    row = next(db.iter_jobs_with_classification(source_id="icc_successfactors_legacy"))

    assert row["contract_category"] == "staff_other"
    assert row["grade_code"] == "P2"
    assert row["grade_mapping_raw_grade_code"] == "P-2"
    assert row["standard_grade_family"] == "UN International Professional"


def test_icc_internship_title_overrides_generic_consultancy_noise(tmp_path):
    db = JobDatabase(tmp_path / "jobs.sqlite3")
    db.initialize()
    source = OrganizationSource(
        id="icc_successfactors_legacy",
        name="International Criminal Court",
        ats_family="successfactors_legacy",
        base_url="https://career5.successfactors.eu/career?company=1657261P",
    )
    db.upsert_job(
        build_job(
            source,
            title="Internship - Human Resources - Learning & Development",
            external_id="24268",
            description="The generic page mentions consultants and a standalone SSA token.",
            apply_url="https://career5.successfactors.eu/career?company=1657261P&career_job_req_id=24268",
            raw={},
        )
    )

    classify_database(db)
    row = next(db.iter_jobs_with_classification(source_id="icc_successfactors_legacy"))

    assert row["contract_category"] == "internship_unknown"
    assert row["grade_mapping_raw_grade_code"] == "Internship"
    assert row["standard_seniority_tier"] == "T0_PATHWAY_OUTSIDE_SYSTEM"


def test_icc_freelance_translator_is_consultancy_eoi_not_fixed_term_staff(tmp_path):
    db = JobDatabase(tmp_path / "jobs.sqlite3")
    db.initialize()
    source = OrganizationSource(
        id="icc_successfactors_legacy",
        name="International Criminal Court",
        ats_family="successfactors_legacy",
        base_url="https://career5.successfactors.eu/career?company=1657261P",
    )
    db.upsert_job(
        build_job(
            source,
            title="Freelance Translator",
            external_id="3861",
            description=(
                "Expression of interest for freelance translators. Some ICC benefits "
                "pages mention fixed term staff, but this announcement is freelance."
            ),
            apply_url="https://career5.successfactors.eu/career?company=1657261P&career_job_req_id=3861",
            raw={},
        )
    )

    classify_database(db)
    row = next(db.iter_jobs_with_classification(source_id="icc_successfactors_legacy"))

    assert row["contract_category"] == "consultant"
    assert row["contract_subtype"] == "Consultancy Expression of Interest / Freelance"
    assert row["grade_mapping_raw_grade_code"] == "Freelance Translator"
    assert row["standard_seniority_tier"] == "T0_NONSTAFF_UNGRADED"


def test_unesco_consultant_level_2_middle_standardizes(tmp_path):
    db = JobDatabase(tmp_path / "jobs.sqlite3")
    db.initialize()
    source = OrganizationSource(
        id="unesco_successfactors",
        name="UNESCO",
        ats_family="successfactors_rmk",
        base_url="https://careers.unesco.org",
    )
    db.upsert_job(
        build_job(
            source,
            title="Consultoria Evaluacion Centro Internacional",
            external_id="1362727657",
            description=(
                "JOB DETAILS Type of contract : Consultant Contract Level : Level 2 - Middle "
                "Hiring Unit : Social and Human Sciences Sector Duty Station : Montevideo"
            ),
            apply_url="https://careers.unesco.org/job/1362727657/",
            raw={"parser": "successfactors_detail"},
        )
    )

    classify_database(db)
    row = next(db.iter_jobs_with_classification(source_id="unesco_successfactors"))

    assert row["contract_category"] == "consultant"
    assert row["grade_code"] == "Level 2 - Middle"
    assert row["grade_mapping_raw_grade_code"] == "Level 2 - Middle"
    assert row["standard_seniority_tier"] == "T3_MID_PROFESSIONAL"


def test_unesco_staff_p5_grade_from_detail_label_standardizes(tmp_path):
    db = JobDatabase(tmp_path / "jobs.sqlite3")
    db.initialize()
    source = OrganizationSource(
        id="unesco_successfactors",
        name="UNESCO",
        ats_family="successfactors_rmk",
        base_url="https://careers.unesco.org",
    )
    db.upsert_job(
        build_job(
            source,
            title="HEAD OF INVESTIGATIONS",
            external_id="1363050857",
            employment_type="Professional",
            description="JOB DETAILS Type of contract : Fixed Term Grade : P-5 Duty Station : Paris",
            apply_url="https://careers.unesco.org/job/Paris-HEAD-OF-INVESTIGATIONS/1363050857/",
            raw={"parser": "successfactors_detail"},
        )
    )

    classify_database(db)
    row = next(db.iter_jobs_with_classification(source_id="unesco_successfactors"))

    assert row["grade_code"] == "P5"
    assert row["grade_mapping_raw_grade_code"] == "P-5"
    assert row["standard_seniority_tier"] == "T5_PRINCIPAL_MANAGER"


def test_unicef_spanish_asistencia_tecnica_consultancy_defaults_to_national_consultant(tmp_path):
    db = JobDatabase(tmp_path / "jobs.sqlite3")
    db.initialize()
    source = OrganizationSource(
        id="unicef_pageup",
        name="UNICEF",
        ats_family="pageup",
        base_url="https://jobs.unicef.org",
    )
    db.upsert_job(
        build_job(
            source,
            title=(
                "Asistencia Tecnica para el Fortalecimiento y Escalamiento de "
                "Intervenciones en Salud y Primera Infancia en Panama"
            ),
            external_id="591323",
            location="Panama",
            description=(
                "Contract type: Consultant. Habilidades requeridas: excelente comunicacion "
                "oral y escrita en espanol y comprension de textos en ingles."
            ),
            apply_url="https://jobs.unicef.org/en-us/job/591323/asistencia-tecnica",
            raw={"detail_html": "<b>Contract type:</b> Consultant"},
        )
    )

    classify_database(db)
    row = next(db.iter_jobs_with_classification(source_id="unicef_pageup"))

    assert row["contract_category"] == "consultant"
    assert row["national_international"] == "national"
    assert row["grade_mapping_raw_grade_code"] == "Consultant"
    assert row["standard_seniority_tier"] == "T0_NONSTAFF_UNGRADED"


def test_unicef_plural_consultants_title_standardizes_as_consultant(tmp_path):
    db = JobDatabase(tmp_path / "jobs.sqlite3")
    db.initialize()
    source = OrganizationSource(
        id="unicef_pageup",
        name="UNICEF",
        ats_family="pageup",
        base_url="https://jobs.unicef.org",
    )
    db.upsert_job(
        build_job(
            source,
            title="UNICEF Supply Division LTA Roster of Consultants: Medicine Dossier Assessors",
            external_id="592755",
            apply_url="https://jobs.unicef.org/en-us/job/592755/example",
        )
    )

    classify_database(db)
    row = next(db.iter_jobs_with_classification(source_id="unicef_pageup"))

    assert row["contract_category"] == "consultant"
    assert row["grade_mapping_raw_grade_code"] == "Consultant"
    assert row["standard_seniority_tier"] == "T0_NONSTAFF_UNGRADED"


def test_unicef_french_stage_title_standardizes_as_internship(tmp_path):
    db = JobDatabase(tmp_path / "jobs.sqlite3")
    db.initialize()
    source = OrganizationSource(
        id="unicef_pageup",
        name="UNICEF",
        ats_family="pageup",
        base_url="https://jobs.unicef.org",
    )
    db.upsert_job(
        build_job(
            source,
            title="Stage en Communication, Les Cayes, Haiti, 26 semaines",
            external_id="593393",
            description="UNICEF Haiti est a la recherche d'un stagiaire.",
            apply_url="https://jobs.unicef.org/en-us/job/593393/stage-en-communication",
        )
    )

    classify_database(db)
    row = next(db.iter_jobs_with_classification(source_id="unicef_pageup"))

    assert row["contract_category"] == "internship_unknown"
    assert row["grade_mapping_raw_grade_code"] == "Internship"
    assert row["standard_seniority_tier"] == "T0_PATHWAY_OUTSIDE_SYSTEM"


def test_unicef_portuguese_estagiario_title_standardizes_as_internship(tmp_path):
    db = JobDatabase(tmp_path / "jobs.sqlite3")
    db.initialize()
    source = OrganizationSource(
        id="unicef_pageup",
        name="UNICEF",
        ats_family="pageup",
        base_url="https://jobs.unicef.org",
    )
    db.upsert_job(
        build_job(
            source,
            title="Estagiario(a) de Comunicacao UNICEF, Manaus, Brasil, 8 meses.",
            external_id="593380",
            description="Buscamos um(a) estagiario(a) para contribuir com comunicacao.",
            apply_url="https://jobs.unicef.org/en-us/job/593380/estagiarioa-de-comunicacao",
        )
    )

    classify_database(db)
    row = next(db.iter_jobs_with_classification(source_id="unicef_pageup"))

    assert row["contract_category"] == "internship_unknown"
    assert row["grade_mapping_raw_grade_code"] == "Internship"
    assert row["standard_seniority_tier"] == "T0_PATHWAY_OUTSIDE_SYSTEM"


def test_icrc_spanish_officer_with_three_to_four_years_maps_to_functional_proxy(tmp_path):
    db = JobDatabase(tmp_path / "jobs.sqlite3")
    db.initialize()
    source = OrganizationSource(
        id="icrc_successfactors",
        name="International Committee of the Red Cross",
        ats_family="successfactors_rmk",
        base_url="https://careers.icrc.org",
    )
    db.upsert_job(
        build_job(
            source,
            title="Oficial de proteccion 2",
            external_id="32478",
            location="Bogota",
            employment_type="staff contract",
            description="Experiencia laboral confirmada, 3-4 años en la misma área de actividad y nivel de responsabilidad.",
            apply_url="https://careers.icrc.org/job/Bogota-BOG-Oficial-de-proteccion-2-32478/1398839933/",
            raw={},
        )
    )

    classify_database(db)
    row = next(db.iter_jobs_with_classification(source_id="icrc_successfactors"))

    assert row["grade_mapping_raw_grade_code"].startswith("Officer / Accountant")
    assert row["standard_seniority_tier"] == "T3_MID_PROFESSIONAL"


def test_unrwa_grade_17_from_inspira_description_standardizes_as_local_grade(tmp_path):
    db = JobDatabase(tmp_path / "jobs.sqlite3")
    db.initialize()
    source = OrganizationSource(
        id="un_inspira",
        name="United Nations Careers",
        ats_family="inspira",
        base_url="https://careers.un.org",
    )
    db.upsert_job(
        build_job(
            source,
            title="Programme Officer, NLA",
            external_id="278868",
            location="Amman",
            employment_type="Fixed Term Appointment",
            description="Type of Contract: Fixed Term Appointment. Grade and Salary: Grade 17.",
            apply_url="https://careers.un.org/jobSearchDescription/278868?language=en",
            raw={},
        )
    )

    classify_database(db)
    row = next(db.iter_jobs_with_classification(source_id="un_inspira"))

    assert row["grade_code"] == "UNRWA Grade 17"
    assert row["grade_mapping_raw_grade_code"] == "UNRWA Grade 17"
    assert row["standard_scope"] == "National / Local"
    assert row["standard_seniority_tier"] == "T4_SENIOR_PROFESSIONAL"


def test_ccog_resource_tree_contains_runtime_subset():
    full_resource_path = _full_markdown_path()
    tree = {entry["code"]: entry for entry in ccog_tree()}

    expected_count = 221 if full_resource_path is not None else 19
    assert len(tree) >= expected_count
    assert {
        "1.A.01",
        "1.A.05",
        "1.A.09.a",
        "1.A.09.c",
        "1.L.09",
        "2.1.02.a",
        "2.2.06",
    } <= set(tree)
    assert tree["1.A.01"]["label"] == "Financial management specialists"
    assert tree["1.A.01"]["family_code"] == "1.A"
    assert tree["1.A.01"]["family_label"] == "Administrative specialists"


def test_ccog_resource_tree_without_private_reference_uses_tracked_subset(monkeypatch):
    ccog_classifier._ccog_entries.cache_clear()
    monkeypatch.setattr(ccog_classifier, "_full_markdown_path", lambda: None)
    try:
        tree = {entry["code"]: entry for entry in ccog_classifier.ccog_tree()}
        assert len(tree) == 19
        assert {
            "1.A.01",
            "1.A.05",
            "1.A.09.a",
            "1.L.09",
            "2.1.02.a",
            "2.2.06",
        } <= set(tree)
        assert tree["1.A.01"]["label"] == "Financial management specialists"
    finally:
        ccog_classifier._ccog_entries.cache_clear()


def test_ccog_full_markdown_ocr_repairs_are_present():
    path = _full_markdown_path()
    if path is None or not path.is_file():
        pytest.skip("full CCOG markdown resource is local/ignored")
    text = path.read_text(encoding="utf-8")

    for malformed in (
        "### I.",
        "### L.",
        "## Family: I.A",
        "## Family: L.A",
        "## Family: L.J",
        "**Family:** I.A \u2014",
        "**Family:** L.A \u2014",
        "**Family:** L.J \u2014",
        "1.B.01a",
    ):
        assert malformed not in text
    assert "1.B.01.a" in text
    assert sum(1 for line in text.splitlines() if line.startswith("### ")) == 221


def test_ccog_normalization_and_medium_helpers():
    assert normalize_ccog_code("I.A.09") == "1.A.09"
    assert normalize_ccog_code("L.J.05") == "1.J.05"
    assert normalize_ccog_code("1.B.01a") == "1.B.01.a"
    assert collapse_ccog_to_medium("1.A.01.c") == "1.A.01"
    assert collapse_ccog_to_medium("1.A.01") == "1.A.01"
    assert collapse_ccog_to_medium("1.A") is None
    assert get_ccog_family("1.A.01.c") == {
        "code": "1.A",
        "label": "Administrative specialists",
    }
    assert get_ccog_medium("1.A.01.c") == {
        "code": "1.A.01",
        "label": "Financial management specialists",
    }


def test_ccog_short_keywords_require_word_boundaries():
    score, matches = _keyword_score("maintain training records", ["AI"])
    assert score == 0
    assert matches == []

    score, matches = _keyword_score("AI adoption for case management", ["AI"])
    assert score > 0
    assert matches == ["AI"]


def test_contract_specific_internship_signals_override_generic_title():
    unpaid = classify_contract(
        FeatureBundle(
            vacancy_id="job-1",
            source_id="unicef_pageup",
            ats_family="pageup",
            title="Internship - Communications",
            description="This internship offers no remuneration.",
        ),
        GradeResult(),
    )
    paid_source = classify_contract(
        FeatureBundle(
            vacancy_id="job-2",
            source_id="unicef_pageup",
            ats_family="pageup",
            title="Internship - Data",
            contract_raw="Paid Internship",
        ),
        GradeResult(),
    )

    assert unpaid.category is ContractCategory.INTERNSHIP_UNPAID
    assert paid_source.category is ContractCategory.INTERNSHIP_PAID


def test_contract_stipend_benefit_without_internship_context_is_not_paid_internship():
    result = classify_contract(
        FeatureBundle(
            vacancy_id="cern-1",
            source_id="cern_custom_html",
            ats_family="custom_html",
            title="Applied Physicist",
            description="CERN benefits include a monthly net allowance and health insurance.",
        ),
        GradeResult(),
    )

    assert result.category is ContractCategory.UNKNOWN


def test_contract_stipend_benefit_with_studentship_context_is_internship():
    result = classify_contract(
        FeatureBundle(
            vacancy_id="cern-2",
            source_id="cern_custom_html",
            ats_family="custom_html",
            title="Administrative Studentship",
            description="This studentship includes a monthly net allowance.",
        ),
        GradeResult(),
    )

    assert result.category is ContractCategory.INTERNSHIP_UNKNOWN


def test_contract_intern_keyword_does_not_match_international():
    result = classify_contract(
        FeatureBundle(
            vacancy_id="job-3",
            source_id="unido_successfactors",
            ats_family="successfactors",
            title="International Scrap and Waste Materials Inspection Expert",
            employment_type="ISA-P4",
        ),
        GradeResult(family="P", code="P4"),
    )

    assert result.category is ContractCategory.STAFF_OTHER
