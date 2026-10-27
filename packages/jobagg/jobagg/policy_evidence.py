"""Explicit owner-held relocation of immutable legacy evidence locators.

Original bootstrap bytes and expected hashes remain authoritative. Payloads are
read in place; no copy, symlink, hash waiver or worker-startup migration exists.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sqlite3

from jobagg.atomic_files import atomic_write_text
from jobagg.publication_fence import _owner, _file_identity

NAME = "evidence_locations.json"
SCHEMA = "jobagg-policy-evidence-locations-1"
MAX_FILES = 1024
MAX_FILE_BYTES = 512 * 1024**2
MAX_TOTAL_BYTES = 4 * 1024**3


def _dump(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _identity(path):
    path = Path(path)
    if not path.is_absolute() or ".." in path.parts or path.resolve() != path:
        raise ValueError("policy_evidence_location_aliased")
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode):
        raise ValueError("policy_evidence_location_nonregular")
    return [info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns]


def digest(path, check, maximum):
    before = _identity(path)
    if before[2] > maximum:
        raise ValueError("policy_evidence_location_size_bound")
    check()
    value = hashlib.sha256()
    consumed = 0
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        info = os.fstat(fd)
        if [info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns] != before:
            raise ValueError("policy_evidence_location_changed")
        while block := os.read(fd, 1024 * 1024):
            check()
            consumed += len(block)
            if consumed > maximum:
                raise ValueError("policy_evidence_location_size_bound")
            value.update(block)
        check()
        if _identity(path) != before:
            raise ValueError("policy_evidence_location_changed")
    finally:
        os.close(fd)
    return value.hexdigest()


class EvidenceLocations:
    def __init__(self, policy):
        self.path = policy.root / NAME
        self.rows = {}
        self.control = None
        if not self.path.exists():
            return
        self.control = _file_identity(self.path)
        if self.control["size"] > 1024**2:
            raise ValueError("policy_evidence_locations_oversized")
        with self.path.open("rb") as stream:
            payload = stream.read(1024**2 + 1)
        if len(payload) > 1024**2:
            raise ValueError("policy_evidence_locations_oversized")
        value = json.loads(payload)
        if (
            value.get("schema") != SCHEMA
            or not isinstance(value.get("files"), dict)
            or not 0 < len(value["files"]) <= MAX_FILES
        ):
            raise ValueError("policy_evidence_locations_invalid")
        root = Path(value["location_root"])
        if not root.is_absolute() or ".." in root.parts:
            raise ValueError("policy_evidence_locations_root_invalid")
        total = 0
        for original, row in value["files"].items():
            path, identity = Path(row["path"]), row["identity"]
            if (
                not Path(original).is_absolute()
                or ".." in Path(original).parts
                or not path.is_absolute()
                or ".." in path.parts
                or not path.is_relative_to(root)
                or not re.fullmatch("[a-f0-9]{64}", str(row["sha256"]))
                or not isinstance(identity, list)
                or len(identity) != 5
                or any(type(item) is not int for item in identity)
                or not 0 <= identity[2] <= MAX_FILE_BYTES
            ):
                raise ValueError("policy_evidence_locations_invalid_entry")
            total += identity[2]
        if total > MAX_TOTAL_BYTES:
            raise ValueError("policy_evidence_locations_total_bound")
        self.rows = value["files"]
        self.verify_control()

    def verify_control(self):
        if self.control is not None and _file_identity(self.path) != self.control:
            raise ValueError("policy_evidence_locations_changed")

    def sha(self, original, expected, fallback, check):
        row = self.rows.get(str(original))
        if row is None:
            return fallback(original, check)
        self.verify_control()
        if row["sha256"] != expected or _identity(row["path"]) != row["identity"]:
            raise ValueError("policy_evidence_location_binding_changed")
        actual = digest(row["path"], check, row["identity"][2])
        if _identity(row["path"]) != row["identity"]:
            raise ValueError("policy_evidence_location_binding_changed")
        self.verify_control()
        return actual


def prepare(policy, *, owner_fd, location_root, locations, check_deadline):
    """Bind exact locators to a stopped import; its evidence pages verify content.

    Existing authority heads are never upgraded implicitly. An interrupted
    manifest/journal handoff is resumable with the identical admitted mapping.
    No payload is read here and no verification result is claimed. Native import
    must still compare every original hash before publishing an authority head.
    """
    _owner(owner_fd, policy.owner)
    if policy.root.resolve(strict=True) != policy.root:
        raise ValueError("policy_evidence_policy_root_aliased")
    if (policy.root / "authority.json").exists():
        raise ValueError("policy_evidence_relocation_requires_pre_authority_boundary")
    bootstrap = policy.root / "bootstrap.json"
    bootstrap_sha = digest(bootstrap, check_deadline, 16 * 1024**2)
    marker = json.loads(policy.marker.read_text())
    if marker.get("bootstrap_sha256") != bootstrap_sha or marker.get("shared_lock") != str(
        policy.owner
    ):
        raise ValueError("policy_evidence_original_bootstrap_binding_changed")
    evidence = json.loads(bootstrap.read_text()).get("evidence", [])
    originals = {item["path"]: item["sha256"] for item in evidence}
    root = Path(location_root)
    if (
        not root.is_absolute()
        or root.resolve(strict=True) != root
        or not root.is_dir()
        or not originals
        or len(originals) != len(evidence)
        or len(originals) > MAX_FILES
        or set(locations) != set(originals)
    ):
        raise ValueError("policy_evidence_complete_exact_mapping_required")
    rows, total = {}, 0
    for original, expected in originals.items():
        check_deadline()
        target = Path(locations[original])
        if not target.is_relative_to(root):
            raise ValueError("policy_evidence_location_outside_reviewed_root")
        before = _identity(target)
        total += before[2]
        if total > MAX_TOTAL_BYTES:
            raise ValueError("policy_evidence_locations_total_bound")
        if before[2] > MAX_FILE_BYTES:
            raise ValueError("policy_evidence_location_size_bound")
        rows[original] = dict(path=str(target), sha256=expected, identity=before)
    value = dict(schema=SCHEMA, bootstrap_sha256=bootstrap_sha, location_root=str(root), files=rows)
    payload = _dump(value) + "\n"
    journal = policy.root / "authority_migration.json"
    checkpoint = None
    if journal.exists():
        original_journal = journal.read_text()
        checkpoint = json.loads(original_journal)
        if checkpoint["phase"] != "import":
            raise ValueError("policy_evidence_relocation_requires_stopped_import")
    path = policy.root / NAME
    if path.exists():
        if path.is_symlink() or path.read_text() != payload:
            raise ValueError("policy_evidence_locations_existing_mapping_differs")
    else:
        atomic_write_text(path, payload)
    if path.stat().st_mode & 0o222:
        path.chmod(path.stat().st_mode & ~0o222)
    seal = digest(path, check_deadline, 1024**2)
    if checkpoint is not None:
        binding = checkpoint["contract"].get("evidence_locations_sha256")
        if binding is not None and binding != seal:
            raise ValueError("policy_evidence_relocation_contract_changed")
        if binding is None:
            archive = policy.root / "authority_migration.before_evidence_locations.json"
            if archive.exists() and archive.read_text() != original_journal:
                raise ValueError("policy_evidence_relocation_original_journal_changed")
            if not archive.exists():
                with archive.open("x") as stream:
                    stream.write(original_journal)
                    stream.flush()
                    os.fsync(stream.fileno())
                archive.chmod(0o444)
            # A previously verified old locator cannot certify its new target.
            # Reset only the explicit import evidence cursor, preserving charges,
            # payload/replay progress and the original journal. Crash before the
            # journal update repeats this harmless reset on the identical map.
            staging = policy.root / "authority.import.sqlite3"
            if staging.exists():
                _identity(staging)
                conn = sqlite3.connect(staging, timeout=1)
                try:
                    phase = conn.execute("SELECT phase FROM import_state WHERE id=1").fetchone()
                    if phase is None or phase[0] == "complete":
                        raise ValueError("policy_evidence_relocation_requires_stopped_import")
                    conn.create_function("jobagg_policy_write_allowed", 0, lambda: 1)
                    check_deadline()
                    with conn:
                        conn.execute("UPDATE import_audit SET after_path='' WHERE id=1")
                finally:
                    conn.close()
            checkpoint["contract"]["evidence_locations_sha256"] = seal
            checkpoint["evidence_location_handoff"] = dict(
                original_journal_sha256=hashlib.sha256(original_journal.encode()).hexdigest(),
                locations_sha256=seal,
            )
            atomic_write_text(journal, _dump(checkpoint) + "\n")
    return dict(
        schema=SCHEMA,
        references=len(rows),
        bytes=total,
        sha256=seal,
        verified=False,
        verification="checkpointed_native_import_required",
    )
