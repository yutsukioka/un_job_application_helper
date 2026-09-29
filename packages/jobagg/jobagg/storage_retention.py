"""Conservative retention for completed deterministic publication copies.

The worker database, HTTP captures, downloaded blobs, live databases/exports,
and publication plans/beforeimages are outside this module's deletion scope.
Only sealed per-run publication inputs and completed-generation export copies
can be retired. A durable receipt records the original bytes' hashes and sizes
before any unlink, so an interrupted retirement can be replayed safely.
"""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import stat
import tempfile
import time


RECEIPT = "storage-retention.json"
_SHA = re.compile(r"[0-9a-f]{64}\Z")
_GENERATION = re.compile(r"[0-9a-f]{32}\Z")
_EXPORT_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*_(?:current|history)\.(?:csv|json)\Z")
_COLD_HELPER = None


def _cold_helper():
    """Load the sibling helper when the standalone dispatcher lacks sys.path."""
    global _COLD_HELPER
    if _COLD_HELPER is None:
        path = Path(__file__).with_name("storage_cold_archive.py")
        descriptor, before = _safe_file(path)
        with os.fdopen(descriptor, "rb") as stream:
            source = stream.read()
            after = os.fstat(stream.fileno())
        latest = _regular(path)
        def identity(info):
            return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)
        if identity(before) != identity(after) or identity(after) != identity(latest):
            raise ValueError("Storage retention cold archive helper changed during read")
        spec = importlib.util.spec_from_file_location("jobagg_storage_cold_for_retention", path)
        if spec is None or spec.loader is None:
            raise ValueError("Storage retention cold archive helper is unavailable")
        module = importlib.util.module_from_spec(spec)
        # The dispatcher may lack jobagg on sys.path. Only the verified sibling
        # module bytes are executed; no user path or content reaches this call.
        exec(compile(source, str(path), "exec"), module.__dict__)  # noqa: S102  # nosec B102
        _COLD_HELPER = module
    return _COLD_HELPER


def _check_deadline(deadline_at):
    if deadline_at is not None and time.monotonic() >= deadline_at:
        raise TimeoutError("Storage retention deadline exhausted")


def _directory(path):
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode):
        raise ValueError(f"Storage retention directory is missing or a symlink: {path}")
    return path


def _regular(path):
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode):
        raise ValueError(f"Storage retention file is not regular: {path}")
    return info


def _safe_file(path):
    before = _regular(path)
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    opened = os.fstat(descriptor)
    if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
        opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns
    ):
        os.close(descriptor)
        raise ValueError(f"Storage retention file changed during open: {path}")
    return descriptor, before


