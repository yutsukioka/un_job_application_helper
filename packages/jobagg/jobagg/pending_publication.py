"""Durable accepted-work fence before publication intent.

This record is neither a publisher intent nor a successful publication.  It
keeps a completed worker batch from being fetched again when projection or
publication admission is deferred.  Replaying an unsealed projection requires
the activated database writer fence, not merely an unchanged pathname.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import uuid
from datetime import UTC, datetime
from pathlib import Path

from jobagg.atomic_files import atomic_write_bytes, atomic_write_text


class PendingPublicationError(ValueError):
    pass


def _sha(path: Path) -> str:
    value = hashlib.sha256()
    from jobagg.publication_deadline import expired
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            if expired(None):
                raise TimeoutError('Pending accepted-work verification deadline exhausted')
            value.update(block)
    return value.hexdigest()


def _json(path: Path) -> dict:
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 16 * 1024**2:
        raise PendingPublicationError("Pending-work control is absent, aliased or oversized")
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise PendingPublicationError("Pending-work control must be an object")
    return value


def marker_path(state_dir: Path) -> Path:
    return Path(state_dir) / "pending_accepted_work.json"


def database_binding(path: Path) -> dict:
    """Bind cheap physical identities and the activated SQL mutation revision.

    Unknown legacy writers are not certified by filesystem timestamps.  Such
    work remains held and can be reconciled explicitly, but cannot be silently
    replayed against a newer database.
    """
    path = Path(path).absolute()
    files = []
    for member in (path, Path(str(path) + "-wal")):
        if not os.path.lexists(member):
            files.append({"path": str(member), "exists": False})
            continue
        info = member.lstat()
        if not stat.S_ISREG(info.st_mode) or member.resolve() != member:
            raise PendingPublicationError("Accepted database family is not a regular, direct path")
        files.append({"path": str(member), "exists": True, "device": info.st_dev,
                      "inode": info.st_ino, "size": info.st_size,
                      "mtime_ns": info.st_mtime_ns, "ctime_ns": info.st_ctime_ns})
    from jobagg import semantic_history

    from jobagg.runtime_authority import binding as runtime_binding
    semantic = runtime_binding(path) or semantic_history.semantic_database_binding(path)
    return {"files": files, "semantic": semantic}


def same_database_binding(expected, actual):
    """A fenced M1 revision survives WAL checkpoints, never data or inode changes.

    Original semantic-history and legacy binding schemas retain exact comparison.
    Physical WAL evidence is retained in the seal even when the runtime authority
    proves a checkpoint did not change the accepted database revision.
    """
    if expected.get("semantic", {}).get("schema") not in {"jobagg-runtime-authority-1", "jobagg-runtime-authority-2", "jobagg-runtime-authority-3"}:
        return expected == actual
    if expected.get("semantic") != actual.get("semantic"):
        return False
    before, after = expected.get("files", []), actual.get("files", [])
    if not before or not after:
        return before == after
    return all(before[0].get(key) == after[0].get(key) for key in
               ("path", "exists", "device", "inode"))


def _run(state_dir: Path, run_id: str) -> Path:
    if not isinstance(run_id, str) or not run_id or Path(run_id).name != run_id:
        raise PendingPublicationError("Invalid pending-work run identity")
    root = Path(state_dir).absolute() / "runs"
    run = root / run_id
    if run.resolve() != run or run.parent != root:
        raise PendingPublicationError("Pending-work run escapes its owner directory")
    return run


def _target(run: Path, supplied=None) -> Path:
    target = Path(supplied) if supplied is not None else run / "publication_snapshot.sqlite3"
    if (not target.is_absolute() or target.resolve() != target or target.is_symlink()
            or target.name in {"request.json", "acceptance.json", "worker_result.json", "publication_intent.json"}):
        raise PendingPublicationError("Pending projection target is not a direct trusted path")
    return target


def control_dir(state_dir, value):
    run = _run(Path(state_dir), value['run_id'])
    target = Path(value.get('publication_control_dir', run))
    if target != run and (target.parent != run / 'publication_batches'
            or re.fullmatch(r'[0-9]{6}', target.name) is None):
        raise PendingPublicationError('Invalid publication continuation directory')
    if target.resolve() != target:
        raise PendingPublicationError('Aliased publication continuation directory')
    return target


def _no_intent(run):
    if os.path.lexists(run / "publication_intent.json"):
        raise PendingPublicationError("Pending work crossed publisher intent; use publication recovery")


def _accepted_proofs(value, run):
    for field, filename in (("request_sha256", "request.json"),
                            ("acceptance_sha256", "acceptance.json"),
                            ("worker_result_sha256", "worker_result.json")):
        path = run / filename
        _json(path)
        if value[field] != _sha(path):
            raise PendingPublicationError("Pending accepted-work proof changed: " + filename)


def seal(state_dir, run_id, *, config_sha256, database, lease, binding=None, projection_target=None, publication_control_dir=None):
    """Seal successful worker evidence before any projection allocation."""
    run = _run(Path(state_dir), run_id)
    controls = control_dir(state_dir, {'run_id': run_id, **({'publication_control_dir': str(publication_control_dir)} if publication_control_dir else {})})
    _no_intent(controls)
    target = _target(run, projection_target or (controls / 'publication_snapshot.sqlite3'))
    request = _json(run / "request.json")
    worker = _json(run / "worker_result.json")
    acceptance = run / "acceptance.json"
    native_queue = worker.get('schema') == 'native-accepted-queue-proof-1'
    if native_queue:
        certificate = _json(acceptance)
        actual = database_binding(Path(database))
        semantic = actual.get('semantic') or {}
        if (certificate.get('kind') != 'native_accepted_queue' or semantic.get('phase') != 'accepted'
                or semantic.get('run_id') != run_id or not semantic.get('writer_fenced')
                or not same_database_binding(worker.get('database_binding', {}), actual)):
            raise PendingPublicationError('Native queue acceptance lacks its frozen revision fence')
    if (request.get("run_id") != run_id or worker.get("run_id") != run_id
            or (not native_queue and worker.get("worker_exit_code") != 0)
            or worker.get("report_sha256") != _sha(acceptance)):
        raise PendingPublicationError("Accepted worker exit/report proof does not match")
    value = {
        "schema_version": 1, "state": "accepted_pending", "run_id": run_id,
        "config_sha256": config_sha256,
        "request_sha256": _sha(run / "request.json"),
        "acceptance_sha256": _sha(acceptance),
        "worker_result_sha256": _sha(run / "worker_result.json"),
        "database": str(Path(database).absolute()),
        "database_binding": binding if binding is not None else database_binding(Path(database)),
        "lease": lease,
        "projection": None, "projection_target": str(target), "publisher_intent_recorded": False,
    }
    if controls != run:
        value['publication_control_dir'] = str(controls)
    path = marker_path(Path(state_dir))
    if path.exists():
        existing = _json(path)
        if existing != value:
            raise PendingPublicationError("Another accepted batch already owns the pending fence")
        return existing
    atomic_write_text(path, json.dumps(value, sort_keys=True) + "\n")
    return value


def inspect(state_dir):
    path = marker_path(Path(state_dir))
    if not os.path.lexists(path):
        return None
    value = _json(path)
    if value.get("schema_version") != 1 or value.get("state") != "accepted_pending":
        raise PendingPublicationError("Unknown pending-work state")
    _run(Path(state_dir), value.get("run_id"))
    return value


def validate(state_dir, *, config_sha256, database, binding=None, projection_target=None):
    value = inspect(state_dir)
    if value is None:
        return None
    run = _run(Path(state_dir), value["run_id"])
    if value["config_sha256"] != config_sha256 or value["database"] != str(Path(database).absolute()):
        raise PendingPublicationError("Pending-work configuration or database changed")
    target = _target(run, projection_target or (control_dir(state_dir, value) / 'publication_snapshot.sqlite3'))
    if value.get("projection_target", str(run / "publication_snapshot.sqlite3")) != str(target):
        raise PendingPublicationError("Pending projection target binding differs")
    _accepted_proofs(value, run)
    _no_intent(control_dir(state_dir, value))
    if value.get("projection") is None:
        actual = binding if binding is not None else database_binding(Path(database))
        semantic = actual.get("semantic") or {}
        if not semantic.get("writer_fenced"):
            raise PendingPublicationError("Unsealed accepted database lacks a certified writer fence")
        if not same_database_binding(value["database_binding"], actual):
            raise PendingPublicationError("Accepted database changed; automatic projection is held")
    else:
        projection = value["projection"]
        path = Path(projection["path"])
        if path != target or path.is_symlink() or _sha(path) != projection["sha256"]:
            raise PendingPublicationError("Sealed pending projection changed")
    return value


def record_projection(state_dir, snapshot):
    value = inspect(state_dir)
    if value is None:
        raise PendingPublicationError("No accepted-work fence")
    run = _run(Path(state_dir), value["run_id"])
    path = Path(snapshot["path"])
    target = _target(run, value.get("projection_target"))
    if path != target or path.is_symlink() or _sha(path) != snapshot["sha256"]:
        raise PendingPublicationError("Projection does not match the accepted run")
    if value.get("projection") is not None and value["projection"] != snapshot:
        raise PendingPublicationError("An already sealed projection cannot be replaced")
    value["projection"] = dict(snapshot)
    atomic_write_text(marker_path(Path(state_dir)), json.dumps(value, sort_keys=True) + "\n")
    return value


def _sync_directory(path):
    # Windows has no portable POSIX directory fsync. Binary files and atomic
    # receipts are still flushed before removal; do not claim Unix semantics.
    if os.name == "nt":
        return
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _control_bytes(path):
    _json(path)
    before = path.stat()
    payload = path.read_bytes()
    after = path.stat()
    if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
        after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns
    ) or len(payload) > 16 * 1024**2:
        raise PendingPublicationError("Continuation control changed while reading")
    return payload


def _process_proof(value):
    if (not isinstance(value, dict)
            or set(value) != {"checked_at", "ps_sha256", "former_pids_groups_and_commands_absent"}
            or value.get("former_pids_groups_and_commands_absent") is not True
            or not isinstance(value.get("ps_sha256"), str)
            or re.fullmatch(r"[a-f0-9]{64}", value["ps_sha256"]) is None):
        raise PendingPublicationError("Exact former-process absence proof required")
    try:
        observed = datetime.fromisoformat(value["checked_at"])
        age = (datetime.now(UTC) - observed).total_seconds()
        if observed.tzinfo is None or not -5 <= age <= 300:
            raise ValueError("stale proof")
    except (TypeError, ValueError, AttributeError) as exc:
        raise PendingPublicationError("Former-process absence proof is stale or malformed") from exc


def _continuation_receipt(head):
    return {"schema_version": 1, "state": "archives_sealed", "accepted": head["accepted"],
            "transaction_id": head["transaction_id"], "files": head["files"],
            "process_evidence": head["process_evidence"]}


def _completion(head, receipt_path):
    return {"schema_version": 1, "state": "cleared_for_continuation", "accepted": head["accepted"],
            "transaction_id": head["transaction_id"], "archive_receipt_sha256": _sha(receipt_path)}


def _verify_previous_continuation(run, head):
    archive = run / "accepted_work_deferrals" / head["transaction_id"]
    if archive.resolve() != archive or _json(archive / "receipt.json") != _continuation_receipt(head):
        raise PendingPublicationError("Previous continuation archive receipt differs")
    if not isinstance(head.get("files"), dict) or set(head["files"]) - {"outcome.json", "publication_request.json"}:
        raise PendingPublicationError("Unknown previous continuation archive member")
    for name, proof in head["files"].items():
        payload = _control_bytes(archive / name)
        if len(payload) != proof["size"] or hashlib.sha256(payload).hexdigest() != proof["sha256"]:
            raise PendingPublicationError("Previous continuation archive bytes differ")
    if _json(archive / "completion.json") != _completion(head, archive / "receipt.json"):
        raise PendingPublicationError("Previous continuation completion differs")


def prepare_resume(state_dir, process_evidence):
    """Archive deferral controls before resuming the same accepted run.

    The caller holds the owner lock, validates the pending DB/projection binding,
    and supplies a fresh result from former_processes_absent. This structured
    proof is trusted local evidence, not independent process verification here.
    All writes inherit the controller's admitted_file_writes scope. No worker
    evidence is replaced and no provider is contacted. Interrupted removals can
    resume only after every exact archive and its durable receipt verify.
    """
    _process_proof(process_evidence)
    value = inspect(state_dir)
    if value is None:
        raise PendingPublicationError("No accepted-work fence to resume")
    proof_run = _run(Path(state_dir), value["run_id"])
    run = control_dir(state_dir, value)
    _no_intent(run)
    _accepted_proofs(value, proof_run)
    head_path = run / "accepted_work_continuation.json"
    names = ("outcome.json", "publication_request.json")
    accepted = {key: value[key] for key in ("run_id", "request_sha256", "acceptance_sha256", "worker_result_sha256")}
    head = _json(head_path) if os.path.lexists(head_path) else None
    if head is not None:
        if (head.get("schema_version") != 1 or head.get("accepted") != accepted
                or head.get("state") not in {"archiving", "complete"}
                or not isinstance(head.get("transaction_id"), str)
                or re.fullmatch(r"[a-f0-9]{32}", head["transaction_id"]) is None):
            raise PendingPublicationError("Continuation receipt binding differs")
        if head["state"] == "complete":
            _verify_previous_continuation(run, head)
    if head is None or (head["state"] == "complete" and any(os.path.lexists(run / name) for name in names)):
        files = {}
        for name in names:
            source = run / name
            if os.path.lexists(source):
                old = _json(source)
                if old.get("run_id") != value["run_id"]:
                    raise PendingPublicationError("Continuation source belongs to another run")
                if name == "publication_request.json" and old.get("worker_acceptance_sha256") != value["acceptance_sha256"]:
                    raise PendingPublicationError("Continuation request acceptance binding differs")
                if name == "outcome.json" and old.get("publication_status") in {"published", "noop"}:
                    raise PendingPublicationError("Published outcome requires publication recovery")
                payload = _control_bytes(source)
                files[name] = {"sha256": hashlib.sha256(payload).hexdigest(), "size": len(payload)}
        head = {"schema_version": 1, "state": "archiving", "transaction_id": uuid.uuid4().hex,
                "accepted": accepted, "files": files, "process_evidence": process_evidence}
        atomic_write_text(head_path, json.dumps(head, sort_keys=True) + "\n")
    if not isinstance(head.get("files"), dict) or set(head["files"]) - set(names):
        raise PendingPublicationError("Unknown continuation archive member")
    if any(os.path.lexists(run / name) and name not in head["files"] for name in names):
        raise PendingPublicationError("Unbound continuation source appeared")
    archive = run / "accepted_work_deferrals" / head["transaction_id"]
    if archive.resolve() != archive:
        raise PendingPublicationError("Continuation archive path is aliased")
    receipt_path = archive / "receipt.json"
    expected = _continuation_receipt(head)
    sealed = _json(receipt_path) if os.path.lexists(receipt_path) else None
    if sealed is not None and sealed != expected:
        raise PendingPublicationError("Continuation archive receipt differs")
    for name, proof in head["files"].items():
        if (not isinstance(proof, dict) or set(proof) != {"sha256", "size"}
                or type(proof["size"]) is not int or not 0 < proof["size"] <= 16 * 1024**2
                or not isinstance(proof["sha256"], str) or re.fullmatch(r"[a-f0-9]{64}", proof["sha256"]) is None):
            raise PendingPublicationError("Invalid continuation archive proof")
        source, destination = run / name, archive / name
        if os.path.lexists(source):
            payload = _control_bytes(source)
            if len(payload) != proof["size"] or hashlib.sha256(payload).hexdigest() != proof["sha256"]:
                raise PendingPublicationError("Continuation source changed before removal")
        elif sealed is None:
            raise PendingPublicationError("Continuation source missing before durable archive receipt")
        else:
            payload = None
        if os.path.lexists(destination):
            archived = _control_bytes(destination)
            if len(archived) != proof["size"] or hashlib.sha256(archived).hexdigest() != proof["sha256"]:
                raise PendingPublicationError("Continuation archive bytes differ")
        else:
            if payload is None or sealed is not None:
                raise PendingPublicationError("Sealed continuation archive is missing")
            atomic_write_bytes(destination, payload)
            if destination.read_bytes() != payload:
                raise PendingPublicationError("Continuation archive read-back differs")
    if sealed is None:
        atomic_write_text(receipt_path, json.dumps(expected, sort_keys=True) + "\n")
        _sync_directory(archive)
        _sync_directory(archive.parent)
    # The receipt covers all archives before the first unlink, including a
    # crash between the two removals. Recheck proof/intent immediately before it.
    _accepted_proofs(value, proof_run)
    for name, proof in head["files"].items():
        _no_intent(run)
        source = run / name
        if os.path.lexists(source):
            payload = _control_bytes(source)
            if len(payload) != proof["size"] or hashlib.sha256(payload).hexdigest() != proof["sha256"]:
                raise PendingPublicationError("Continuation source changed before unlink")
            source.unlink()
            _sync_directory(run)
    completion_path = archive / "completion.json"
    completion = _completion(head, receipt_path)
    if os.path.lexists(completion_path):
        if _json(completion_path) != completion:
            raise PendingPublicationError("Continuation completion receipt differs")
    else:
        atomic_write_text(completion_path, json.dumps(completion, sort_keys=True) + "\n")
    if head["state"] != "complete":
        head["state"] = "complete"
        atomic_write_text(head_path, json.dumps(head, sort_keys=True) + "\n")
    return {"run_id": value["run_id"], "projection": value.get("projection"),
            "projection_target": value.get("projection_target", str(run / "publication_snapshot.sqlite3")),
            "continuation": str(head_path), "archive_receipt": str(receipt_path)}


def release_after_intent(state_dir, *, run_id):
    """The immutable publisher intent takes over ownership before unlink."""
    value = inspect(state_dir)
    if value is None or value["run_id"] != run_id:
        raise PendingPublicationError("Pending-work release identity differs")
    run = control_dir(state_dir, value)
    intent = _json(run / "publication_intent.json")
    if intent.get("run_id") != run_id or intent.get("request_sha256") != _sha(run / "publication_request.json"):
        raise PendingPublicationError("Durable publication intent is not bound to its request")
    marker_path(Path(state_dir)).unlink()
    fd = os.open(Path(state_dir), os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def record_selection(state_dir, selection):
    value = inspect(state_dir)
    if value is None:
        raise PendingPublicationError('Selection requires accepted-work fence')
    _no_intent(control_dir(state_dir, value))
    if value.get('selection') is not None and value['selection'] != selection:
        raise PendingPublicationError('Already admitted dependency selection differs')
    value['selection'] = selection
    atomic_write_text(marker_path(Path(state_dir)), json.dumps(value, sort_keys=True) + '\n')
    return value
