"""Real files and SQLite exercise export crash boundaries and legacy recovery."""

import errno
import hashlib
import json
from pathlib import Path
import sqlite3

import pytest

from jobagg.db import JobDatabase
from jobagg.models import JobRecord
from jobagg.pipelines.exports import export_jobs
from jobagg.pipelines import publication_exports as publication


def _hash(data):
    return hashlib.sha256(data).hexdigest()


@pytest.fixture
def export_case(tmp_path):
    output = tmp_path / "live"
    output.mkdir()
    db = JobDatabase(output / "test_jobs.sqlite3")
    db.initialize()
    db.upsert_job(JobRecord(
        source_id="test", org_id="Test", ats_family="test", external_id="001",
        title="Officer", apply_url="https://example.org/001", description="Full public text",
        raw={"unicode": "東京", "nested": {"lines": "one\ntwo", "array": [1, 2]}},
    ))
    root = tmp_path / "state" / "generations" / "generation1"
    root.mkdir(parents=True)
    target = output / "test_jobs_current.json"
    old = b'[{"old":"existing export"}]'
    target.write_bytes(old)
    item = {
        "database": str(db.path), "path": str(target), "format": "json",
        "status": "open", "history_only": False,
    }
    plan = {"generation_id": "generation1", "generation_root": str(root),
            "created_at": "2026-09-15T00:00:00+00:00"}
    return plan, item, target, old


def _journal(plan):
    return json.loads((Path(plan["generation_root"]) / publication.JOURNAL_NAME).read_text())


@pytest.mark.parametrize("stage", [
    "before_export_prepared_seal", "after_export_prepare_checkpoint",
    "during_export_backup_copy", "before_export_backup_seal", "after_export_backup_seal",
    "after_export_backup_checkpoint", "after_export_replace_intent",
    "during_export_target_copy", "before_export_target_seal", "after_export_target_seal",
    "after_export_verified_checkpoint", "after_export_replace",
])
def test_each_crash_boundary_recovers_without_losing_original(export_case, monkeypatch, stage):
    plan, item, target, old = export_case

    def fail(point, key):
        if point == stage:
            raise RuntimeError("interrupted at " + stage)

    with pytest.raises(RuntimeError, match="interrupted"):
        publication.publish_exports(plan, [item], fault=fail)
    root = Path(plan["generation_root"])
    sealed_before = target.stat()
    if stage not in {"before_export_prepared_seal"}:
        monkeypatch.setattr(publication, "export_jobs", lambda *a, **kw: pytest.fail("rerender"))
    result = publication.publish_exports(plan, [item])
    assert result["complete"] is True
    assert json.loads(target.read_text())[0]["description"] == "Full public text"
    assert (root / "before_exports" / target.name).read_bytes() == old
    checkpoint = _journal(plan)["entries"][str(target)]
    assert checkpoint["phase"] == "replaced_verified"
    assert checkpoint["old"] == {"sha256": _hash(old), "size": len(old)}
    assert checkpoint["new"]["sha256"] == _hash(target.read_bytes())
    assert checkpoint["backup_status"] == "verified"
    if stage in {"after_export_target_seal", "after_export_verified_checkpoint", "after_export_replace"}:
        assert (target.stat().st_ino, target.stat().st_mtime_ns) == (
            sealed_before.st_ino, sealed_before.st_mtime_ns
        )


def test_enospc_in_backup_does_not_seal_partial_copy(export_case):
    plan, item, target, old = export_case

    def no_space(stage, key):
        if stage == "during_export_backup_copy":
            raise OSError(errno.ENOSPC, "simulated full disk")

    with pytest.raises(OSError) as raised:
        publication.publish_exports(plan, [item], fault=no_space)
    assert raised.value.errno == errno.ENOSPC
    assert target.read_bytes() == old
    backup = Path(plan["generation_root"]) / "before_exports" / target.name
    assert not backup.exists()
    assert publication.publish_exports(plan, [item])["complete"]
    assert backup.read_bytes() == old


