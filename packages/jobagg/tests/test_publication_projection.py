from copy import deepcopy
import fcntl
import hashlib
import os
from pathlib import Path
import sqlite3
import time

import pytest

from jobagg.models import JobRecord
from jobagg.pipelines.live_publication import plan_publication
from jobagg.publication_projection import projection_diagnostics
from jobagg.publication_snapshot import create_publication_snapshot, validate_publication_snapshot
from test_live_publication import add_detail, add_document, add_frame, setup as live_setup


@pytest.fixture
def setup(tmp_path):
    return live_setup.__wrapped__(tmp_path)


def snapshot(setup, *, projection=True, name="projection.sqlite3", **kwargs):
    root = setup["root"].resolve()
    lock = root / "projection.lock"
    target = root / name
    with lock.open("a+") as owner:
        fcntl.flock(owner, fcntl.LOCK_EX | fcntl.LOCK_NB)
        result = create_publication_snapshot(
            setup["worker"].path.resolve(),
            target,
            "a" * 64,
            owner.fileno(),
            lock,
            projection=projection,
            **kwargs,
        )
    return target, result


def plan(setup, database):
    result = plan_publication(database, setup["registry"], setup["output"])
    for key in ("worker_database", "created_at", "publication_projection"):
        result.pop(key, None)
    return result


def test_projection_excludes_baseline_bulk_preserves_accepted_details_frames_and_bytes(setup):
    record, proof = add_detail(setup)
    add_document(setup, record, proof)
    add_frame(setup)
    unobserved = JobRecord(
        source_id="test_custom_html",
        org_id="Test",
        ats_family="custom_html",
        external_id="legacy",
        title="Retained historical role",
        apply_url="https://example.org/old",
        description="retained live baseline " * 100000,
    )
    setup["worker"].upsert_job(unobserved)
    with setup["worker"].connect() as conn:
        conn.execute("CREATE TABLE baseline_inventory_jobs(id TEXT,payload BLOB)")
        conn.execute(
            "INSERT INTO baseline_inventory_jobs VALUES('old',?)", (b"archive bytes" * 100000,)
        )
        conn.execute(
            "INSERT INTO attachment_blobs VALUES('unreferenced','application/pdf',?,?)",
            (1000000, b"x" * 1000000),
        )
    full_plan = plan(setup, setup["worker"].path)
    target, receipt = snapshot(setup)
    assert receipt["kind"] == "sqlite_publication_projection"
    assert target.stat().st_mode & 0o222 == 0 and target.stat().st_nlink == 1
    assert target.stat().st_size < setup["worker"].path.stat().st_size / 5
    assert plan(setup, target) == full_plan
    with sqlite3.connect(target.as_uri() + "?mode=ro", uri=True) as conn:
        assert conn.execute("SELECT count(*) FROM jobs").fetchone()[0] == 1
        assert conn.execute("SELECT count(*) FROM attachment_blobs").fetchone()[0] == 1
        assert not conn.execute(
            "SELECT 1 FROM sqlite_master WHERE name='baseline_inventory_jobs'"
        ).fetchone()
        metadata = projection_diagnostics(conn)
        assert metadata["worker_jobs"] == 2 and metadata["jobs_without_observations"] == 1
        assert metadata["baseline_archived_jobs"] == 1
        assert conn.execute("PRAGMA integrity_check").fetchall() == [("ok",)]
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
    assert not any(Path(str(target) + suffix).exists() for suffix in ("-wal", "-shm", "-journal"))


