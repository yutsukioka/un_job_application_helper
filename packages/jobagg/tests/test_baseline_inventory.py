import hashlib
import json
import os
import sqlite3

import pytest

from jobagg.baseline_inventory import _owner, reconcile_inventory, retain_baseline_for_listing
from jobagg.db import JobDatabase
from jobagg.models import JobRecord
from jobagg.pipelines.document_tasks import discover_documents


def description(word="Retained"):
    return (
        word
        + ": Responsibilities include coordinating programmes, preparing project reports and evaluating results. Requirements: university degree and five years of relevant experience. "
        * 5
    )


def job(identity="1", *, source="test_custom_html", org="Historical Organization", body=None):
    return JobRecord(
        source_id=source,
        org_id=org,
        ats_family="custom_html",
        external_id=identity,
        title="Officer " + identity,
        apply_url="https://example.org/jobs/" + identity,
        source_url="https://example.org/jobs/" + identity,
        description=description() if body is None else body,
        raw={
            "reviewed_override": "preserved",
            "_jobagg_main_text_verification": {"complete": True},
        },
    )


@pytest.fixture
def setup(tmp_path):
    live, worker = JobDatabase(tmp_path / "live.sqlite3"), JobDatabase(tmp_path / "worker.sqlite3")
    live.initialize()
    worker.initialize()
    with worker.connect() as conn:
        conn.executescript("""
        CREATE TABLE remediation_observations(job_key TEXT PRIMARY KEY,proof TEXT);
        CREATE TABLE remediation_tasks(task_id TEXT PRIMARY KEY,status TEXT,payload TEXT);
        INSERT INTO remediation_tasks VALUES('original','blocked','original queue');
        """)
    registry = tmp_path / "registry.yaml"
    registry.write_text(
        "sources:\n- id: test_custom_html\n  name: Test\n  ats_family: custom_html\n  base_url: https://example.org\n  enabled: true\n- id: disabled\n  name: Disabled\n  ats_family: custom_html\n  base_url: https://disabled.org\n  enabled: false\n"
    )
    gate = tmp_path / ".jobagg-publication-state.json"
    gate.write_text(json.dumps({"state": "complete", "generation_id": "retained-generation"}))
    lock = tmp_path / "owner.lock"
    lock.touch()
    return {"live": live, "worker": worker, "registry": registry, "gate": gate, "lock": lock}


def run(setup, **options):
    with _owner(setup["lock"]) as fd:
        return reconcile_inventory(
            setup["live"].path,
            setup["worker"].path,
            setup["registry"],
            setup["lock"],
            owner_fd=fd,
            **options,
        )


def domain(path):
    with sqlite3.connect(path) as conn:
        return {
            name: conn.execute(f"SELECT * FROM {name} ORDER BY 1").fetchall()
            for name in ("jobs", "attachment_blobs", "job_attachments", "change_events")
        }


def add_document(
    setup, record, content=b"exact retained bytes", *, claimed_sha=None, attachment_id="doc"
):
    digest = claimed_sha or hashlib.sha256(content).hexdigest()
    with setup["live"].connect() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO attachment_blobs VALUES(?,?,?,?)",
            (digest, "application/pdf", len(content), content),
        )
        conn.execute(
            "INSERT INTO job_attachments VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                attachment_id,
                record.identity_key(),
                record.source_id,
                "https://example.org/terms.pdf",
                None,
                "Terms of Reference",
                "tor",
                1,
                "complete",
                digest,
                "Original full attachment text",
                '{"original":true}',
            ),
        )
        raw = json.loads(
            conn.execute(
                "SELECT raw_json FROM jobs WHERE job_key=?", (record.identity_key(),)
            ).fetchone()[0]
        )
        raw["attachments"] = [
            {
                "url": "https://example.org/terms.pdf",
                "binary_ref": {"table": "attachment_blobs", "key": digest},
                "fidelity_complete": True,
            }
        ]
        conn.execute(
            "UPDATE jobs SET raw_json=? WHERE job_key=?", (json.dumps(raw), record.identity_key())
        )
    return digest