@pytest.mark.parametrize("failed_phase", ["replaced_verified", "manifest"])
def test_failed_receipt_write_after_rename_recovers_without_replacement(
    export_case, monkeypatch, failed_phase
):
    plan, item, target, _ = export_case
    save = publication._save

    def fail_save(path, value):
        name = Path(path).name
        if (
            failed_phase == "manifest" and name == "exports.json"
            or failed_phase == "replaced_verified" and name == publication.JOURNAL_NAME
            and value["entries"][str(target)]["phase"] == "replaced_verified"
        ):
            raise OSError(errno.ENOSPC, "receipt write unavailable")
        save(path, value)

    monkeypatch.setattr(publication, "_save", fail_save)
    with pytest.raises(OSError):
        publication.publish_exports(plan, [item])
    before = target.stat()
    monkeypatch.setattr(publication, "_save", save)
    monkeypatch.setattr(publication, "export_jobs", lambda *a, **kw: pytest.fail("rerender"))
    assert publication.publish_exports(plan, [item])["complete"]
    assert (target.stat().st_ino, target.stat().st_mtime_ns) == (before.st_ino, before.st_mtime_ns)


def test_target_changed_during_temporary_copy_is_preserved(export_case):
    plan, item, target, old = export_case

    def external_write(stage, key):
        if stage == "before_export_target_seal":
            target.write_bytes(b"unrelated concurrent write")

    with pytest.raises(ValueError, match="target_changed_during_copy"):
        publication.publish_exports(plan, [item], fault=external_write)
    assert target.read_bytes() == b"unrelated concurrent write"
    assert (Path(plan["generation_root"]) / "before_exports" / target.name).read_bytes() == old


def test_partial_legacy_backup_is_retained_and_separate_verified_copy_created(export_case):
    plan, item, target, old = export_case
    backup = Path(plan["generation_root"]) / "before_exports" / target.name
    backup.parent.mkdir()
    backup.write_bytes(b"partial unknown evidence")
    result = publication.publish_exports(plan, [item])
    assert result["complete"]
    assert backup.read_bytes() == b"partial unknown evidence"
    checkpoint = _journal(plan)["entries"][str(target)]
    assert Path(checkpoint["backup_path"]).read_bytes() == old
    assert checkpoint["backup_path"] != str(backup)
    assert result["legacy_rollback_evidence"]


def _legacy_receipt(plan, item, target):
    root = Path(plan["generation_root"])
    prepared = root / "exports" / target.name
    export_jobs(JobDatabase(item["database"], read_only=True), output_path=prepared, status="open")
    target.write_bytes(prepared.read_bytes())
    receipt = {**item, "sha256": _hash(prepared.read_bytes()), "prepared": str(prepared)}
    manifest_path = root / "exports.json"
    manifest_path.write_text(json.dumps({str(target): receipt}))
    return prepared, manifest_path


def test_legacy_receipt_and_unknown_backup_are_preserved_exactly(export_case, monkeypatch):
    plan, item, target, _ = export_case
    _, manifest_path = _legacy_receipt(plan, item, target)
    backup = Path(plan["generation_root"]) / "before_exports" / target.name
    backup.parent.mkdir()
    backup.write_bytes(b"unverifiable original evidence")
    manifest_before = manifest_path.read_bytes()
    target_before = target.stat()
    monkeypatch.setattr(publication, "export_jobs", lambda *a, **kw: pytest.fail("legacy rerender"))
    result = publication.publish_exports(plan, [item])
    assert result["complete"]
    assert manifest_path.read_bytes() == manifest_before
    assert backup.read_bytes() == b"unverifiable original evidence"
    assert target.stat().st_mtime_ns == target_before.st_mtime_ns
    assert result["legacy_rollback_evidence"][0]["evidence"]["status"] == "unverified_legacy_backup"
    assert result["legacy_rollback_evidence"][0]["evidence"]["preimage_certified"] is False


def test_legacy_missing_target_restored_from_bound_prepared_bytes(export_case, monkeypatch):
    plan, item, target, _ = export_case
    prepared, manifest = _legacy_receipt(plan, item, target)
    original = manifest.read_bytes()
    target.unlink()
    monkeypatch.setattr(publication, "export_jobs", lambda *a, **kw: pytest.fail("legacy rerender"))
    assert publication.publish_exports(plan, [item])["complete"]
    assert target.read_bytes() == prepared.read_bytes()
    assert manifest.read_bytes() == original


@pytest.mark.parametrize("corrupt", ["prepared", "target"])
def test_legacy_unexpected_bytes_hold_publication(export_case, corrupt):
    plan, item, target, _ = export_case
    prepared, manifest = _legacy_receipt(plan, item, target)
    (prepared if corrupt == "prepared" else target).write_bytes(b"changed")
    before = manifest.read_bytes()
    with pytest.raises(ValueError, match="legacy_.*(changed|conflict)"):
        publication.publish_exports(plan, [item])
    assert manifest.read_bytes() == before


