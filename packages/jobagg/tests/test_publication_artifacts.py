"""Bounded real-file fixtures: exact records, recovery and content reuse."""
import hashlib
import json
from pathlib import Path

import pytest
from test_publication_exports import export_case as _export_case

from jobagg import publication_artifacts as artifacts
from jobagg.pipelines import publication_exports as publication
from jobagg.pipelines.exports import export_jobs
from jobagg.retained_artifacts import open_artifact


@pytest.fixture
def export_case(tmp_path):
    return _export_case.__wrapped__(tmp_path)


def policy(plan):
    plan["publication_policy"] = {"schema_version": 1, "policy_epoch": "fixture", "export_storage": "record_chunks_v1"}
    return plan


def logical(tmp_path, generation="g1", name="export.json"):
    return tmp_path / "state" / "generations" / generation / "exports-v2" / name


@pytest.mark.parametrize("output_format", ["json", "csv"])
def test_record_encoding_matches_legacy_exactly_and_only_changed_record_grows(tmp_path, output_format):
    rows = [{"external_id": str(n), "title": "Text" + "a" * 10000, "raw": {"multiline": "α\nβ"}} for n in range(40)]

    class Database:
        def iter_jobs_with_classification(self, **kwargs):
            return iter(rows)

    def render(generation):
        path = logical(tmp_path, generation)
        writer = artifacts.RecordWriter(path, translate_newlines=output_format == "json")
        export_jobs(Database(), output_path=path, output_format=output_format, output_stream=writer)
        expected = writer.seal()
        full = tmp_path / (generation + ".full")
        export_jobs(Database(), output_path=full, output_format=output_format)
        with open_artifact(path) as stream:
            assert stream.read() == full.read_bytes()
        assert not path.exists()
        assert artifacts.fingerprint(path) == expected
        return path, set(artifacts.referenced_objects(path))

    first, initial = render("g1")
    _, repeated = render("g2")
    assert repeated == initial
    rows[20]["title"] = "Replacement" + "b" * 10000
    third, changed = render("g3")
    assert len(changed - initial) == 1
    assert len(initial - changed) == 1
    assert sum(path.stat().st_size for path in changed - initial) < 15000
    assert artifacts.fingerprint(first) != artifacts.fingerprint(third)


@pytest.mark.parametrize("stage", [
    "before_export_prepared_seal", "after_export_prepare_checkpoint",
    "during_export_backup_copy", "before_export_backup_seal", "after_export_backup_seal",
    "after_export_backup_checkpoint", "after_export_replace_intent",
    "during_export_target_copy", "before_export_target_seal", "after_export_target_seal",
    "after_export_verified_checkpoint", "after_export_replace",
])
def test_chunk_publication_recovers_each_boundary(export_case, stage):
    plan, item, target, old = export_case
    policy(plan)

    def fail(point, key):
        if point == stage:
            raise RuntimeError("fixture interruption")

    with pytest.raises(RuntimeError, match="fixture interruption"):
        publication.publish_exports(plan, [item], fault=fail)
    assert publication.publish_exports(plan, [item])["complete"]
    root = Path(plan["generation_root"])
    backup = root / "before_exports" / target.name
    with open_artifact(backup) as stream:
        assert stream.read() == old
    prepared = root / "exports-v2" / target.name
    with open_artifact(prepared) as stream:
        assert stream.read() == target.read_bytes()
    assert not prepared.exists() and not backup.exists()
    assert json.loads(target.read_text())[0]["description"] == "Full public text"


def test_old_target_reuses_previous_record_chunks_without_duplicate_full_backup(export_case):
    plan, item, target, _ = export_case
    policy(plan)
    publication.publish_exports(plan, [item])
    first = Path(plan["generation_root"]) / "exports-v2" / target.name
    old = publication._fingerprint(target)
    newroot = Path(plan["generation_root"]).parent / "generation2"
    newroot.mkdir()
    checkpoint = {"old": old, "new": {"sha256": "0" * 64, "size": 0}, "item": item, "old_state_known": True, "content_addressed": True}
    store = artifacts._store(first)
    before = set(store.iterdir())
    publication._backup(checkpoint, newroot)
    assert set(store.iterdir()) == before
    assert artifacts.referenced_objects(first) == artifacts.referenced_objects(checkpoint["backup_path"])
    assert artifacts.artifact_inventory(checkpoint["backup_path"], old["sha256"], old["size"])[0]["path"].endswith(".publication.json")