def test_missing_live_job_becomes_uncertified_worker_baseline_and_original_is_exact(setup):
    original = job()
    original.status = "closed"
    setup["live"].upsert_job(original)
    with setup["live"].connect() as conn:
        conn.execute(
            "UPDATE jobs SET application_ready=1,source_listed_current=1,trusted_current=1"
        )
    before_live = domain(setup["live"].path)
    preview = run(setup)
    assert preview["inserted_with_text"] == 1
    result = run(setup, execute=True, expected_preview_sha256=preview["preview_sha256"])
    assert result["jobs_examined"] == 1 and not result["completeness_certified"]
    current = setup["worker"].get_job("test_custom_html:1")
    assert current["org_id"] == original.org_id
    assert current["description"] == original.description
    assert current["status"] == "closed"
    assert [
        current[k] for k in ("application_ready", "trusted_current", "source_listed_current")
    ] == [0, 0, 0]
    assert current["last_seen_at"] == before_live["jobs"][0][18]
    with setup["worker"].connect() as conn:
        archived = json.loads(
            conn.execute("SELECT row_json FROM baseline_inventory_jobs").fetchone()[0]
        )
        assert (
            archived["org_id"] == original.org_id and archived["job_key"] == original.identity_key()
        )
        assert json.loads(archived["raw_json"])["_jobagg_main_text_verification"]["complete"]
        assert conn.execute("SELECT count(*) FROM remediation_observations").fetchone()[0] == 0
        assert tuple(conn.execute("SELECT * FROM remediation_tasks").fetchone()) == (
            "original",
            "blocked",
            "original queue",
        )
        listing = job(org="test_custom_html", body="")
        receipt = retain_baseline_for_listing(conn, listing)
        assert (
            receipt["job_key"] == listing.identity_key()
            and not receipt["current_membership_certified"]
        )
    assert domain(setup["live"].path) == before_live
    second = run(setup, execute=True)
    assert second["dispositions"] == {"preserve_worker_same_body": 1}
    with setup["worker"].connect() as conn:
        assert conn.execute("SELECT count(*) FROM baseline_inventory_jobs").fetchone()[0] == 1


def test_accepted_newer_worker_and_queue_are_never_overwritten(setup):
    live = job()
    setup["live"].upsert_job(live)
    newer = job(org="test_custom_html", body=description("Newer accepted"))
    setup["worker"].upsert_job(newer)
    with setup["worker"].connect() as conn:
        conn.execute(
            "INSERT INTO remediation_observations VALUES(?,?)",
            (newer.identity_key(), "accepted proof"),
        )
    before = domain(setup["worker"].path)
    result = run(setup, execute=True, fill_empty_stubs=True)
    assert result["worker_observations_retained"] == 1
    assert domain(setup["worker"].path) == before


def test_stub_fill_preserves_worker_membership_metadata_raw_and_beforeimage(setup):
    live = job()
    setup["live"].upsert_job(live)
    stub = job(org="test_custom_html", body="")
    stub.raw = {"current_listing_raw": "keep me"}
    setup["worker"].upsert_job(stub)
    with setup["worker"].connect() as conn:
        conn.execute(
            "UPDATE jobs SET source_listed_current=1,trusted_current=1,source_freshness_status='fresh',source_health_status='ok',application_ready=1"
        )
    assert run(setup)["dispositions"] == {"preserve_worker_content": 1}
    assert run(setup, execute=True, fill_empty_stubs=True)["dispositions"] == {"fill_empty_stub": 1}
    actual = setup["worker"].get_job(stub.identity_key())
    assert actual["description"] == live.description
    assert [
        actual[k]
        for k in (
            "source_listed_current",
            "trusted_current",
            "source_freshness_status",
            "source_health_status",
            "application_ready",
        )
    ] == [1, 1, "fresh", "ok", 0]
    assert actual["raw"]["current_listing_raw"] == "keep me"
    with setup["worker"].connect() as conn:
        assert (
            conn.execute("SELECT count(*) FROM baseline_inventory_beforeimages").fetchone()[0] == 1
        )


