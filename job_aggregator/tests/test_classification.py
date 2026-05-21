import sqlite3

from jobagg.classification import classify_database
from jobagg.classification.classifiers.ccog import _keyword_score, ccog_tree
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
    assert row["contract_category"] != "consultant"


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


def test_internship_is_not_standard_grade_even_when_grade_signal_exists(tmp_path):
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
    assert row["standard_seniority_tier"] is None
    assert row["standard_employment_category"] is None


def test_cern_studentship_is_internship_and_not_standard_grade(tmp_path):
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
    assert row["standard_seniority_tier"] is None
    assert row["standard_employment_category"] is None


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


def test_generic_consultant_is_not_standard_grade(tmp_path):
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
    assert row["standard_seniority_tier"] is None
    assert row["standard_employment_category"] is None


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


def test_ccog_resource_tree_contains_runtime_subset():
    tree = {entry["code"]: entry for entry in ccog_tree()}

    assert 10 <= len(tree) < 221
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
