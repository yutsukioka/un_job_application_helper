"""Accepted immutable evidence survives pending or failed later refresh work."""

import json
import sqlite3

import pytest

from jobagg.pipelines.live_publication import plan_publication
from test_live_publication import add_detail, add_document, run, setup as base_setup, sha, write
from test_publication_projection import plan, snapshot


@pytest.fixture
def setup(tmp_path):
    return base_setup.__wrapped__(tmp_path)


@pytest.mark.parametrize(
    "status", ["pending", "blocked", "inflight", "not_observed", "unavailable"]
)
def test_valid_prior_detail_publishes_documents_without_finishing_new_refresh(setup, status):
    record, proof = add_detail(setup)
    document, data = add_document(setup, record, proof)
    with setup["worker"].connect() as conn:
        conn.execute(
            "UPDATE remediation_tasks SET status=?,receipt=? WHERE kind='detail'",
            (status, '{"later_failure":"timeout"}'),
        )
        conn.execute(
            "INSERT INTO remediation_attempts VALUES('later-failure','detail-001','test_custom_html','detail',1800000000,1800000010,'blocked','{}')"
        )
    result = run(setup, execute=True)
    assert result["status"] == "published"
    with setup["worker"].connect() as conn:
        assert (
            conn.execute("SELECT status FROM remediation_tasks WHERE kind='detail'").fetchone()[0]
            == status
        )
    for name in ("test_jobs.sqlite3", "all_jobs.sqlite3"):
        with sqlite3.connect(setup["output"] / name) as conn:
            assert (
                conn.execute(
                    "SELECT content FROM attachment_blobs WHERE content_sha256=?",
                    (document["content_sha256"],),
                ).fetchone()[0]
                == data
            )
            assert (
                conn.execute("SELECT job_key FROM job_attachments").fetchone()[0]
                == record.identity_key()
            )
    assert run(setup, execute=True)["status"] == "no_changes"


def test_repeated_same_attachment_damage_receives_a_new_repair_generation(setup):
    record, proof = add_detail(setup)
    add_document(setup, record, proof)
    assert run(setup, execute=True)["status"] == "published"
    database = setup["output"] / "all_jobs.sqlite3"
    repair_keys = []
    for _ in range(2):
        with sqlite3.connect(database) as conn:
            conn.execute("DELETE FROM job_attachments")
        repair_keys.append(run(setup)["plan"]["changes"][0]["publication_key"])
        assert run(setup, execute=True)["status"] == "published"
    assert repair_keys[0] != repair_keys[1]
    assert run(setup, execute=True)["status"] == "no_changes"


@pytest.mark.parametrize(
    "damage",
    [
        "no_attempt",
        "not_successful",
        "not_finished",
        "other_source",
        "other_task",
        "bad_receipt",
        "missing_artifact",
        "changed_artifact",
        "wrong_identity",
    ],
)
def test_mutable_done_task_cannot_replace_missing_or_bad_immutable_acceptance(setup, damage):
    add_detail(setup)
    with setup["worker"].connect() as conn:
        if damage == "no_attempt":
            conn.execute("DELETE FROM remediation_attempts")
        elif damage == "not_successful":
            conn.execute("UPDATE remediation_attempts SET status='blocked'")
        elif damage == "not_finished":
            conn.execute("UPDATE remediation_attempts SET finished_at=NULL")
        elif damage == "other_source":
            conn.execute("UPDATE remediation_attempts SET source_id='other'")
        elif damage == "other_task":
            conn.execute("UPDATE remediation_attempts SET task_id='missing'")
        elif damage == "bad_receipt":
            conn.execute("UPDATE remediation_attempts SET evidence='[]'")
        else:
            path = setup["root"] / "capture/001/detail.json"
            if damage == "missing_artifact":
                path.unlink()
            elif damage == "changed_artifact":
                path.write_text('{"tampered": true}')
            else:
                artifact = json.loads(path.read_text())
                artifact["job"]["external_id"] = "wrong"
                write(path, artifact)
                conn.execute(
                    "UPDATE remediation_attempts SET evidence=?",
                    (json.dumps({"detail_path": str(path), "detail_sha256": sha(path)}),),
                )
    result = run(setup, execute=True)
    assert result["status"] == "no_changes"
    assert result["rejected"][0]["reason"] == "completed_detail_artifact_not_bound"