def test_original_attachment_bytes_and_text_are_archived_without_active_binary_refs(setup):
    original = job()
    setup["live"].upsert_job(original)
    content_sha = add_document(setup, original)
    result = run(setup, execute=True)
    assert result["attachment_statuses"] == {"verified_original_bytes": 1}
    with setup["worker"].connect() as conn:
        assert (
            bytes(
                conn.execute(
                    "SELECT content FROM baseline_inventory_blobs WHERE content_sha256=?",
                    (content_sha,),
                ).fetchone()[0]
            )
            == b"exact retained bytes"
        )
        association = json.loads(
            conn.execute("SELECT row_json FROM baseline_inventory_attachments").fetchone()[0]
        )
        assert association["extracted_text"] == "Original full attachment text"
        assert conn.execute("SELECT count(*) FROM job_attachments").fetchone()[0] == 0
    raw = setup["worker"].get_job("test_custom_html:1")["raw"]
    assert "attachments" not in raw
    # A fresh detail does not inherit fake operational attachment successes.
    fresh = job(
        org="test_custom_html",
        body=description("Fresh") + '<a href="/current-tor.pdf">Terms of Reference</a>',
    )
    fresh.raw = {}
    setup["worker"].upsert_job(fresh)
    actual = setup["worker"].get_job(fresh.identity_key())
    assert "attachments" not in actual["raw"]
    assert any(item["url"].endswith("/current-tor.pdf") for item in discover_documents(fresh))
    with setup["worker"].connect() as conn:
        assert retain_baseline_for_listing(conn, job(org="test_custom_html", body="")) is None


def test_byte_budget_preserves_unresolved_reference_and_later_retry_fills_bytes(setup):
    original = job()
    setup["live"].upsert_job(original)
    add_document(setup, original)
    assert run(setup, execute=True, max_bytes=0)["attachment_statuses"] == {
        "unresolved_batch_byte_budget": 1
    }
    with setup["worker"].connect() as conn:
        assert conn.execute("SELECT count(*) FROM baseline_inventory_blobs").fetchone()[0] == 0
    run(setup, execute=True)
    with setup["worker"].connect() as conn:
        row = conn.execute(
            "SELECT blob_status,evidence_json FROM baseline_inventory_attachments"
        ).fetchone()
        assert row[0] == json.loads(row[1])["blob_status"] == "verified_original_bytes"


def test_wrong_blob_hash_is_explicitly_unresolved_and_not_copied(setup):
    original = job()
    setup["live"].upsert_job(original)
    add_document(setup, original, claimed_sha="0" * 64)
    assert run(setup, execute=True)["attachment_statuses"] == {
        "unresolved_content_hash_mismatch": 1
    }
    with setup["worker"].connect() as conn:
        assert conn.execute("SELECT count(*) FROM baseline_inventory_blobs").fetchone()[0] == 0


def test_disabled_unknown_and_ambiguous_identities_are_archive_only(setup):
    for row in (
        job(source="disabled"),
        job("2", source="unknown"),
        job("3"),
        job("3", org="Other Organization"),
    ):
        setup["live"].upsert_job(row)
    result = run(setup, execute=True)
    assert result["dispositions"] == {
        "archive_disabled_source": 1,
        "archive_unknown_source": 1,
        "archive_identity_conflict": 2,
    }
    with setup["worker"].connect() as conn:
        assert conn.execute("SELECT count(*) FROM jobs").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM baseline_inventory_jobs").fetchone()[0] == 4


def test_cursor_batches_are_bounded_and_cover_every_live_row(setup):
    for identity in ("1", "2", "3"):
        setup["live"].upsert_job(job(identity))
    first = run(setup, execute=True, max_jobs=2)
    assert first["jobs_examined"] == 2 and first["has_more"]
    second = run(setup, execute=True, max_jobs=2, after_key=first["next_cursor"])
    assert second["jobs_examined"] == 1 and not second["has_more"]
    with setup["worker"].connect() as conn:
        assert conn.execute("SELECT count(*) FROM jobs").fetchone()[0] == 3


def test_changed_preview_or_interrupted_commit_rolls_back_all_mutations(setup):
    setup["live"].upsert_job(job())
    preview = run(setup)
    setup["worker"].upsert_job(job(org="test_custom_html", body=description("Changed")))
    with pytest.raises(ValueError, match="preview"):
        run(setup, execute=True, expected_preview_sha256=preview["preview_sha256"])
    before = domain(setup["worker"].path)

    def fault(*_):
        raise RuntimeError("simulated interruption")

    with pytest.raises(RuntimeError, match="simulated"):
        run(setup, execute=True, fault=fault)
    assert domain(setup["worker"].path) == before
    with setup["worker"].connect() as conn:
        assert not conn.execute(
            "SELECT 1 FROM sqlite_master WHERE name='baseline_inventory_jobs'"
        ).fetchone()


