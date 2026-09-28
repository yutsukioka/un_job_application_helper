"""Publisher regressions use real production DB merge/classification and exports."""

import gzip
import hashlib
import json
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
import sqlite3

import pytest

from jobagg.db import JobDatabase
from jobagg.models import JobRecord
from jobagg.pipelines.live_publication import GATE_NAME, plan_publication, publish_incremental


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, default=lambda x: x.isoformat()))


def body(word):
    return (
        f"{word}. Responsibilities: "
        + "Coordinate the preparation of reports and work with international partners. " * 25
        + " Requirements: A university degree and five years of relevant experience."
    )


@pytest.fixture
def setup(tmp_path):
    output = tmp_path / "live"
    output.mkdir()
    registry = tmp_path / "registry.yaml"
    registry.write_text(
        "sources:\n- id: test_custom_html\n  name: Test\n  ats_family: custom_html\n  base_url: https://example.org/jobs\n  enabled: true\n- id: disabled\n  name: Disabled\n  ats_family: custom_html\n  base_url: https://example.org/disabled\n  enabled: false\n"
    )
    worker = JobDatabase(tmp_path / "worker.sqlite3")
    worker.initialize()
    with worker.connect() as conn:
        conn.executescript("""
        CREATE TABLE remediation_observations(job_key TEXT PRIMARY KEY, source_id TEXT, checked_at REAL, source_description_sha256 TEXT,database_description_sha256 TEXT,proof TEXT);
        CREATE TABLE remediation_documents(task_id TEXT PRIMARY KEY,job_key TEXT,source_id TEXT,url TEXT,content_sha256 TEXT,text_sha256 TEXT,manifest TEXT);
        CREATE TABLE remediation_tasks(task_id TEXT PRIMARY KEY,source_id TEXT,kind TEXT,external_id TEXT,status TEXT,receipt TEXT,payload TEXT);
        CREATE TABLE remediation_attempts(attempt_id TEXT PRIMARY KEY,task_id TEXT,source_id TEXT,kind TEXT,started_at REAL,finished_at REAL,status TEXT,evidence TEXT);
        """)
    old = JobRecord(
        source_id="test_custom_html",
        org_id="Test",
        ats_family="custom_html",
        external_id="001",
        title="Programme officer",
        apply_url="https://example.org/jobs/001",
        description=body("Old"),
        location="Nairobi",
        raw={
            "reviewed_override": {"keep": True},
            "_jobagg_main_text_verification": {"complete": False, "reason": "known gap"},
            "attachments": [],
        },
        first_seen_at=datetime(2020, 1, 1, tzinfo=UTC),
        last_seen_at=datetime(2020, 1, 1, tzinfo=UTC),
    )
    for name in ("test_jobs.sqlite3", "all_jobs.sqlite3"):
        db = JobDatabase(output / name)
        db.initialize()
        db.upsert_job(old)
    return {
        "root": tmp_path,
        "output": output,
        "registry": registry,
        "worker": worker,
        "state": tmp_path / "state",
        "old": old,
    }


def add_detail(setup, *, description=None, observed="2025-09-14T12:00:00+00:00", external_id="001"):
    root, worker = setup["root"], setup["worker"]
    record = JobRecord(
        source_id="test_custom_html",
        org_id="Test",
        ats_family="custom_html",
        external_id=external_id,
        title="Programme officer",
        apply_url="https://example.org/jobs/" + external_id,
        description=description or body("New"),
        raw={},
        first_seen_at=datetime.fromisoformat(observed),
        last_seen_at=datetime.fromisoformat(observed),
    )
    data = record.description.encode()
    digest = hashlib.sha256(data).hexdigest()
    capture = root / "capture" / external_id / "http.json"
    artifact = capture.with_suffix(".body.gz")
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_bytes(gzip.compress(data))
    meta = {
        "phase": {"kind": "detail", "job_id": external_id},
        "status_code": 200,
        "body_captured": True,
        "artifact": str(artifact),
        "body_sha256": digest,
        "body_bytes": len(data),
    }
    write(capture, meta)
    proof = {
        "source_id": record.source_id,
        "external_id": external_id,
        "observed_at": observed,
        "parsed_source_text_sha256": digest,
        "captures": [{"path": str(capture), "sha256": sha(capture)}],
        "completeness_certified": False,
    }
    detail_path = capture.parent / "detail.json"
    write(detail_path, {"job": asdict(record), "proof": proof})
    worker.upsert_job(record)
    with worker.connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO remediation_observations VALUES(?,?,?,?,?,?)",
            (
                record.identity_key(),
                record.source_id,
                datetime.fromisoformat(observed).timestamp(),
                digest,
                digest,
                json.dumps(proof),
            ),
        )
        conn.execute(
            "INSERT OR REPLACE INTO remediation_tasks VALUES(?,?,?,?,?,?,?)",
            (
                "detail-" + external_id,
                record.source_id,
                "detail",
                external_id,
                "done",
                json.dumps({"detail_path": str(detail_path), "detail_sha256": sha(detail_path)}),
                "{}",
            ),
        )
        conn.execute(
            "INSERT OR REPLACE INTO remediation_attempts VALUES(?,?,?,?,?,?,?,?)",
            (
                "accepted-" + external_id,
                "detail-" + external_id,
                record.source_id,
                "detail",
                datetime.fromisoformat(observed).timestamp(),
                datetime.fromisoformat(observed).timestamp(),
                "done",
                json.dumps({"detail_path": str(detail_path), "detail_sha256": sha(detail_path)}),
            ),
        )
    return record, proof


