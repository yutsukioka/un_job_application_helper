"""Explicit verified representations for historical evidence; never a startup scan.

A grant pins a small immutable snapshot of the existing representation manifest.
Producer paths/hashes and the original physical identity stay as provenance.
Current captures, live databases and current recovery inputs do not use this API.
"""

from contextlib import contextmanager
import hashlib
import io
import json
import math
import os
from pathlib import Path
import re
import stat
import time

from jobagg import storage_compression as storage

SCHEMA = "historical-representation-grant-1"
PREFIX = "historical-representation/"
MAX_CONTROL = 16 * 1024**2
MAX_ARTIFACT = 1024**3
KINDS = {"plan", "beforeimage", "export", "snapshot"}


def check(deadline):
    if type(deadline) not in (int, float) or not math.isfinite(deadline):
        raise ValueError("historical_storage_finite_deadline_required")
    if time.monotonic() >= deadline:
        raise TimeoutError("historical_storage_deadline")


def canonical(path):
    path = Path(path)
    if not path.is_absolute() or ".." in path.parts or path.resolve() != path:
        raise ValueError("historical_storage_aliased_path")
    return path


def identity(path):
    info = canonical(path).lstat()
    if not stat.S_ISREG(info.st_mode):
        raise ValueError("historical_storage_nonregular")
    return [info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns]


def record(path, *, deadline_at, max_bytes):
    check(deadline_at)
    path = canonical(path)
    before = identity(path)
    if before[2] > max_bytes:
        raise ValueError("historical_storage_byte_bound")
    digest, total = hashlib.sha256(), 0
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        info = os.fstat(fd)
        if [info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns] != before:
            raise ValueError("historical_storage_changed_during_open")
        while block := os.read(fd, min(1024**2, max_bytes + 1 - total)):
            check(deadline_at)
            total += len(block)
            if total > max_bytes:
                raise ValueError("historical_storage_byte_bound")
            digest.update(block)
        check(deadline_at)
        if identity(path) != before or total != before[2]:
            raise ValueError("historical_storage_changed_during_read")
    finally:
        os.close(fd)
    return {
        "path": str(path),
        "sha256": digest.hexdigest(),
        "size_bytes": total,
        "identity": before,
    }


def document(path, expected_sha256, *, deadline_at):
    before = record(path, deadline_at=deadline_at, max_bytes=MAX_CONTROL)
    if before["sha256"] != expected_sha256:
        raise ValueError("historical_storage_control_hash_changed")
    with Path(path).open("rb") as stream:
        payload = stream.read(MAX_CONTROL + 1)
    if len(payload) > MAX_CONTROL or identity(path) != before["identity"]:
        raise ValueError("historical_storage_control_changed")
    value = json.loads(payload)
    if not isinstance(value, dict):
        raise ValueError("historical_storage_control_object_required")
    return value, before