def test_attempt_binding_preserves_public_field_guard(setup):
    add_detail(setup)
    with setup["worker"].connect() as conn:
        conn.execute("UPDATE remediation_tasks SET status='pending' WHERE kind='detail'")
        conn.execute("UPDATE jobs SET title='Unproven listing title'")
    result = run(setup, execute=True)
    assert result["status"] == "no_changes"
    assert (
        result["rejected"][0]["reason"]
        == "worker_public_fields_differ_from_completed_detail_artifact"
    )


def test_projection_retains_exact_accepted_attempt_not_whole_refresh_history(setup):
    record, proof = add_detail(setup)
    add_document(setup, record, proof)
    old = json.loads((setup["root"] / "capture/001/detail.json").read_text())
    old["proof"]["observed_at"] = "2024-01-01T00:00:00+00:00"
    older_path = setup["root"] / "older.json"
    write(older_path, old)
    with setup["worker"].connect() as conn:
        conn.execute("UPDATE remediation_tasks SET status='pending' WHERE kind='detail'")
        conn.execute(
            "INSERT INTO remediation_attempts VALUES('old','detail-001','test_custom_html','detail',1,2,'done',?)",
            (json.dumps({"detail_path": str(older_path), "detail_sha256": sha(older_path)}),),
        )
        conn.execute(
            "INSERT INTO remediation_attempts VALUES('unrelated','listing','test_custom_html','listing',1,2,'done','{}')"
        )
        conn.execute(
            "INSERT INTO remediation_attempts VALUES('failed','detail-001','test_custom_html','detail',3,4,'blocked','{}')"
        )
    full_plan = plan(setup, setup["worker"].path)
    assert len(full_plan["changes"]) == 1 and not full_plan["rejected"]
    path, receipt = snapshot(setup)
    assert plan(setup, path) == full_plan
    manifest = receipt["projection"]["manifest"]
    assert manifest["version"] == "publication-projection-2"
    assert manifest["tables"]["remediation_attempts"]["source_rows"] == 4
    assert manifest["tables"]["remediation_attempts"]["rows"] == 1
    with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as conn:
        assert conn.execute("SELECT attempt_id FROM remediation_attempts").fetchall() == [
            ("accepted-001",)
        ]


def test_projection_cannot_repair_invalid_attempt_artifact(setup):
    add_detail(setup)
    (setup["root"] / "capture/001/detail.json").write_text("{}")
    original = plan_publication(setup["worker"].path, setup["registry"], setup["output"])
    assert original["rejected"][0]["reason"] == "completed_detail_artifact_not_bound"
    path, _ = snapshot(setup)
    assert plan(setup, path) == plan(setup, setup["worker"].path)


def test_multiple_documents_have_stable_publication_key_across_projection_order(setup):
    record, proof = add_detail(setup)
    document, _ = add_document(setup, record, proof)
    # Insert a lexically earlier task second. The projection copies in primary
    # key order; insertion order must not affect published document identity.
    second = {
        **document,
        "attachment_id": "a-doc",
        "url": "https://example.org/jobs/001/addendum.txt",
        "final_url": "https://example.org/jobs/001/addendum.txt",
    }
    manifest = setup["root"] / "second-document.json"
    write(manifest, second)
    with setup["worker"].connect() as conn:
        conn.execute(
            "INSERT INTO remediation_documents VALUES(?,?,?,?,?,?,?)",
            (
                "a-doc",
                record.identity_key(),
                record.source_id,
                second["url"],
                document["content_sha256"],
                document["text_sha256"],
                json.dumps(second),
            ),
        )
        conn.execute(
            "INSERT INTO remediation_tasks VALUES(?,?,?,?,?,?,?)",
            (
                "a-doc",
                record.source_id,
                "document",
                "a-doc",
                "done",
                json.dumps({"manifest": str(manifest), "sha256": sha(manifest)}),
                json.dumps(
                    {
                        "url": second["url"],
                        "job_key": record.identity_key(),
                        "parent_description_sha256": proof["parsed_source_text_sha256"],
                    }
                ),
            ),
        )
    expected = plan(setup, setup["worker"].path)
    assert [doc["attachment_id"] for doc in expected["changes"][0]["documents"]] == [
        "a-doc",
        "doc1",
    ]
    path, _ = snapshot(setup)
    projected = plan(setup, path)
    assert projected == expected
    assert projected["changes"][0]["publication_key"] == expected["changes"][0]["publication_key"]