def run(setup, **kwargs):
    return publish_incremental(
        setup["worker"].path, setup["registry"], setup["output"], setup["state"], **kwargs
    )


def live(setup):
    return JobDatabase(setup["output"] / "all_jobs.sqlite3")


def test_listing_stub_never_erases_live(setup):
    stub = JobRecord(
        source_id="test_custom_html",
        org_id="Test",
        ats_family="custom_html",
        external_id="001",
        title="Programme officer",
        apply_url="https://example.org/jobs/001",
    )
    setup["worker"].upsert_job(stub)
    result = run(setup, execute=True)
    assert result["status"] == "no_changes"
    assert result["listing_only"] == 1
    assert live(setup).get_job("test:001")["description"] == body("Old")
    assert not setup["state"].exists()


def test_publish_preserves_unknown_fields_negative_proof_and_history(setup):
    record, _ = add_detail(setup)
    result = run(setup, execute=True)
    assert result["status"] == "published"
    assert result["exports"] == 8
    for path in (setup["output"] / "all_jobs.sqlite3", setup["output"] / "test_jobs.sqlite3"):
        row = JobDatabase(path).get_job("test:001")
        assert row["description"] == record.description
        assert row["location"] == "Nairobi"
        assert row["raw"]["reviewed_override"] == {"keep": True}
        assert not row["raw"]["_jobagg_main_text_verification"]["complete"]
        assert (
            row["raw"]["_jobagg_main_text_verification"]["previous_verification"]["reason"]
            == "known gap"
        )
        assert row["application_ready"] == 0
        with sqlite3.connect(path) as conn:
            assert conn.execute("SELECT count(*) FROM vacancy_snapshots").fetchone()[0] == 2
    exported = json.loads((setup["output"] / "all_jobs_current.json").read_text())
    assert exported[0]["description"] == record.description


def test_identical_observation_is_noop(setup):
    add_detail(setup)
    run(setup, execute=True)
    before = sha(setup["output"] / "all_jobs.sqlite3")
    result = run(setup, execute=True)
    assert result["status"] == "no_changes" and result["unchanged"] == 1
    assert sha(setup["output"] / "all_jobs.sqlite3") == before


def test_older_observation_rejected(setup):
    add_detail(setup, observed="2019-01-01T00:00:00+00:00")
    result = run(setup, execute=True)
    assert result["status"] == "no_changes"
    assert "older_than_live_observation" in result["rejected"][0]["reason"]
    assert live(setup).get_job("test:001")["description"] == body("Old")


def test_tampered_captured_body_rejected(setup):
    add_detail(setup)
    (setup["root"] / "capture" / "001" / "http.body.gz").write_bytes(
        gzip.compress(b"Wrong vacancy")
    )
    result = run(setup, execute=True)
    assert result["status"] == "no_changes"
    assert "captured_response_bytes_differ" in result["rejected"][0]["reason"]