@pytest.mark.parametrize(
    "failure",
    [
        "missing_job",
        "missing_task",
        "missing_successful_attempt",
        "missing_blob",
        "bad_blob",
        "missing_document_task",
        "pending_document_task",
        "wrong_parent",
    ],
)
def test_projection_never_filters_away_existing_failure_evidence(setup, failure):
    record, proof = add_detail(setup)
    manifest, _ = add_document(setup, record, proof, wrong_parent=failure == "wrong_parent")
    with setup["worker"].connect() as conn:
        if failure == "missing_job":
            conn.execute("DELETE FROM jobs")
        elif failure == "missing_task":
            conn.execute("DELETE FROM remediation_tasks WHERE kind='detail'")
        elif failure == "missing_successful_attempt":
            conn.execute("DELETE FROM remediation_attempts")
        elif failure == "missing_blob":
            conn.execute("DELETE FROM attachment_blobs")
        elif failure == "bad_blob":
            conn.execute("UPDATE attachment_blobs SET content=x'010203'")
        elif failure == "missing_document_task":
            conn.execute("DELETE FROM remediation_tasks WHERE kind='document'")
        elif failure == "pending_document_task":
            conn.execute("UPDATE remediation_tasks SET status='pending' WHERE kind='document'")
    expected = plan(setup, setup["worker"].path)
    assert expected["rejected"]
    target, _ = snapshot(setup)
    assert plan(setup, target) == expected
    with sqlite3.connect(target.as_uri() + "?mode=ro", uri=True) as conn:
        assert conn.execute("SELECT count(*) FROM remediation_observations").fetchone()[0] == 1
        assert (
            conn.execute("SELECT content_sha256 FROM remediation_documents").fetchone()[0]
            == manifest["content_sha256"]
        )


def test_empty_observations_still_copy_listing_frames_and_original_unobserved_count(setup):
    add_frame(setup)
    setup["worker"].upsert_job(
        JobRecord(
            source_id="test_custom_html",
            org_id="Test",
            ats_family="custom_html",
            external_id="old",
            title="Historical baseline",
            apply_url="https://example.org/old",
        )
    )
    expected = plan(setup, setup["worker"].path)
    assert expected["listing_frames"] and expected["listing_only"] == 1
    target, _ = snapshot(setup)
    assert plan(setup, target) == expected


def test_sealed_projection_request_validates_and_is_independent_of_later_worker_state(setup):
    add_detail(setup)
    target, receipt = snapshot(setup)
    acceptance = setup["root"] / "acceptance.json"
    acceptance.write_text('{"accepted":true}')
    acceptance_sha = hashlib.sha256(acceptance.read_bytes()).hexdigest()
    # Rebuild with the actual acceptance binding.
    os.chmod(target, 0o644)
    target.unlink()
    lock = setup["root"] / "projection.lock"
    with lock.open("a+") as owner:
        fcntl.flock(owner, fcntl.LOCK_EX | fcntl.LOCK_NB)
        receipt = create_publication_snapshot(
            setup["worker"].path.resolve(),
            target.resolve(),
            acceptance_sha,
            owner.fileno(),
            lock.resolve(),
            projection=True,
        )
    request = {
        "worker_database": str(setup["worker"].path.resolve()),
        "worker_acceptance_path": str(acceptance.resolve()),
        "worker_acceptance_sha256": acceptance_sha,
        "publication_snapshot": receipt,
        "worker_database_files": [{"path": str(target.resolve()), "sha256": receipt["sha256"]}],
    }
    with setup["worker"].connect() as conn:
        conn.execute("DELETE FROM remediation_observations")
    checked = validate_publication_snapshot(request, setup["worker"].path.resolve())
    assert checked["validation_receipt"]["mode"] == "sealed_sqlite_projection"
    assert checked["effective_database"] == str(target.resolve())
    broken = deepcopy(request)
    broken["publication_snapshot"]["projection"]["manifest"]["filters"]["jobs"] = "all_rows"
    with pytest.raises(ValueError, match="projection"):
        validate_publication_snapshot(broken, setup["worker"].path.resolve())


def test_full_snapshot_compatibility_and_projection_deadline_cleanup(setup):
    add_detail(setup)
    target, receipt = snapshot(setup, projection=False, name="full.sqlite3")
    assert receipt["kind"] == "sqlite_backup" and "projection" not in receipt
    with sqlite3.connect(target.as_uri() + "?mode=ro", uri=True) as conn:
        assert projection_diagnostics(conn) is None
    with pytest.raises(TimeoutError):
        snapshot(setup, deadline_at=time.monotonic() - 1)
    assert not (setup["root"] / "projection.sqlite3").exists()
    assert not list(setup["root"].glob(".publication-backup-*"))
