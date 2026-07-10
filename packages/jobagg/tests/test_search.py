import json
import sqlite3

from jobagg.classification import classify_database
from jobagg.classification.models import VacancyLocation
from jobagg.db import JobDatabase
from jobagg.filters.explain import explain_job_match
from jobagg.filters.query import search_collected_jobs, search_vacancies
from jobagg.filters.schemas import VacancyFilters, VacancySearchRequest
from jobagg.models import OrganizationSource
from jobagg.normalize import build_job
from jobagg.scheduler import main


def _source(source_id: str, ats_family: str) -> OrganizationSource:
    return OrganizationSource(
        id=source_id,
        name=source_id,
        ats_family=ats_family,
        base_url="https://example.org",
    )


def _db(tmp_path) -> JobDatabase:
    db = JobDatabase(tmp_path / "jobs.sqlite3")
    db.initialize()
    return db


def _nairobi_p_search(**overrides) -> VacancySearchRequest:
    values = {
        "status": ["open"],
        "cities": ["Nairobi"],
        "countries_iso3": ["KEN"],
        "national_international": ["international"],
        "grade_families": ["P"],
        "grade_codes": ["P2", "P3", "P4"],
        "location_types": ["primary", "duty_station", "outposted"],
        "limit": 50,
    }
    values.update(overrides)
    return VacancySearchRequest(**values)


def test_vacancy_locations_schema_and_indexes(tmp_path):
    db = _db(tmp_path)
    with sqlite3.connect(db.path) as conn:
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        indexes = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'index'"
            )
        }

    assert "vacancy_locations" in tables
    assert {
        "idx_vacloc_city_key",
        "idx_vacloc_country_iso3",
        "idx_vacloc_city_country",
        "idx_vacloc_type",
        "idx_vacloc_vacancy",
    } <= indexes


def test_nairobi_international_p_grade_search_matches_inspira_and_unicef(tmp_path):
    db = _db(tmp_path)
    inspira = _source("un_inspira", "inspira")
    unicef = _source("unicef_pageup", "pageup")
    db.upsert_job(
        build_job(
            inspira,
            title="Associate Programme Management Officer, P-2",
            external_id="273426",
            location="Nairobi",
            apply_url="https://careers.un.org/jobSearchDescription/273426?language=en",
            raw={
                "dutyStation": [{"description": "Nairobi"}],
                "jl": {"name": "P-2"},
                "jf": {"Code": "PGM", "Name": "Programme Management"},
            },
        )
    )
    db.upsert_job(
        build_job(
            inspira,
            title="Legal Officer, P-3",
            external_id="273427",
            location="Nairobi",
            apply_url="https://careers.un.org/jobSearchDescription/273427?language=en",
            raw={
                "dutyStation": [{"description": "Nairobi"}],
                "jl": {"name": "P-3"},
            },
        )
    )
    db.upsert_job(
        build_job(
            unicef,
            title="Partnerships Officer, P-2, Outposted to Nairobi, Kenya",
            external_id="unicef-1",
            location="Kenya",
            apply_url="https://jobs.unicef.org/jobs/unicef-1",
        )
    )
    db.upsert_job(
        build_job(
            unicef,
            title="Supply & Logistics Manager, P-4, Nairobi - Kenya",
            external_id="unicef-2",
            location="Kenya",
            apply_url="https://jobs.unicef.org/jobs/unicef-2",
        )
    )
    db.upsert_job(
        build_job(
            unicef,
            title="Administrative Assistant, G-5, Nairobi - Kenya",
            external_id="unicef-local",
            location="Kenya",
            apply_url="https://jobs.unicef.org/jobs/unicef-local",
        )
    )

    classify_database(db)
    response = search_collected_jobs(db, _nairobi_p_search())

    assert response.total == 4
    assert {result["grade_code"] for result in response.results} == {"P2", "P3", "P4"}
    assert len({result["job_key"] for result in response.results}) == 4
    assert response.facets["grades"] == {"P2": 2, "P3": 1, "P4": 1}
    assert response.facets["organizations"] == {"unicef_pageup": 2, "un_inspira": 2}
    source_fields = {
        result["match_evidence"]["location"]["source_field"]
        for result in response.results
    }
    assert "raw.dutyStation.description" in source_fields
    assert "title" in source_fields