def test_transaction_exception_rolls_back_and_recovery_finishes(setup):
    add_detail(setup)

    def fail(stage, path):
        if stage == "before_database_commit":
            raise RuntimeError("simulated process interruption")

    with pytest.raises(RuntimeError):
        run(setup, execute=True, fault=fail)
    assert live(setup).get_job("test:001")["description"] == body("Old")
    assert JobDatabase(setup["output"] / "test_jobs.sqlite3").get_job("test:001")[
        "description"
    ] == body("Old")
    assert json.loads((setup["output"] / GATE_NAME).read_text())["state"] != "complete"
    assert run(setup, execute=True)["status"] == "published"


def test_partial_database_commit_recovers_without_duplicate_history(setup):
    add_detail(setup)

    def fail(stage, path):
        if stage == "after_database_commit":
            raise RuntimeError("crashed after source transaction")

    with pytest.raises(RuntimeError):
        run(setup, execute=True, fault=fail)
    assert live(setup).get_job("test:001")["description"] == body("Old")
    result = run(setup, execute=True)
    assert result["status"] == "published"
    with sqlite3.connect(setup["output"] / "test_jobs.sqlite3") as conn:
        assert conn.execute("SELECT count(*) FROM vacancy_snapshots").fetchone()[0] == 2


def test_cross_source_alias_is_quarantined(setup):
    add_detail(setup)
    with live(setup).connect() as conn:
        conn.execute(
            "INSERT INTO consolidated_job_aliases VALUES(?,?,?,?,?,?,?,?)",
            (
                "test:001",
                "other:001",
                "test_custom_html",
                "other",
                "001",
                "https://example.org/jobs/001",
                "review",
                "2026-01-01",
            ),
        )
    plan = plan_publication(setup["worker"].path, setup["registry"], setup["output"])
    assert not plan["changes"]
    assert plan["rejected"][0]["reason"] == "cross_source_alias_requires_review"


def test_new_complete_parser_detail_inserts_without_completeness_claim(setup):
    add_detail(setup, external_id="002")
    result = run(setup, execute=True)
    assert result["status"] == "published"
    assert live(setup).get_job("test:002")["application_ready"] == 0
    assert not result["completeness_certified"]


def test_read_only_preview_does_not_write(setup):
    add_detail(setup)
    before = {str(path): sha(path) for path in setup["output"].glob("*.sqlite3")}
    result = run(setup)
    assert result["status"] == "preview" and len(result["plan"]["changes"]) == 1
    assert not setup["state"].exists()
    assert not (setup["output"] / GATE_NAME).exists()
    assert before == {str(path): sha(path) for path in setup["output"].glob("*.sqlite3")}


def add_document(setup, record, proof, *, wrong_parent=False):
    root = setup["root"]
    data = b"Terms of Reference\nThese are the duties for vacancy 001."
    digest = hashlib.sha256(data).hexdigest()
    text = data.decode()
    text_sha = hashlib.sha256(text.encode()).hexdigest()
    binary = root / "blobs" / digest
    binary.parent.mkdir(exist_ok=True)
    binary.write_bytes(data)
    capture = root / "document_capture" / "http.json"
    artifact = capture.with_suffix(".body.gz")
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_bytes(gzip.compress(data))
    write(
        capture,
        {
            "status_code": 200,
            "body_captured": True,
            "artifact": str(artifact),
            "body_sha256": digest,
            "body_bytes": len(data),
        },
    )
    url = "https://example.org/jobs/001/tor.txt"
    manifest = {
        "source_id": record.source_id,
        "job_key": record.identity_key(),
        "attachment_id": "doc1",
        "url": url,
        "final_url": url,
        "retrieved_at": "2025-09-14T12:01:00+00:00",
        "content_sha256": digest,
        "text_sha256": text_sha,
        "extracted_text": text,
        "parent_description_sha256": "wrong"
        if wrong_parent
        else proof["parsed_source_text_sha256"],
        "binary_path": str(binary),
        "capture_paths": [str(capture)],
        "media_type": "text/plain",
        "fidelity_complete": False,
    }
    path = capture.parent / "document.json"
    write(path, manifest)
    with setup["worker"].connect() as conn:
        conn.execute(
            "INSERT INTO attachment_blobs VALUES(?,?,?,?)", (digest, "text/plain", len(data), data)
        )
        conn.execute(
            "INSERT INTO remediation_documents VALUES(?,?,?,?,?,?,?)",
            (
                "doc1",
                record.identity_key(),
                record.source_id,
                url,
                digest,
                text_sha,
                json.dumps(manifest),
            ),
        )
        conn.execute(
            "INSERT INTO remediation_tasks VALUES(?,?,?,?,?,?,?)",
            (
                "doc1",
                record.source_id,
                "document",
                "doc1",
                "done",
                json.dumps({"manifest": str(path), "sha256": sha(path)}),
                json.dumps(
                    {
                        "url": url,
                        "job_key": record.identity_key(),
                        "parent_description_sha256": manifest["parent_description_sha256"],
                    }
                ),
            ),
        )
    return manifest, data


