"""Lossless, content-addressed retention for historical publication exports.

Raw captures, database before-images and snapshots are never candidates. Readers
verify original bytes through tombstones; unresolved/current generations remain
fully materialized. No archive object is garbage-collected by this module.
"""

from __future__ import annotations

from contextlib import contextmanager
import argparse
from datetime import datetime, timezone
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import tempfile
import time
import zlib

from jobagg.atomic_files import atomic_write_text


DIRECTORIES = {"exports-v2", "before_exports", "before_exports_verified"}


def _direct(path):
    path = Path(path).absolute()
    if path.resolve() != path or (path.exists() and not stat.S_ISREG(path.lstat().st_mode)):
        raise ValueError("retention_requires_direct_regular_file")
    return path


def _layout(path):
    path = _direct(path)
    for generation in path.parents:
        if generation.parent.name == "generations":
            relative = path.relative_to(generation)
            if len(relative.parts) < 2 or relative.parts[0] not in DIRECTORIES:
                break
            return generation, generation.parent.parent / "retained-blobs"
    raise ValueError("retention_path_outside_historical_exports")


def tombstone(path):
    return Path(str(path) + ".retained.json")


def _receipt(path):
    generation, store = _layout(path)
    receipt = json.loads(_direct(tombstone(path)).read_text())
    if (
        receipt.get("schema_version") != 1
        or receipt.get("original_path") != str(Path(path).absolute())
        or not re.fullmatch("[0-9a-f]{64}", str(receipt.get("sha256", "")))
        or type(receipt.get("size")) is not int
        or receipt["size"] < 0
    ):
        raise ValueError("invalid_retention_receipt")
    return receipt, _direct(store / (receipt["sha256"] + ".gz"))


def _stream_hash(stream):
    digest, size = hashlib.sha256(), 0
    for block in iter(lambda: stream.read(1024 * 1024), b""):
        digest.update(block)
        size += len(block)
    return {"sha256": digest.hexdigest(), "size": size}


def _verify_blob(blob, expected):
    """Treat decoding errors and changed bytes as the same integrity failure."""
    try:
        with gzip.open(blob, "rb") as stream:
            actual = _stream_hash(stream)
    except (EOFError, gzip.BadGzipFile, zlib.error) as exc:
        raise ValueError("retained_artifact_hash_changed") from exc
    if actual != expected:
        raise ValueError("retained_artifact_hash_changed")


@contextmanager
def open_artifact(path):
    """Read original bytes, even after lossless archival; no materializing write."""
    path = _direct(path)
    if path.exists():
        with path.open("rb") as stream:
            yield stream
        return
    receipt, blob = _receipt(path)
    # Validate before allowing a recovery copy to consume any bytes.
    _verify_blob(blob, {k: receipt[k] for k in ("sha256", "size")})
    with gzip.open(blob, "rb") as stream:
        yield stream


def archived_fingerprint(path):
    if not tombstone(path).exists():
        return None
    with open_artifact(path) as stream:
        return _stream_hash(stream)


def _sync(path):
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def archive(path, expected):
    """Caller holds the publication shared owner and has checked eligibility."""
    path = _direct(path)
    _, store = _layout(path)
    if not path.exists():
        if archived_fingerprint(path) != expected:
            raise ValueError("archived_export_differs")
        return
    from jobagg.pipelines.publication_exports import _fingerprint

    if _fingerprint(path) != expected:
        raise ValueError("retention_candidate_changed")
    store.mkdir(exist_ok=True)
    blob = _direct(store / (expected["sha256"] + ".gz"))
    if not blob.exists():
        fd, temporary = tempfile.mkstemp(prefix=".archive-", dir=store)
        try:
            with os.fdopen(fd, "wb") as output, path.open("rb") as source:
                with gzip.GzipFile(filename="", mode="wb", fileobj=output, mtime=0) as compressed:
                    for block in iter(lambda: source.read(1024 * 1024), b""):
                        compressed.write(block)
                output.flush()
                os.fsync(output.fileno())
            with gzip.open(temporary, "rb") as stream:
                if _stream_hash(stream) != expected:
                    raise ValueError("archive_roundtrip_failed")
            os.replace(temporary, blob)
            _sync(store)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
    with gzip.open(blob, "rb") as stream:
        if _stream_hash(stream) != expected:
            raise ValueError("archive_object_changed")
    receipt = {
        "schema_version": 1,
        "original_path": str(path),
        **expected,
        "encoding": "gzip",
        "retained_at": datetime.now(timezone.utc).isoformat(),
    }
    atomic_write_text(tombstone(path), json.dumps(receipt, sort_keys=True) + "\n")
    _sync(path.parent)
    if _fingerprint(path) != expected:
        raise ValueError("retention_original_changed_before_retirement")
    path.unlink()
    _sync(path.parent)


