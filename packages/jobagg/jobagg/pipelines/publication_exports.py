"""Recoverable export replacement under the caller's exclusive writer owner.

The legacy exports.json remains the receipt manifest. This module adds a journal
that binds old/new bytes before touching an export. It does not prune, relocate,
or certify unverifiable legacy rollback evidence.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import stat
import tempfile
import time

from jobagg.publication_deadline import expired
from jobagg.atomic_files import atomic_write_text
from jobagg.db import JobDatabase
from jobagg.pipelines.exports import export_jobs
from jobagg.retained_artifacts import archived_fingerprint, open_artifact


JOURNAL_NAME = "export-checkpoints.json"


def _save(path, value):
    atomic_write_text(path, json.dumps(value, sort_keys=True, ensure_ascii=True) + "\n")
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
            digest.update(block)
    after = path.lstat()
    if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
        after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns
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


def _copy_sealed(source, target, expected, *, stage, key, target_before=None, fault=None):
    """Never use existence as evidence that a copy completed.

    A partial temporary file cannot replace the sealed path. The original source
    and any previously sealed target remain untouched until verification/fsync.
    """
    source, target = Path(source), Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    _require(source, expected, "export_copy_source_changed")
    fd, temporary = tempfile.mkstemp(prefix="." + target.name + ".", dir=target.parent)
    try:
        digest = hashlib.sha256()
        size = 0
        with os.fdopen(fd, "wb") as output, open_artifact(source) as input_file:
            for block in iter(lambda: input_file.read(1024 * 1024), b""):
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
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _render(item, root, *, fault=None):
    # A separate directory avoids replacing unjournaled legacy preparation.
    prepared = root / "exports-v2" / Path(item["path"]).name
    prepared.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix="." + prepared.name + ".", dir=prepared.parent)
    os.close(fd)
    try:
        export_jobs(
            JobDatabase(item["database"], read_only=True),
            output_path=temporary,
            output_format=item["format"],
            status=item["status"],
            history_only=item["history_only"],
        )
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
    prepared, new = _render(item, root, fault=fault)
    checkpoint = {
        "item": item,
        "prepared": prepared,
        "new": new,
        "old": old,
        "old_state_known": True,
        "legacy_receipt": False,
        "phase": "prepared_verified",
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
        _copy_sealed(target, backup, old, stage="export_backup", key=str(target), fault=fault)
    _require(backup, old, "export_backup_readback_failed")
    checkpoint["backup_path"] = str(backup)
    checkpoint["backup_status"] = "verified"


def _validate_checkpoint(checkpoint, item, root):
    if checkpoint.get("item") != item:
        raise ValueError("export_checkpoint_specification_changed")
    if checkpoint.get("phase") not in {
        "prepared_verified", "backup_verified", "replace_intent", "replaced_verified"
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
                    "replace_intent", "replaced_verified"
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
                checkpoint["prepared"], key, new, stage="export_target", key=key,
                target_before=current, fault=fault
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