def test_document_original_bytes_and_association_published(setup):
    record, proof = add_detail(setup)
    document, data = add_document(setup, record, proof)
    result = run(setup, execute=True)
    assert result["status"] == "published"
    for path in (setup["output"] / "all_jobs.sqlite3", setup["output"] / "test_jobs.sqlite3"):
        with sqlite3.connect(path) as conn:
            assert (
                conn.execute(
                    "SELECT content FROM attachment_blobs WHERE content_sha256=?",
                    (document["content_sha256"],),
                ).fetchone()[0]
                == data
            )
            association = conn.execute(
                "SELECT job_key,url,status,extracted_text FROM job_attachments"
            ).fetchone()
            assert association == (
                "test:001",
                document["url"],
                "captured_unverified",
                data.decode(),
            )
    row = live(setup).get_job("test:001")
    assert not row["raw"]["attachments"][0]["fidelity_complete"]
    assert not row["raw"]["attachment_verification"]["complete"]


def test_wrong_parent_document_is_quarantined(setup):
    record, proof = add_detail(setup)
    add_document(setup, record, proof, wrong_parent=True)
    preview = run(setup)["plan"]
    assert not preview["changes"][0]["documents"]
    assert "document_identity_parent_or_hash_conflict" in preview["rejected"][0]["reason"]
    assert run(setup, execute=True)["status"] == "published"
    with live(setup).connect() as conn:
        assert conn.execute("SELECT count(*) FROM job_attachments").fetchone()[0] == 0


def test_retained_longer_full_body_is_not_replaced_by_parser_truncation(setup):
    add_detail(setup, description=body("New")[:1200])
    result = run(setup, execute=True)
    assert result["status"] == "no_changes"
    assert (
        result["rejected"][0]["reason"]
        == "shorter_than_retained_live_text_requires_full_source_contract"
    )
    assert live(setup).get_job("test:001")["description"] == body("Old")


def test_export_interruption_recovers_and_retains_prepared_bytes(setup):
    add_detail(setup)

    def fail(stage, path):
        if stage == "after_export_replace":
            raise RuntimeError("simulated export interruption")

    with pytest.raises(RuntimeError):
        run(setup, execute=True, fault=fail)
    state = json.loads((setup["output"] / GATE_NAME).read_text())
    assert state["state"] == "exporting"
    root = Path(state["plan_path"]).parent
    assert len(json.loads((root / "exports.json").read_text())) == 1
    assert run(setup, execute=True)["status"] == "published"
    assert len(json.loads((root / "exports.json").read_text())) == 8


def test_newer_concurrent_live_row_blocks_recovery(setup):
    add_detail(setup)

    def fail(stage, path):
        if stage == "after_database_commit":
            raise RuntimeError("simulated interruption")

    with pytest.raises(RuntimeError):
        run(setup, execute=True, fault=fail)
    with live(setup).connect() as conn:
        conn.execute("UPDATE jobs SET title='Reviewed concurrent title' WHERE job_key='test:001'")
    with pytest.raises(ValueError, match="live_row_preimage_changed_before_commit"):
        run(setup, execute=True)
    assert json.loads((setup["output"] / GATE_NAME).read_text())["state"] != "complete"