def _digest(path, *, deadline_at=None):
    _check_deadline(deadline_at)
    descriptor, before = _safe_file(path)
    digest = hashlib.sha256()
    with os.fdopen(descriptor, "rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            _check_deadline(deadline_at)
            digest.update(block)
        after = os.fstat(stream.fileno())
    latest = _regular(path)
    def signature(value):
        return (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns)
    if signature(before) != signature(after) or signature(after) != signature(latest):
        raise ValueError(f"Storage retention file changed during hashing: {path}")
    return {"sha256": digest.hexdigest(), "size": after.st_size}


def _json(path):
    descriptor, _ = _safe_file(path)
    with os.fdopen(descriptor, "r", encoding="utf-8") as stream:
        value = json.load(stream)
    if not isinstance(value, dict):
        raise ValueError(f"Storage retention receipt is not an object: {path}")
    return value


def _sha(value):
    if not isinstance(value, str) or not _SHA.fullmatch(value):
        raise ValueError("Storage retention expected SHA-256 is invalid")
    return value


def _timestamp(value):
    if not isinstance(value, str):
        raise ValueError("Storage retention completion time is missing")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Storage retention completion time lacks timezone")
    return parsed.timestamp()


def _inside(base, relative):
    if not isinstance(relative, str) or not relative or Path(relative).is_absolute():
        raise ValueError("Storage retention evidence path must be relative")
    parts = Path(relative).parts
    if any(part in {"", ".", ".."} for part in parts):
        raise ValueError("Storage retention evidence path escapes its group")
    path = base.joinpath(*parts)
    for parent in [base, *list(path.parents)[:-1]]:
        if parent == base.parent:
            break
        if parent == base or parent.is_relative_to(base):
            _directory(parent)
    return path


def _same_path(value, expected):
    return isinstance(value, str) and Path(value) == expected


def _check_owner(owner_fd, shared_lock):
    if owner_fd is None or shared_lock is None:
        raise ValueError("Execution requires the inherited shared owner descriptor and lock path")
    shared_lock = Path(shared_lock)
    lock_stat = _regular(shared_lock)
    opened = os.fstat(owner_fd)
    if (lock_stat.st_dev, lock_stat.st_ino) != (opened.st_dev, opened.st_ino):
        raise ValueError("Storage retention owner descriptor differs from shared lock")
    with shared_lock.open("a+") as probe:
        try:
            fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            fcntl.flock(owner_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        else:
            fcntl.flock(probe, fcntl.LOCK_UN)
            raise ValueError("Storage retention requires a held exclusive shared owner")


def _sync_dir(path):
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _seal_json(path, value):
    """Create an intent without replacing an existing receipt."""
    _directory(path.parent)
    descriptor, temporary = tempfile.mkstemp(prefix=".storage-retention-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(value, stream, sort_keys=True, separators=(",", ":"))
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, path)
        _sync_dir(path.parent)
    finally:
        Path(temporary).unlink(missing_ok=True)


def _replace_json(path, value):
    descriptor, temporary = tempfile.mkstemp(prefix=".storage-retention-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(value, stream, sort_keys=True, separators=(",", ":"))
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        _sync_dir(path.parent)
    finally:
        Path(temporary).unlink(missing_ok=True)


def _run_publication(run, *, deadline_at=None):
    """Return (terminal, result, control paths), including verified recovery."""
    _check_deadline(deadline_at)
    outcome_path, request_path = run / "outcome.json", run / "publication_request.json"
    if not outcome_path.exists() or not request_path.exists():
        return False, None, []
    outcome, request = _json(outcome_path), _json(request_path)
    if outcome.get("run_id") != run.name or request.get("run_id") != run.name:
        raise ValueError(f"Storage retention run identity differs: {run}")
    if outcome.get("status") in {"complete", "incomplete"} and outcome.get(
        "publication_status"
    ) in {"published", "noop"} and outcome.get("publication_recovery_allowed") is False:
        result_path = run / "publication_result.json"
        result = _json(result_path)
        if _sha(outcome.get("publication_result_sha256")) != _digest(result_path, deadline_at=deadline_at)["sha256"]:
            raise ValueError(f"Storage retention publication result hash differs: {run}")
        if result.get("publication_request_sha256") != _digest(request_path, deadline_at=deadline_at)["sha256"]:
            raise ValueError(f"Storage retention publication request hash differs: {run}")
        if result.get("status") != outcome["publication_status"]:
            raise ValueError(f"Storage retention publication status differs: {run}")
        return True, result, [outcome_path, request_path, result_path]
    resolution_path = run / "publication_resolution.json"
    if not resolution_path.exists():
        return False, None, [outcome_path, request_path]
    resolution = _json(resolution_path)
    state_path = run / "state.json"
    final_state = _json(state_path)
    if (
        resolution.get("run_id") != run.name
        or resolution.get("previous_outcome_sha256") != _digest(outcome_path, deadline_at=deadline_at)["sha256"]
        or final_state.get("run_id") != run.name
        or final_state.get("status") not in {"complete", "incomplete"}
        or final_state.get("publication_status") != "published"
        or final_state.get("publication_recovery_allowed") is not False
    ):
        raise ValueError(f"Storage retention recovered run state differs: {run}")
    references = []
    for key in ("request", "result"):
        _check_deadline(deadline_at)
        binding = resolution.get(key)
        if not isinstance(binding, dict):
            raise ValueError(f"Storage retention recovery {key} binding missing: {run}")
        path = _inside(run, binding.get("path"))
        if _digest(path, deadline_at=deadline_at)["sha256"] != _sha(binding.get("sha256")):
            raise ValueError(f"Storage retention recovery {key} hash differs: {run}")
        references.append(path)
    recovery_request, result = map(_json, references)
    if (
        recovery_request.get("recover_publication") is not True
        or recovery_request.get("run_id") != run.name
        or result.get("run_id") != run.name
        or result.get("status") != "published"
        or result.get("publication_request_sha256") != _digest(references[0], deadline_at=deadline_at)["sha256"]
        or recovery_request.get("publication_snapshot") != request.get("publication_snapshot")
        or recovery_request.get("worker_database_files") != request.get("worker_database_files")
    ):
        raise ValueError(f"Storage retention recovery request/result differs: {run}")
    binding_path = run / "publication_generation_binding.json"
    binding = _json(binding_path)
    if recovery_request.get("recovery_generation_binding") != binding:
        raise ValueError(f"Storage retention recovery generation binding differs: {run}")
    publication = result.get("publication", {})
    generation_id = publication.get("generation_id")
    if not _GENERATION.fullmatch(str(generation_id or "")):
        raise ValueError(f"Storage retention recovery generation ID differs: {run}")
    generation_result_path = (
        run.parents[2] / "deterministic-live-publication" / "generations"
        / generation_id / "result.json"
    )
    if not generation_result_path.exists() and not generation_result_path.is_symlink():
        # A historical generation may have been moved by older tooling. Its
        # recovered run stays protected until an operator verifies that move.
        return False, result, [outcome_path, request_path, resolution_path, state_path,
                               *references, binding_path]
    generation_result = _json(generation_result_path)
    if (
        publication != generation_result
        or binding.get("generation_id") != generation_id
        or generation_result.get("state") != "complete"
        or generation_result.get("status") != "published"
        or generation_result.get("database_transactions_complete") is not True
    ):
        raise ValueError(f"Storage retention recovered publication generation differs: {run}")
    return True, result, [outcome_path, request_path, resolution_path, state_path,
                          *references, binding_path, generation_result_path]


def _run_group(run, *, deadline_at=None):
    _check_deadline(deadline_at)
    request_path = run / "publication_request.json"
    if not request_path.exists():
        return None, None
    request = _json(request_path)
    snapshot = request.get("publication_snapshot")
    if snapshot is None:
        return None, None
    expected = run / "publication_snapshot.sqlite3"
    acceptance_path = run / "acceptance.json"
    if (
        request.get("run_id") != run.name
        or not isinstance(snapshot, dict)
        or snapshot.get("schema_version") != 1
        or snapshot.get("kind") not in {"sqlite_backup", "sqlite_publication_projection"}
        or not _same_path(snapshot.get("path"), expected)
        or type(snapshot.get("size_bytes")) is not int
        or snapshot["size_bytes"] < 0
        or request.get("worker_database_files") != [
            {"path": str(expected), "sha256": snapshot.get("sha256")}
        ]
        or not _same_path(request.get("worker_acceptance_path"), acceptance_path)
        or snapshot.get("worker_acceptance_sha256") != request.get("worker_acceptance_sha256")
    ):
        raise ValueError(f"Storage retention snapshot binding differs: {run}")
    _sha(snapshot["sha256"])
    if _digest(acceptance_path, deadline_at=deadline_at)["sha256"] != _sha(request.get("worker_acceptance_sha256")):
        raise ValueError(f"Storage retention worker acceptance changed: {run}")
    terminal, result, controls = _run_publication(run, deadline_at=deadline_at)
    if not terminal:
        return None, result
    if result.get("publication_snapshot") != snapshot:
        raise ValueError(f"Storage retention result snapshot differs: {run}")
    publication = result.get("publication", {})
    if result["status"] == "published" and (
        publication.get("state") != "complete"
        or publication.get("status") != "published"
        or publication.get("database_transactions_complete") is not True
        or not _GENERATION.fullmatch(str(publication.get("generation_id", "")))
    ):
        raise ValueError(f"Storage retention run lacks completed publication: {run}")
    outcome = _json(run / "state.json") if (run / "publication_resolution.json").exists() else _json(run / "outcome.json")
    completed_at = outcome.get("finished_at") or outcome.get("started_at")
    record = {
        "kind": "run_snapshot", "id": run.name, "path": run,
        "time": _timestamp(completed_at),
        "files": [{"path": "publication_snapshot.sqlite3", "sha256": snapshot["sha256"],
                   "size": snapshot["size_bytes"]}],
        "controls": [acceptance_path, *controls],
        "generation_id": publication.get("generation_id"),
    }
    return record, result


def _generation_group(generation, *, deadline_at=None):
    _check_deadline(deadline_at)
    if not _GENERATION.fullmatch(generation.name):
        raise ValueError(f"Storage retention unrecognized generation directory: {generation}")
    result_path, plan_path = generation / "result.json", generation / "plan.json"
    if not result_path.exists():
        return None
    result = _json(result_path)
    # Plans can exceed 100 MB. Read and hash the hot or cold plan only for a
    # selected group. A cold manifest has already bound its original SHA.
    cold_path = generation / "storage-cold-archive.json"
    if cold_path.exists() or cold_path.is_symlink():
        if _cold_helper().archived_plan_binding(generation) != result.get("plan_sha256"):
            raise ValueError(f"Storage retention cold plan receipt differs: {generation}")
        plan_control = cold_path
    elif plan_path.exists() or plan_path.is_symlink():
        _regular(plan_path)
        plan_control = plan_path
    else:
        return None  # Historical generation lacks a verifiable plan.
    if (
        result.get("generation_id") != generation.name
        or result.get("state") != "complete"
        or result.get("status") != "published"
        or result.get("database_transactions_complete") is not True
        or not isinstance(result.get("plan_sha256"), str)
        or not _SHA.fullmatch(result["plan_sha256"])
    ):
        raise ValueError(f"Storage retention generation result/plan differs: {generation}")
    manifest_path, journal_path = generation / "exports.json", generation / "export-checkpoints.json"
    if not manifest_path.exists() or not journal_path.exists():
        return None  # Old generation without the recoverable export contract.
    manifest, journal = _json(manifest_path), _json(journal_path)
    entries = journal.get("entries")
    if (
        journal.get("schema_version") != 1
        or journal.get("generation_id") != generation.name
        or not isinstance(entries, dict)
        or set(entries) != set(manifest)
        or result.get("exports") != len(manifest)
    ):
        raise ValueError(f"Storage retention export receipts differ: {generation}")
    files = []
    for target, checkpoint in entries.items():
        _check_deadline(deadline_at)
        if not isinstance(checkpoint, dict) or checkpoint.get("phase") != "replaced_verified":
            raise ValueError(f"Storage retention export checkpoint incomplete: {generation}")
        if not isinstance(target, str) or Path(target).parent != generation.parents[2] / "output":
            raise ValueError(f"Storage retention export target is outside live output: {generation}")
        if not _EXPORT_NAME.fullmatch(Path(target).name):
            raise ValueError(f"Storage retention export target name is unrecognized: {generation}")
        prepared = checkpoint.get("prepared")
        expected_prepared = generation / "exports-v2" / Path(target).name
        if not _same_path(prepared, expected_prepared):
            return None  # Legacy preparation is kept intact.
        new = checkpoint.get("new")
        manifest_record = manifest.get(target)
        if (
            not isinstance(new, dict)
            or not isinstance(manifest_record, dict)
            or manifest_record.get("sha256") != new.get("sha256")
        ):
            raise ValueError(f"Storage retention prepared export binding differs: {generation}")
        files.append({"path": "exports-v2/" + expected_prepared.name,
                      "sha256": _sha(new.get("sha256")), "size": new.get("size")})
        backup = checkpoint.get("backup_path")
        if backup:
            old = checkpoint.get("old")
            expected_backup = generation / "before_exports" / Path(target).name
            if not _same_path(backup, expected_backup):
                return None  # Verified alternative or legacy candidate is preserved.
            if checkpoint.get("backup_status") != "verified" or not isinstance(old, dict):
                raise ValueError(f"Storage retention export backup is unverified: {generation}")
            files.append({"path": "before_exports/" + expected_backup.name,
                          "sha256": _sha(old.get("sha256")), "size": old.get("size")})
        if checkpoint.get("legacy_backup") or checkpoint.get("unverified_backup_candidates"):
            return None  # Do not retire uncertain legacy rollback evidence.
    if any(type(item["size"]) is not int or item["size"] < 0 for item in files):
        raise ValueError(f"Storage retention export size is invalid: {generation}")
    for dirname in ("exports-v2", "before_exports"):
        _check_deadline(deadline_at)
        directory = generation / dirname
        if directory.exists():
            _directory(directory)
            if any(path.is_symlink() for path in directory.iterdir()):
                raise ValueError(f"Storage retention export directory has a symlink: {directory}")
            actual = {str(path.relative_to(generation)) for path in directory.iterdir()}
            allowed = {item["path"] for item in files if item["path"].startswith(dirname + "/")}
            if actual != allowed and not ((generation / RECEIPT).exists() and actual <= allowed):
                return None  # Preserve a generation with unrecognized or interrupted files.
        elif any(item["path"].startswith(dirname + "/") for item in files):
            if not (generation / RECEIPT).exists():
                raise ValueError(f"Storage retention missing export directory: {directory}")
    if not files:
        return None
    if len({item["path"] for item in files}) != len(files):
        raise ValueError(f"Storage retention duplicate export copy path: {generation}")
    return {
        "kind": "generation_exports", "id": generation.name, "path": generation,
        "time": _timestamp(result.get("completed_at")), "files": files,
        "controls": [result_path, plan_control, manifest_path, journal_path],
    }


def _validate_selected(group, *, deadline_at=None):
    if group["kind"] != "generation_exports":
        return
    plan_path = group["path"] / "plan.json"
    result = _json(group["path"] / "result.json")
    cold_path = group["path"] / "storage-cold-archive.json"
    if cold_path.exists() or cold_path.is_symlink():
        cold = _cold_helper().verify_archived_generation(group["path"], deadline_at=deadline_at)
        if cold["objects"]["plan.json"]["sha256"] != result.get("plan_sha256"):
            raise ValueError(f"Storage retention selected cold plan differs: {group['path']}")
    else:
        plan = _json(plan_path)
        if (
            plan.get("generation_id") != group["id"]
            or not _same_path(plan.get("generation_root"), group["path"])
            or _digest(plan_path, deadline_at=deadline_at)["sha256"] != result.get("plan_sha256")
        ):
            raise ValueError(f"Storage retention selected generation plan differs: {group['path']}")


def _verify_controls(group, saved=None, *, deadline_at=None):
    controls = {}
    for path in group["controls"]:
        _check_deadline(deadline_at)
        controls[str(path)] = _digest(path, deadline_at=deadline_at)
    if saved is not None and controls != saved:
        raise ValueError(f"Storage retention control receipt changed: {group['path']}")
    return controls


def _validate_receipt(group, receipt):
    if (
        receipt.get("schema_version") != 1
        or receipt.get("kind") != group["kind"]
        or receipt.get("group_id") != group["id"]
        or receipt.get("phase") not in {"intent", "complete"}
        or receipt.get("files") != group["files"]
        or not isinstance(receipt.get("controls"), dict)
    ):
        raise ValueError(f"Storage retention receipt differs: {group['path']}")


def _retire(group, *, deadline_at=None):
    receipt_path = group["path"] / RECEIPT
    _check_deadline(deadline_at)
    _validate_selected(group, deadline_at=deadline_at)
    _check_deadline(deadline_at)
    signatures = None
    if receipt_path.exists() or receipt_path.is_symlink():
        receipt = _json(receipt_path)
        _validate_receipt(group, receipt)
        _verify_controls(group, receipt["controls"], deadline_at=deadline_at)
    else:
        controls = _verify_controls(group, deadline_at=deadline_at)
        signatures = {}
        for item in group["files"]:
            _check_deadline(deadline_at)
            path = _inside(group["path"], item["path"])
            if _digest(path, deadline_at=deadline_at) != {
                "sha256": item["sha256"], "size": item["size"]
            }:
                raise ValueError(f"Storage retention source bytes differ: {path}")
            stat_value = _regular(path)
            signatures[item["path"]] = (
                stat_value.st_dev, stat_value.st_ino, stat_value.st_size,
                stat_value.st_mtime_ns, stat_value.st_ctime_ns,
            )
        receipt = {
            "schema_version": 1, "kind": group["kind"], "group_id": group["id"],
            "phase": "intent", "files": group["files"], "controls": controls,
            "created_at": datetime.now().astimezone().isoformat(),
        }
        _seal_json(receipt_path, receipt)
    deleted = 0
    for item in group["files"]:
        _check_deadline(deadline_at)
        path = _inside(group["path"], item["path"])
        if not path.exists() and not path.is_symlink():
            # An absent file is allowed only after the durable intent exists.
            continue
        if receipt["phase"] == "complete":
            raise ValueError(f"Storage retention completed receipt has a live file: {path}")
        if signatures is not None:
            current = _regular(path)
            if (
                current.st_dev, current.st_ino, current.st_size,
                current.st_mtime_ns, current.st_ctime_ns,
            ) != signatures[item["path"]]:
                raise ValueError(f"Storage retention source changed before unlink: {path}")
        elif _digest(path, deadline_at=deadline_at) != {
            "sha256": item["sha256"], "size": item["size"]
        }:
            raise ValueError(f"Storage retention source changed before unlink: {path}")
        path.unlink()
        _sync_dir(path.parent)
        deleted += item["size"]
    if receipt["phase"] != "complete":
        _replace_json(receipt_path, {**receipt, "phase": "complete"})
    return deleted


def verified_retired_generation(generation):
    """Confirm a completed export retirement before another scanner skips it."""
    generation = Path(generation).absolute()
    receipt_path = generation / RECEIPT
    if not receipt_path.exists() and not receipt_path.is_symlink():
        return False
    group = _generation_group(generation)
    if group is None or group["kind"] != "generation_exports":
        raise ValueError(f"Storage retention receipt has no verified generation: {generation}")
    receipt = _json(receipt_path)
    _validate_receipt(group, receipt)
    if receipt["phase"] != "complete":
        raise ValueError(f"Storage retention export retirement is unfinished: {generation}")
    _verify_controls(group, receipt["controls"])
    _validate_selected(group)
    for item in group["files"]:
        path = _inside(generation, item["path"])
        if path.exists() or path.is_symlink():
            raise ValueError(f"Storage retention completed receipt has a live file: {path}")
    return True


def prune_completed(
    root, *, keep_completed=2, max_groups=1, execute=False, owner_fd=None,
    shared_lock=None, deadline_at=None, as_of=None,
):
    """Preview or retire a bounded number of verified derived artifact groups.

    ``root`` is the physical jobagg storage directory, containing the
    deterministic dispatcher, publication state, and live output directories.
    Preview does no hashing of large derivative files and never writes. Execute
    requires the same held exclusive owner as the dispatcher/publisher.
    Exact publication snapshots retain the newest two, the latest completed
    one per UTC day for 30 days, and the latest per UTC month for 12 months.
    """
    if type(keep_completed) is not int or keep_completed < 2:
        raise ValueError("Storage retention must keep at least two completed groups of each kind")
    if type(max_groups) is not int or max_groups < 1:
        raise ValueError("Storage retention max_groups must be positive")
    _check_deadline(deadline_at)
    if as_of is None:
        as_of = datetime.now(UTC)
    if not isinstance(as_of, datetime) or as_of.tzinfo is None:
        raise ValueError("Storage retention as_of must be a timezone-aware datetime")
    today = as_of.astimezone(UTC).date()
    root = _directory(Path(root).absolute())
    if execute:
        _check_owner(owner_fd, shared_lock)
    runs = _directory(root / "deterministic-dispatcher-proposal" / "runs")
    generations = _directory(root / "deterministic-live-publication" / "generations")
    output = _directory(root / "output")
    gate_path = output / ".jobagg-publication-state.json"
    current_generation = None
    if gate_path.exists() or gate_path.is_symlink():
        gate = _json(gate_path)
        if gate.get("state") not in {"complete", "publishing", "exporting"}:
            raise ValueError("Storage retention live publication gate is unrecognized")
        current_generation = gate.get("generation_id")
        if not _GENERATION.fullmatch(str(current_generation or "")):
            raise ValueError("Storage retention live publication gate generation is invalid")
        if (
            gate.get("state") != "complete"
            or gate.get("status") != "published"
            or gate.get("database_transactions_complete") is not True
        ):
            raise ValueError("Storage retention requires a complete published live gate")
    elif execute:
        raise ValueError("Storage retention requires a complete published live gate")

    _check_deadline(deadline_at)
    run_groups, protected_generations = [], set()
    for run in sorted(runs.iterdir()):
        _check_deadline(deadline_at)
        if run.name == ".DS_Store":
            _regular(run)
            continue
        _directory(run)
        group, result = _run_group(run, deadline_at=deadline_at)
        _check_deadline(deadline_at)
        if group is not None:
            run_groups.append(group)
        else:
            binding_path = run / "publication_generation_binding.json"
            if binding_path.exists() or binding_path.is_symlink():
                binding = _json(binding_path)
                generation_id = binding.get("generation_id")
                if not _GENERATION.fullmatch(str(generation_id or "")):
                    raise ValueError(f"Storage retention unresolved generation binding invalid: {run}")
                protected_generations.add(generation_id)
            if result and result.get("publication", {}).get("generation_id"):
                protected_generations.add(result["publication"]["generation_id"])
            result_path = run / "publication_result.json"
            if result_path.exists() or result_path.is_symlink():
                unfinished_result = _json(result_path)
                generation_id = unfinished_result.get("publication", {}).get("generation_id")
                if generation_id:
                    if not _GENERATION.fullmatch(str(generation_id)):
                        raise ValueError(f"Storage retention unresolved result generation invalid: {run}")
                    protected_generations.add(generation_id)
    generation_groups = []
    for generation in sorted(generations.iterdir()):
        _check_deadline(deadline_at)
        if generation.name == ".DS_Store":
            _regular(generation)
            continue
        _directory(generation)
        group = _generation_group(generation, deadline_at=deadline_at)
        _check_deadline(deadline_at)
        if group is not None:
            generation_groups.append(group)

    newest_runs = {group["id"] for group in sorted(
        run_groups, key=lambda item: item["time"], reverse=True
    )[:keep_completed]}
    checkpoint_reasons = {
        "newest_two_or_more": newest_runs,
        "daily_30_days": set(), "monthly_12_months": set(), "future_dated": set(),
    }
    latest_day, latest_month = {}, {}
    for group in run_groups:
        _check_deadline(deadline_at)
        completed = datetime.fromtimestamp(group["time"], UTC)
        day = completed.date()
        days_old = (today - day).days
        months_old = (today.year - day.year) * 12 + today.month - day.month
        if days_old < 0:
            checkpoint_reasons["future_dated"].add(group["id"])
        elif days_old < 30:
            current = latest_day.get(day)
            if current is None or group["time"] > current["time"]:
                latest_day[day] = group
        if 0 <= months_old < 12:
            month = (day.year, day.month)
            current = latest_month.get(month)
            if current is None or group["time"] > current["time"]:
                latest_month[month] = group
    checkpoint_reasons["daily_30_days"].update(group["id"] for group in latest_day.values())
    checkpoint_reasons["monthly_12_months"].update(
        group["id"] for group in latest_month.values()
    )
    protected_runs = set().union(*checkpoint_reasons.values())
    protected_generations.update(group["id"] for group in sorted(
        generation_groups, key=lambda item: item["time"], reverse=True
    )[:keep_completed])
    if current_generation:
        protected_generations.add(current_generation)
    candidates = []
    skipped_protected = {"run_snapshot": 0, "generation_exports": 0}
    for group in [*run_groups, *generation_groups]:
        _check_deadline(deadline_at)
        receipt_path = group["path"] / RECEIPT
        receipt = None
        if receipt_path.exists() or receipt_path.is_symlink():
            receipt = _json(receipt_path)
            _validate_receipt(group, receipt)
        if receipt and receipt["phase"] == "complete":
            if any((_inside(group["path"], item["path"]).exists() or
                    _inside(group["path"], item["path"]).is_symlink())
                   for item in group["files"]):
                raise ValueError(f"Storage retention completed receipt has remaining file: {group['path']}")
            continue
        protected = group["id"] in (
            protected_runs if group["kind"] == "run_snapshot" else protected_generations
        )
        if protected and not receipt:
            skipped_protected[group["kind"]] += 1
            continue
        candidates.append(group)
    # Replay intents first, then alternate classes so a bounded cycle can make
    # progress on both the run and generation backlogs.
    pending = sorted(
        (group for group in candidates if (group["path"] / RECEIPT).exists()),
        key=lambda group: (group["time"], group["id"]),
    )
    queues = {
        kind: sorted(
            (group for group in candidates if group["kind"] == kind
             and not (group["path"] / RECEIPT).exists()),
            key=lambda group: (group["time"], group["id"]),
        )
        for kind in ("run_snapshot", "generation_exports")
    }
    selected = pending[:max_groups]
    while len(selected) < max_groups and any(queues.values()):
        _check_deadline(deadline_at)
        for kind in ("run_snapshot", "generation_exports"):
            if len(selected) >= max_groups:
                break
            if queues[kind]:
                selected.append(queues[kind].pop(0))
    _check_deadline(deadline_at)
    described = [{
        "kind": group["kind"], "id": group["id"],
        "path": str(group["path"]), "files": len(group["files"]),
        "estimated_bytes": sum(item["size"] for item in group["files"]),
        "replaying_intent": (group["path"] / RECEIPT).exists(),
    } for group in selected]
    summary = {
        "schema_version": 1, "mode": "execute" if execute else "preview",
        "eligible_groups": len(candidates),
        "eligible_by_kind": {
            kind: {
                "groups": sum(group["kind"] == kind for group in candidates),
                "estimated_bytes": sum(
                    item["size"] for group in candidates if group["kind"] == kind
                    for item in group["files"]
                ),
            }
            for kind in ("run_snapshot", "generation_exports")
        },
        "skipped_protected": skipped_protected,
        "protected_run_checkpoints": {
            reason: len(ids) for reason, ids in checkpoint_reasons.items()
        },
        "eligible_estimated_bytes": sum(
            item["size"] for group in candidates for item in group["files"]
        ),
        "selected": described,
        "selected_estimated_bytes": sum(item["estimated_bytes"] for item in described),
        "deleted_bytes": 0,
        "removed_groups": 0,
        "deadline_deferred": False,
    }
    if execute:
        for group in selected:
            try:
                _check_deadline(deadline_at)
                summary["deleted_bytes"] += _retire(group, deadline_at=deadline_at)
                summary["removed_groups"] += 1
            except TimeoutError:
                summary["deadline_deferred"] = True
                break
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--shared-lock", type=Path)
    parser.add_argument("--keep-completed", type=int, default=2)
    parser.add_argument("--max-groups", type=int, default=1)
    parser.add_argument("--max-seconds", type=float)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args(argv)
    deadline = time.monotonic() + args.max_seconds if args.max_seconds else None
    if args.execute:
        lock = args.shared_lock or args.root / "remediation" / "manual-fetch-owner.lock"
        _regular(lock)
        with lock.open("a+") as owner:
            try:
                fcntl.flock(owner, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                print(json.dumps({"schema_version": 1, "mode": "execute", "owner_busy": True}))
                return 2
            result = prune_completed(
                args.root, keep_completed=args.keep_completed,
                max_groups=args.max_groups, execute=True,
                owner_fd=owner.fileno(), shared_lock=lock, deadline_at=deadline,
            )
    else:
        result = prune_completed(args.root, keep_completed=args.keep_completed,
                                 max_groups=args.max_groups, deadline_at=deadline)
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