@pytest.mark.parametrize("damage", ["association", "extracted_text", "parent", "raw"])
def test_publication_receipt_does_not_hide_attachment_drift_and_repairs_are_idempotent(
    setup, damage
):
    record, proof = add_detail(setup)
    add_document(setup, record, proof)
    assert run(setup, execute=True)["status"] == "published"
    database = setup["output"] / "all_jobs.sqlite3"
    with sqlite3.connect(database) as conn:
        original_key = conn.execute(
            "SELECT publication_key FROM jobagg_publication_receipts"
        ).fetchone()[0]
        if damage == "association":
            conn.execute("DELETE FROM job_attachments")
        elif damage == "extracted_text":
            conn.execute("UPDATE job_attachments SET extracted_text='Stale text'")
        elif damage == "parent":
            metadata = json.loads(
                conn.execute("SELECT metadata_json FROM job_attachments").fetchone()[0]
            )
            metadata["parent_description_sha256"] = "stale-parent"
            conn.execute("UPDATE job_attachments SET metadata_json=?", (json.dumps(metadata),))
        else:
            raw = json.loads(
                conn.execute(
                    "SELECT raw_json FROM jobs WHERE job_key=?", (record.identity_key(),)
                ).fetchone()[0]
            )
            raw["attachments"] = []
            conn.execute(
                "UPDATE jobs SET raw_json=? WHERE job_key=?",
                (json.dumps(raw), record.identity_key()),
            )
    preview = run(setup)
    change = preview["plan"]["changes"][0]
    assert change["repair_of_publication_key"] == original_key
    assert change["publication_key"] != original_key
    assert run(setup, execute=True)["status"] == "published"
    with sqlite3.connect(database) as conn:
        assert conn.execute("SELECT count(*) FROM jobagg_publication_receipts").fetchone()[0] == 2
        assert (
            conn.execute(
                "SELECT count(*) FROM jobagg_publication_receipts WHERE publication_key=?",
                (original_key,),
            ).fetchone()[0]
            == 1
        )
    assert run(setup, execute=True)["status"] == "no_changes"


def test_reconciliation_never_overwrites_corrupted_content_addressed_blob(setup):
    record, proof = add_detail(setup)
    add_document(setup, record, proof)
    assert run(setup, execute=True)["status"] == "published"
    with sqlite3.connect(setup["output"] / "all_jobs.sqlite3") as conn:
        conn.execute("UPDATE attachment_blobs SET content=x'01'")
    assert run(setup)["plan"]["changes"][0]["repair_of_publication_key"]
    with pytest.raises(
        ValueError,
        match="live_beforeimage_document_blob_conflict|live_attachment_hash_collision_or_corruption",
    ):
        run(setup, execute=True)


def test_reconciliation_preserves_reviewed_document_purpose_and_reports_conflict(setup):
    record, proof = add_detail(setup)
    add_document(setup, record, proof)
    assert run(setup, execute=True)["status"] == "published"
    with sqlite3.connect(setup["output"] / "all_jobs.sqlite3") as conn:
        conn.execute("UPDATE job_attachments SET category='reference',required_for_complete_text=0")
    result = run(setup, execute=True)
    assert result["status"] == "no_changes"
    assert result["rejected"][0]["reason"] == "live_document_purpose_conflict_requires_review"
    with sqlite3.connect(setup["output"] / "all_jobs.sqlite3") as conn:
        assert conn.execute(
            "SELECT category,required_for_complete_text FROM job_attachments"
        ).fetchone() == ("reference", 0)