def add_frame(setup, *, external_ids=("001", "002"), observed="2025-09-14T12:00:00+00:00"):
    source = "test_custom_html"
    root = setup["root"] / "listing_capture"
    root.mkdir(exist_ok=True)
    jobs = [
        asdict(
            JobRecord(
                source_id=source,
                org_id="Test",
                ats_family="custom_html",
                external_id=key,
                title="Listing title " + key,
                apply_url="https://example.org/jobs/" + key,
                description=None,
            )
        )
        for key in external_ids
    ]
    path = root / "listing.json"
    write(path, {"source_id": source, "observed_at": observed, "jobs": jobs})
    http = root / "http" / "1.json"
    artifact = http.with_suffix(".body.gz")
    artifact.parent.mkdir(exist_ok=True)
    data = b"<html>Captured listing</html>"
    artifact.write_bytes(gzip.compress(data))
    write(
        http,
        {
            "status_code": 200,
            "body_captured": True,
            "artifact": str(artifact),
            "body_sha256": hashlib.sha256(data).hexdigest(),
            "body_bytes": len(data),
            "phase": {"kind": "listing"},
        },
    )
    proof = {
        "complete": False,
        "method": "unsupported",
        "capture_paths": [],
        "observed_count": len(jobs),
    }
    with setup["worker"].connect() as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS remediation_sources(source_id TEXT PRIMARY KEY,last_list_at REAL,listing_ids TEXT,listing_proof TEXT)"
        )
        conn.execute(
            "INSERT OR REPLACE INTO remediation_sources VALUES(?,?,?,?)",
            (
                source,
                datetime.fromisoformat(observed).timestamp(),
                json.dumps(["test:" + key for key in external_ids]),
                json.dumps(proof),
            ),
        )
        conn.execute(
            "INSERT OR REPLACE INTO remediation_tasks VALUES(?,?,?,?,?,?,?)",
            (
                "listing",
                source,
                "listing",
                "listing",
                "done",
                json.dumps(
                    {"frame_path": str(path), "frame_sha256": sha(path), "enumeration": proof}
                ),
                "{}",
            ),
        )
    return path


def test_listing_inventory_publishes_discoveries_without_erasing_rich_details(setup):
    add_frame(setup)
    result = run(setup, execute=True)
    assert result["status"] == "published"
    assert result["published_listing_frames"] == 1
    assert live(setup).get_job("test:001")["description"] == body("Old")
    assert live(setup).get_job("test:001")["title"] == "Programme officer"
    assert live(setup).get_job("test:002") is None
    with live(setup).connect() as conn:
        rows = conn.execute(
            "SELECT external_id,published_detail,observed_in_latest_listing FROM live_listing_inventory ORDER BY external_id"
        ).fetchall()
        assert [tuple(row) for row in rows] == [("001", 1, 1), ("002", 0, 1)]
    assert run(setup, execute=True)["status"] == "no_changes"


def test_listing_and_detail_same_generation_preserve_both(setup):
    record, _ = add_detail(setup)
    add_frame(setup)
    result = run(setup, execute=True)
    assert result["status"] == "published"
    assert live(setup).get_job("test:001")["description"] == record.description
    assert (
        live(setup).get_job("test:001")["raw"]["_jobagg_listing_verification"][
            "observed_in_latest_listing"
        ]
        is True
    )


def test_repaired_binary_is_retried_without_manifest_change(setup):
    record, proof = add_detail(setup)
    document, data = add_document(setup, record, proof)
    path = Path(document["binary_path"])
    path.write_bytes(b"corrupt")
    assert run(setup, execute=True)["status"] == "published"
    path.write_bytes(data)
    assert run(setup, execute=True)["status"] == "published"
    with live(setup).connect() as conn:
        assert conn.execute("SELECT count(*) FROM job_attachments").fetchone()[0] == 1


def test_corrected_extraction_same_binary_updates_table_and_raw(setup):
    record, proof = add_detail(setup)
    document, _ = add_document(setup, record, proof)
    assert run(setup, execute=True)["status"] == "published"
    document["extracted_text"] += "\nCorrected reading order."
    document["text_sha256"] = hashlib.sha256(document["extracted_text"].encode()).hexdigest()
    path = setup["root"] / "document_capture" / "document.json"
    write(path, document)
    with setup["worker"].connect() as conn:
        conn.execute(
            "UPDATE remediation_documents SET text_sha256=?,manifest=? WHERE task_id='doc1'",
            (document["text_sha256"], json.dumps(document)),
        )
        conn.execute(
            "UPDATE remediation_tasks SET receipt=? WHERE task_id='doc1'",
            (json.dumps({"manifest": str(path), "sha256": sha(path)}),),
        )
    assert run(setup, execute=True)["status"] == "published"
    with live(setup).connect() as conn:
        assert (
            conn.execute("SELECT extracted_text FROM job_attachments").fetchone()[0]
            == document["extracted_text"]
        )
    assert (
        live(setup).get_job("test:001")["raw"]["attachments"][0]["extracted_text"]
        == document["extracted_text"]
    )


