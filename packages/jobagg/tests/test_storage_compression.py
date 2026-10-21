from __future__ import annotations

import gzip
import hashlib
import json
import shutil
import sqlite3
import stat
from pathlib import Path

import pytest

from jobagg import storage_compression as storage


def _write_json(root: Path, name: str = "artifact.json", *, size: int = 8192) -> tuple[Path, bytes]:
    payload = json.dumps({"records": ["x" * 128] * (size // 128)}, sort_keys=True).encode("utf-8")
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    path.chmod(0o600)
    return path, payload


def _compress(
    root: Path,
    path: Path,
    *,
    retire: bool = True,
    retire_approval: bool = True,
    readers: bool = True,
    sealed_sqlite: bool = False,
) -> dict:
    return storage.compress_artifact(
        root,
        path,
        execute=True,
        shared_lock=_owner_path(root),
        codec="gzip",
        retire_legacy=retire,
        readers_support_manifest=readers,
        retirement_approved=retire_approval,
        sealed_sqlite=sealed_sqlite,
    )


def _owner_path(root: Path) -> Path:
    path = root / "shared-owner.lock"
    path.touch(exist_ok=True)
    return path


def _reconcile(root: Path, **kwargs) -> dict:
    return storage.reconcile_artifacts(root, shared_lock=_owner_path(root), **kwargs)


def _representation(root: Path, relative: str) -> dict:
    manifest = json.loads((root / ".jobagg-storage" / "representations.json").read_text())
    return manifest["artifacts"][relative]


def test_preview_is_read_only_and_round_trips(tmp_path: Path) -> None:
    path, payload = _write_json(tmp_path)
    before = {
        item.relative_to(tmp_path).as_posix(): item.read_bytes()
        for item in tmp_path.rglob("*")
        if item.is_file()
    }

    report = storage.preview_artifact(tmp_path, path)

    assert report["eligible"] is True
    assert report["verified"] is True
    assert report["blocked_reason"] is None
    assert report["logical_sha256"] == hashlib.sha256(payload).hexdigest()
    assert report["logical_bytes"] == len(payload)
    assert report["compressed_bytes"] < report["logical_bytes"]
    assert report["savings_bytes"] > 0
    assert not (tmp_path / ".jobagg-storage").exists()
    after = {
        item.relative_to(tmp_path).as_posix(): item.read_bytes()
        for item in tmp_path.rglob("*")
        if item.is_file()
    }
    assert after == before
    resolved = storage.resolve_artifact(tmp_path, path)
    assert resolved.data == payload
    with storage.stream_artifact(tmp_path, path) as stream:
        assert tuple(stream.attempts) == ("legacy_hot:selected",)
        assert b"".join(stream) == payload


def test_below_minimum_is_blocked_without_measurement(tmp_path: Path) -> None:
    path = tmp_path / "small.json"
    path.write_bytes(b'{"ok":true}')
    report = storage.preview_artifact(tmp_path, path)
    assert report["eligible"] is False
    assert report["blocked_reason"] == "below_min_bytes"
    assert report["compressed_bytes"] is None
    assert not (tmp_path / ".jobagg-storage").exists()


def test_no_measure_reports_ineligible_reason(tmp_path: Path) -> None:
    path, _ = _write_json(tmp_path)
    report = storage.preview_artifact(tmp_path, path, measure=False)
    assert report["eligible"] is True
    assert report["blocked_reason"] == "not_measured"
    assert report["verified"] is False
    assert not (tmp_path / ".jobagg-storage").exists()


def test_install_creates_owner_only_object_and_manifest(tmp_path: Path) -> None:
    path, payload = _write_json(tmp_path)
    report = _compress(tmp_path, path, retire=False)

    assert report["executed"] is True
    assert report["verified"] is True
    assert report["retired"] is False
    assert path.exists()
    object_path = tmp_path / report["physical_path"]
    assert object_path.is_file()
    assert stat.S_IMODE(object_path.stat().st_mode) == 0o600
    assert object_path.stat().st_nlink == 1
    store = tmp_path / ".jobagg-storage"
    assert stat.S_IMODE(store.stat().st_mode) == 0o700
    lock_file = store / "representations.json.lock"
    assert stat.S_IMODE(lock_file.stat().st_mode) == 0o600
    entry = _representation(tmp_path, "artifact.json")
    assert stat.S_IMODE((store / "representations.json").stat().st_mode) == 0o600
    assert stat.S_IMODE((store / "representations.journal.jsonl").stat().st_mode) == 0o600
    assert entry["logical_sha256"] == report["logical_sha256"]
    assert entry["legacy"]["retired"] is False
    assert any(
        item["physical_sha256"] == report["physical_sha256"]
        for item in entry["representations"]
    )
    assert storage.resolve_artifact(tmp_path, path).data == payload


def test_retirement_requires_both_explicit_approvals(tmp_path: Path) -> None:
    path, _ = _write_json(tmp_path)
    for readers, approved in ((False, False), (True, False), (False, True)):
        with pytest.raises(storage.ArtifactContractError, match="approval"):
            _compress(tmp_path, path, retire=True, readers=readers, retire_approval=approved)
        assert path.exists()
    assert not (tmp_path / ".jobagg-storage").exists()


def test_retire_removes_legacy_and_resolution_still_works(tmp_path: Path) -> None:
    path, payload = _write_json(tmp_path)
    report = _compress(tmp_path, path)

    assert report["retired"] is True
    assert report["removed_bytes"] == len(payload)
    assert not path.exists()
    entry = _representation(tmp_path, "artifact.json")
    assert entry["legacy"]["retired"] is True
    assert entry["legacy"]["retired_utc"]
    assert all(item["codec"] != "identity" for item in entry["representations"])
    with storage.stream_artifact(tmp_path, path) as stream:
        assert "gzip:selected" in stream.attempts
        assert b"".join(stream) == payload


def test_reconcile_is_a_noop_on_a_clean_store(tmp_path: Path) -> None:
    path, _ = _write_json(tmp_path)
    _compress(tmp_path, path, retire=False)
    summary = _reconcile(tmp_path)
    assert summary["open_intents"] == 0
    assert summary["processed_intents"] == 0
    assert summary["completed"] == 0
    assert summary["held"] == 0
    assert summary["abandoned"] == 0


def test_reconcile_removes_orphaned_temporary_files(tmp_path: Path) -> None:
    path, _ = _write_json(tmp_path)
    _compress(tmp_path, path, retire=False)
    temporary = tmp_path / ".jobagg-storage" / "tmp"
    orphan = temporary / "object-orphaned"
    orphan.write_bytes(b"x" * 4096)

    summary = _reconcile(tmp_path)

    assert not orphan.exists()
    assert summary["temporary_files_removed"] == 1
    assert summary["temporary_bytes_removed"] == 4096


def test_reconcile_completes_an_interrupted_install(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path, payload = _write_json(tmp_path)
    real_append = storage._append_journal

    def fail_complete(journal_path, record, *, deadline_at=None):
        if record.get("kind") == "complete":
            raise RuntimeError("simulated crash before completion")
        return real_append(journal_path, record, deadline_at=deadline_at)

    with monkeypatch.context() as patch:
        patch.setattr(storage, "_append_journal", fail_complete)
        with pytest.raises(RuntimeError, match="simulated crash"):
            _compress(tmp_path, path)

    assert not path.exists()
    summary = _reconcile(tmp_path)
    assert summary["open_intents"] == 1
    assert summary["completed"] == 1
    assert summary["objects"][0]["state"] == "completed"
    assert storage.resolve_artifact(tmp_path, path).data == payload
    assert _reconcile(tmp_path)["open_intents"] == 0


def test_reconcile_abandons_a_missing_object(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path, payload = _write_json(tmp_path)
    real_append = storage._append_journal

    def fail_complete(journal_path, record, *, deadline_at=None):
        if record.get("kind") == "complete":
            raise RuntimeError("simulated crash")
        return real_append(journal_path, record, deadline_at=deadline_at)

    with monkeypatch.context() as patch:
        patch.setattr(storage, "_append_journal", fail_complete)
        with pytest.raises(RuntimeError):
            _compress(tmp_path, path, retire=False)
    report = _representation(tmp_path, "artifact.json")["representations"][0]
    (tmp_path / report["physical_path"]).unlink()

    summary = _reconcile(tmp_path)
    assert summary["abandoned"] == 1
    assert summary["objects"][0]["state"] == "abandoned"
    assert path.exists()
    assert storage.resolve_artifact(tmp_path, path).data == payload


def test_reconcile_holds_when_legacy_changed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path, _ = _write_json(tmp_path)
    real_append = storage._append_journal

    def fail_complete(journal_path, record, *, deadline_at=None):
        if record.get("kind") == "complete":
            raise RuntimeError("simulated crash")
        return real_append(journal_path, record, deadline_at=deadline_at)

    with monkeypatch.context() as patch:
        patch.setattr(storage, "_append_journal", fail_complete)
        with pytest.raises(RuntimeError):
            _compress(tmp_path, path)
    path.write_bytes(b'{"changed":"identity"}')

    summary = _reconcile(tmp_path)
    assert summary["held"] == 1
    assert summary["objects"][0]["state"] == "held"
    journal = (tmp_path / ".jobagg-storage" / "representations.journal.jsonl").read_text()
    assert '"reason":"legacy_file_changed"' in journal


def test_reconcile_rejects_non_positive_max_objects(tmp_path: Path) -> None:
    with pytest.raises(storage.ArtifactContractError, match="positive"):
        _reconcile(tmp_path, max_objects=0)


def test_reconcile_rejects_a_malformed_intent(tmp_path: Path) -> None:
    store = tmp_path / ".jobagg-storage"
    store.mkdir(mode=0o700)
    record = {
        "format": storage.JOURNAL_FORMAT,
        "schema_version": storage.SCHEMA_VERSION,
        "kind": "intent",
        "intent_id": "broken",
        "logical_path": "artifact.json",
    }
    (store / storage.JOURNAL_NAME).write_text(json.dumps(record) + "\n")
    with pytest.raises(storage.ArtifactContractError, match="Journal"):
        _reconcile(tmp_path)


def test_corrupt_object_is_rejected(tmp_path: Path) -> None:
    path, _ = _write_json(tmp_path)
    report = _compress(tmp_path, path)
    object_path = tmp_path / report["physical_path"]
    original = object_path.read_bytes()
    object_path.write_bytes(original[:-1] + bytes([original[-1] ^ 0xFF]))
    with pytest.raises(storage.ArtifactIntegrityError):
        storage.resolve_artifact(tmp_path, path)


def test_missing_object_is_rejected(tmp_path: Path) -> None:
    path, _ = _write_json(tmp_path)
    report = _compress(tmp_path, path)
    (tmp_path / report["physical_path"]).unlink()
    with pytest.raises((storage.ArtifactMissingError, storage.ArtifactIntegrityError)):
        storage.resolve_artifact(tmp_path, path)


def test_changed_legacy_copy_conflicts_with_manifest(tmp_path: Path) -> None:
    path, _ = _write_json(tmp_path)
    _compress(tmp_path, path, retire=False)
    path.write_bytes(b'{"replaced":"with other evidence"}')
    with pytest.raises(storage.ArtifactIntegrityError, match="manifest binding"):
        storage.resolve_artifact(tmp_path, path)


@pytest.mark.parametrize("artifact_name", ["plan.json", "beforeimages/old.json"])
def test_resolves_an_existing_dispatcher_cold_archive(
    tmp_path: Path, artifact_name: str
) -> None:
    generation_id = "1" * 32
    generation = tmp_path / "generations" / generation_id
    generation.mkdir(parents=True)
    payload = b'{"cold":"exact evidence"}\n'
    hot = generation / artifact_name
    hot.parent.mkdir(parents=True, exist_ok=True)
    hot.write_bytes(payload)
    logical_sha = hashlib.sha256(payload).hexdigest()
    archive_root = tmp_path / "cold_archive"
    object_path = archive_root / "objects" / logical_sha[:2] / f"{logical_sha}.json.gz"
    object_path.parent.mkdir(parents=True)
    object_path.write_bytes(gzip.compress(payload, mtime=0))
    receipt = {
        "schema_version": storage.COLD_ARCHIVE_SCHEMA_VERSION,
        "generation_id": generation_id,
        "archive_root": str(archive_root),
        "objects": {
            artifact_name: {
                "sha256": logical_sha,
                "object_sha256": hashlib.sha256(object_path.read_bytes()).hexdigest(),
                "size_bytes": len(payload),
                "object_size_bytes": object_path.stat().st_size,
                "object_path": str(object_path),
            }
        },
    }
    (generation / storage.COLD_ARCHIVE_MANIFEST).write_text(json.dumps(receipt))
    hot.unlink()

    with storage.stream_artifact(tmp_path, hot) as stream:
        assert tuple(stream.attempts) == ("legacy_cold_archive:selected",)
        assert b"".join(stream) == payload


def test_missing_manifest_object_falls_back_to_cold_archive(tmp_path: Path) -> None:
    generation_id = "3" * 32
    generation = tmp_path / "generations" / generation_id
    generation.mkdir(parents=True)
    payload = b'{"cold":"manifest fallback","pad":"' + b"x" * 8192 + b'"}\n'
    hot = generation / "plan.json"
    hot.write_bytes(payload)
    logical_sha = hashlib.sha256(payload).hexdigest()
    archive_root = tmp_path / "cold_archive"
    object_path = archive_root / "objects" / logical_sha[:2] / f"{logical_sha}.json.gz"
    object_path.parent.mkdir(parents=True)
    object_path.write_bytes(gzip.compress(payload, mtime=0))
    receipt = {
        "schema_version": storage.COLD_ARCHIVE_SCHEMA_VERSION,
        "generation_id": generation_id,
        "archive_root": str(archive_root),
        "objects": {
            "plan.json": {
                "sha256": logical_sha,
                "object_sha256": hashlib.sha256(object_path.read_bytes()).hexdigest(),
                "size_bytes": len(payload),
                "object_size_bytes": object_path.stat().st_size,
                "object_path": str(object_path),
            }
        },
    }
    (generation / storage.COLD_ARCHIVE_MANIFEST).write_text(json.dumps(receipt))

    report = _compress(tmp_path, hot)
    assert not hot.exists()
    (tmp_path / report["physical_path"]).unlink()

    with storage.stream_artifact(tmp_path, hot) as stream:
        assert tuple(stream.attempts) == (
            "gzip:missing",
            "legacy_cold_archive:selected",
        )
        assert b"".join(stream) == payload


def test_cold_archive_object_corruption_is_rejected(tmp_path: Path) -> None:
    generation_id = "2" * 32
    generation = tmp_path / "generations" / generation_id
    generation.mkdir(parents=True)
    payload = b'{"cold":"exact evidence"}\n'
    logical_sha = hashlib.sha256(payload).hexdigest()
    archive_root = tmp_path / "cold_archive"
    object_path = archive_root / "objects" / logical_sha[:2] / f"{logical_sha}.json.gz"
    object_path.parent.mkdir(parents=True)
    compressed = bytearray(gzip.compress(payload, mtime=0))
    compressed[-1] ^= 0xFF
    object_path.write_bytes(compressed)
    receipt = {
        "schema_version": storage.COLD_ARCHIVE_SCHEMA_VERSION,
        "generation_id": generation_id,
        "archive_root": str(archive_root),
        "objects": {
            "plan.json": {
                "sha256": logical_sha,
                "object_sha256": hashlib.sha256(compressed).hexdigest(),
                "size_bytes": len(payload),
                "object_size_bytes": len(compressed),
                "object_path": str(object_path),
            }
        },
    }
    (generation / storage.COLD_ARCHIVE_MANIFEST).write_text(json.dumps(receipt))

    with pytest.raises((storage.ArtifactIntegrityError, storage.ArtifactContractError)):
        storage.resolve_artifact(tmp_path, generation / "plan.json")


def test_expected_identity_mismatch_is_rejected(tmp_path: Path) -> None:
    path, payload = _write_json(tmp_path)
    with pytest.raises(storage.ArtifactIntegrityError, match="differs"):
        storage.resolve_artifact(
            tmp_path,
            path,
            expected_sha256=hashlib.sha256(b"other").hexdigest(),
        )
    with pytest.raises(storage.ArtifactIntegrityError, match="length"):
        storage.resolve_artifact(tmp_path, path, expected_bytes=len(payload) + 1)
    assert storage.resolve_artifact(tmp_path, path).data == payload


def test_incomplete_stream_raises(tmp_path: Path) -> None:
    payload = b"x" * (storage._CHUNK + 1024)
    path = tmp_path / "large.json"
    path.write_bytes(payload)
    stream = storage.stream_artifact(tmp_path, path)
    with pytest.raises(storage.ArtifactReadIncompleteError):
        with stream:
            next(iter(stream))
            assert stream.complete is False


def test_iterating_to_the_end_marks_stream_complete(tmp_path: Path) -> None:
    path, payload = _write_json(tmp_path)
    collected = b""
    with storage.stream_artifact(tmp_path, path) as stream:
        for block in stream:
            collected += block
    assert stream.complete is True
    assert collected == payload


def test_compression_rejects_store_paths(tmp_path: Path) -> None:
    with pytest.raises(storage.ArtifactContractError, match="storage"):
        storage.preview_artifact(
            tmp_path, tmp_path / ".jobagg-storage" / "representations.json"
        )


def test_manifest_symlink_is_rejected(tmp_path: Path) -> None:
    path, _ = _write_json(tmp_path)
    store = tmp_path / ".jobagg-storage"
    store.mkdir(mode=0o700)
    outside = tmp_path.parent / "other-manifest.json"
    outside.write_text("{}")
    (store / "representations.json").symlink_to(outside)
    with pytest.raises(storage.ArtifactContractError):
        storage.preview_artifact(tmp_path, path)


def test_sealed_sqlite_preview_and_restore(tmp_path: Path) -> None:
    database = tmp_path / "evidence.sqlite3"
    connection = sqlite3.connect(database)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("CREATE TABLE evidence (id INTEGER PRIMARY KEY, body TEXT)")
    connection.executemany(
        "INSERT INTO evidence(body) VALUES (?)", [("x" * 512,)] * 100
    )
    connection.commit()
    connection.close()

    unsealed = storage.preview_artifact(tmp_path, database)
    assert unsealed["blocked_reason"] == "sqlite_requires_sealed_snapshot"
    preview = storage.preview_artifact(tmp_path, database, sealed_sqlite=True)
    assert preview["eligible"] is True
    assert preview["verified"] is True
    report = _compress(tmp_path, database, sealed_sqlite=True)
    assert report["retired"] is True
    assert not database.exists()

    scratch_parent = tmp_path / "scratch"
    scratch_parent.mkdir()
    snapshot = storage.restore_sqlite(tmp_path, database, scratch_parent=scratch_parent)
    scratch_root = snapshot.scratch_root
    try:
        assert snapshot.size > 0
        assert stat.S_IMODE(snapshot.scratch_root.stat().st_mode) == 0o700
        assert stat.S_IMODE(snapshot.path.stat().st_mode) == 0o400
        connection = snapshot.open_readonly()
        try:
            assert connection.execute("SELECT count(*) FROM evidence").fetchone()[0] == 100
            assert connection.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        finally:
            connection.close()
    finally:
        snapshot.cleanup()
    assert not scratch_root.exists()


def test_cli_restore_sqlite_verifies_and_cleans_scratch(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    database = tmp_path / "cli.sqlite3"
    connection = sqlite3.connect(database)
    connection.execute("CREATE TABLE evidence (id INTEGER PRIMARY KEY, body TEXT)")
    connection.execute("INSERT INTO evidence(body) VALUES (?)", ("x" * 512,))
    connection.commit()
    connection.close()
    _compress(tmp_path, database, sealed_sqlite=True)
    scratch_parent = tmp_path / "scratch"
    scratch_parent.mkdir()

    assert (
        storage.main(
            [
                "restore-sqlite",
                "--root",
                str(tmp_path),
                "--path",
                str(database),
                "--scratch-parent",
                str(scratch_parent),
            ]
        )
        == 0
    )
    report = json.loads(capsys.readouterr().out)
    assert report["verified"] is True
    assert report["scratch_retained"] is False
    assert report["path"] is None
    assert list(scratch_parent.iterdir()) == []

    assert (
        storage.main(
            [
                "restore-sqlite",
                "--root",
                str(tmp_path),
                "--path",
                str(database),
                "--scratch-parent",
                str(scratch_parent),
                "--keep",
            ]
        )
        == 0
    )
    kept = json.loads(capsys.readouterr().out)
    assert kept["scratch_retained"] is True
    kept_path = Path(kept["path"])
    assert kept_path.is_file()
    assert stat.S_IMODE(kept_path.stat().st_mode) == 0o400
    connection = sqlite3.connect(f"file:{kept_path.as_posix()}?mode=ro&immutable=1", uri=True)
    try:
        assert connection.execute("SELECT count(*) FROM evidence").fetchone()[0] == 1
    finally:
        connection.close()
    shutil.rmtree(kept["scratch_root"])


def test_sqlite_with_live_sidecars_rejects_sealed_preview(tmp_path: Path) -> None:
    database = tmp_path / "wal.sqlite3"
    connection = sqlite3.connect(database)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("CREATE TABLE evidence (id INTEGER PRIMARY KEY, body TEXT)")
    connection.execute("INSERT INTO evidence(body) VALUES (?)", ("x" * 128,))
    connection.commit()
    try:
        assert Path(f"{database}-wal").exists()
        with pytest.raises(storage.ArtifactContractError):
            storage.preview_artifact(tmp_path, database, sealed_sqlite=True)
    finally:
        connection.close()


def test_sqlite_with_rollback_journal_rejects_sealed_preview(tmp_path: Path) -> None:
    database = tmp_path / "rollback.sqlite3"
    database.write_bytes(b"SQLite format 3\x00" + b"x" * 4096)
    Path(f"{database}-journal").write_bytes(b"journal")
    with pytest.raises(storage.ArtifactContractError, match="sidecars"):
        storage.preview_artifact(tmp_path, database, sealed_sqlite=True)


def test_sqlite_restore_rejects_non_sqlite_path(tmp_path: Path) -> None:
    path, _ = _write_json(tmp_path)
    with pytest.raises(storage.ArtifactContractError, match="SQLite"):
        storage.restore_sqlite(tmp_path, path)


def test_copy_streams_verified_bytes(tmp_path: Path) -> None:
    path, payload = _write_json(tmp_path)
    _compress(tmp_path, path)
    destination = tmp_path / "restored.json"
    report = storage.copy_artifact(tmp_path, path, destination)
    assert destination.read_bytes() == payload
    assert report["size_bytes"] == len(payload)
    assert report["sha256"] == hashlib.sha256(payload).hexdigest()


def test_copy_does_not_publish_partial_file(tmp_path: Path) -> None:
    path, payload = _write_json(tmp_path)
    destination = tmp_path / "partial.json"
    with pytest.raises(storage.ArtifactTooLargeError):
        storage.copy_artifact(
            tmp_path, path, destination, max_output_bytes=len(payload) // 2
        )
    assert not destination.exists()


def test_gzip_cannot_carry_a_zstandard_dictionary(tmp_path: Path) -> None:
    path, _ = _write_json(tmp_path)
    with pytest.raises(storage.ArtifactContractError, match="Zstandard"):
        storage.preview_artifact(
            tmp_path,
            path,
            codec="gzip",
            dictionary_sha256=hashlib.sha256(b"dictionary").hexdigest(),
        )


def test_cli_rejects_identity_as_a_compression_codec(tmp_path: Path) -> None:
    path, _ = _write_json(tmp_path)
    with pytest.raises(SystemExit) as error:
        storage.main(
            [
                "preview",
                "--root",
                str(tmp_path),
                "--path",
                str(path),
                "--codec",
                "identity",
            ]
        )
    assert error.value.code == 2


def test_cli_codecs_preview_compress_reconcile_and_copy(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path, payload = _write_json(tmp_path)
    assert storage.main(["codecs"]) == 0
    codecs = json.loads(capsys.readouterr().out)
    assert "identity" in codecs["codecs"]
    assert "gzip" in codecs["codecs"]

    assert storage.main(["preview", "--root", str(tmp_path), "--path", str(path)]) == 0
    preview = json.loads(capsys.readouterr().out)
    assert preview["eligible"] is True
    assert preview["verified"] is True

    assert (
        storage.main(
            [
                "compress",
                "--root",
                str(tmp_path),
                "--path",
                str(path),
                "--execute",
                "--shared-lock",
                str(_owner_path(tmp_path)),
                "--retire-legacy",
                "--readers-support-manifest",
                "--retirement-approved",
            ]
        )
        == 0
    )
    compressed = json.loads(capsys.readouterr().out)
    assert compressed["executed"] is True
    assert compressed["retired"] is True

    assert storage.main(["reconcile", "--root", str(tmp_path), "--shared-lock", str(_owner_path(tmp_path))]) == 0
    reconciled = json.loads(capsys.readouterr().out)
    assert reconciled["open_intents"] == 0
    assert reconciled["processed_intents"] == 0

    destination = tmp_path / "cli-copy.json"
    assert (
        storage.main(
            [
                "copy",
                "--root",
                str(tmp_path),
                "--path",
                str(path),
                "--destination",
                str(destination),
            ]
        )
        == 0
    )
    assert destination.read_bytes() == payload


def test_cli_reports_structured_errors(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert (
        storage.main(
            ["preview", "--root", str(tmp_path), "--path", str(tmp_path / "missing.json")]
        )
        == 2
    )
    error = json.loads(capsys.readouterr().err)
    assert error["command"] == "preview"
    assert error["error"] == "ArtifactMissingError"



@pytest.mark.parametrize("operation", ["compress", "reconcile"])
def test_mutation_requires_shared_owner(tmp_path: Path, operation: str) -> None:
    path, payload = _write_json(tmp_path)
    with pytest.raises(storage.ArtifactContractError, match="shared owner"):
        if operation == "compress":
            storage.compress_artifact(tmp_path, path, execute=True, codec="gzip")
        else:
            storage.reconcile_artifacts(tmp_path)
    assert path.read_bytes() == payload
    assert not (tmp_path / storage.STORAGE_DIRECTORY).exists()


@pytest.mark.parametrize("operation", ["compress", "reconcile"])
def test_mutation_respects_busy_shared_owner(tmp_path: Path, operation: str) -> None:
    path, payload = _write_json(tmp_path)
    lock = _owner_path(tmp_path)
    with lock.open("r+b") as handle:
        storage._lock_nonblocking(handle)
        try:
            with pytest.raises(storage.ArtifactStorageError, match="already held"):
                if operation == "compress":
                    _compress(tmp_path, path)
                else:
                    _reconcile(tmp_path)
        finally:
            storage._unlock_store(handle)
    assert path.read_bytes() == payload
    assert not (tmp_path / storage.STORAGE_DIRECTORY).exists()


def test_reconcile_verifies_logical_bytes_before_retirement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path, payload = _write_json(tmp_path)
    real_append = storage._append_journal

    def interrupt(journal_path, record, *, deadline_at=None):
        if record.get("kind") == "installed":
            raise RuntimeError("interrupted before retirement")
        return real_append(journal_path, record, deadline_at=deadline_at)

    with monkeypatch.context() as patch:
        patch.setattr(storage, "_append_journal", interrupt)
        with pytest.raises(RuntimeError):
            _compress(tmp_path, path)
    journal = tmp_path / storage.STORAGE_DIRECTORY / storage.JOURNAL_NAME
    records = [json.loads(line) for line in journal.read_text().splitlines()]
    intent = records[0]
    # A valid compressed stream and matching physical hash can still bind the
    # wrong logical content. Recovery must not delete the correct original.
    wrong = gzip.compress(payload.replace(b"x", b"y"), mtime=0)
    (tmp_path / intent["physical_path"]).write_bytes(wrong)
    intent["physical_sha256"] = hashlib.sha256(wrong).hexdigest()
    intent["physical_bytes"] = len(wrong)
    journal.write_text("".join(json.dumps(record) + "\n" for record in records))

    with pytest.raises(storage.ArtifactIntegrityError, match="hash differs"):
        _reconcile(tmp_path)
    assert path.read_bytes() == payload
    assert _representation(tmp_path, "artifact.json")["legacy"]["retired"] is False


def test_manifest_object_parent_symlink_is_rejected(tmp_path: Path) -> None:
    path, _ = _write_json(tmp_path)
    report = _compress(tmp_path, path)
    objects = tmp_path / storage.STORAGE_DIRECTORY / storage.OBJECTS_DIRECTORY
    moved = tmp_path / "moved-objects"
    objects.rename(moved)
    objects.symlink_to(moved, target_is_directory=True)
    assert (tmp_path / report["physical_path"]).exists()
    with pytest.raises(storage.ArtifactContractError, match="Directory is linked"):
        storage.resolve_artifact(tmp_path, path)


@pytest.mark.parametrize("codec", ["gzip", "zstd"])
def test_explicit_codecs_round_trip_and_recover(
    tmp_path: Path, codec: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    if codec == "zstd" and not storage.zstd_available():
        pytest.skip("optional zstandard runtime is not installed")
    path, payload = _write_json(tmp_path)
    real_append = storage._append_journal

    def interrupt(journal_path, record, *, deadline_at=None):
        if record.get("kind") == "complete":
            raise RuntimeError("interrupted completion")
        return real_append(journal_path, record, deadline_at=deadline_at)

    with monkeypatch.context() as patch:
        patch.setattr(storage, "_append_journal", interrupt)
        with pytest.raises(RuntimeError):
            storage.compress_artifact(
                tmp_path, path, codec=codec, execute=True,
                shared_lock=_owner_path(tmp_path), retire_legacy=True,
                readers_support_manifest=True, retirement_approved=True,
            )
    assert not path.exists()
    assert _reconcile(tmp_path)["completed"] == 1
    restored = storage.resolve_artifact(tmp_path, path)
    assert restored.descriptor.codec == codec
    assert restored.data == payload
