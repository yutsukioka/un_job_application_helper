"""Publication coverage checks identity associations, not just shared bytes."""

import json
import sqlite3

import pytest

from jobagg.fetch_coverage import census
from test_live_publication import add_detail, add_document, add_frame, run, setup as base_setup


@pytest.fixture
def setup(tmp_path):
    fixture = base_setup.__wrapped__(tmp_path)
    record, proof = add_detail(fixture)
    document, _ = add_document(fixture, record, proof)
    add_frame(fixture, external_ids=("001",))
    assert run(fixture, execute=True)["status"] == "published"
    fixture["document"] = document
    return fixture


def coverage(setup, **kwargs):
    return census(
        setup["registry"], setup["worker"].path, setup["output"] / "all_jobs.sqlite3", **kwargs
    )


def test_complete_database_readback_separates_task_and_binary_counts(setup):
    result = coverage(setup)
    totals = result["totals"]
    assert totals["current_document_done_tasks"] == 1
    assert totals["current_document_blobs_match"] == 1
    assert totals["current_live_document_blobs_match"] == 1
    assert totals["current_live_documents_verified"] == 1
    assert totals["current_source_documents_verified"] == 1
    assert result["document_content_hash_counts"]["live_verified"] == 1
    assert result["disabled_count"] == 1
    assert result["publication_gate_complete"]
    assert result["publication_gate_unchanged_during_census"]
    assert result["completeness_certified"] is False
    item = result["enabled_sources"][0]["document_reconciliation"][0]
    assert item["live"]["resolved_job_key"] == "test:001"
    assert item["source"]["checks"]["verified"]


@pytest.mark.parametrize(
    "damage",
    ["association", "text", "parent", "metadata", "raw", "blob", "live_parent_text", "purpose"],
)
def test_blob_presence_never_masks_association_or_fidelity_gap(setup, damage):
    with sqlite3.connect(setup["output"] / "all_jobs.sqlite3") as conn:
        if damage == "association":
            conn.execute("DELETE FROM job_attachments")
        elif damage == "text":
            conn.execute("UPDATE job_attachments SET extracted_text='Wrong extraction'")
        elif damage in {"parent", "metadata"}:
            metadata = json.loads(
                conn.execute("SELECT metadata_json FROM job_attachments").fetchone()[0]
            )
            metadata["parent_description_sha256" if damage == "parent" else "final_url"] = "wrong"
            conn.execute("UPDATE job_attachments SET metadata_json=?", (json.dumps(metadata),))
        elif damage == "raw":
            raw = json.loads(
                conn.execute("SELECT raw_json FROM jobs WHERE job_key='test:001'").fetchone()[0]
            )
            raw["attachments"] = []
            conn.execute("UPDATE jobs SET raw_json=? WHERE job_key='test:001'", (json.dumps(raw),))
        elif damage == "blob":
            conn.execute("UPDATE attachment_blobs SET content=x'010203'")
        elif damage == "purpose":
            conn.execute(
                "UPDATE job_attachments SET category='unrelated',required_for_complete_text=0"
            )
        else:
            conn.execute("UPDATE jobs SET description='Unrelated body' WHERE job_key='test:001'")
    result = coverage(setup)
    totals = result["totals"]
    assert totals["current_live_documents_verified"] == 0
    assert totals["current_source_documents_verified"] == 1
    assert totals["current_live_document_blobs_match"] == (0 if damage == "blob" else 1)
    assert any(
        g["gap"] == "current_document_publication_unverified"
        for g in result["enabled_sources"][0]["gaps"]
    )


def test_source_destination_is_checked_independently(setup):
    with sqlite3.connect(setup["output"] / "test_jobs.sqlite3") as conn:
        conn.execute("DELETE FROM job_attachments")
    result = coverage(setup)
    assert result["totals"]["current_source_documents_verified"] == 0
    assert result["totals"]["current_live_documents_verified"] == 1


def test_missing_source_database_is_reported_without_creating_it(setup, tmp_path):
    missing = tmp_path / "other-output"
    result = coverage(setup, source_output_dir=missing)
    assert not missing.exists()
    source = result["enabled_sources"][0]
    assert not source["source_live_database_available"]
    assert source["source_live_database_error"]
    assert source["counts"]["current_source_documents_verified"] == 0
    assert source["counts"]["current_live_documents_verified"] == 1


def test_two_tasks_sharing_same_blob_have_one_distinct_binary(setup):
    with setup["worker"].connect() as conn:
        conn.execute(
            "INSERT INTO remediation_tasks SELECT 'doc2',source_id,kind,'doc2',status,receipt,payload FROM remediation_tasks WHERE task_id='doc1'"
        )
        conn.execute(
            "INSERT INTO remediation_documents SELECT 'doc2',job_key,source_id,url,content_sha256,text_sha256,manifest FROM remediation_documents WHERE task_id='doc1'"
        )
    result = coverage(setup)
    assert result["totals"]["current_document_done_tasks"] == 2
    assert result["totals"]["current_live_documents_verified"] == 2
    assert result["document_content_hash_counts"]["current_done"] == 1
    assert result["document_content_hash_counts"]["live_verified"] == 1


def test_wrong_worker_manifest_cannot_become_verified_publication(setup):
    with setup["worker"].connect() as conn:
        document = json.loads(
            conn.execute("SELECT manifest FROM remediation_documents").fetchone()[0]
        )
        document["source_id"] = "different-source"
        conn.execute("UPDATE remediation_documents SET manifest=?", (json.dumps(document),))
    result = coverage(setup)
    assert result["totals"]["current_document_blobs_match"] == 1
    assert result["totals"]["current_document_manifest_matches"] == 0
    assert result["totals"]["current_live_documents_verified"] == 0


def test_cross_source_alias_is_identity_failure_not_matching_blob_success(setup):
    with sqlite3.connect(setup["output"] / "all_jobs.sqlite3") as conn:
        columns = [r[1] for r in conn.execute("PRAGMA table_info(consolidated_job_aliases)")]
        values = {
            "duplicate_job_key": "test:001",
            "canonical_job_key": "test:001",
            "canonical_source_id": "other-source",
        }
        # The production alias table may have additional non-null evidence fields.
        row = {key: values.get(key, "fixture") for key in columns}
        conn.execute(
            "INSERT INTO consolidated_job_aliases("
            + ",".join(columns)
            + ") VALUES("
            + ",".join("?" for _ in columns)
            + ")",
            list(row.values()),
        )
    result = coverage(setup)
    assert result["totals"]["current_live_document_blobs_match"] == 1
    assert result["totals"]["current_live_documents_verified"] == 0
    assert (
        "cross_source_alias_requires_review"
        in result["enabled_sources"][0]["document_reconciliation"][0]["live"]["errors"][0]
    )
