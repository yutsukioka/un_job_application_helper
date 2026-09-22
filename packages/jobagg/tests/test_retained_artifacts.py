"""Retention preserves exact rollback bytes and cannot touch active generations."""

import gzip
import hashlib
import json
from pathlib import Path

import pytest

from jobagg import retained_artifacts as retention
from jobagg.pipelines import publication_exports as exports
import test_publication_exports as fixtures

export_case = fixtures.export_case


def complete_case(export_case):
    plan, item, target, old = export_case
    exports.publish_exports(plan, [item])
    root = Path(plan["generation_root"])
    plan_path = root / "plan.json"
    plan_path.write_text(json.dumps(plan))
    result = {
        "state": "complete",
        "status": "published",
        "completed_at": "2020-01-01T00:00:00+00:00",
        "generation_id": root.name,
        "plan_path": str(plan_path),
        "plan_sha256": hashlib.sha256(plan_path.read_bytes()).hexdigest(),
    }
    (root / "result.json").write_text(json.dumps(result))
    gate = target.parent / ".jobagg-publication-state.json"
    gate.write_text(json.dumps({**result, "generation_id": "newer-generation"}))
    lock = root.parent.parent / "owner.lock"
    lock.touch()
    return plan, item, target, old, root, gate, lock


def test_archive_roundtrip_recovery_and_dry_run_preserve_evidence(export_case):
    plan, item, target, old, root, gate, lock = complete_case(export_case)
    args = (root.parent.parent, target.parent, lock)
    preview = retention.retain(*args)
    assert preview["status"] == "dry_run" and len(preview["candidates"]) == 2
    original = {entry["path"]: Path(entry["path"]).read_bytes() for entry in preview["candidates"]}
    receipts = (root / "exports.json").read_bytes(), (root / "export-checkpoints.json").read_bytes()
    retention.retain(*args, execute=True)
    for path, expected in original.items():
        assert not Path(path).exists()
        with retention.open_artifact(path) as stream:
            assert stream.read() == expected
        assert exports._fingerprint(path) == {
            "sha256": hashlib.sha256(expected).hexdigest(),
            "size": len(expected),
        }
    assert receipts == (
        (root / "exports.json").read_bytes(),
        (root / "export-checkpoints.json").read_bytes(),
    )
    target.unlink()  # Exercise actual publisher recovery from retained prepared bytes.
    assert exports.publish_exports(plan, [item])["complete"]
    assert json.loads(target.read_text())[0]["description"] == "Full public text"
    assert retention.retain(*args, execute=True)["candidates"] == []


def test_current_unresolved_and_young_generations_are_not_retired(export_case):
    plan, item, target, old, root, gate, lock = complete_case(export_case)
    value = json.loads(gate.read_text())
    value["generation_id"] = root.name
    gate.write_text(json.dumps(value))
    assert retention.candidates(root.parent.parent, target.parent) == []
    value["state"] = "exporting"
    gate.write_text(json.dumps(value))
    with pytest.raises(ValueError, match="unresolved"):
        retention.candidates(root.parent.parent, target.parent)


def test_corrupt_archive_never_replaces_original_or_serves_wrong_bytes(export_case):
    plan, item, target, old, root, gate, lock = complete_case(export_case)
    candidate = retention.candidates(root.parent.parent, target.parent)[0]
    expected = {k: candidate[k] for k in ("sha256", "size")}
    store = root.parent.parent / "retained-blobs"
    store.mkdir()
    blob = store / (expected["sha256"] + ".gz")
    blob.write_bytes(gzip.compress(b"wrong"))
    with pytest.raises(ValueError, match="archive_object_changed"):
        retention.archive(candidate["path"], expected)
    assert Path(candidate["path"]).exists()
    blob.unlink()
    retention.archive(candidate["path"], expected)
    blob.write_bytes(gzip.compress(b"wrong"))
    with pytest.raises(ValueError, match="hash_changed"):
        exports._fingerprint(candidate["path"])