def test_symlink_live_target_rejected(setup):
    path = setup["output"] / "test_jobs.sqlite3"
    actual = setup["root"] / "elsewhere.sqlite3"
    path.rename(actual)
    path.symlink_to(actual)
    with pytest.raises(ValueError, match="symlink_target"):
        run(setup, execute=True)


def test_hardlinked_live_target_rejected(setup):
    import os

    path = setup["output"] / "test_jobs.sqlite3"
    path.unlink()
    os.link(setup["output"] / "all_jobs.sqlite3", path)
    with pytest.raises(ValueError, match="share_inode"):
        run(setup, execute=True)


def test_deadline_after_preparation_leaves_live_gate_open(setup, monkeypatch):
    from jobagg.pipelines import live_publication as module

    add_detail(setup)
    original = module._prepare
    monkeypatch.setattr(module.time, "monotonic", lambda: 0)

    def expired(plan, state):
        result = original(plan, state)
        monkeypatch.setattr(module.time, "monotonic", lambda: 2)
        return result

    monkeypatch.setattr(module, "_prepare", expired)
    result = run(setup, execute=True, deadline_at=1)
    assert result["status"] == "deferred"
    assert result["observation_set_sha256"]
    assert not (setup["output"] / GATE_NAME).exists()
    assert live(setup).get_job("test:001")["description"] == body("Old")


