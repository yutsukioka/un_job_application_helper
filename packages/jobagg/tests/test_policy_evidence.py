import hashlib
import json
import sqlite3
import time

import pytest

from jobagg import policy_evidence, policy_migration
from jobagg.policy_authority import PolicyAuthority
from test_policy_authority import native  # noqa: F401

# ruff: noqa: F811


def evidence(native, tmp_path, *, payload=b"original history"):
    policy, owner = native
    original = tmp_path / "removed-original.json"
    backup = tmp_path / "backup"
    backup.mkdir()
    target = backup / "history.jsonl"
    target.write_bytes(payload)
    value = json.loads((policy.root / "bootstrap.json").read_text())
    value["evidence"] = [dict(path=str(original), sha256=hashlib.sha256(payload).hexdigest())]
    (policy.root / "bootstrap.json").write_text(json.dumps(value))
    marker = json.loads(policy.marker.read_text())
    marker["bootstrap_sha256"] = hashlib.sha256(
        (policy.root / "bootstrap.json").read_bytes()
    ).hexdigest()
    policy.marker.write_text(json.dumps(marker))
    return policy, owner, original, backup, target


def prepare(policy, owner, original, backup, target):
    return policy_evidence.prepare(
        policy,
        owner_fd=owner.fileno(),
        location_root=backup,
        locations={str(original): str(target)},
        check_deadline=lambda: None,
    )


def step(policy, owner):
    return policy_migration.step(
        policy,
        owner_fd=owner.fileno(),
        epoch="test",
        pauses={source: 120 for source in policy.source_ids},
        reviewed_writers=[dict(name="fixture all writers fenced", fenced=True)],
        limit=1,
    )


def finish(policy, owner):
    for _ in range(100):
        if step(policy, owner)["complete"]:
            return PolicyAuthority(policy)
    pytest.fail("unbounded migration")


def test_stopped_import_relocates_in_place_without_changing_original_proofs(native, tmp_path):
    policy, owner, original, backup, target = evidence(native, tmp_path)
    bootstrap = (policy.root / "bootstrap.json").read_bytes()
    marker = policy.marker.read_bytes()
    for _ in range(20):
        if step(policy, owner)["phase"] == "evidence":
            break
    else:
        pytest.fail("evidence phase absent")
    old_journal = (policy.root / "authority_migration.json").read_bytes()
    assert prepare(policy, owner, original, backup, target)["verified"] is False
    assert (
        policy.root / "authority_migration.before_evidence_locations.json"
    ).read_bytes() == old_journal
    authority = finish(policy, owner)
    authority.owner_fd = owner.fileno()
    assert not original.exists() and target.read_bytes() == b"original history"
    assert (
        policy.root / "bootstrap.json"
    ).read_bytes() == bootstrap and policy.marker.read_bytes() == marker
    assert (
        str(policy.root / policy_evidence.NAME)
        in json.loads(authority.head.read_text())["immutable_controls"]
    )
    authority.audit(limit=1, now=time.time(), maximum_lag_seconds=100)


def test_mapped_large_legacy_file_passes_bounded_import_and_ongoing_audit(native, tmp_path):
    payload = b"x" * (16 * 1024**2 + 1)
    policy, owner, original, backup, target = evidence(native, tmp_path, payload=payload)
    prepare(policy, owner, original, backup, target)
    authority = finish(policy, owner)
    authority.owner_fd = owner.fileno()
    assert authority.audit(limit=1, now=time.time(), maximum_lag_seconds=100) == 1
    target.write_bytes(b"y" * len(payload))
    with pytest.raises(ValueError, match="location_binding_changed"):
        authority.audit(limit=1, now=time.time(), maximum_lag_seconds=100)
    with sqlite3.connect(authority.path) as conn:
        assert conn.execute("SELECT count(*) FROM evidence").fetchone()[0] == 1


def test_wrong_payload_cannot_publish_authority_even_if_locator_is_admitted(native, tmp_path):
    policy, owner, original, backup, target = evidence(native, tmp_path)
    target.write_bytes(b"broken backup")
    prepare(policy, owner, original, backup, target)
    with pytest.raises(ValueError, match="original_evidence_changed"):
        finish(policy, owner)
    assert not (policy.root / "authority.json").exists()
    assert not original.exists()


def test_location_map_rejects_partial_inventory_alias_and_wrong_owner(native, tmp_path):
    policy, owner, original, backup, target = evidence(native, tmp_path)
    with pytest.raises(ValueError, match="complete_exact_mapping"):
        policy_evidence.prepare(
            policy,
            owner_fd=owner.fileno(),
            location_root=backup,
            locations={},
            check_deadline=lambda: None,
        )
    alias = backup / "alias"
    alias.symlink_to(target)
    with pytest.raises(ValueError, match="aliased"):
        prepare(policy, owner, original, backup, alias)
    other = tmp_path / "other-owner"
    other.touch()
    with other.open("r+") as fd, pytest.raises(ValueError, match="descriptor_differs"):
        policy_evidence.prepare(
            policy,
            owner_fd=fd.fileno(),
            location_root=backup,
            locations={str(original): str(target)},
            check_deadline=lambda: None,
        )


def test_interrupted_evidence_page_resumes_and_mapped_control_tampering_is_rejected(
    native, tmp_path
):
    policy, owner, original, backup, target = evidence(native, tmp_path)
    prepare(policy, owner, original, backup, target)
    for _ in range(20):
        if step(policy, owner)["phase"] == "evidence":
            break

    def interrupted():
        raise TimeoutError("finite test deadline")

    with pytest.raises(TimeoutError):
        policy_migration.step(
            policy,
            owner_fd=owner.fileno(),
            epoch="test",
            pauses={source: 120 for source in policy.source_ids},
            reviewed_writers=[dict(name="fixture all writers fenced", fenced=True)],
            limit=1,
            check_deadline=interrupted,
        )
    authority = finish(policy, owner)
    path = policy.root / policy_evidence.NAME
    path.chmod(0o644)
    with pytest.raises(ValueError, match="bootstrap_control_changed"):
        authority.revision()


def test_relocation_resets_only_evidence_cursor_and_cannot_skip_corrupt_target(native, tmp_path):
    policy, owner, original, backup, target = evidence(native, tmp_path)
    for _ in range(20):
        if step(policy, owner)["phase"] == "evidence":
            break
    staging = policy.root / "authority.import.sqlite3"
    with sqlite3.connect(staging) as conn:
        conn.execute("UPDATE import_audit SET after_path='zzzz' WHERE id=1")
    target.write_bytes(b"broken copy")
    prepare(policy, owner, original, backup, target)
    with sqlite3.connect(staging) as conn:
        assert conn.execute("SELECT after_path FROM import_audit").fetchone()[0] == ""
    with pytest.raises(ValueError, match="original_evidence_changed"):
        finish(policy, owner)
    assert not (policy.root / "authority.json").exists()


def test_digest_rejects_growth_during_read_at_declared_byte_bound(tmp_path, monkeypatch):
    path = tmp_path / "proof"
    path.write_bytes(b"proof")
    original_read = policy_evidence.os.read
    modified = False

    def grow(fd, size):
        nonlocal modified
        value = original_read(fd, size)
        if not modified:
            modified = True
            with path.open("ab") as stream:
                stream.write(b"extra")
        return value

    monkeypatch.setattr(policy_evidence.os, "read", grow)
    with pytest.raises(ValueError, match="size_bound"):
        policy_evidence.digest(path, lambda: None, 5)
