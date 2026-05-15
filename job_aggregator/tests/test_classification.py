import sqlite3

from jobagg.classification import classify_database
from jobagg.classification.classifiers.ccog import ccog_tree
from jobagg.db import JobDatabase
from jobagg.filters.facets import facet_counts
from jobagg.filters.query import search_vacancies
from jobagg.filters.schemas import VacancyFilters
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
    assert row["contract_category"] == "staff_other"
    assert row["national_international"] == "local"
    assert row["work_modality"] == "multiple_locations"
    assert row["ccog_primary_code"] == "2.2.06"
    assert row["needs_review"] is False


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
    assert row["work_modality"] == "onsite"
    assert row["country_iso3"] == "RWA"
    assert row["city"] == "Kigali"
    assert row["ccog_primary_code"] == "1.L.09"

    facets = facet_counts(db, VacancyFilters(region="Africa"))
    assert facets["contract_categories"]["volunteering_unv"] == 1
    assert facets["unv_categories"]["un_volunteer_specialist"] == 1


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

    db.upsert_classification_override(
        vacancy_id=job.identity_key(),
        field_name="ccog_primary_code",
        override_value="1.A.01",
        reason="test correction",
    )
    classify_database(db)
    overridden = next(db.iter_jobs_with_classification(source_id="unicef_pageup"))
    assert overridden["ccog_primary_code"] == "1.A.01"

    with sqlite3.connect(db.path) as conn:
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name LIKE 'vacancy_%'"
            )
        }
    assert {"vacancy_source_features", "vacancy_classifications", "vacancy_locations"} <= tables


def test_ccog_resource_tree_has_expected_count():
    assert len(ccog_tree()) == 221