def test_inherited_owner_fd_must_actually_own_lock(tmp_path):
    import fcntl
    import subprocess
    import sys
    from jobagg.publish_worker import _owner

    path = tmp_path / "owner.lock"
    with path.open("a+") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with _owner(path, handle.fileno()):
            pass
        fcntl.flock(handle, fcntl.LOCK_UN)
        script = "import fcntl,sys; f=open(sys.argv[1],'a+'); fcntl.flock(f,fcntl.LOCK_EX); print('locked',flush=True); sys.stdin.read(1)"
        child = subprocess.Popen(
            [sys.executable, "-c", script, str(path)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            text=True,
        )
        try:
            assert child.stdout.readline().strip() == "locked"
            with pytest.raises(BlockingIOError):
                with _owner(path, handle.fileno()):
                    pass
        finally:
            child.communicate("x", timeout=5)


def test_dispatcher_request_report_binds_actual_publication(setup):
    from jobagg.publish_worker import main

    add_detail(setup)
    acceptance = setup["root"] / "worker-acceptance.json"
    write(acceptance, {"status": "accepted"})
    lock = setup["root"] / "owner.lock"
    request = setup["root"] / "request.json"
    request_payload = {
        "schema_version": 1,
        "run_id": "test-run",
        "expected_source_ids": ["test_custom_html"],
        "registry_sha256": sha(setup["registry"]),
        "worker_acceptance_path": str(acceptance),
        "worker_acceptance_sha256": sha(acceptance),
        "worker_database": str(setup["worker"].path),
        "worker_database_files": [
            {"path": str(setup["worker"].path), "sha256": sha(setup["worker"].path)}
        ],
        "shared_lock_path": str(lock),
        "source_manifest_sha256": "test-manifest",
        "worker_status": "incomplete",
    }
    write(request, request_payload)
    report = setup["root"] / "report.json"
    args = [
        "--worker-database",
        str(setup["worker"].path),
        "--registry",
        str(setup["registry"]),
        "--output-dir",
        str(setup["output"]),
        "--state-dir",
        str(setup["state"]),
        "--shared-lock",
        str(lock),
        "--request",
        str(request),
        "--report",
        str(report),
        "--execute",
    ]
    assert main(args) == 0
    result = json.loads(report.read_text())
    assert result["status"] == "published"
    assert result["run_id"] == "test-run"
    assert result["publication_request_sha256"] == sha(request)
    assert result["observation_set_sha256"]
    assert not result["whole_job_completeness_certified"]


def test_wrong_worker_snapshot_hash_prevents_publication(setup):
    from argparse import Namespace
    from jobagg.publish_worker import _request

    receipt = setup["root"] / "accepted.json"
    write(receipt, {"accepted": True})
    request = setup["root"] / "request.json"
    write(
        request,
        {
            "schema_version": 1,
            "run_id": "run",
            "worker_database": str(setup["worker"].path),
            "shared_lock_path": str(setup["root"] / "lock"),
            "registry_sha256": sha(setup["registry"]),
            "worker_acceptance_path": str(receipt),
            "worker_acceptance_sha256": sha(receipt),
            "worker_database_files": [{"path": str(setup["worker"].path), "sha256": "wrong"}],
        },
    )
    args = Namespace(
        request=request,
        worker_database=setup["worker"].path,
        shared_lock=setup["root"] / "lock",
        registry=setup["registry"],
    )
    with pytest.raises(ValueError, match="binding changed"):
        _request(args)


def test_complete_workday_listing_records_absence_without_closure(setup):
    from jobagg.pipelines.inventory_checks import verify_listing
    from jobagg.pipelines.sync_source import load_sources

    setup["registry"].write_text(
        "sources:\n- id: test_custom_html\n  name: Test\n  ats_family: workday\n  base_url: https://example.org/jobs\n  enabled: true\n  extra:\n    cxs_base_url: https://example.org/cxs/test\n    page_size: 20\n"
    )
    path = add_frame(setup, external_ids=("002",))
    frame = json.loads(path.read_text())
    frame["jobs"][0]["raw"] = {"externalPath": "/job/002"}
    write(path, frame)
    data = json.dumps({"jobPostings": [{"externalPath": "/job/002"}], "total": 1}).encode()
    capture = path.parent / "http" / "1.json"
    artifact = capture.with_suffix(".body.gz")
    artifact.write_bytes(gzip.compress(data))
    request = {"appliedFacets": {}, "limit": 20, "offset": 0, "searchText": ""}
    write(
        capture,
        {
            "status_code": 200,
            "body_captured": True,
            "artifact": str(artifact),
            "body_sha256": hashlib.sha256(data).hexdigest(),
            "body_bytes": len(data),
            "phase": {"kind": "listing"},
            "url": "https://example.org/cxs/test/jobs",
            "response_url": "https://example.org/cxs/test/jobs",
            "method": "POST",
            "public_pagination_request": request,
            "request_body_sha256": hashlib.sha256(
                json.dumps(request, separators=(",", ":")).encode()
            ).hexdigest(),
        },
    )
    item = frame["jobs"][0]
    record = JobRecord(
        source_id=item["source_id"],
        org_id=item["org_id"],
        ats_family=item["ats_family"],
        external_id=item["external_id"],
        title=item["title"],
        apply_url=item["apply_url"],
        raw=item["raw"],
    )
    proof = verify_listing(load_sources(setup["registry"])[0], [record], [capture])
    assert proof["complete"]
    with setup["worker"].connect() as conn:
        conn.execute("UPDATE remediation_sources SET listing_proof=?", (json.dumps(proof),))
        conn.execute(
            "UPDATE remediation_tasks SET receipt=? WHERE kind='listing'",
            (
                json.dumps(
                    {"frame_path": str(path), "frame_sha256": sha(path), "enumeration": proof}
                ),
            ),
        )
    result = run(setup, execute=True)
    assert result["status"] == "published"
    existing = live(setup).get_job("test:001")
    assert existing["status"] == "open"
    assert existing["description"] == body("Old")
    assert existing["source_listed_current"] == 0
    assert existing["raw"]["_jobagg_listing_verification"]["observed_in_latest_listing"] is False
    with live(setup).connect() as conn:
        assert conn.execute("SELECT inventory_complete FROM live_listing_frames").fetchone()[0] == 1


def test_incomplete_frame_does_not_assert_absence(setup):
    add_frame(setup, external_ids=("001",))
    assert run(setup, execute=True)["status"] == "published"
    add_frame(setup, external_ids=("002",), observed="2025-09-14T15:00:00+00:00")
    assert run(setup, execute=True)["status"] == "published"
    with live(setup).connect() as conn:
        row = conn.execute(
            "SELECT observed_in_latest_listing FROM live_listing_inventory WHERE external_id='001'"
        ).fetchone()
        assert row[0] is None
    assert live(setup).get_job("test:001")["status"] == "open"
    assert live(setup).get_job("test:001")["description"] == body("Old")
