"""Immutable publication inputs and narrowly audited legacy WAL recovery.

Filesystem validation never opens SQLite. A caller may permit the legacy empty
WAL exception only while holding the verified shared owner. Backup creation
verifies that owner itself and is the only operation here which opens SQLite.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import stat
import tempfile
import time

from jobagg.publication_deadline import expired
from datetime import UTC, datetime
from pathlib import Path

from jobagg.storage_budget import (
    MIN_PROJECTION_ALLOCATION_BYTES,
    PROJECTION_RECEIPT_MAX_BYTES,
)


def _check(deadline_at):
    if expired(deadline_at):
        raise TimeoutError("Publication snapshot deadline exhausted")


def _path(value):
    path = Path(value)
    if not path.is_absolute():
        raise ValueError("Publication binding path must be absolute")
    return path


def _stamp(value):
    return (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns)


def _regular(path):
    value = path.lstat()
    if not stat.S_ISREG(value.st_mode):
        raise ValueError("Publication binding is not a regular unaliased file: " + str(path))
    return value


def _unchanged(path, stamp):
    try:
        current = _regular(path)
    except (FileNotFoundError, ValueError) as exc:
        raise ValueError("Publication binding changed during validation") from exc
    if _stamp(current) != stamp:
        raise ValueError("Publication binding changed during validation")


def stable_file(path, *, deadline_at=None):
    """Hash one stable regular file without following a final-component symlink."""
    path = _path(path)
    _check(deadline_at)
    try:
        before = _regular(path)
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except (FileNotFoundError, OSError) as exc:
        raise ValueError("Publication binding changed or is unreadable: " + str(path)) from exc
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode) or _stamp(before) != _stamp(opened):
            raise ValueError("Publication binding changed during open")
        digest = hashlib.sha256()
        while block := os.read(descriptor, 1024 * 1024):
            _check(deadline_at)
            digest.update(block)
        after = os.fstat(descriptor)
        if _stamp(opened) != _stamp(after):
            raise ValueError("Publication binding changed during hashing")
    finally:
        os.close(descriptor)
    _unchanged(path, _stamp(after))
    _check(deadline_at)
    return {
        "path": str(path),
        "sha256": digest.hexdigest(),
        "size_bytes": after.st_size,
        "stat": dict(zip(("device", "inode", "size", "mtime_ns", "ctime_ns"), _stamp(after))),
    }


def _record_stamp(record):
    return tuple(record["stat"][key] for key in ("device", "inode", "size", "mtime_ns", "ctime_ns"))


def _bound(record, expected):
    if not re.fullmatch(r"[0-9a-f]{64}", str(expected)) or record["sha256"] != expected:
        raise ValueError("Worker database/WAL binding changed")


def _exists(path):
    # Unlike exists(), includes dangling symlinks so they cannot evade validation.
    return os.path.lexists(path)


def validate_publication_snapshot(request, database, *, owner_held=False, deadline_at=None):
    """Return the effective DB and receipt; never change input files or request.

    Legacy v1 bindings remain exact, except an originally absent, newly present,
    stable zero-byte WAL under the shared owner. Every originally bound file,
    including an originally empty WAL, must still have its original digest.
    A sealed backup request instead binds only its backup and acceptance receipt.
    """
    database = _path(database)
    if _path(request["worker_database"]).resolve() != database.resolve():
        raise ValueError("Publication worker database path differs")
    acceptance = stable_file(request["worker_acceptance_path"], deadline_at=deadline_at)
    if acceptance["sha256"] != request["worker_acceptance_sha256"]:
        raise ValueError("Worker acceptance receipt changed")
    snapshot = request.get("publication_snapshot")
    if snapshot is not None:
        if (
            not isinstance(snapshot, dict)
            or snapshot.get("schema_version") != 1
            or snapshot.get("kind") not in {"sqlite_backup", "sqlite_publication_projection"}
            or snapshot.get("source_database") != request["worker_database"]
            or snapshot.get("worker_acceptance_sha256") != acceptance["sha256"]
        ):
            raise ValueError("Malformed sealed publication snapshot binding")
        if snapshot["kind"] == "sqlite_publication_projection":
            from jobagg.publication_projection import _sha as manifest_sha
            from jobagg.publication_projection import validate_manifest
            projection = snapshot.get("projection", {})
            validate_manifest(projection.get("manifest"))
            if manifest_sha(projection["manifest"]) != projection.get("sha256"):
                raise ValueError("Publication projection manifest binding differs")
        effective = _path(snapshot["path"])
        if effective.resolve() == database.resolve():
            raise ValueError("Publication backup aliases the mutable worker database")
        if _exists(database) and _regular(database).st_ino == _regular(effective).st_ino:
            # Compare devices too: inode numbers are not unique across mounts.
            left, right = database.stat(), effective.stat()
            if left.st_dev == right.st_dev:
                raise ValueError("Publication backup aliases the mutable worker database")
        allowed = {effective.resolve()}
        expected_files = [{"path": str(effective), "sha256": snapshot["sha256"]}]
        if request.get("worker_database_files") != expected_files:
            raise ValueError("Sealed publication snapshot files differ")
        sidecars = [Path(str(effective) + suffix) for suffix in ("-wal", "-shm", "-journal")]
        if any(_exists(path) for path in sidecars):
            raise ValueError("Sealed publication snapshot has mutable sidecars")
        mode = "sealed_sqlite_projection" if snapshot["kind"] == "sqlite_publication_projection" else "sealed_sqlite_backup"
    else:
        effective = database
        _regular(database)
        if _exists(Path(str(database) + "-wal")):
            _regular(Path(str(database) + "-wal"))
        allowed = {database.resolve(), Path(str(database) + "-wal").resolve()}
        sidecars = []
        mode = "legacy_exact_files"
    files = request.get("worker_database_files")
    if not isinstance(files, list) or not files:
        raise ValueError("Worker database/WAL binding is incomplete")
    records, seen = [], set()
    for item in files:
        path = _path(item["path"])
        resolved = path.resolve()
        if resolved not in allowed or resolved in seen:
            raise ValueError("Worker database/WAL binding has unexpected or duplicate files")
        seen.add(resolved)
        record = stable_file(path, deadline_at=deadline_at)
        _bound(record, item["sha256"])
        records.append(record)
    if effective.resolve() not in seen:
        raise ValueError("Worker database/WAL binding is incomplete")
    exception = None
    absent_wal = None
    if snapshot is not None:
        if type(snapshot.get("size_bytes")) is not int or records[0]["size_bytes"] != snapshot["size_bytes"]:
            raise ValueError("Sealed publication snapshot size differs")
        if _regular(effective).st_mode & 0o222:
            raise ValueError("Sealed publication snapshot must be read-only")
    else:
        wal = Path(str(database) + "-wal")
        if wal.resolve() not in seen:
            if _exists(wal):
                if owner_held is not True:
                    raise ValueError("Unbound WAL exception requires verified shared owner")
                extra = stable_file(wal, deadline_at=deadline_at)
                if extra["size_bytes"] != 0:
                    raise ValueError("Unbound WAL is nonempty; recovery refused")
                records.append(extra)
                mode = "legacy_empty_wal_exception"
                exception = {
                    "kind": "originally_unbound_zero_byte_wal",
                    "verified_shared_owner": True,
                    "wal": extra,
                    "original_bindings_preserved": True,
                }
            else:
                absent_wal = wal
    # Verify the complete file set again after the last hash. This also detects a
    # previously absent WAL appearing, even empty, during validation.
    for record in [acceptance, *records]:
        _unchanged(Path(record["path"]), _record_stamp(record))
    if absent_wal is not None and _exists(absent_wal):
        raise ValueError("Worker WAL appeared during validation")
    if any(_exists(path) for path in sidecars):
        raise ValueError("Sealed publication sidecar appeared during validation")
    _check(deadline_at)
    return {
        "effective_database": str(effective),
        "validation_receipt": {
            "schema_version": 1,
            "validated_at": datetime.now(UTC).isoformat(),
            "mode": mode,
            "worker_database": str(database),
            "effective_database": str(effective),
            "worker_acceptance": acceptance,
            "validated_files": records,
            "legacy_exception": exception,
        },
    }


def _verify_owner(owner_fd, shared_lock):
    path = _path(shared_lock)
    actual, inherited = _regular(path), os.fstat(owner_fd)
    if (actual.st_dev, actual.st_ino) != (inherited.st_dev, inherited.st_ino):
        raise ValueError("Snapshot owner FD does not match shared lock")
    with path.open("a+") as probe:
        try:
            fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            fcntl.flock(owner_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        else:
            fcntl.flock(probe, fcntl.LOCK_UN)
            raise ValueError("Snapshot creation requires a held shared owner")


def _sync_snapshot_directory(path):
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _projection_operation(binding):
    encoded = json.dumps(binding, sort_keys=True, separators=(",", ":")).encode()
    return "projection-" + hashlib.sha256(encoded).hexdigest()


def _write_projection_receipt(path, receipt):
    """The already consumed projection grant includes this bounded sidecar."""
    from contextlib import nullcontext

    from jobagg.atomic_files import admitted_file_writes, atomic_write_bytes
    from jobagg.storage_budget import StorageBudgetDeferred

    payload = json.dumps(receipt, sort_keys=True, separators=(",", ":")).encode()
    if len(payload) > PROJECTION_RECEIPT_MAX_BYTES:
        raise StorageBudgetDeferred("publication_projection_receipt_limit_exceeded")

    def prepaid(target, size):
        if target != path or size != len(payload):
            raise ValueError("Projection receipt escaped its prepaid bound")
        return nullcontext()

    with admitted_file_writes(prepaid):
        atomic_write_bytes(path, payload)


def _finish_budgeted_projection(binding, target, temporary, receipt_path, budget, *, deadline_at):
    from jobagg.publication_projection import _sha, validate_manifest
    from jobagg.storage_budget import StorageBudgetError

    info = _regular(receipt_path)
    if info.st_nlink != 1 or info.st_size > PROJECTION_RECEIPT_MAX_BYTES:
        raise ValueError("Projection recovery receipt is unbounded or aliased")
    receipt, receipt_record = _json_document(receipt_path, deadline_at=deadline_at)
    if (set(receipt) != {"schema_version", "binding", "snapshot", "identity"}
            or receipt.get("schema_version") != 1 or receipt.get("binding") != binding):
        raise ValueError("Projection recovery receipt binding differs")
    snapshot = receipt["snapshot"]
    identity = receipt["identity"]
    if (not isinstance(identity, list) or len(identity) != 4
            or any(type(item) is not int for item in identity)
            or snapshot.get("schema_version") != 1 or snapshot.get("kind") != "sqlite_publication_projection"
            or snapshot.get("path") != str(target) or snapshot.get("source_database") != binding["source_database"]
            or snapshot.get("worker_acceptance_sha256") != binding["worker_acceptance_sha256"]
            or type(snapshot.get("size_bytes")) is not int or not 0 < snapshot["size_bytes"] <= binding["main_cap_bytes"]
            or not re.fullmatch(r"[a-f0-9]{64}", str(snapshot.get("sha256")))):
        raise ValueError("Projection recovery payload binding differs")
    projection = snapshot.get("projection", {})
    validate_manifest(projection.get("manifest"))
    if _sha(projection["manifest"]) != projection.get("sha256"):
        raise ValueError("Projection recovery manifest differs")
    operation_id = _projection_operation(binding)
    state = budget.snapshot()
    expected_operation = {"category": "work", "maximum": binding["max_bytes"],
                          "consumed": binding["max_bytes"], "path": str(target), "writer_id": budget.writer_id}
    outstanding = state["operations"].get(operation_id)
    completed = state["completions"].get(operation_id)
    if outstanding != expected_operation and completed != {
            **expected_operation, "proof_sha256": receipt_record["sha256"]}:
        raise StorageBudgetError("Projection recovery lacks its exact charged operation")
    paths = [path for path in (target, temporary) if _exists(path)]
    if not paths:
        raise ValueError("Sealed projection payload is missing")
    for path in (target, temporary):
        if any(_exists(Path(str(path) + suffix)) for suffix in ("-wal", "-shm", "-journal")):
            raise ValueError("Sealed projection has mutable sidecars")
    candidate = paths[0]
    record = stable_file(candidate, deadline_at=deadline_at)
    actual = _regular(candidate)
    if (record["sha256"] != snapshot["sha256"] or record["size_bytes"] != snapshot["size_bytes"]
            or list(_stamp(actual)[:4]) != identity or actual.st_mode & 0o222
            or actual.st_nlink != len(paths) or max(actual.st_size, actual.st_blocks * 512) > binding["main_cap_bytes"]):
        raise ValueError("Sealed projection bytes, identity or allocation changed")
    if os.path.samefile(candidate, binding["source_database"]):
        raise ValueError("Sealed projection aliases its mutable source")
    if len(paths) == 2 and not os.path.samefile(target, temporary):
        raise ValueError("Projection publication target differs from retained temporary")
    _unchanged(receipt_path, _record_stamp(receipt_record))
    _unchanged(candidate, _record_stamp(record))
    if not _exists(target):
        os.link(temporary, target)
        _sync_snapshot_directory(target.parent)
    if _exists(temporary):
        # Only this exact verified sealed inode may be retired after the final
        # name is durably linked. A partial/unproven temporary is never removed.
        if not os.path.samefile(target, temporary) or list(_stamp(_regular(target))[:4]) != identity:
            raise ValueError("Projection identity changed during publication")
        temporary.unlink()
        _sync_snapshot_directory(target.parent)
    if outstanding is not None:
        budget.resume(operation_id).settle(binding["max_bytes"], proof_sha256=receipt_record["sha256"])
    return snapshot


def _create_budgeted_projection(source, target, acceptance_sha256, budget, max_bytes, *, deadline_at, selection=None):
    """Resume only a sealed, hashed inode and its original durable operation."""
    import sqlite3

    from jobagg.publication_projection import build_projection
    from jobagg.storage_budget import (
        PROJECTION_CONTROL_BYTES,
        StorageBudgetDeferred,
        StorageBudgetError,
    )

    if type(max_bytes) is not int or max_bytes < MIN_PROJECTION_ALLOCATION_BYTES:
        raise StorageBudgetError("Finite projection allocation bound must include its recovery controls")
    if source.resolve() != source or target.resolve() != target or not target.parent.parent.is_dir():
        raise StorageBudgetError("Budgeted projection requires direct paths and an existing scratch parent")
    budget.validate_writer(expected_cycle_id=budget.cycle_id, expected_scope_hash=budget.scope_hash,
                           expected_ledger_path=budget.path, allowed_phases=("accepted_pending", "publication_admitted") if selection is not None else ("accepted_pending",), required_path=target)
    if budget.category_for(target) != "work":
        raise StorageBudgetError("Projection target must be registered work")
    binding = {"source_database": str(source), "target": str(target),
               "worker_acceptance_sha256": acceptance_sha256, "max_bytes": max_bytes,
               "main_cap_bytes": ((max_bytes - PROJECTION_CONTROL_BYTES) // 2 // 4096) * 4096,
               "cycle_id": budget.cycle_id, "scope_hash": budget.scope_hash,
               "ledger_path": str(budget.path), "writer_id": budget.writer_id}
    if selection is not None:
        binding['selection'] = selection
    operation_id = _projection_operation(binding)
    temporary = target.with_name("." + target.name + "." + operation_id + ".sqlite3")
    receipt_path = target.with_name(target.name + ".sealed.json")
    if _exists(receipt_path):
        return _finish_budgeted_projection(binding, target, temporary, receipt_path, budget, deadline_at=deadline_at)
    if _exists(target) or _exists(temporary) or any(
            _exists(Path(str(path) + suffix)) for path in (target, temporary) for suffix in ("-wal", "-shm", "-journal")):
        raise StorageBudgetDeferred("publication_projection_unsealed_artifact_requires_review")
    state = budget.snapshot()
    if operation_id in state["completions"]:
        raise StorageBudgetError("Completed projection is missing its recovery receipt")
    prior = state["operations"].get(operation_id)
    expected = {"category": "work", "maximum": max_bytes, "consumed": 0,
                "path": str(target), "writer_id": budget.writer_id}
    if prior is not None and prior not in (expected, {**expected, "consumed": max_bytes}):
        raise StorageBudgetError("Projection operation binding differs")
    operation = budget.resume(operation_id) if prior is not None else budget.reserve(
        "work", max_bytes, path=target, operation_id=operation_id)
    if prior is None or prior["consumed"] == 0:
        operation.consume(max_bytes)
    target.parent.mkdir(exist_ok=True)
    descriptor = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(descriptor)
    origin = copy = None
    try:
        origin = sqlite3.connect(source.as_uri() + "?mode=ro", uri=True, timeout=1)
        copy = sqlite3.connect(temporary, timeout=1)
        projection_receipt = build_projection(origin, copy, deadline_at=deadline_at, max_bytes=binding["main_cap_bytes"], selection=selection)
        _check(deadline_at)
        copy.set_progress_handler(lambda: int(deadline_at is not None and time.monotonic() >= deadline_at), 1000)
        if copy.execute("PRAGMA journal_mode=DELETE").fetchone()[0].lower() != "delete":
            raise ValueError("Publication projection could not seal journal mode")
        if copy.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
            raise ValueError("Publication projection integrity check failed")
    finally:
        for connection in (origin, copy):
            if connection is not None:
                connection.close()
    if any(_exists(Path(str(temporary) + suffix)) for suffix in ("-wal", "-shm", "-journal")):
        raise ValueError("Publication projection has unsealed sidecars")
    info = _regular(temporary)
    if max(info.st_size, info.st_blocks * 512) > binding["main_cap_bytes"]:
        raise StorageBudgetDeferred("publication_projection_allocation_limit_exceeded")
    os.chmod(temporary, 0o444)
    with temporary.open("rb") as handle:
        os.fsync(handle.fileno())
    record = stable_file(temporary, deadline_at=deadline_at)
    snapshot = {"schema_version": 1, "kind": "sqlite_publication_projection", "path": str(target),
                "sha256": record["sha256"], "size_bytes": record["size_bytes"], "source_database": str(source),
                "worker_acceptance_sha256": acceptance_sha256, "projection": projection_receipt}
    receipt = {"schema_version": 1, "binding": binding, "snapshot": snapshot,
               "identity": list(_stamp(_regular(temporary))[:4])}
    _write_projection_receipt(receipt_path, receipt)
    return _finish_budgeted_projection(binding, target, temporary, receipt_path, budget, deadline_at=deadline_at)


def create_publication_snapshot(source, target, acceptance_sha256, owner_fd, shared_lock, *, deadline_at=None, projection=False, budget=None, max_bytes=None, selection=None):
    """Create one fsynced, read-only SQLite backup; never overwrite an old backup."""
    import sqlite3

    source, target = _path(source), _path(target)
    _check(deadline_at)
    _verify_owner(owner_fd, shared_lock)
    _regular(source)
    if not re.fullmatch(r"[0-9a-f]{64}", str(acceptance_sha256)):
        raise ValueError("Snapshot requires a worker acceptance digest")
    if target.resolve() == source.resolve():
        raise ValueError("Publication snapshot target exists or aliases its source")
    if budget is not None:
        from jobagg.storage_budget import StorageBudgetError
        if not projection:
            raise StorageBudgetError("Finite projection allocation bound required")
        return _create_budgeted_projection(source, target, acceptance_sha256, budget, max_bytes, deadline_at=deadline_at, selection=selection)
    if _exists(target):
        raise ValueError("Publication snapshot target exists or aliases its source")
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=".publication-backup-", suffix=".sqlite3", dir=target.parent)
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        def progress(*_):
            _check(deadline_at)

        with sqlite3.connect(source.as_uri() + "?mode=ro", uri=True, timeout=1) as origin:
            with sqlite3.connect(temporary, timeout=1) as copy:
                if projection:
                    from jobagg.publication_projection import build_projection
                    projection_receipt = build_projection(origin, copy, deadline_at=deadline_at,
                                                          max_bytes=max_bytes, selection=selection)
                else:
                    origin.backup(copy, pages=256, progress=progress, sleep=0.05)
                _check(deadline_at)
                copy.set_progress_handler(lambda: int(deadline_at is not None and time.monotonic() >= deadline_at), 1000)
                if copy.execute("PRAGMA journal_mode=DELETE").fetchone()[0].lower() != "delete":
                    raise ValueError("Publication snapshot could not seal journal mode")
                if copy.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
                    raise ValueError("Publication snapshot integrity check failed")
        # sqlite3 context managers commit but do not close their connection.
        origin.close()
        copy.close()
        _check(deadline_at)
        if any(_exists(Path(str(temporary) + suffix)) for suffix in ("-wal", "-shm", "-journal")):
            raise ValueError("Publication snapshot has unsealed sidecars")
        os.chmod(temporary, 0o444)
        with temporary.open("rb") as handle:
            os.fsync(handle.fileno())
        record = stable_file(temporary, deadline_at=deadline_at)
        # link+unlink publishes atomically without replacing an existing generation.
        os.link(temporary, target)
        temporary.unlink()
        directory = os.open(target.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
        return {
            "schema_version": 1,
            "kind": "sqlite_publication_projection" if projection else "sqlite_backup",
            "path": str(target),
            "sha256": record["sha256"],
            "size_bytes": record["size_bytes"],
            "source_database": str(source),
            "worker_acceptance_sha256": acceptance_sha256,
            **({"projection": projection_receipt} if projection else {}),
        }
    except sqlite3.OperationalError as exc:
        if (
            getattr(exc, "sqlite_errorcode", None) == sqlite3.SQLITE_INTERRUPT
            and deadline_at is not None
            and time.monotonic() >= deadline_at
        ):
            raise TimeoutError("Publication snapshot deadline exhausted during SQLite validation") from exc
        raise
    finally:
        # Close on failed backup/validation too, then remove only our unpublished temp.
        for connection in (locals().get("origin"), locals().get("copy")):
            if connection is not None:
                connection.close()
        for suffix in ("", "-wal", "-shm", "-journal"):
            Path(str(temporary) + suffix).unlink(missing_ok=True)


_RECOVERY_IDENTITY = (
    "run_id", "source_manifest_sha256", "registry_sha256", "expected_source_ids",
    "worker_acceptance_path", "worker_acceptance_sha256", "worker_database",
    "worker_database_files", "publication_snapshot", "publication_policy",
)


def _recovery_identity(request):
    import json

    value = {key: request[key] for key in _RECOVERY_IDENTITY if key in request}
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _json_document(path, *, deadline_at=None):
    import json

    path = _path(path)
    record = stable_file(path, deadline_at=deadline_at)
    content = path.read_bytes()
    if hashlib.sha256(content).hexdigest() != record["sha256"]:
        raise ValueError("Recovery document changed during read")
    _unchanged(path, _record_stamp(record))
    return json.loads(content), record


def _same_path(left, right):
    return _path(left).resolve() == _path(right).resolve()


def _gate_documents(request, effective_database, output_dir, state_dir, *, deadline_at=None):
    """Inspect existing journal only; absence can never authorize replanning."""
    gate_path = _path(output_dir) / ".jobagg-publication-state.json"
    gate, gate_record = _json_document(gate_path, deadline_at=deadline_at)
    generation = gate.get("generation_id")
    if not isinstance(generation, str) or not re.fullmatch(r"[0-9a-f]{32}", generation):
        raise ValueError("Recovery gate generation is invalid")
    if gate.get("state") not in {"publishing", "exporting", "complete"}:
        raise ValueError("Recovery gate state is invalid")
    root = _path(state_dir) / "generations" / generation
    plan_path = root / "plan.json"
    if not _same_path(gate["plan_path"], plan_path):
        raise ValueError("Recovery gate plan path differs")
    plan, plan_record = _json_document(plan_path, deadline_at=deadline_at)
    if (
        plan_record["sha256"] != gate.get("plan_sha256")
        or plan.get("generation_id") != generation
        or not _same_path(plan["generation_root"], root)
        or not _same_path(plan["output_dir"], output_dir)
        or not _same_path(plan["worker_database"], effective_database)
        or plan.get("registry_sha256") != request.get("registry_sha256")
        or plan.get("observation_set_sha256") != gate.get("observation_set_sha256")
        or not re.fullmatch(r"[0-9a-f]{64}", str(plan.get("observation_set_sha256")))
    ):
        raise ValueError("Recovery gate/plan/request binding differs")
    sources = [item.get("worker_row", {}).get("source_id") for item in plan.get("changes", [])]
    sources.extend(item.get("source_id") for item in plan.get("listing_frames", []))
    for source_id in sources:
        if source_id not in request.get("expected_source_ids", []):
            raise ValueError("Recovery plan source is outside original request")
    _unchanged(gate_path, _record_stamp(gate_record))
    return gate, plan, plan_record, root


def capture_recovery_generation(request, effective_database, output_dir, state_dir, *, prior_binding=None, owner_held=False, deadline_at=None, expired_authorization=None):
    """Bind an existing unfinished generation once; persist before starting child.

    The original immutable request's time window and worker file digests must
    already have been validated by the dispatcher. A complete gate with no prior
    binding cannot establish which generation belonged to an interrupted intent.
    """
    if owner_held is not True:
        raise ValueError("Recovery generation binding requires verified shared owner")
    if prior_binding is not None:
        validate_recovery_generation(
            {**request, "recovery_generation_binding": prior_binding},
            effective_database, output_dir, state_dir, deadline_at=deadline_at,
        )
        return prior_binding
    gate, plan, record, _ = _gate_documents(
        request, effective_database, output_dir, state_dir, deadline_at=deadline_at
    )
    if gate["state"] == "complete":
        raise ValueError("Complete recovery gate lacks prior generation binding; review required")
    try:
        clocks = [datetime.fromisoformat(str(value).replace("Z", "+00:00")) for value in (
            request["generated_at"], plan["created_at"], gate["started_at"], request["deadline_at"]
        )]
    except (KeyError, ValueError) as exc:
        raise ValueError("Original recovery request time evidence missing") from exc
    if any(clock.tzinfo is None for clock in clocks) or not clocks[0] <= clocks[1] <= clocks[2]:
        raise ValueError("Recovery generation is outside original publication request window")
    identity = _recovery_identity(request)
    ownership = (gate.get("request_identity_sha256"), plan.get("request_identity_sha256"))
    if any(value is not None for value in ownership) and ownership != (identity, identity):
        raise ValueError("Recovery generation request ownership differs")
    authorization = {
        "schema_version": 1, "kind": "reviewed_expired_publication",
        "request_identity_sha256": identity, "generation_id": gate["generation_id"],
        "plan_sha256": record["sha256"], "original_generated_at": request["generated_at"],
        "original_deadline_at": request["deadline_at"], "gate_started_at": gate["started_at"],
    }
    if expired_authorization is not None and expired_authorization != authorization:
        raise ValueError("Expired generation authorization differs from immutable evidence")
    if clocks[2] > clocks[3] and ownership != (identity, identity) and expired_authorization != authorization:
        raise ValueError("Recovery generation is outside original publication request window")
    return {
        "schema_version": 1,
        "kind": "existing_publication_generation",
        "request_identity_sha256": _recovery_identity(request),
        "generation_id": gate["generation_id"],
        "plan_path": str(record["path"]),
        "plan_sha256": record["sha256"],
        "observation_set_sha256": gate["observation_set_sha256"],
        "worker_database": str(effective_database),
        "output_dir": str(output_dir),
        "state_dir": str(state_dir),
        "registry_sha256": request["registry_sha256"],
        "initial_gate_state": gate["state"],
        "expiry_recovery_authorization": expired_authorization,
        "request_ownership_verified": ownership == (identity, identity),
        "original_generated_at": request["generated_at"],
        "original_deadline_at": request["deadline_at"],
    }


def validate_recovery_generation(request, effective_database, output_dir, state_dir, *, deadline_at=None):
    """Require the pinned generation; return completed result without new planning."""
    binding = request.get("recovery_generation_binding")
    if (
        not isinstance(binding, dict)
        or binding.get("schema_version") != 1
        or binding.get("kind") != "existing_publication_generation"
        or binding.get("request_identity_sha256") != _recovery_identity(request)
        or binding.get("initial_gate_state") not in {"publishing", "exporting"}
        or not _same_path(binding["worker_database"], effective_database)
        or not _same_path(binding["output_dir"], output_dir)
        or not _same_path(binding["state_dir"], state_dir)
        or binding.get("registry_sha256") != request.get("registry_sha256")
    ):
        raise ValueError("Recovery request lacks matching immutable generation binding")
    gate, plan, record, root = _gate_documents(
        request, effective_database, output_dir, state_dir, deadline_at=deadline_at
    )
    if (
        gate["generation_id"] != binding.get("generation_id")
        or record["sha256"] != binding.get("plan_sha256")
        or not _same_path(record["path"], binding["plan_path"])
        or gate["observation_set_sha256"] != binding.get("observation_set_sha256")
    ):
        raise ValueError("Recovery generation changed since first binding")
    if plan.get("publication_policy") != request.get("publication_policy"):
        raise ValueError("Recovery publication policy differs from immutable plan")
    completed = None
    if gate["state"] == "complete":
        result, _ = _json_document(root / "result.json", deadline_at=deadline_at)
        if result != gate or result.get("status") != "published" or result.get("database_transactions_complete") is not True:
            raise ValueError("Completed recovery generation receipt differs from gate")
        completed = result
    return {"binding": binding, "gate": gate, "completed_result": completed}


def capture_unstarted_publication(request, output_dir, state_dir, *, owner_held=False, deadline_at=None):
    """Prove no generation started: monotonic journal gate predates this intent.

    Publication always installs its gate before touching any live destination and
    never restores an older gate. Caller must hold the owner lock after the child
    process group has terminated. This is not permission to resume another gate.
    """
    if owner_held is not True:
        raise ValueError("Unstarted publication review requires shared owner")
    gate, record = _json_document(_path(output_dir) / '.jobagg-publication-state.json', deadline_at=deadline_at)
    start = datetime.fromisoformat(request['generated_at'].replace('Z', '+00:00'))
    completed = datetime.fromisoformat(gate.get('completed_at', '').replace('Z', '+00:00'))
    if (gate.get('state') != 'complete' or gate.get('status') != 'published'
            or gate.get('database_transactions_complete') is not True
            or start.tzinfo is None or completed.tzinfo is None or completed >= start
            or gate.get('request_identity_sha256') == _recovery_identity(request)):
        raise ValueError("Gate does not prove publication remained unstarted")
    generation = gate.get('generation_id', '')
    if not re.fullmatch(r'[0-9a-f]{32}', generation):
        raise ValueError("Invalid preceding generation")
    root = _path(state_dir) / 'generations' / generation
    if not _same_path(gate['plan_path'], root / 'plan.json'):
        raise ValueError("Preceding generation path differs")
    plan, plan_record = _json_document(root / 'plan.json', deadline_at=deadline_at)
    result, result_record = _json_document(root / 'result.json', deadline_at=deadline_at)
    if result != gate or plan_record['sha256'] != gate['plan_sha256']:
        raise ValueError("Preceding completed journal differs")
    return {'schema_version': 1, 'kind': 'verified_unstarted_publication',
            'request_identity_sha256': _recovery_identity(request),
            'prior_gate': gate, 'prior_gate_sha256': record['sha256'],
            'prior_plan': plan_record, 'prior_result': result_record,
            'generation_not_started': True}