@pytest.mark.parametrize("corrupt", ["chunk", "manifest_hash", "manifest_size", "escape", "symlink"])
def test_corrupt_evidence_never_replaces_live_target(export_case, corrupt):
    plan, item, target, old = export_case
    policy(plan)
    def stop(stage, key):
        if stage == "after_export_prepare_checkpoint":
            raise RuntimeError("pause")
    with pytest.raises(RuntimeError):
        publication.publish_exports(plan, [item], fault=stop)
    prepared = Path(plan["generation_root"]) / "exports-v2" / target.name
    sidecar = artifacts.sidecar(prepared)
    value = json.loads(sidecar.read_text())
    chunk = artifacts.referenced_objects(prepared)[0]
    if corrupt == "chunk":
        chunk.write_bytes(b"corruption")
    elif corrupt == "manifest_hash":
        value["sha256"] = "0" * 64
    elif corrupt == "manifest_size":
        value["size"] += 1
    elif corrupt == "escape":
        value["chunks"][0]["sha256"] = "../../external"
    else:
        external = target.parent / "outside"
        external.write_bytes(chunk.read_bytes())
        chunk.unlink()
        chunk.symlink_to(external)
    sidecar.write_text(json.dumps(value))
    with pytest.raises(ValueError):
        publication.publish_exports(plan, [item])
    assert target.read_bytes() == old


def test_chunk_capacity_is_reserved_before_write(tmp_path):
    path = logical(tmp_path)
    def refuse(path, size):
        raise ValueError("capacity denied")
    with pytest.raises(ValueError, match="capacity denied"):
        artifacts.RecordWriter(path, consume=refuse)
    assert not artifacts._store(path).exists()
    assert not artifacts.sidecar(path).exists()


@pytest.mark.parametrize("deny_at", [1, 2])
def test_reused_capture_charges_each_missing_parent_before_mkdir(tmp_path, deny_at):
    previous = logical(tmp_path)
    writer = artifacts.RecordWriter(previous)
    writer.write('[{"id":"retained"}]')
    expected = writer.seal()
    target = previous.parent.parent.parent / "g2" / "before_exports" / "jobs.json"
    missing = [target.parent.parent, target.parent]
    charges = []

    def refuse(path, size):
        assert not path.exists(), "allocation happened before its charge"
        charges.append((path, size))
        if len(charges) == deny_at:
            raise ValueError("capacity denied")

    with pytest.raises(ValueError, match="capacity denied"):
        artifacts.capture(tmp_path / "absent-original", target, expected,
                          candidates=[previous], consume=refuse)
    assert charges == [(path, 4096) for path in missing[:deny_at]]
    assert all(path.exists() for path in missing[:deny_at - 1])
    assert all(not path.exists() for path in missing[deny_at - 1:])
    assert not artifacts.sidecar(target).exists()
    assert artifacts.fingerprint(previous) == expected


def test_reused_capture_charges_parents_and_manifest_without_new_objects(tmp_path):
    previous = logical(tmp_path)
    writer = artifacts.RecordWriter(previous)
    writer.write('[{"id":"retained"}]')
    expected = writer.seal()
    target = previous.parent.parent.parent / "g2" / "before_exports" / "jobs.json"
    objects = set(artifacts._store(previous).iterdir())
    charges = []

    def consume(path, size):
        assert not path.exists(), "allocation happened before its charge"
        charges.append((path, size))

    artifacts.capture(tmp_path / "absent-original", target, expected,
                      candidates=[previous], consume=consume)
    assert charges[:2] == [(target.parent.parent, 4096), (target.parent, 4096)]
    manifest = artifacts.sidecar(target)
    assert charges[2:] == [(manifest, manifest.stat().st_size + 4096)]
    assert set(artifacts._store(previous).iterdir()) == objects
    assert artifacts.fingerprint(target) == expected


def test_large_record_is_bounded_and_empty_export_roundtrips(tmp_path):
    path = logical(tmp_path)
    writer = artifacts.RecordWriter(path)
    data = b"z" * (artifacts.CHUNK_BYTES * 2 + 7)
    writer.write_bytes(data)
    expected = writer.seal()
    assert expected == {"sha256": hashlib.sha256(data).hexdigest(), "size": len(data)}
    receipt = json.loads(artifacts.sidecar(path).read_text())
    assert [chunk["size"] for chunk in receipt["chunks"]] == [artifacts.CHUNK_BYTES, artifacts.CHUNK_BYTES, 7]
    with open_artifact(path) as stream:
        assert stream.read() == data


def test_deadline_stops_chunk_readback_before_opening_objects(tmp_path, monkeypatch):
    import time
    path = logical(tmp_path)
    writer = artifacts.RecordWriter(path)
    writer.write("[]")
    expected = writer.seal()
    monkeypatch.setattr(artifacts, "_chunk", lambda *args: pytest.fail("read after deadline"))
    deadline = time.monotonic() - 1
    with pytest.raises(TimeoutError, match="deadline"):
        artifacts.fingerprint(path, deadline_at=deadline)
    with pytest.raises(TimeoutError, match="deadline"):
        artifacts.artifact_inventory(path, expected["sha256"], expected["size"], deadline_at=deadline)
    with pytest.raises(TimeoutError, match="deadline"):
        artifacts.referenced_objects(path, deadline_at=deadline)