def test_search_filters_by_taxonomy_facets(tmp_path):
    db = _db(tmp_path)
    source = _source("unicef_pageup", "pageup")
    db.upsert_job(
        build_job(
            source,
            title="Finance Officer, P-3, Nairobi, Kenya",
            external_id="taxonomy-search",
            location="Nairobi, Kenya",
            description="Responsible for budgeting, accounting, and project financial control.",
            apply_url="https://jobs.unicef.org/jobs/taxonomy-search",
        )
    )
    db.upsert_job(
        build_job(
            source,
            title="Human Resources Officer, P-3, Nairobi, Kenya",
            external_id="taxonomy-search-miss",
            location="Nairobi, Kenya",
            description="Responsible for recruitment and learning.",
            apply_url="https://jobs.unicef.org/jobs/taxonomy-search-miss",
        )
    )

    classify_database(db)
    response = search_collected_jobs(
        db,
        VacancySearchRequest(
            mandate_network_codes=["MAGNET"],
            mandate_family_codes=["MAGNET.finance"],
            occupational_medium_codes=["1.A.01"],
            capability_tags=["budgeting"],
            contract_groups=["staff"],
            seniority_groups=["mid"],
        ),
    )

    assert response.total == 1
    assert response.results[0]["title"] == "Finance Officer, P-3, Nairobi, Kenya"
    assert "budgeting" in response.facets["capability_tags"]
    assert response.facets["mandate_families"] == {"MAGNET.finance": 1}
    explanation = explain_job_match(
        db,
        "unicef_pageup:taxonomy-search",
        VacancySearchRequest(capability_tags=["budgeting"], contract_groups=["staff"]),
    )
    assert explanation["matched"] is True


def test_seniority_filter_values_are_or_within_group(tmp_path):
    db = _db(tmp_path)
    source = _source("un_inspira", "inspira")
    db.upsert_job(
        build_job(
            source,
            title="Programme Officer, P-3",
            external_id="mid",
            location="Geneva",
            apply_url="https://careers.un.org/jobSearchDescription/mid?language=en",
            raw={"jl": {"name": "P-3"}},
        )
    )
    db.upsert_job(
        build_job(
            source,
            title="Senior Programme Officer, P-5",
            external_id="senior",
            location="Geneva",
            apply_url="https://careers.un.org/jobSearchDescription/senior?language=en",
            raw={"jl": {"name": "P-5"}},
        )
    )
    db.upsert_job(
        build_job(
            source,
            title="Director, D-1",
            external_id="director",
            location="Geneva",
            apply_url="https://careers.un.org/jobSearchDescription/director?language=en",
            raw={"jl": {"name": "D-1"}},
        )
    )

    classify_database(db)
    response = search_collected_jobs(
        db,
        VacancySearchRequest(seniority_groups=["mid", "senior"]),
    )

    assert response.total == 2
    assert {row["job_key"] for row in response.results} == {"un_inspira:mid", "un_inspira:senior"}


def test_text_search_falls_back_to_like_when_fts_table_is_absent(tmp_path):
    db = _db(tmp_path)
    source = _source("unicef_pageup", "pageup")
    db.upsert_job(
        build_job(
            source,
            title="Finance Officer, P-3, Nairobi, Kenya",
            external_id="no-fts",
            location="Nairobi, Kenya",
            description="Responsible for budgeting and accounting controls.",
            apply_url="https://jobs.unicef.org/jobs/no-fts",
        )
    )
    classify_database(db)
    with db.connect() as conn:
        conn.execute("DROP TABLE IF EXISTS jobs_fts")

    assert db.fts_available() is False
    collected = search_collected_jobs(db, VacancySearchRequest(text="budgeting"))
    legacy = search_vacancies(db, VacancyFilters(text="budgeting"))

    assert collected.total == 1
    assert legacy[0]["job_key"] == "unicef_pageup:no-fts"


