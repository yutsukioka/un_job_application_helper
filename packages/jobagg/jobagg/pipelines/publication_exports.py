"""Recoverable export replacement under the caller's exclusive writer owner.

The legacy exports.json remains the receipt manifest. This module adds a journal
that binds old/new bytes before touching an export. It does not prune, relocate,
or certify unverifiable legacy rollback evidence.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
import sqlite3
import sys
import tempfile
from pathlib import Path

from jobagg.atomic_files import atomic_write_text
from jobagg.db import JobDatabase
from jobagg.pipelines.exports import export_jobs
from jobagg.publication_deadline import expired
from jobagg.verification_budget import VerificationBudgetExceeded, check, scope
from jobagg.publication_storage import charge, ensure_directory
from jobagg.retained_artifacts import archived_fingerprint, open_artifact

JOURNAL_NAME = "export-checkpoints.json"


def _save(path, value):
    payload = json.dumps(value, sort_keys=True, ensure_ascii=True) + "\n"
    charge(path, len(payload.encode()) + 4096)
    atomic_write_text(path, payload)
    _sync_directory(Path(path).parent)


def _sync_directory(path):
    if os.name == "nt":
        return
    # A durability checkpoint must fail if directory synchronization fails;
    # silently ignoring an open error would overstate a completed checkpoint.
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _fingerprint(path):
    path = Path(path)
    try:
        before = path.lstat()
    except FileNotFoundError:
        return archived_fingerprint(path)
    if not stat.S_ISREG(before.st_mode):
        raise ValueError(f"export_evidence_not_regular_file:{path}")
    digest = hashlib.sha256()
    with path.open("rb") as source:
        opened = os.fstat(source.fileno())
        if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
            raise ValueError(f"export_evidence_identity_changed:{path}")
        for block in iter(lambda: source.read(1024 * 1024), b""):
            check()
            digest.update(block)
    after = path.lstat()
    if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
    ):
        raise ValueError(f"export_evidence_changed_during_hash:{path}")
    return {"sha256": digest.hexdigest(), "size": after.st_size}


def _require(path, expected, reason):
    actual = _fingerprint(path)
    if actual is None or actual != expected:
        raise ValueError(f"{reason}:{path}")
    return actual


def _fault(fault, stage, key):
    if fault:
        fault(stage, key)


def _clone_file(source, target):
    """Create a distinct copy-on-write inode, or request the portable fallback.

    The destination must not exist. macOS clones share data extents, never a
    mutable inode: later writes to a live export cannot change sealed evidence.
    Only unavailable cloning permits fallback; capacity/permission/I/O failures
    must retain the normal publication hold rather than retry a larger copy.
    """
    if sys.platform != "darwin":
        return False
    import ctypes
    import errno

    clone = getattr(ctypes.CDLL(None, use_errno=True), "fclonefileat", None)
    if clone is None:
        return False
    clone.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_char_p, ctypes.c_int]
    clone.restype = ctypes.c_int
    try:
        source_fd = os.open(source, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        # Retained legacy evidence may exist only through open_artifact().
        return False
    try:
        before = os.fstat(source_fd)
        if not stat.S_ISREG(before.st_mode):
            raise ValueError(f"export_clone_source_not_regular_file:{source}")
        directory_fd = os.open(Path(target).parent, os.O_RDONLY)
        try:
            result = clone(source_fd, directory_fd, os.fsencode(Path(target).name), 0)
            error = ctypes.get_errno() if result != 0 else 0
        finally:
            os.close(directory_fd)
        if result != 0:
            if error in {errno.ENOTSUP, errno.EOPNOTSUPP, errno.ENOSYS, errno.EXDEV}:
                return False
            raise OSError(error, os.strerror(error), str(target))
        after = os.fstat(source_fd)
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            raise ValueError(f"export_clone_source_changed:{source}")
        return True
    finally:
        os.close(source_fd)


def _copy_sealed(source, target, expected, *, stage, key, target_before=None, fault=None):
    """Never use existence as evidence that a copy completed.

    A partial temporary file cannot replace the sealed path. The original source
    and any previously sealed target remain untouched until verification/fsync.
    """
    source, target = Path(source), Path(target)
    charge(target, expected["size"] + 4096)
    ensure_directory(target.parent)
    _require(source, expected, "export_copy_source_changed")
    # Cloning requires an absent destination. A private staging directory keeps
    # that name exclusive without unlinking/reusing a reserved temporary path.
    with tempfile.TemporaryDirectory(prefix="." + target.name + ".", dir=target.parent) as staging:
        temporary = Path(staging) / "copy"
        if _clone_file(source, temporary):
            _fault(fault, "during_" + stage + "_copy", key)
            with temporary.open("rb") as output:
                os.fchmod(output.fileno(), 0o600)
                os.fsync(output.fileno())
        else:
            digest = hashlib.sha256()
            size = 0
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as output, open_artifact(source) as input_file:
                for block in iter(lambda: input_file.read(1024 * 1024), b""):
                    check()
                    output.write(block)
                    digest.update(block)
                    size += len(block)
                    _fault(fault, "during_" + stage + "_copy", key)
                output.flush()
                os.fsync(output.fileno())
            if {"sha256": digest.hexdigest(), "size": size} != expected:
                raise ValueError("export_copy_stream_hash_mismatch")
        _require(temporary, expected, "export_copy_readback_failed")
        _fault(fault, "before_" + stage + "_seal", key)
        if _fingerprint(target) != target_before:
            raise ValueError("export_target_changed_during_copy")
        os.replace(temporary, target)
        _sync_directory(target.parent)
        _fault(fault, "after_" + stage + "_seal", key)
        _require(target, expected, "export_sealed_copy_readback_failed")


class _ExportStream:
    def __init__(self, stream, maximum_bytes, *, translate_newlines=False):
        self.stream, self.maximum_bytes, self.size = stream, maximum_bytes, 0
        self.translate_newlines = translate_newlines

    def write(self, value):
        check()
        count = len(
            (value.replace("\n", os.linesep) if self.translate_newlines else value).encode("utf-8")
        )
        if self.maximum_bytes is not None and self.size + count > self.maximum_bytes:
            raise ValueError("bootstrap_export_byte_limit")
        self.size += count
        return self.stream.write(value)

    def flush(self):
        check()
        return self.stream.flush()

    def record_boundary(self):
        check()
        return getattr(self.stream, "record_boundary", lambda: None)()


def _export(item, stream, maximum_bytes):
    db = JobDatabase(item["database"], read_only=True)
    try:
        with db.connection_scope() as conn:
            conn.set_progress_handler(lambda: int(expired(None)), 1000)
            export_jobs(
                db,
                output_path=item["path"],
                output_format=item["format"],
                status=item["status"],
                history_only=item["history_only"],
                output_stream=_ExportStream(
                    stream, maximum_bytes, translate_newlines=item["format"] == "json"
                ),
            )
    except sqlite3.OperationalError as error:
        if str(error) == "interrupted" and expired(None):
            raise VerificationBudgetExceeded("publication export SQL deadline") from error
        raise
    check()


def _render(item, root, *, fault=None, content_addressed=False, consume=None, maximum_bytes=None):
    # A separate directory avoids replacing unjournaled legacy preparation.
    prepared = root / "exports-v2" / Path(item["path"]).name
    ensure_directory(prepared.parent)
    if content_addressed:
        from jobagg.publication_artifacts import RecordWriter

        writer = RecordWriter(
            prepared, translate_newlines=item["format"] == "json", consume=consume
        )
        _export(item, writer, maximum_bytes)
        _fault(fault, "before_export_prepared_seal", item["path"])
        return str(prepared), writer.seal()
    from jobagg.publication_storage import active

    if active():
        raise ValueError("admitted_publication_requires_record_chunk_exports")
    fd, temporary = tempfile.mkstemp(prefix="." + prepared.name + ".", dir=prepared.parent)
    os.close(fd)
    try:
        with open(
            temporary, "w", encoding="utf-8", newline="" if item["format"] == "csv" else None
        ) as stream:
            _export(item, stream, maximum_bytes)
        with open(temporary, "rb") as handle:
            os.fsync(handle.fileno())
        expected = _fingerprint(temporary)
        _fault(fault, "before_export_prepared_seal", item["path"])
        os.replace(temporary, prepared)
        _sync_directory(prepared.parent)
        return str(prepared), expected
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _prior_export_hashes(plan):
    """Known historical hashes are comparison evidence, not a preimage binding.

    Legacy generations did not record the old hash before replacement. Even a
    matching historical artifact cannot retroactively prove it was that exact
    export's preimage. Preserve this distinction in every imported checkpoint.
    """
    result = {}
    root = Path(plan["generation_root"])
    for directory in sorted(root.parent.iterdir()):
        if directory == root or not directory.is_dir() or directory.is_symlink():
            continue
        try:
            completion = json.loads((directory / "result.json").read_text())
            if completion.get("state") != "complete" or completion.get("status") != "published":
                continue
            if completion.get("completed_at", "") >= plan.get("created_at", ""):
                continue
            receipts = json.loads((directory / "exports.json").read_text())
        except (OSError, ValueError, TypeError):
            continue
        for path, receipt in receipts.items():
            if isinstance(receipt, dict) and receipt.get("sha256"):
                result.setdefault(path, set()).add(receipt["sha256"])
    return result


def _legacy_backup(plan, item, prior_hashes):
    path = Path(plan["generation_root"]) / "before_exports" / Path(item["path"]).name
    evidence = _fingerprint(path)
    if evidence is None:
        return {"status": "legacy_backup_absent", "preimage_certified": False}
    return {
        "path": str(path),
        **evidence,
        "status": (
            "matches_historical_export"
            if evidence["sha256"] in prior_hashes.get(item["path"], set())
            else "unverified_legacy_backup"
        ),
        "preimage_certified": False,
    }


def _initialize_checkpoint(plan, item, receipt, prior_hashes, *, fault=None):
    root, target = Path(plan["generation_root"]), Path(item["path"])
    old = _fingerprint(target)
    if receipt is not None:
        if any(receipt.get(key) != value for key, value in item.items()):
            raise ValueError("legacy_export_receipt_specification_changed")
        # The saved receipt binds these prepared bytes. Do not rerender legacy
        # output under a newer exporter and quietly change its expected digest.
        prepared = receipt["prepared"]
        new = _fingerprint(prepared)
        if new is None or new["sha256"] != receipt["sha256"]:
            raise ValueError("legacy_prepared_export_hash_changed")
        if old is not None and old != new:
            raise ValueError("legacy_receipted_export_target_conflict")
        return {
            "item": item,
            "prepared": prepared,
            "new": new,
            "old": None,
            "old_state_known": False,
            "legacy_receipt": True,
            "legacy_backup": _legacy_backup(plan, item, prior_hashes),
            "phase": "replaced_verified" if old == new else "replace_intent",
        }
    content_addressed = (
        plan.get("publication_policy", {}).get("export_storage") == "record_chunks_v1"
    )
    prepared, new = _render(
        item,
        root,
        fault=fault,
        content_addressed=content_addressed,
        consume=charge,
        maximum_bytes=plan.get("bootstrap_max_export_bytes"),
    )
    checkpoint = {
        "item": item,
        "prepared": prepared,
        "new": new,
        "old": old,
        "old_state_known": True,
        "legacy_receipt": False,
        "phase": "prepared_verified",
        "content_addressed": content_addressed,
    }
    legacy_path = root / "before_exports" / target.name
    if legacy_path.exists() or legacy_path.is_symlink():
        checkpoint["legacy_backup"] = _legacy_backup(plan, item, prior_hashes)
    return checkpoint


def _backup(checkpoint, root, *, fault=None):
    old, new = checkpoint["old"], checkpoint["new"]
    target = Path(checkpoint["item"]["path"])
    if not checkpoint["old_state_known"]:
        # Imported legacy receipts retain explicit uncertainty, never a made-up
        # original hash. Their current export can only be restored from bound
        # prepared bytes if absent, or recognized if already matching.
        return
    if old is None or old == new:
        checkpoint["backup_status"] = "not_required"
        return
    backup = Path(checkpoint.get("backup_path") or root / "before_exports" / target.name)
    found = _fingerprint(backup)
    if found is not None and found != old:
        # Preserve the unknown/partial file in place. A separately named verified
        # backup can be made only while the target still supplies the bound old
        # bytes. This is also safe for the interrupted legacy generation.
        checkpoint.setdefault("unverified_backup_candidates", []).append(
            {"path": str(backup), **found}
        )
        backup = root / "before_exports_verified" / old["sha256"] / target.name
        found = _fingerprint(backup)
        if found is not None and found != old:
            raise ValueError("verified_export_backup_hash_changed")
    if found is None:
        _require(target, old, "export_original_unavailable_for_backup")
        if checkpoint.get("content_addressed"):
            from jobagg.publication_artifacts import capture

            candidates = []
            for generation in sorted(root.parent.iterdir(), reverse=True):
                if generation.is_dir() and not generation.is_symlink() and generation != root:
                    candidates.append(generation / "exports-v2" / target.name)
            _fault(fault, "during_export_backup_copy", str(target))
            _fault(fault, "before_export_backup_seal", str(target))
            capture(target, backup, old, candidates=candidates, consume=charge)
            _fault(fault, "after_export_backup_seal", str(target))
        else:
            _copy_sealed(target, backup, old, stage="export_backup", key=str(target), fault=fault)
    _require(backup, old, "export_backup_readback_failed")
    checkpoint["backup_path"] = str(backup)
    checkpoint["backup_status"] = "verified"


def _validate_checkpoint(checkpoint, item, root):
    if checkpoint.get("item") != item:
        raise ValueError("export_checkpoint_specification_changed")
    if checkpoint.get("phase") not in {
        "prepared_verified",
        "backup_verified",
        "replace_intent",
        "replaced_verified",
    }:
        raise ValueError("export_checkpoint_phase_invalid")
    prepared = Path(checkpoint["prepared"])
    if not prepared.resolve().is_relative_to(root.resolve()):
        raise ValueError("export_prepared_path_outside_generation")
    _require(prepared, checkpoint["new"], "prepared_export_hash_changed")
    backup_path = checkpoint.get("backup_path")
    if backup_path:
        if not Path(backup_path).resolve().is_relative_to(root.resolve()):
            raise ValueError("export_backup_path_outside_generation")
        _require(backup_path, checkpoint["old"], "verified_export_backup_hash_changed")


def publish_exports(plan, items, *, deadline_at=None, fault=None):
    """Return durable progress; caller keeps the API gate closed until complete."""
    try:
        with scope(deadline_at, check_after=False):
            result = _publish_exports(plan, items, deadline_at=deadline_at, fault=fault)
            if result["complete"]:
                check()
            return result
    except VerificationBudgetExceeded:
        # A selected export may have replaced bytes before expiry. Keep its
        # exact journal/escrow for recovery; never report a provider failure or
        # manufacture a count from an expired partial pass.
        return {"complete": False, "exports": None, "reason": "deadline_during_export"}


def _publish_exports(plan, items, *, deadline_at=None, fault=None):
    root = Path(plan["generation_root"])
    manifest_path, journal_path = root / "exports.json", root / JOURNAL_NAME
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
    journal = (
        json.loads(journal_path.read_text())
        if journal_path.exists()
        else {"schema_version": 1, "generation_id": plan["generation_id"], "entries": {}}
    )
    if journal.get("schema_version") != 1 or journal.get("generation_id") != plan["generation_id"]:
        raise ValueError("export_checkpoint_generation_mismatch")
    items = list(items)
    keys = {item["path"] for item in items}
    if len(keys) != len(items) or set(manifest) - keys or set(journal["entries"]) - keys:
        raise ValueError("unexpected_export_receipt_or_checkpoint")
    prior_hashes = None
    for item in items:
        key = item["path"]
        if expired(deadline_at):
            return {"complete": False, "exports": len(manifest), "reason": "deadline_before_export"}
        checkpoint = journal["entries"].get(key)
        if checkpoint is None:
            if prior_hashes is None:
                prior_hashes = _prior_export_hashes(plan)
            checkpoint = _initialize_checkpoint(
                plan, item, manifest.get(key), prior_hashes, fault=fault
            )
            journal["entries"][key] = checkpoint
            _save(journal_path, journal)
            _fault(fault, "after_export_prepare_checkpoint", key)
        _validate_checkpoint(checkpoint, item, root)
        current = _fingerprint(key)
        new, old = checkpoint["new"], checkpoint["old"]
        if current == new:
            # Rename may have completed before the journal or manifest write.
            # Original preimage evidence remains required for new replacements.
            if checkpoint["old_state_known"] and old is not None and old != new:
                if not checkpoint.get("backup_path"):
                    raise ValueError("already_replaced_export_lacks_verified_backup")
                _require(checkpoint["backup_path"], old, "export_backup_readback_failed")
        else:
            if current != old:
                # A missing target after a recorded replace intent is repairable
                # from sealed bytes, but unrelated new content must never vanish.
                if current is not None or checkpoint["phase"] not in {
                    "replace_intent",
                    "replaced_verified",
                }:
                    raise ValueError("export_target_preimage_conflict")
            _backup(checkpoint, root, fault=fault)
            checkpoint["phase"] = "backup_verified"
            _save(journal_path, journal)
            _fault(fault, "after_export_backup_checkpoint", key)
            checkpoint["phase"] = "replace_intent"
            _save(journal_path, journal)
            _fault(fault, "after_export_replace_intent", key)
            observed = _fingerprint(key)
            if observed != current:
                raise ValueError("export_target_changed_before_replace")
            _copy_sealed(
                checkpoint["prepared"],
                key,
                new,
                stage="export_target",
                key=key,
                target_before=current,
                fault=fault,
            )
        checkpoint["phase"] = "replaced_verified"
        _save(journal_path, journal)
        _fault(fault, "after_export_verified_checkpoint", key)
        receipt = {**item, "sha256": new["sha256"], "prepared": checkpoint["prepared"]}
        if key in manifest and manifest[key] != receipt:
            raise ValueError("export_receipt_conflicts_with_checkpoint")
        if key not in manifest:
            manifest[key] = receipt
            _save(manifest_path, manifest)
            _fault(fault, "after_export_replace", key)
    warnings = [
        {"path": key, "evidence": checkpoint["legacy_backup"]}
        for key, checkpoint in journal["entries"].items()
        if "legacy_backup" in checkpoint
    ]
    warnings.extend(
        {"path": key, "unverified_backup_candidates": checkpoint["unverified_backup_candidates"]}
        for key, checkpoint in journal["entries"].items()
        if checkpoint.get("unverified_backup_candidates")
    )
    return {"complete": True, "exports": len(manifest), "legacy_rollback_evidence": warnings}
