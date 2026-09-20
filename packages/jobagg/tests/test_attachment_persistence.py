import hashlib
import json
import sqlite3

from jobagg.db import JobDatabase
from jobagg.models import OrganizationSource, SourceRunDiagnostics, SyncResult
from jobagg.normalize import build_job
from jobagg.pipelines.consolidation import consolidate_bundle_databases


BODY = "Responsibilities include programme delivery and reporting. Qualifications include relevant education and extensive professional experience."


def make_job(source, key, raw, description=BODY):
    return build_job(
        source,
        title="Programme Officer",
        external_id=key,
        apply_url=f"https://example.org/{source.id}/{key}",
        closes_at="2099-12-31",
        description=description,
        raw=raw,
    )


def test_attachment_bytes_text_and_incomplete_gate_survive_consolidation(tmp_path):
    blob = b"%PDF-1.7 fixture binary"
    digest = hashlib.sha256(blob).hexdigest()
    for name, complete in [("one", True), ("two", False)]:
        source = OrganizationSource(name, name, "test", "https://example.org")
        db = JobDatabase(tmp_path / f"{name}_jobs.sqlite3")
        db.initialize()
        record = {
            "attachment_id": name + "-pdf",
            "extracted_text": "Full required terms of reference",
            "content_sha256": digest,
            "binary_ref": {"table": "attachment_blobs", "key": digest, "column": "content"},
        }
        job = make_job(
            source,
            "1",
            {
                "attachments": [record],
                "attachment_verification": {"complete": complete, "discovery_complete": True},
            },
        )
        db.upsert_job(job)
        db.add_source_run(
            SyncResult(
                source_id=name,
                fetched=1,
                diagnostics=SourceRunDiagnostics(
                    source_id=name,
                    health_status="ok",
                    run_classification="ok",
                    pagination_complete=True,
                ),
            )
        )
        with db.connect() as conn:
            conn.execute(
                "INSERT INTO attachment_blobs VALUES(?,?,?,?)",
                (digest, "application/pdf", len(blob), blob),
            )
            conn.execute(
                "INSERT INTO job_attachments VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    name + "-pdf",
                    job.identity_key(),
                    name,
                    "https://example.org/jd.pdf",
                    None,
                    "JD",
                    "job_description",
                    1,
                    "complete",
                    digest,
                    record["extracted_text"],
                    json.dumps(record),
                ),
            )
    result = consolidate_bundle_databases(output_dir=tmp_path)
    with sqlite3.connect(result.db_path) as conn:
        assert conn.execute("SELECT count(*) FROM attachment_blobs").fetchone()[0] == 1
        assert conn.execute("SELECT content FROM attachment_blobs").fetchone()[0] == blob
        assert conn.execute("SELECT count(*) FROM job_attachments").fetchone()[0] == 2
        assert not conn.execute("PRAGMA foreign_key_check").fetchall()
        assert dict(conn.execute("SELECT source_id,application_ready FROM jobs")) == {
            "one": 1,
            "two": 0,
        }
        raw = json.loads(
            conn.execute("SELECT raw_json FROM jobs WHERE source_id='one'").fetchone()[0]
        )
        assert raw["attachments"][0]["extracted_text"] == "Full required terms of reference"
    assert not result.application_ready_json_path.exists()


def test_refresh_keeps_document_evidence_and_invalidates_changed_job_text(tmp_path):
    source = OrganizationSource("one", "One", "test", "https://example.org")
    db = JobDatabase(tmp_path / "one_jobs.sqlite3")
    db.initialize()
    raw = {
        "attachments": [{"extracted_text": "Full terms"}],
        "attachment_verification": {
            "complete": True,
            "discovery_complete": True,
            "verified_at": "2026-09-10T00:00:00Z",
        },
    }
    job = make_job(source, "1", raw)
    db.upsert_job(job)
    db.upsert_job(make_job(source, "1", {}))
    assert db.get_job(job.identity_key())["raw"]["attachment_verification"]["complete"] is True
    db.upsert_job(
        make_job(
            source, "1", {}, description=BODY + " Additional new eligibility requirements apply."
        )
    )
    saved = db.get_job(job.identity_key())["raw"]
    assert saved["attachments"] == raw["attachments"]
    assert saved["attachment_verification"]["complete"] is False
    assert saved["attachment_verification"]["discovery_complete"] is False


def test_changed_document_link_invalidates_discovery_even_when_text_is_identical(tmp_path):
    source = OrganizationSource("one", "One", "test", "https://example.org")
    db = JobDatabase(tmp_path / "one_jobs.sqlite3")
    db.initialize()
    raw = {
        "detail_html": '<p>Full description</p><a href="old.pdf">Terms</a>',
        "attachment_verification": {"complete": True, "discovery_complete": True},
    }
    job = make_job(source, "1", raw)
    db.upsert_job(job)
    db.upsert_job(
        make_job(source, "1", {"detail_html": '<p>Full description</p><a href="new.pdf">Terms</a>'})
    )
    saved = db.get_job(job.identity_key())["raw"]["attachment_verification"]
    assert saved["complete"] is False
    assert saved["discovery_complete"] is False