def candidates(state_dir, output_dir, *, retain_days=7, now=None, max_files=100):
    """Select historical originals and verify every archived blob encountered.

    ``max_files`` limits originals selected, not historical verification I/O.
    Reaching that limit ends the scan; this is not a whole-store integrity audit.
    """
    if not isinstance(retain_days, int) or retain_days < 1:
        raise ValueError("retain_days_must_be_positive")
    from jobagg.pipelines.publication_exports import _fingerprint

    state_dir, output_dir = Path(state_dir).absolute(), Path(output_dir).absolute()
    if state_dir.resolve() != state_dir or output_dir.resolve() != output_dir:
        raise ValueError("retention_roots_must_be_direct")
    gate = json.loads(_direct(output_dir / ".jobagg-publication-state.json").read_text())
    if gate.get("state") != "complete":
        raise ValueError("unresolved_publication_prevents_retention")
    cutoff = (time.time() if now is None else now) - retain_days * 86400
    found = []
    verified = {}
    for root in sorted((state_dir / "generations").iterdir()):
        if root.is_symlink() or not root.is_dir() or root.name == gate.get("generation_id"):
            continue
        result_path = root / "result.json"
        if not result_path.exists():
            continue
        result = json.loads(_direct(result_path).read_text())
        if result.get("state") != "complete" or result.get("status") != "published":
            continue
        completed = datetime.fromisoformat(result["completed_at"].replace("Z", "+00:00"))
        if completed.tzinfo is None or completed.timestamp() > cutoff:
            continue
        plan_path = _direct(root / "plan.json")
        if (
            result.get("generation_id") != root.name
            or result.get("plan_path") != str(plan_path)
            or hashlib.sha256(plan_path.read_bytes()).hexdigest() != result.get("plan_sha256")
        ):
            raise ValueError("retention_generation_binding_changed")
        journal_path = root / "export-checkpoints.json"
        if not journal_path.exists():  # Unverifiable legacy generations stay intact.
            continue
        journal = json.loads(_direct(journal_path).read_text())
        receipts = json.loads(_direct(root / "exports.json").read_text())
        if (
            journal.get("generation_id") != root.name
            or journal.get("schema_version") != 1
            or set(journal["entries"]) != set(receipts)
        ):
            raise ValueError("retention_export_journal_incomplete")
        for key, entry in journal["entries"].items():
            if (
                entry.get("phase") != "replaced_verified"
                or receipts[key].get("sha256") != entry["new"]["sha256"]
                or receipts[key].get("prepared") != entry["prepared"]
            ):
                raise ValueError("retention_export_not_verified")
            for path, expected in [
                (entry["prepared"], entry["new"]),
                (entry.get("backup_path"), entry.get("old")),
            ]:
                if path is None:
                    continue
                path = _direct(path)
                generation, _ = _layout(path)
                if generation != root:
                    raise ValueError("retention_export_binding_changed")
                if not path.exists() and tombstone(path).exists():
                    receipt, blob = _receipt(path)
                    if {
                        key: receipt[key] for key in ("sha256", "size")
                    } != expected or not blob.exists():
                        raise ValueError("retention_export_binding_changed")
                    digest = receipt["sha256"]
                    if digest not in verified:
                        _verify_blob(blob, {key: receipt[key] for key in ("sha256", "size")})
                        verified[digest] = receipt["size"]
                    elif verified[digest] != receipt["size"]:
                        raise ValueError("retained_artifact_hash_changed")
                    continue
                if _fingerprint(path) != expected:
                    raise ValueError("retention_export_binding_changed")
                if path.exists():
                    found.append({"path": str(path), **expected})
                    if len(found) >= max_files:
                        return list({item["path"]: item for item in found}.values())
    return list({item["path"]: item for item in found}.values())


def retain(state_dir, output_dir, shared_lock, *, retain_days=7, execute=False, max_files=100):
    from jobagg.remediation_worker import shared_owner

    if type(max_files) is not int or not 1 <= max_files <= 1000:
        raise ValueError("retention_max_files_out_of_bounds")
    lock = _direct(shared_lock)
    if not lock.exists():
        raise ValueError("retention_requires_existing_shared_owner")
    with shared_owner(lock):
        selected = candidates(state_dir, output_dir, retain_days=retain_days, max_files=max_files)
        if execute:
            for item in selected:
                archive(item["path"], {key: item[key] for key in ("sha256", "size")})
    return {
        "status": "archived" if execute else "dry_run",
        "candidates": selected,
        "original_bytes": sum(x["size"] for x in selected),
        "raw_capture_deletions": 0,
        "snapshot_deletions": 0,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--shared-lock", type=Path, required=True)
    parser.add_argument("--retain-days", type=int, default=7)
    parser.add_argument("--max-files", type=int, default=100)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args(argv)
    print(
        json.dumps(
            retain(
                args.state_dir,
                args.output_dir,
                args.shared_lock,
                retain_days=args.retain_days,
                execute=args.execute,
                max_files=args.max_files,
            ),
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
