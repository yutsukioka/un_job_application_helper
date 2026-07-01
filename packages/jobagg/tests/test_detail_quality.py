from jobagg.db import JobDatabase
from jobagg.detail_quality import (
    DETAIL_QUALITY_COMPLETE,
    DETAIL_QUALITY_LIST_ONLY,
    detail_quality_status,
    oracle_detail_payload_has_substantive_content,
)
from jobagg.models import JobRecord


def test_oracle_short_description_only_is_list_only():
    raw = {
        "source_priority": "oracle_hcm_ce",
        "oracle_site_number": "CX_1",
        "ShortDescriptionStr": "The Programme Analyst supports the portfolio team.",
    }

    assert (
        detail_quality_status(
            title="Programme Analyst",
            description="The Programme Analyst supports the portfolio team.",
            raw=raw,
        )
        == DETAIL_QUALITY_LIST_ONLY
    )


def test_oracle_long_structured_short_description_can_be_complete():
    description = (
        "Organizational Setting "
        + "This role supports aviation operations. " * 30
        + "Responsibilities "
        + "Coordinate implementation and reporting. " * 20
        + "Requirements "
        + "Advanced expertise and relevant experience are required. " * 20
    )
    raw = {
        "source_priority": "oracle_hcm_ce",
        "oracle_site_number": "CX_3001",
        "ShortDescriptionStr": description,
    }

    assert oracle_detail_payload_has_substantive_content(raw)
    assert (
        detail_quality_status(
            title="Administrative Assistant",
            description=description,
            raw=raw,
        )
        == DETAIL_QUALITY_COMPLETE
    )


def test_oracle_apply_by_short_description_can_be_complete_when_substantive():
    description = (
        "Apply by: 30/06/2026\n\n"
        "Introduction "
        + "This assignment supports migration programming, stakeholder coordination, "
        "case management, data analysis, reporting, and direct operational delivery. " * 80
    )
    raw = {
        "source_priority": "oracle_hcm_ce",
        "oracle_site_number": "CX_1001",
        "ShortDescriptionStr": description,
    }

    assert oracle_detail_payload_has_substantive_content(raw)
    assert (
        detail_quality_status(
            title="Intern",
            description=description,
            raw=raw,
        )
        == DETAIL_QUALITY_COMPLETE
    )


def test_db_preserves_existing_complete_description_when_new_oracle_summary_is_incomplete(tmp_path):
    db = JobDatabase(tmp_path / "jobs.sqlite3")
    db.initialize()
    full_description = (
        "Responsibilities "
        + "Deliver programme management and stakeholder coordination. " * 20
        + "Qualifications "
        + "Advanced degree and relevant experience are required. " * 20
    )
    db.upsert_jobs(
        [
            JobRecord(
                source_id="undp_oracle_hcm",
                org_id="undp_oracle_hcm",
                ats_family="oracle_hcm",
                external_id="34131",
                title="Programme Analyst",
                apply_url="https://example.org/34131",
                description=full_description,
                raw={
                    "source_priority": "oracle_hcm_ce",
                    "oracle_site_number": "CX_1",
                    "ExternalResponsibilitiesStr": "Deliver programme management.",
                    "ExternalQualificationsStr": "Advanced degree required.",
                },
            )
        ]
    )

    db.upsert_jobs(
        [
            JobRecord(
                source_id="undp_oracle_hcm",
                org_id="undp_oracle_hcm",
                ats_family="oracle_hcm",
                external_id="34131",
                title="Programme Analyst",
                apply_url="https://example.org/34131",
                description="The Programme Analyst supports the portfolio team.",
                raw={
                    "source_priority": "oracle_hcm_ce",
                    "oracle_site_number": "CX_1",
                    "ShortDescriptionStr": "The Programme Analyst supports the portfolio team.",
                },
            )
        ]
    )

    row = db.get_job("undp_oracle_hcm:34131")

    assert row is not None
    assert row["description"] == full_description
    assert row["raw"]["ExternalResponsibilitiesStr"] == "Deliver programme management."
