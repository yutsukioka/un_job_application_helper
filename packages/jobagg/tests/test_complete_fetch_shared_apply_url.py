"""Distinct public requisitions must survive shared application directories."""
import sqlite3

import pytest

from jobagg.db import JobDatabase
from jobagg.models import OrganizationSource
from jobagg.normalize import build_job
from jobagg.pipelines.consolidation import consolidate_bundle_databases


@pytest.mark.parametrize(
    "source_ids,families,external_ids,expected_open",
    [
        (("ifad", "ifad"), ("peoplesoft", "peoplesoft"), ("4994", "37861"), 2),
        (("eu", "eu"), ("eu_careers_static", "eu_careers_static"), ("CA-007", "TA-018"), 2),
        (("a", "b"), ("inspira", "inspira"), ("one", "two"), 2),
        (("a", "b"), ("inspira", "peoplesoft"), ("123", "123"), 2),
        (("a", "b"), ("inspira", "inspira"), (None, None), 2),
        (("a", "b"), ("inspira", "inspira"), ("123", "123"), 2),
    ],
)
def test_shared_url_requires_corroborating_vacancy_identity(
    tmp_path, source_ids, families, external_ids, expected_open
):
    for index, (source_id, family, external_id) in enumerate(zip(source_ids, families, external_ids)):
        source = OrganizationSource(source_id, source_id, family, "https://careers.example.org")
        db = JobDatabase(tmp_path / f"{source_id}_jobs.sqlite3")
        db.initialize()
        job = build_job(
            source, title=f"Distinct public role {index}", external_id=external_id,
            apply_url="https://careers.example.org/search?language=en",
            description="Responsibilities and qualifications for this public vacancy.",
            closes_at="2099-12-31",
        )
        db.upsert_job(job)
    result = consolidate_bundle_databases(output_dir=tmp_path)
    with sqlite3.connect(result.db_path) as conn:
        assert conn.execute("SELECT count(*) FROM jobs").fetchone()[0] == 2
        assert conn.execute("SELECT count(*) FROM jobs WHERE status='open'").fetchone()[0] == expected_open
        aliases = conn.execute("SELECT duplicate_external_id FROM consolidated_job_aliases").fetchall()
        assert len(aliases) == 2 - expected_open


def test_same_source_legacy_keys_still_deduplicate_by_exact_requisition_id(tmp_path):
    source = OrganizationSource("source", "Source", "inspira", "https://careers.example.org")
    db = JobDatabase(tmp_path / "source_jobs.sqlite3")
    db.initialize()
    for identity in ("old-key", "new-key"):
        db.upsert_job(build_job(source, title="Same known public role", external_id=identity,
                               apply_url=f"https://careers.example.org/job/{identity}",
                               description="Responsibilities and qualifications for this public vacancy.",
                               closes_at="2099-12-31"))
    with db.connect() as conn:
        conn.execute("UPDATE jobs SET external_id='123'")
    result = consolidate_bundle_databases(output_dir=tmp_path)
    with sqlite3.connect(result.db_path) as conn:
        assert conn.execute("SELECT count(*) FROM jobs WHERE status='open'").fetchone()[0] == 1
        assert conn.execute("SELECT reason FROM consolidated_job_aliases").fetchone()[0] == "same_ats_external_id"