def test_gate_change_during_import_rolls_back_and_unowned_fd_is_rejected(setup):
    setup["live"].upsert_job(job())

    def fault(*_):
        setup["gate"].write_text(json.dumps({"state": "publishing", "generation_id": "next"}))

    with pytest.raises(ValueError, match="gate"):
        run(setup, execute=True, fault=fault)
    with setup["worker"].connect() as conn:
        assert conn.execute("SELECT count(*) FROM jobs").fetchone()[0] == 0
    with setup["lock"].open("r+") as unowned, pytest.raises(ValueError, match="held shared owner"):
        reconcile_inventory(
            setup["live"].path,
            setup["worker"].path,
            setup["registry"],
            setup["lock"],
            owner_fd=unowned.fileno(),
        )


def test_aliasing_live_as_worker_and_symlink_inputs_are_rejected(setup):
    link = setup["worker"].path.parent / "hardlink.sqlite3"
    os.link(setup["live"].path, link)
    with _owner(setup["lock"]) as fd, pytest.raises(ValueError, match="alias"):
        reconcile_inventory(
            setup["live"].path, link, setup["registry"], setup["lock"], owner_fd=fd, execute=True
        )
    symlink = setup["worker"].path.parent / "symlink.sqlite3"
    symlink.symlink_to(setup["live"].path)
    with _owner(setup["lock"]) as fd, pytest.raises(ValueError, match="regular"):
        reconcile_inventory(
            symlink, setup["worker"].path, setup["registry"], setup["lock"], owner_fd=fd
        )


def test_duplicate_document_bytes_only_consume_budget_once(setup):
    original = job()
    setup["live"].upsert_job(original)
    add_document(setup, original, content=b"abc", attachment_id="a")
    add_document(setup, original, content=b"abc", attachment_id="b")
    result = run(setup, execute=True, max_bytes=3)
    assert result["attachment_statuses"] == {"verified_original_bytes": 2}
    assert result["verified_distinct_blob_bytes"] == 3


def test_metadata_budget_stops_on_complete_record_and_reports_cursor(setup):
    setup["live"].upsert_job(job("1"))
    setup["live"].upsert_job(job("2"))
    first_size = run(setup, max_jobs=1)["prepared_metadata_bytes"]
    result = run(setup, max_jobs=2, max_metadata_bytes=first_size + 1)
    assert result["jobs_examined"] == 1 and result["has_more"]
    with pytest.raises(ValueError, match="max_metadata_bytes"):
        run(setup, execute=True, max_metadata_bytes=1)
    with setup["worker"].connect() as conn:
        assert conn.execute("SELECT count(*) FROM jobs").fetchone()[0] == 0


def test_identical_existing_body_without_observation_gets_only_marker_and_retention_link(setup):
    live = job()
    setup["live"].upsert_job(live)
    add_document(setup, live)
    matching = job(org="test_custom_html")
    matching.raw = {"worker_existing_metadata": "retain exactly"}
    setup["worker"].upsert_job(matching)
    with setup["worker"].connect() as conn:
        conn.execute("UPDATE jobs SET source_listed_current=1,source_health_status='ok'")
        before = dict(conn.execute("SELECT * FROM jobs").fetchone())
    result = run(setup, execute=True)
    assert result["dispositions"] == {"adopt_matching_worker_body": 1}
    with setup["worker"].connect() as conn:
        after = dict(conn.execute("SELECT * FROM jobs").fetchone())
        assert {k: v for k, v in after.items() if k != "raw_json"} == {
            k: v for k, v in before.items() if k != "raw_json"
        }
        raw = json.loads(after["raw_json"])
        assert raw["worker_existing_metadata"] == "retain exactly" and "attachments" not in raw
        assert conn.execute("SELECT count(*) FROM remediation_observations").fetchone()[0] == 0
        assert (
            json.loads(
                conn.execute("SELECT row_json FROM baseline_inventory_beforeimages").fetchone()[0]
            )
            == before
        )
        assert retain_baseline_for_listing(conn, job(org="test_custom_html", body=""))
    assert run(setup, execute=True)["dispositions"] == {"preserve_worker_same_body": 1}