@pytest.mark.parametrize("execute", [False, True])
@pytest.mark.parametrize("corruption", ["truncated", "wrong_payload", "invalid_header", "invalid_deflate"])
def test_archived_corruption_aborts_scan_without_changing_evidence(export_case, execute, corruption):
    plan, item, target, old, root, gate, lock = complete_case(export_case)
    candidate, remaining = retention.candidates(root.parent.parent, target.parent)
    expected = {key: candidate[key] for key in ("sha256", "size")}
    retention.archive(candidate["path"], expected)
    blob = root.parent.parent / "retained-blobs" / (expected["sha256"] + ".gz")
    damaged = {
        "truncated": blob.read_bytes()[:-4],
        "wrong_payload": gzip.compress(b"wrong"),
        "invalid_header": b"not gzip",
        "invalid_deflate": b"\x1f\x8b\x08\x00" + b"\x00" * 6 + b"\x06" + b"\x00" * 8,
    }
    blob.write_bytes(damaged[corruption])
    evidence = {path: path.read_bytes() for path in root.rglob("*") if path.is_file()}
    with pytest.raises(ValueError, match="^retained_artifact_hash_changed$"):
        retention.retain(root.parent.parent, target.parent, lock, execute=execute)
    assert evidence == {path: path.read_bytes() for path in root.rglob("*") if path.is_file()}
    assert Path(remaining["path"]).exists()
    with pytest.raises(ValueError, match="^retained_artifact_hash_changed$"):
        with retention.open_artifact(candidate["path"]):
            pytest.fail("A corrupt archive must fail before yielding any bytes")


@pytest.mark.parametrize("execute", [False, True])
def test_scan_verifies_shared_blobs_once_beyond_original_limit(export_case, monkeypatch, execute):
    plan, item, target, old, root, gate, lock = complete_case(export_case)
    args = (root.parent.parent, target.parent, lock)
    retention.retain(*args, execute=True)
    journal_path = root / "export-checkpoints.json"
    receipts_path = root / "exports.json"
    journal = json.loads(journal_path.read_text())
    receipts = json.loads(receipts_path.read_text())
    key = next(iter(journal["entries"]))
    journal["entries"]["shared-reference"] = journal["entries"][key]
    receipts["shared-reference"] = receipts[key]
    journal_path.write_text(json.dumps(journal))
    receipts_path.write_text(json.dumps(receipts))
    evidence = {path: path.read_bytes() for path in root.rglob("*") if path.is_file()}
    hashed = []
    stream_hash = retention._stream_hash

    def track(stream):
        hashed.append(Path(stream.name))
        return stream_hash(stream)

    monkeypatch.setattr(retention, "_stream_hash", track)
    result = retention.retain(*args, max_files=1, execute=execute)
    assert result["candidates"] == []
    assert len(hashed) == len(set(hashed)) == 2
    assert evidence == {path: path.read_bytes() for path in root.rglob("*") if path.is_file()}


def test_symlinks_and_changed_generation_proof_fail_closed(export_case, tmp_path):
    plan, item, target, old, root, gate, lock = complete_case(export_case)
    with pytest.raises(ValueError, match="outside_historical"):
        retention.archive(target, exports._fingerprint(target))
    prepared = next((root / "exports-v2").iterdir())
    saved = prepared.read_bytes()
    prepared.unlink()
    outside = tmp_path / "outside"
    outside.write_bytes(saved)
    prepared.symlink_to(outside)
    with pytest.raises(ValueError, match="direct_regular"):
        retention.candidates(root.parent.parent, target.parent)


def test_interruption_before_unlink_keeps_original_and_is_resumable(export_case, monkeypatch):
    plan, item, target, old, root, gate, lock = complete_case(export_case)
    candidate = retention.candidates(root.parent.parent, target.parent)[0]
    expected = {k: candidate[k] for k in ("sha256", "size")}
    path = Path(candidate["path"])
    original = path.read_bytes()
    unlink = Path.unlink

    def fail(self, *a, **kw):
        if self == path:
            raise OSError("interrupted")
        return unlink(self, *a, **kw)

    monkeypatch.setattr(Path, "unlink", fail)
    with pytest.raises(OSError, match="interrupted"):
        retention.archive(path, expected)
    assert path.read_bytes() == original and retention.tombstone(path).exists()
    monkeypatch.setattr(Path, "unlink", unlink)
    retention.archive(path, expected)
    with retention.open_artifact(path) as stream:
        assert stream.read() == original