def test_historical_hash_match_does_not_invent_a_legacy_preimage_binding(export_case):
    plan, item, target, old = export_case
    _legacy_receipt(plan, item, target)
    root = Path(plan["generation_root"])
    backup = root / "before_exports" / target.name
    backup.parent.mkdir()
    backup.write_bytes(old)
    previous = root.parent / "earlier"
    previous.mkdir()
    (previous / "result.json").write_text(json.dumps({
        "state": "complete", "status": "published", "completed_at": "2026-09-14T00:00:00+00:00"
    }))
    (previous / "exports.json").write_text(json.dumps({str(target): {"sha256": _hash(old)}}))
    result = publication.publish_exports(plan, [item])
    evidence = result["legacy_rollback_evidence"][0]["evidence"]
    assert evidence["status"] == "matches_historical_export"
    assert evidence["preimage_certified"] is False


def test_unchanged_export_does_not_replace_or_backup_again(export_case):
    plan, item, target, _ = export_case
    export_jobs(JobDatabase(item["database"], read_only=True), output_path=target, status="open")
    before = target.stat()
    result = publication.publish_exports(plan, [item])
    assert result["complete"]
    assert (target.stat().st_ino, target.stat().st_mtime_ns) == (before.st_ino, before.st_mtime_ns)
    assert not (Path(plan["generation_root"]) / "before_exports").exists()


@pytest.mark.parametrize("which", ["prepared", "backup_path"])
def test_bound_evidence_corruption_blocks_retry(export_case, which):
    plan, item, target, old = export_case

    def fail(stage, key):
        if stage == "after_export_backup_checkpoint":
            raise RuntimeError("stop")

    with pytest.raises(RuntimeError):
        publication.publish_exports(plan, [item], fault=fail)
    checkpoint = _journal(plan)["entries"][str(target)]
    Path(checkpoint[which]).write_bytes(b"corrupt")
    with pytest.raises(ValueError, match="hash_changed"):
        publication.publish_exports(plan, [item])
    assert target.read_bytes() == old


def test_export_read_only_does_not_request_writer_pragmas(export_case, monkeypatch):
    plan, item, _, _ = export_case
    monkeypatch.setattr(JobDatabase, "_apply_pragmas", lambda *_: pytest.fail("writer pragmas"))
    assert publication.publish_exports(plan, [item])["complete"]


def test_readonly_database_rejects_writes_and_missing_file(export_case, tmp_path):
    _, item, _, _ = export_case
    db = JobDatabase(item["database"], read_only=True)
    with db.connection_scope() as conn:
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            conn.execute("DELETE FROM jobs")
    missing = tmp_path / "no-directory" / "absent.sqlite3"
    with pytest.raises(sqlite3.OperationalError):
        list(JobDatabase(missing, read_only=True).iter_jobs())
    assert not missing.parent.exists()


def test_readonly_export_includes_committed_wal_rows(export_case):
    _, item, target, _ = export_case
    db = JobDatabase(item["database"])
    with db.connection_scope() as writer:
        writer.execute("UPDATE jobs SET description='Committed WAL content'")
        writer.commit()
        assert Path(item["database"] + "-wal").stat().st_size > 0
        export_jobs(JobDatabase(item["database"], read_only=True), output_path=target)
        assert json.loads(target.read_text())[0]["description"] == "Committed WAL content"


def test_streaming_json_is_byte_identical_to_legacy_encoding(export_case):
    _, item, target, _ = export_case
    db = JobDatabase(item["database"], read_only=True)
    rows = list(db.iter_jobs_with_classification())
    export_jobs(db, output_path=target)
    assert target.read_text() == json.dumps(rows, indent=2, ensure_ascii=True)
    export_jobs(db, output_path=target, status="closed")
    assert target.read_bytes() == b"[]"


def test_json_starts_writing_before_fetching_next_row(tmp_path):
    target = tmp_path / "export.json"

    class Rows:
        def iter_jobs_with_classification(self, **kwargs):
            yield {"body": "x" * 100_000}
            assert target.stat().st_size > 0
            yield {"body": "next"}

    export_jobs(Rows(), output_path=target)
    assert len(json.loads(target.read_text())) == 2