def test_unv_region_does_not_create_nairobi_duty_station(tmp_path):
    db = _db(tmp_path)
    source = _source("unv_uvp", "unv")
    db.upsert_job(
        build_job(
            source,
            title="Access to Finance Specialist",
            external_id="unv-1",
            location="Rwanda",
            apply_url="https://app.unv.org/opportunities/unv-1",
            raw={
                "name": "Access to Finance Specialist",
                "country": {"longDescription": "Rwanda", "props": {"codeISO2": "RW"}},
                "dutyStations": [{"longDescription": "Kigali"}],
                "unvRegion": {"longDescription": "Nairobi Regional Office"},
                "categoryName": {
                    "value": {"code": "SPECIALIST"},
                    "longDescription": "Specialist",
                },
                "volunteerType": {"longDescription": "International"},
                "workLocation": {"longDescription": "On UN premises"},
                "isOnsite": True,
            },
        )
    )

    classify_database(db)
    response = search_collected_jobs(
        db,
        VacancySearchRequest(cities=["Nairobi"], countries_iso3=["KEN"]),
    )

    assert response.total == 0
    locations = list(db.iter_vacancy_locations("unv_uvp:unv-1"))
    assert locations[0]["city"] == "Kigali"
    assert locations[0]["location_type"] == "duty_station"