def dump(value):
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def exclusive(path, payload):
    """Publish a complete immutable file without overwriting another operation."""
    path = canonical(path)
    temporary = path.with_name(path.name + ".tmp-" + os.urandom(8).hex())
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o400)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, path)
        storage._sync_dir(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


class _Raw(io.RawIOBase):
    def __init__(self, stream, deadline):
        self.stream, self.deadline, self.pending = stream, deadline, memoryview(b"")

    def readable(self):
        return True

    def readinto(self, buffer):
        check(self.deadline)
        if not self.pending:
            try:
                self.pending = memoryview(next(self.stream))
            except StopIteration:
                return 0
        count = min(len(buffer), len(self.pending))
        buffer[:count] = self.pending[:count]
        self.pending = self.pending[count:]
        return count


class Grant:
    """Trusted operator grant, always loaded using its independently pinned hash."""

    def __init__(self, value, *, control_path, control_sha256):
        self.value, self.control_path, self.sha256 = value, str(control_path), control_sha256
        if value.get("schema") != SCHEMA or value.get("kind") not in KINDS:
            raise ValueError("historical_storage_grant_schema")
        self.root = canonical(value["root"])
        self.path = canonical(value["logical_path"])
        self.manifest = canonical(value["manifest_path"])
        self.physical = canonical(value["physical_path"])
        for path in (self.path, self.manifest, self.physical, canonical(control_path)):
            if not path.is_relative_to(self.root):
                raise ValueError("historical_storage_grant_outside_root")
        for key in ("logical_sha256", "manifest_sha256", "physical_sha256", "review_sha256"):
            if not re.fullmatch("[a-f0-9]{64}", str(value.get(key))):
                raise ValueError("historical_storage_grant_hash")
        if (
            type(value.get("logical_bytes")) is not int
            or not 0 < value["logical_bytes"] <= MAX_ARTIFACT
        ):
            raise ValueError("historical_storage_grant_size")
        for key in ("original_identity", "manifest_identity", "physical_identity"):
            if (
                not isinstance(value.get(key), list)
                or len(value[key]) != 5
                or any(type(x) is not int or x < 0 for x in value[key])
            ):
                raise ValueError("historical_storage_grant_identity")
        if value["original_identity"][2] != value["logical_bytes"]:
            raise ValueError("historical_storage_grant_size")

    def check(self, *, deadline_at, expected_sha256, original_identity=None):
        check(deadline_at)
        value = self.value
        if expected_sha256 != value["logical_sha256"] or (
            original_identity is not None and list(original_identity) != value["original_identity"]
        ):
            raise ValueError("historical_storage_original_binding_changed")
        saved, _ = document(self.control_path, self.sha256, deadline_at=deadline_at)
        if saved != value:
            raise ValueError("historical_storage_grant_changed")
        if (
            identity(self.manifest) != value["manifest_identity"]
            or identity(self.physical) != value["physical_identity"]
        ):
            raise ValueError("historical_storage_representation_identity_changed")
        manifest, _ = document(self.manifest, value["manifest_sha256"], deadline_at=deadline_at)
        entry = storage._validate_manifest(self.root, manifest)["artifacts"].get(
            str(self.path.relative_to(self.root))
        )
        if (
            entry is None
            or entry["logical_sha256"] != expected_sha256
            or entry["logical_bytes"] != value["logical_bytes"]
        ):
            raise ValueError("historical_storage_representation_binding_changed")
        allowed = [
            r
            for r in entry["representations"]
            if (
                r["codec"] == "zstd"
                and r["codec_params"] == {"level": 3, "dictionary_sha256": None}
                and self.root / r["physical_path"] == self.physical
                and r["physical_sha256"] == value["physical_sha256"]
                and r["physical_bytes"] == value["physical_identity"][2]
            )
        ]
        if len(allowed) != 1 or len(entry["representations"]) != 1:
            raise ValueError("historical_storage_exact_zstd_representation_required")
        if self.path.exists() or self.path.is_symlink():
            if identity(self.path) != value["original_identity"]:
                raise ValueError("historical_storage_original_identity_changed")
        return value

    @contextmanager
    def open(self, path, *, expected_sha256, deadline_at, max_bytes):
        if canonical(path) != self.path:
            raise ValueError("historical_storage_wrong_logical_path")
        self.check(deadline_at=deadline_at, expected_sha256=expected_sha256)
        if self.value["logical_bytes"] > max_bytes:
            raise ValueError("historical_storage_byte_bound")
        with storage.stream_artifact(
            self.root,
            self.path,
            expected_sha256=expected_sha256,
            expected_bytes=self.value["logical_bytes"],
            max_output_bytes=max_bytes,
            deadline_at=deadline_at,
            manifest_path=self.manifest,
        ) as stream:
            if stream.descriptor.legacy or stream.descriptor.codec != "zstd":
                stream.abandon()
                raise ValueError("historical_storage_representation_not_selected")
            buffered = io.BufferedReader(_Raw(stream, deadline_at), buffer_size=65536)
            try:
                yield buffered
                # Buffered read-ahead is not proof that the caller consumed EOF.
                if buffered.read(1) or not stream.complete:
                    raise storage.ArtifactReadIncompleteError(
                        "Historical artifact read did not consume its complete stream"
                    )
            finally:
                buffered.close()
        self.check(deadline_at=deadline_at, expected_sha256=expected_sha256)

    def verify(self, *, deadline_at):
        with self.open(
            self.path,
            expected_sha256=self.value["logical_sha256"],
            deadline_at=deadline_at,
            max_bytes=self.value["logical_bytes"],
        ) as stream:
            while stream.read(1024**2):
                check(deadline_at)
        return self.value

    @contextmanager
    def restore_snapshot(self, *, scratch_parent, deadline_at, reserve_bytes):
        if self.value["kind"] != "snapshot":
            raise ValueError("historical_storage_sealed_snapshot_required")
        self.check(deadline_at=deadline_at, expected_sha256=self.value["logical_sha256"])
        with storage.restore_sqlite(
            self.root,
            self.path,
            scratch_parent=canonical(scratch_parent),
            expected_sha256=self.value["logical_sha256"],
            expected_bytes=self.value["logical_bytes"],
            max_output_bytes=self.value["logical_bytes"],
            deadline_at=deadline_at,
            reserve_bytes=reserve_bytes,
            manifest_path=self.manifest,
        ) as snapshot:
            yield snapshot
        self.check(deadline_at=deadline_at, expected_sha256=self.value["logical_sha256"])


def load_grant(path, expected_sha256, *, deadline_at):
    value, _ = document(path, expected_sha256, deadline_at=deadline_at)
    grant = Grant(value, control_path=path, control_sha256=expected_sha256)
    grant.check(deadline_at=deadline_at, expected_sha256=value["logical_sha256"])
    return grant


def seal_grant(
    root,
    path,
    *,
    kind,
    expected_sha256,
    expected_bytes,
    original_identity,
    review_sha256,
    shared_lock,
    owner_fd,
    deadline_at,
):
    """Seal one reviewed representation while retaining its original by default.

    This proves representation bytes only. The migration caller supplies the
    independently checked producer and protected dependency/reader review.
    """
    from jobagg.runtime_source_head import owner

    owner(owner_fd, canonical(shared_lock))
    root, path = canonical(root), canonical(path)
    if kind not in KINDS or path == root or not path.is_relative_to(root):
        raise ValueError("historical_storage_exact_logical_scope_required")
    if (
        type(expected_bytes) is not int
        or not 0 < expected_bytes <= MAX_ARTIFACT
        or not isinstance(original_identity, (tuple, list))
        or len(original_identity) != 5
        or original_identity[2] != expected_bytes
        or not re.fullmatch("[a-f0-9]{64}", review_sha256)
    ):
        raise ValueError("historical_storage_review_binding_required")
    if path.exists() and identity(path) != list(original_identity):
        raise ValueError("historical_storage_original_identity_changed")
    manifest = storage._read_manifest(
        root / storage.STORAGE_DIRECTORY / storage.MANIFEST_NAME, root
    )
    relative = str(path.relative_to(root))
    entry = manifest["artifacts"].get(relative)
    if (
        entry is None
        or entry["logical_sha256"] != expected_sha256
        or entry["logical_bytes"] != expected_bytes
    ):
        raise ValueError("historical_storage_installed_binding_required")
    entries = [
        r
        for r in entry["representations"]
        if r["codec"] == "zstd" and r["codec_params"] == {"level": 3, "dictionary_sha256": None}
    ]
    if len(entries) != 1:
        raise ValueError("historical_storage_exact_zstd_representation_required")
    entry = {**entry, "representations": entries}
    selected = {**storage._empty_manifest(), "artifacts": {relative: entry}}
    payload = storage._manifest_bytes(selected)
    directory = root / storage.STORAGE_DIRECTORY / "reviews"
    canonical(directory)
    directory.mkdir(mode=0o700, exist_ok=True)
    canonical(directory)
    manifest_sha = hashlib.sha256(payload).hexdigest()
    manifest_path = directory / (manifest_sha + ".manifest.json")
    if not manifest_path.exists():
        exclusive(manifest_path, payload)
    elif (
        record(manifest_path, deadline_at=deadline_at, max_bytes=MAX_CONTROL)["sha256"]
        != manifest_sha
    ):
        raise ValueError("historical_storage_snapshot_manifest_changed")
    physical = root / entries[0]["physical_path"]
    value = {
        "schema": SCHEMA,
        "kind": kind,
        "root": str(root),
        "logical_path": str(path),
        "logical_sha256": expected_sha256,
        "logical_bytes": expected_bytes,
        "original_identity": list(original_identity),
        "manifest_path": str(manifest_path),
        "manifest_sha256": manifest_sha,
        "manifest_identity": identity(manifest_path),
        "physical_path": str(physical),
        "physical_sha256": entries[0]["physical_sha256"],
        "physical_identity": identity(physical),
        "review_sha256": review_sha256,
    }
    payload = dump(value)
    sha = hashlib.sha256(payload).hexdigest()
    target = directory / (sha + ".grant.json")
    if not target.exists():
        exclusive(target, payload)
    grant = load_grant(target, sha, deadline_at=deadline_at)
    grant.verify(deadline_at=deadline_at)
    owner(owner_fd, shared_lock)
    return grant


def attest_catalogue(conn, grant, *, owner_path, owner_fd, deadline_at):
    """Owner-held idle/inactive attachment; never change old proofs or dispositions."""
    from jobagg import runtime_authority as authority, runtime_control_catalog as catalogue
    from jobagg.runtime_source_head import owner

    owner(owner_fd, canonical(owner_path))
    state = authority.state(conn)
    imported = catalogue.receipt(conn)
    if state is not None:
        state = authority.validate(conn)
        if state["phase"] != "idle" or state["owner_path"] != str(owner_path):
            raise ValueError("historical_storage_catalogue_requires_idle_owner")
    manifest = json.loads(
        conn.execute("SELECT manifest_json FROM jobagg_legacy_control_state WHERE id=1").fetchone()[
            0
        ]
    )
    if manifest["shared_owner"] != str(owner_path):
        raise ValueError("historical_storage_catalogue_owner_changed")
    row = conn.execute(
        "SELECT sha256,identity_json FROM jobagg_legacy_dependencies WHERE path=?",
        (str(grant.path),),
    ).fetchone()
    if (
        row is None
        or row[0] is None
        or row[0] != grant.value["logical_sha256"]
        or json.loads(row[1]) != grant.value["original_identity"]
    ):
        raise ValueError("historical_storage_original_catalogue_proof_required")
    grant.verify(deadline_at=deadline_at)
    value = {
        "schema": "historical-catalogue-representation-1",
        "epoch": imported["epoch"],
        "inventory_sha256": imported["inventory_sha256"],
        "catalogue_sha256": imported["sha256"],
        "worker_database": str(canonical(conn.execute("PRAGMA database_list").fetchone()[2])),
        "owner": str(owner_path),
        "grant_path": grant.control_path,
        "grant_sha256": grant.sha256,
        "original_sha256": row[0],
        "original_identity": json.loads(row[1]),
    }
    key = PREFIX + hashlib.sha256(str(grant.path).encode()).hexdigest()
    prior = conn.execute(
        "SELECT review_json FROM jobagg_legacy_review_holds WHERE run_id=?", (key,)
    ).fetchone()
    if prior and json.loads(prior[0]) != value:
        raise ValueError("historical_storage_catalogue_grant_changed")
    conn.execute(
        "INSERT OR IGNORE INTO jobagg_legacy_review_holds VALUES(?,?)",
        (key, json.dumps(value, sort_keys=True)),
    )
    from jobagg.runtime_containment import invalidate

    invalidate(conn, "historical representation dependency changed; explicit review required")
    return value


def catalogue_grant(conn, path, *, expected_sha256, deadline_at):
    from jobagg import runtime_authority as authority, runtime_control_catalog as catalogue

    key = PREFIX + hashlib.sha256(str(canonical(path)).encode()).hexdigest()
    row = conn.execute(
        "SELECT review_json FROM jobagg_legacy_review_holds WHERE run_id=?", (key,)
    ).fetchone()
    if row is None:
        raise ValueError("historical_storage_catalogue_grant_missing")
    value, imported = json.loads(row[0]), catalogue.receipt(conn)
    state = authority.state(conn)
    if state is not None:
        state = authority.validate(conn)
    manifest = json.loads(
        conn.execute("SELECT manifest_json FROM jobagg_legacy_control_state WHERE id=1").fetchone()[
            0
        ]
    )
    if value.get("owner") != manifest["shared_owner"] or (
        state is not None and value.get("owner") != state["owner_path"]
    ):
        raise ValueError("historical_storage_catalogue_owner_changed")
    if (
        value.get("schema") != "historical-catalogue-representation-1"
        or value["epoch"] != imported["epoch"]
        or value["inventory_sha256"] != imported["inventory_sha256"]
        or value["catalogue_sha256"] != imported["sha256"]
        or value["worker_database"]
        != str(canonical(conn.execute("PRAGMA database_list").fetchone()[2]))
        or value["original_sha256"] != expected_sha256
    ):
        raise ValueError("historical_storage_catalogue_binding_changed")
    grant = load_grant(value["grant_path"], value["grant_sha256"], deadline_at=deadline_at)
    if grant.path != Path(path):
        raise ValueError("historical_storage_wrong_logical_path")
    grant.check(
        deadline_at=deadline_at,
        expected_sha256=expected_sha256,
        original_identity=value["original_identity"],
    )
    return grant