def test_volunteer_kind_filter_separates_unv_from_generic_volunteer_programmes(tmp_path):
    db = _db(tmp_path)
    unv = _source("unv_uvp", "unv")
    fao = _source("fao_taleo", "taleo")
    db.upsert_job(
        build_job(
            unv,
            title="Access to Finance Specialist",
            external_id="unv-specialist",
            location="Rwanda",
            apply_url="https://app.unv.org/opportunities/unv-specialist",
            raw={
                "name": "Access to Finance Specialist",
                "country": {"longDescription": "Rwanda", "props": {"codeISO2": "RW"}},
                "dutyStations": [{"longDescription": "Kigali"}],
                "categoryName": {
                    "value": {"code": "SPECIALIST"},
                    "longDescription": "Specialist",
                },
                "volunteerType": {"longDescription": "International"},
                "isOnsite": True,
            },
        )
    )
    db.upsert_job(
        build_job(
            fao,
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

    unv_response = search_collected_jobs(
        db,
        VacancySearchRequest(volunteer_kinds=["un_volunteer"]),
    )
    generic_response = search_collected_jobs(
        db,
        VacancySearchRequest(volunteer_kinds=["volunteer"]),
    )
    all_response = search_collected_jobs(db, VacancySearchRequest())

    assert [row["job_key"] for row in unv_response.results] == ["unv_uvp:unv-specialist"]
    assert [row["job_key"] for row in generic_response.results] == ["fao_taleo:2600018"]
    assert all_response.facets["volunteer_kinds"] == {"un_volunteer": 1, "volunteer": 1}


def test_multiple_locations_placeholder_does_not_match_city_search(tmp_path):
    db = _db(tmp_path)
    source = _source("wfp_workday", "workday")
    db.upsert_job(
        build_job(
            source,
            title="Programme Officer, P-3 - Multiple locations",
            external_id="wfp-1",
            location="2 Locations",
            apply_url="https://wfp.org/jobs/wfp-1",
            raw={"locationsText": "2 Locations"},
        )
    )

    classify_database(db)
    response = search_collected_jobs(db, _nairobi_p_search(grade_codes=["P3"]))

    assert response.total == 0
    locations = list(db.iter_vacancy_locations("wfp_workday:wfp-1"))
    assert locations[0]["location_type"] == "multiple_unknown"
    assert locations[0]["city_key"] is None


def test_region_search_uses_matching_vacancy_location_for_multi_location_jobs(tmp_path):
    db = _db(tmp_path)
    source = _source("unicef_pageup", "pageup")
    job = build_job(
        source,
        title="Programme Officer, P-3",
        external_id="multi-region",
        location="Bangkok, Thailand",
        apply_url="https://jobs.unicef.org/jobs/multi-region",
    )
    db.upsert_job(job)
    classify_database(db)
    db.replace_vacancy_locations(
        job.identity_key(),
        [
            VacancyLocation(
                vacancy_id=job.identity_key(),
                city="Bangkok",
                city_key="bangkok",
                country="Thailand",
                country_iso3="THA",
                region="Asia",
                location_type="primary",
                is_primary=True,
                confidence=0.95,
                source_field="test.primary",
            ),
            VacancyLocation(
                vacancy_id=job.identity_key(),
                city="Nairobi",
                city_key="nairobi",
                country="Kenya",
                country_iso3="KEN",
                region="Africa",
                location_type="outposted",
                confidence=0.90,
                source_field="test.outposted",
            ),
        ],
    )

    response = search_collected_jobs(db, VacancySearchRequest(regions=["Africa"]))
    explanation = explain_job_match(db, job.identity_key(), VacancySearchRequest(regions=["Africa"]))

    assert response.total == 1
    assert explanation["matched"] is True
    explanation_filters = {check["filter"] for check in explanation["checks"]}
    assert "location" in explanation_filters
    assert "region" not in explanation_filters
    assert response.results[0]["duty_station"] == "Nairobi, Kenya"
    assert response.results[0]["match_evidence"]["location"]["source_field"] == "test.outposted"


def test_location_confidence_thresholds_can_be_relaxed(tmp_path):
    db = _db(tmp_path)
    source = _source("unicef_pageup", "pageup")
    db.upsert_job(
        build_job(
            source,
            title="Partnerships Officer, P-2, Outposted to Nairobi, Kenya",
            external_id="unicef-3",
            location="Kenya",
            apply_url="https://jobs.unicef.org/jobs/unicef-3",
        )
    )

    classify_database(db)
    strict = search_collected_jobs(
        db,
        _nairobi_p_search(
            grade_codes=["P2"],
            min_location_confidence=0.90,
        ),
    )
    relaxed = search_collected_jobs(
        db,
        _nairobi_p_search(
            grade_codes=["P2"],
            min_location_confidence=0.90,
            include_low_confidence=True,
        ),
    )

    assert strict.total == 0
    assert relaxed.total == 1
    assert relaxed.results[0]["match_evidence"]["location"]["confidence"] == 0.85


def test_remote_city_anchor_is_separate_from_default_duty_station_search(tmp_path):
    db = _db(tmp_path)
    source = _source("unicef_pageup", "pageup")
    db.upsert_job(
        build_job(
            source,
            title="Programme Officer, P-3, Nairobi, Kenya (Remote)",
            external_id="unicef-remote",
            location="Kenya",
            apply_url="https://jobs.unicef.org/jobs/unicef-remote",
        )
    )

    classify_database(db)
    default = search_collected_jobs(db, _nairobi_p_search(grade_codes=["P3"]))
    with_remote_anchor = search_collected_jobs(
        db,
        _nairobi_p_search(
            grade_codes=["P3"],
            location_types=["remote_anchor"],
        ),
    )

    assert default.total == 0
    assert with_remote_anchor.total == 1
    evidence = with_remote_anchor.results[0]["match_evidence"]["location"]
    assert evidence["location_type"] == "remote_anchor"
    assert evidence["evidence"]["matched_city"] == "nairobi"


def test_search_cli_writes_markdown_for_combined_criteria(tmp_path):
    db = _db(tmp_path)
    source = _source("unicef_pageup", "pageup")
    db.upsert_job(
        build_job(
            source,
            title="Partnerships Officer, P-2, Outposted to Nairobi, Kenya",
            external_id="unicef-md",
            location="Kenya",
            apply_url="https://jobs.unicef.org/jobs/unicef-md",
        )
    )
    db.upsert_job(
        build_job(
            source,
            title="Administrative Assistant, G-5, Nairobi - Kenya",
            external_id="unicef-local-md",
            location="Kenya",
            apply_url="https://jobs.unicef.org/jobs/unicef-local-md",
        )
    )
    classify_database(db)
    output = tmp_path / "nairobi_p_roles.md"

    exit_code = main(
        [
            "--db",
            str(db.path),
            "search",
            "--city",
            "Nairobi",
            "--country",
            "KEN",
            "--scope",
            "international",
            "--grade",
            "P2",
            "--format",
            "markdown",
            "--output",
            str(output),
        ]
    )

    text = output.read_text(encoding="utf-8")
    assert exit_code == 0
    assert text.startswith("# Job Search Results")
    assert "Total matches: 1" in text
    assert "| Title | Organization | Location | Grade |" in text
    assert "[Partnerships Officer, P-2, Outposted to Nairobi, Kenya]" in text
    assert "Administrative Assistant" not in text


def test_date_only_upper_bounds_include_same_day_datetimes(tmp_path):
    db = _db(tmp_path)
    source = _source("unicef_pageup", "pageup")
    db.upsert_job(
        build_job(
            source,
            title="Programme Officer, P-4, Nairobi, Kenya",
            external_id="same-day-close",
            location="Kenya",
            closes_at="2026-06-07T15:30:00+00:00",
            posted_at="2026-05-20T09:00:00+00:00",
            apply_url="https://jobs.unicef.org/jobs/same-day-close",
        )
    )
    classify_database(db)

    collected = search_collected_jobs(
        db,
        VacancySearchRequest(
            closing_date_to="2026-06-07",
            posted_date_to="2026-05-20",
            exclude_expired_open=False,
        ),
    )
    legacy = search_vacancies(
        db,
        VacancyFilters(closing_date_to="2026-06-07", posted_date_to="2026-05-20"),
    )

    assert collected.total == 1
    assert len(legacy) == 1
    explanation = explain_job_match(
        db,
        "unicef_pageup:same-day-close",
        VacancySearchRequest(
            closing_date_to="2026-06-07",
            posted_date_to="2026-05-20",
            exclude_expired_open=False,
        ),
    )
    assert explanation["matched"] is True


def test_default_collected_search_excludes_expired_open_rows(tmp_path):
    db = _db(tmp_path)
    source = _source("unicef_pageup", "pageup")
    db.upsert_job(
        build_job(
            source,
            title="Expired Programme Officer, P-3",
            external_id="expired-open",
            location="Nairobi",
            closes_at="2020-01-01T00:00:00+00:00",
            apply_url="https://jobs.unicef.org/jobs/expired-open",
        )
    )
    db.upsert_job(
        build_job(
            source,
            title="Current Programme Officer, P-3",
            external_id="current-open",
            location="Nairobi",
            closes_at="2099-01-01T00:00:00+00:00",
            apply_url="https://jobs.unicef.org/jobs/current-open",
        )
    )
    classify_database(db)

    collected = search_collected_jobs(db, VacancySearchRequest())

    assert collected.total == 1
    assert collected.results[0]["job_key"] == "unicef_pageup:current-open"


def test_collected_search_pagination_uses_stable_job_key_tiebreaker(tmp_path):
    db = _db(tmp_path)
    source = _source("unicef_pageup", "pageup")
    for external_id in ("charlie", "alpha", "bravo"):
        db.upsert_job(
            build_job(
                source,
                title=f"Programme Officer {external_id}",
                external_id=external_id,
                location="Nairobi",
                closes_at="2099-01-01T00:00:00+00:00",
                posted_at="2026-01-01T00:00:00+00:00",
                apply_url=f"https://jobs.unicef.org/jobs/{external_id}",
            )
        )
    classify_database(db)

    expected = [
        "unicef_pageup:alpha",
        "unicef_pageup:bravo",
        "unicef_pageup:charlie",
    ]
    for sort in ("closing_date_asc", "closing_date_desc", "posted_date_desc"):
        actual = [
            search_collected_jobs(
                db,
                VacancySearchRequest(sort=sort, limit=1, offset=offset),
                include_facets=False,
            ).results[0]["job_key"]
            for offset in range(3)
        ]
        assert actual == expected


def test_filter_cli_writes_markdown_for_unv_facets(tmp_path):
    db = _db(tmp_path)
    source = _source("unv_uvp", "unv")
    db.upsert_job(
        build_job(
            source,
            title="Access to Finance Specialist",
            external_id="unv-md",
            location="Rwanda",
            apply_url="https://app.unv.org/opportunities/unv-md",
            raw={
                "country": {"longDescription": "Rwanda", "props": {"codeISO2": "RW"}},
                "dutyStations": [{"longDescription": "Kigali"}],
                "categoryName": {
                    "value": {"code": "SPECIALIST"},
                    "longDescription": "Specialist",
                },
                "volunteerType": {"longDescription": "International"},
                "isOnsite": True,
            },
        )
    )
    classify_database(db)
    output = tmp_path / "unv_africa.md"

    exit_code = main(
        [
            "--db",
            str(db.path),
            "filter",
            "--region",
            "Africa",
            "--contract-category",
            "volunteering_unv",
            "--format",
            "markdown",
            "--output",
            str(output),
        ]
    )

    text = output.read_text(encoding="utf-8")
    assert exit_code == 0
    assert text.startswith("# Filtered Vacancies")
    assert "Total matches: 1" in text
    assert "Access to Finance Specialist" in text
    assert "Kigali" in text


def test_audit_classification_cli_reports_source_quality(tmp_path):
    db = _db(tmp_path)
    source = _source("unicef_pageup", "pageup")
    db.upsert_job(
        build_job(
            source,
            title="Programme Officer, P-4, Nairobi, Kenya",
            external_id="unicef-audit-1",
            location="Kenya",
            apply_url="https://jobs.unicef.org/jobs/unicef-audit-1",
        )
    )
    db.upsert_job(
        build_job(
            source,
            title="Administrative Assistant, GS5, Temporary Appointment, Khartoum, Sudan",
            external_id="unicef-audit-2",
            location="Sudan",
            apply_url="https://jobs.unicef.org/jobs/unicef-audit-2",
        )
    )
    classify_database(db)
    output = tmp_path / "classification_audit.json"

    exit_code = main(
        [
            "--db",
            str(db.path),
            "audit-classification",
            "--source-id",
            "unicef_pageup",
            "--format",
            "json",
            "--output",
            str(output),
        ]
    )

    payload = json.loads(output.read_text(encoding="utf-8"))
    assert exit_code == 0
    assert payload["overall"]["total_jobs"] == 2
    assert payload["overall"]["classified_jobs"] == 2
    assert payload["sources"][0]["source_id"] == "unicef_pageup"
    assert "quality_score" in payload["sources"][0]


def test_search_explain_and_debug_show_filter_evaluation(tmp_path):
    db = _db(tmp_path)
    source = _source("unicef_pageup", "pageup")
    db.upsert_job(
        build_job(
            source,
            title="Programme Officer, P-4, Nairobi, Kenya",
            external_id="unicef-explain-p4",
            location="Kenya",
            apply_url="https://jobs.unicef.org/jobs/unicef-explain-p4",
        )
    )
    db.upsert_job(
        build_job(
            source,
            title="Programme Adviser, P-5, FTA, Nairobi, Kenya",
            external_id="unicef-explain-p5",
            location="Kenya",
            apply_url="https://jobs.unicef.org/jobs/unicef-explain-p5",
        )
    )
    classify_database(db)
    search_output = tmp_path / "search_explain.json"

    exit_code = main(
        [
            "--db",
            str(db.path),
            "search",
            "--city",
            "Nairobi",
            "--country",
            "KEN",
            "--scope",
            "international",
            "--grade",
            "P2",
            "--grade",
            "P3",
            "--grade",
            "P4",
            "--explain",
            "--output",
            str(search_output),
        ]
    )

    payload = json.loads(search_output.read_text(encoding="utf-8"))
    assert exit_code == 0
    assert payload["total"] == 1
    assert payload["results"][0]["grade_code"] == "P4"
    assert any(check["filter"] == "grade_code" for check in payload["results"][0]["filter_evaluation"])

    debug_output = tmp_path / "debug.json"
    exit_code = main(
        [
            "--db",
            str(db.path),
            "search-debug",
            "--job-key",
            "unicef_pageup:unicef-explain-p5",
            "--city",
            "Nairobi",
            "--country",
            "KEN",
            "--scope",
            "international",
            "--grade",
            "P2",
            "--grade",
            "P3",
            "--grade",
            "P4",
            "--format",
            "json",
            "--output",
            str(debug_output),
        ]
    )

    debug = json.loads(debug_output.read_text(encoding="utf-8"))
    assert exit_code == 1
    assert debug["found"] is True
    assert debug["matched"] is False
    assert "grade_code" in debug["reason"]

    no_filter_output = tmp_path / "debug_no_filters.txt"
    exit_code = main(
        [
            "--db",
            str(db.path),
            "search-debug",
            "--job-key",
            "unicef_pageup:unicef-explain-p4",
            "--output",
            str(no_filter_output),
        ]
    )
    assert exit_code == 0
    assert "Final: matched" in no_filter_output.read_text(encoding="utf-8")

    missing_output = tmp_path / "debug_missing.json"
    exit_code = main(
        [
            "--db",
            str(db.path),
            "search-debug",
            "--job-key",
            "unicef_pageup:missing",
            "--format",
            "json",
            "--output",
            str(missing_output),
        ]
    )
    missing = json.loads(missing_output.read_text(encoding="utf-8"))
    assert exit_code == 1
    assert missing["found"] is False
    assert missing["reason"] == "job_key not found"


def test_saved_search_add_list_run_and_remove(tmp_path):
    db = _db(tmp_path)
    source = _source("unicef_pageup", "pageup")
    db.upsert_job(
        build_job(
            source,
            title="Programme Officer, P-4, Nairobi, Kenya",
            external_id="unicef-saved-p4",
            location="Kenya",
            apply_url="https://jobs.unicef.org/jobs/unicef-saved-p4",
        )
    )
    db.upsert_job(
        build_job(
            source,
            title="Programme Adviser, P-5, FTA, Nairobi, Kenya",
            external_id="unicef-saved-p5",
            location="Kenya",
            apply_url="https://jobs.unicef.org/jobs/unicef-saved-p5",
        )
    )
    classify_database(db)
    saved_path = tmp_path / "saved_searches.json"

    exit_code = main(
        [
            "--db",
            str(db.path),
            "--saved-searches",
            str(saved_path),
            "saved-search",
            "add",
            "nairobi-international-p2-p4",
            "--city",
            "Nairobi",
            "--country",
            "KE",
            "--scope",
            "international",
            "--grade",
            "P2",
            "--grade",
            "P3",
            "--grade",
            "P4",
        ]
    )
    assert exit_code == 0

    list_output = tmp_path / "saved_list.json"
    exit_code = main(
        [
            "--db",
            str(db.path),
            "--saved-searches",
            str(saved_path),
            "saved-search",
            "list",
            "--format",
            "json",
            "--output",
            str(list_output),
        ]
    )
    listed = json.loads(list_output.read_text(encoding="utf-8"))
    assert exit_code == 0
    assert listed[0]["name"] == "nairobi-international-p2-p4"
    assert listed[0]["request"]["grade_codes"] == ["P2", "P3", "P4"]

    run_output = tmp_path / "saved_run.json"
    exit_code = main(
        [
            "--db",
            str(db.path),
            "--saved-searches",
            str(saved_path),
            "saved-search",
            "run",
            "nairobi-international-p2-p4",
            "--format",
            "json",
            "--explain",
            "--output",
            str(run_output),
        ]
    )
    run = json.loads(run_output.read_text(encoding="utf-8"))
    assert exit_code == 0
    assert run["saved_search"]["name"] == "nairobi-international-p2-p4"
    assert run["response"]["total"] == 1
    assert run["response"]["results"][0]["grade_code"] == "P4"
    assert run["response"]["results"][0]["filter_evaluation"]

    exit_code = main(
        [
            "--db",
            str(db.path),
            "--saved-searches",
            str(saved_path),
            "saved-search",
            "remove",
            "nairobi-international-p2-p4",
        ]
    )
    assert exit_code == 0
    assert json.loads(saved_path.read_text(encoding="utf-8"))["saved_searches"] == {}
