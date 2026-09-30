"""Lossless cold storage for completed publication plans and row beforeimages.

Only ``plan.json`` and direct ``beforeimages/*.json`` files are eligible.  The
current publication gate, newest two completed generations, and every unfinished
generation remain hot.  This deliberately leaves worker captures, attachments,
export receipts, and export rollback material alone.

The manifest is committed only after every compressed object is durable and
verified.  Removing a source after that point can therefore be resumed safely.
Restoration writes the original bytes back to their original paths.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime
import fcntl
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import tempfile
import time


MANIFEST = "storage-cold-archive.json"
EXPORT_RETENTION_RECEIPT = "storage-retention.json"
_GENERATION = re.compile(r"[0-9a-f]{32}\Z")
_SHA = re.compile(r"[0-9a-f]{64}\Z")
_CHUNK = 1024 * 1024


def _check_deadline(deadline_at: float | None) -> None:
    if deadline_at is not None and time.monotonic() >= deadline_at:
        raise TimeoutError("Cold archive time budget exhausted")


def _sync_dir(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _json(path: Path) -> dict:
    if not _regular(path):
        raise ValueError(f"Required regular JSON file is missing: {path}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON object required: {path}")
    return value


def _regular(path: Path) -> bool:
    try:
        item = path.lstat()
    except FileNotFoundError:
        return False
    return stat.S_ISREG(item.st_mode) and item.st_nlink == 1


def _digest(path: Path, *, deadline_at: float | None = None) -> tuple[str, int]:
    _check_deadline(deadline_at)
    if not _regular(path):
        raise ValueError(f"File is missing, linked, or not regular: {path}")
    before = path.lstat()
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as source:
        opened = os.fstat(source.fileno())
        if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
            raise ValueError(f"File changed before reading: {path}")
        for block in iter(lambda: source.read(_CHUNK), b""):
            _check_deadline(deadline_at)
            digest.update(block)
            size += len(block)
    after = path.lstat()
    if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
        after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns
    ):
        raise ValueError(f"File changed while reading: {path}")
    return digest.hexdigest(), size


def _archive_root(generation: Path) -> Path:
    return generation.parent.parent / "cold_archive"


def _object_path(generation: Path, sha256: str) -> Path:
    return _archive_root(generation) / "objects" / sha256[:2] / (sha256 + ".json.gz")


def _ensure_real_directory(path: Path) -> None:
    if path.exists() and (path.is_symlink() or not path.is_dir()):
        raise ValueError(f"Cold archive directory is linked or invalid: {path}")
    path.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise ValueError(f"Cold archive directory became linked: {path}")


def _assert_real_archive_parents(generation: Path, object_parent: Path) -> None:
    for path in (_archive_root(generation), _archive_root(generation) / "objects", object_parent):
        if path.is_symlink() or (path.exists() and not path.is_dir()):
            raise ValueError(f"Cold archive directory is linked or invalid: {path}")


def _validate_entry(generation: Path, relative: str, entry: dict) -> Path:
    if relative != "plan.json" and not re.fullmatch(r"beforeimages/[^/]+\.json", relative):
        raise ValueError("Cold archive has an unexpected relative path")
    if not isinstance(entry, dict) or not all(
        isinstance(entry.get(key), str) and _SHA.fullmatch(entry[key])
        for key in ("sha256", "object_sha256")
    ) or not all(
        type(entry.get(key)) is int and entry[key] >= 0
        for key in ("size_bytes", "object_size_bytes")
    ):
        raise ValueError("Malformed cold archive object binding")
    expected = _object_path(generation, entry["sha256"])
    _assert_real_archive_parents(generation, expected.parent)
    if entry.get("object_path") != str(expected):
        raise ValueError("Cold archive object path differs")
    return expected


def _verify_object(generation: Path, relative: str, entry: dict, *, deadline_at: float | None = None) -> None:
    path = _validate_entry(generation, relative, entry)
    compressed_sha, compressed_size = _digest(path, deadline_at=deadline_at)
    if (compressed_sha, compressed_size) != (
        entry["object_sha256"], entry["object_size_bytes"]
    ):
        raise ValueError(f"Compressed object differs: {relative}")
    original_sha = hashlib.sha256()
    original_size = 0
    with gzip.open(path, "rb") as source:
        for block in iter(lambda: source.read(_CHUNK), b""):
            _check_deadline(deadline_at)
            original_sha.update(block)
            original_size += len(block)
    if (original_sha.hexdigest(), original_size) != (
        entry["sha256"], entry["size_bytes"]
    ):
        raise ValueError(f"Decompressed object differs: {relative}")


def _manifest_metadata(generation: Path) -> dict:
    """Validate the small receipt only; caller chooses whether to inspect object bytes."""
    generation = Path(generation).absolute()
    manifest = _json(generation / MANIFEST)
    if (
        manifest.get("schema_version") != 1
        or manifest.get("generation_id") != generation.name
        or not _GENERATION.fullmatch(generation.name)
        or manifest.get("archive_root") != str(_archive_root(generation))
        or not isinstance(manifest.get("objects"), dict)
        or "plan.json" not in manifest["objects"]
    ):
        raise ValueError("Malformed cold archive manifest")
    for relative, entry in manifest["objects"].items():
        _validate_entry(generation, relative, entry)
    return manifest


def archived_plan_binding(generation_path: str | Path) -> str:
    """Return the receipt's plan digest without scanning archived object bytes."""
    generation = Path(generation_path).absolute()
    return _manifest_metadata(generation)["objects"]["plan.json"]["sha256"]


def verify_archived_generation(generation_path: str | Path, *, deadline_at: float | None = None) -> dict:
    """Validate the manifest and every compressed object, including source bytes if present."""
    generation = Path(generation_path).absolute()
    manifest = _manifest_metadata(generation)
    for relative, entry in manifest["objects"].items():
        _check_deadline(deadline_at)
        _verify_object(generation, relative, entry, deadline_at=deadline_at)
        source = generation / relative
        if source.exists() or source.is_symlink():
            if _digest(source, deadline_at=deadline_at) != (entry["sha256"], entry["size_bytes"]):
                raise ValueError(f"Hot source differs from cold archive: {relative}")
    with gzip.open(_object_path(generation, manifest["objects"]["plan.json"]["sha256"]), "rt", encoding="utf-8") as source:
        plan = json.load(source)
    _check_deadline(deadline_at)
    if plan.get("generation_id") != generation.name or plan.get("generation_root") != str(generation):
        raise ValueError("Archived publication plan identity differs")
    for relative, digest in _plan_beforeimage_bindings(generation, plan).items():
        if relative not in manifest["objects"] or manifest["objects"][relative]["sha256"] != digest:
            raise ValueError("Archived beforeimage differs from plan binding")
    return manifest


def plan_available(generation_path: str | Path, expected_sha256: str) -> bool:
    """Check whether the exact plan is hot or recoverable from cold storage."""
    generation = Path(generation_path).absolute()
    plan = generation / "plan.json"
    if plan.exists() or plan.is_symlink():
        return _digest(plan)[0] == expected_sha256
    if not (generation / MANIFEST).exists():
        return False
    manifest = verify_archived_generation(generation)
    return manifest["objects"]["plan.json"]["sha256"] == expected_sha256


def _load_gate(output_dir: Path) -> dict:
    gate = _json(output_dir / ".jobagg-publication-state.json")
    if (
        gate.get("state") != "complete"
        or gate.get("status") != "published"
        or gate.get("database_transactions_complete") is not True
    ):
        raise ValueError("Current publication gate is unresolved")
    if not _GENERATION.fullmatch(str(gate.get("generation_id", ""))):
        raise ValueError("Current publication gate has no valid generation")
    return gate


def _completed(generation: Path) -> dict | None:
    path = generation / "result.json"
    if not path.exists():
        return None
    try:
        result = _json(path)
        if (
            result.get("state") != "complete"
            or result.get("status") != "published"
            or result.get("database_transactions_complete") is not True
            or result.get("generation_id") != generation.name
            or not isinstance(result.get("plan_sha256"), str)
            or not _SHA.fullmatch(result["plan_sha256"])
            or Path(result.get("plan_path", "")).absolute() != generation / "plan.json"
        ):
            return None
        completed_at = datetime.fromisoformat(str(result["completed_at"]).replace("Z", "+00:00"))
        if completed_at.tzinfo is None:
            return None
        return {"result": result, "completed_at": completed_at}
    except (ValueError, KeyError, TypeError, OSError):
        return None


def _plan_beforeimage_bindings(generation: Path, plan: dict) -> dict[str, str]:
    refs: dict[str, str] = {}
    for group in ("changes", "listing_frames"):
        rows = plan.get(group, [])
        if not isinstance(rows, list):
            raise ValueError("Publication plan has malformed destinations")
        for row in rows:
            for destination in row.get("destinations", []):
                path = destination.get("beforeimage_path")
                digest = destination.get("beforeimage_sha256")
                if path is None:
                    continue
                candidate = Path(path).absolute()
                if (
                    candidate.parent != generation / "beforeimages"
                    or not candidate.name.endswith(".json")
                    or not isinstance(digest, str)
                    or not _SHA.fullmatch(digest)
                ):
                    raise ValueError("Publication plan references an unsafe beforeimage")
                relative = str(candidate.relative_to(generation))
                if relative in refs and refs[relative] != digest:
                    raise ValueError("Publication plan has conflicting beforeimage hashes")
                refs[relative] = digest
    return refs


def _referenced_beforeimages(generation: Path, plan: dict, *, deadline_at: float | None = None) -> set[str]:
    refs = _plan_beforeimage_bindings(generation, plan)
    for relative, digest in refs.items():
        if _digest(generation / relative, deadline_at=deadline_at)[0] != digest:
            raise ValueError("Publication beforeimage differs from plan binding")
    return set(refs)


def _source_files(generation: Path, expected_plan_sha256: str, *, deadline_at: float | None = None) -> list[Path]:
    plan_path = generation / "plan.json"
    if _digest(plan_path, deadline_at=deadline_at)[0] != expected_plan_sha256:
        raise ValueError("Completed publication plan hash differs")
    plan = _json(plan_path)
    if plan.get("generation_id") != generation.name or plan.get("generation_root") != str(generation):
        raise ValueError("Completed publication plan identity differs")
    directory = generation / "beforeimages"
    if directory.exists() and (directory.is_symlink() or not directory.is_dir()):
        raise ValueError("Beforeimages directory is not a real directory")
    beforeimages = sorted(directory.glob("*.json")) if directory.exists() else []
    for item in beforeimages:
        _digest(item, deadline_at=deadline_at)
    found = {str(path.relative_to(generation)) for path in beforeimages}
    if not _referenced_beforeimages(generation, plan, deadline_at=deadline_at).issubset(found):
        raise ValueError("Publication plan beforeimage is missing")
    return [plan_path, *beforeimages]


def _bound_generations(dispatcher_state_dir: str | Path, publication_state_dir: Path) -> set[str]:
    """Keep plans referenced by dispatcher recovery bindings and no-write proofs hot."""
    runs = Path(dispatcher_state_dir).absolute() / "runs"
    if not runs.is_dir() or runs.is_symlink():
        raise ValueError("Dispatcher runs directory is missing or linked")
    bound: set[str] = set()
    for run in runs.iterdir():
        if run.name == ".DS_Store" and _regular(run):
            continue
        if run.is_symlink() or not run.is_dir():
            raise ValueError("Dispatcher runs directory has an unexpected entry")
        path = run / "publication_generation_binding.json"
        if path.exists() or path.is_symlink():
            binding = _json(path)
            identifier = binding.get("generation_id")
            if not isinstance(identifier, str) or not _GENERATION.fullmatch(identifier):
                raise ValueError("Dispatcher recovery binding has no valid generation")
            bound.add(identifier)
        no_write = run / "publication_no_write_resolution.json"
        if no_write.exists() or no_write.is_symlink():
            resolution = _json(no_write)
            proof = resolution.get("proof")
            prior = proof.get("prior_plan") if isinstance(proof, dict) else None
            if not isinstance(prior, dict) or not isinstance(prior.get("path"), str):
                raise ValueError("Dispatcher no-write proof has no valid prior plan")
            plan = Path(prior["path"])
            identifier = plan.parent.name
            if (
                not plan.is_absolute()
                or not _GENERATION.fullmatch(identifier)
                or plan != publication_state_dir / "generations" / identifier / "plan.json"
                or not isinstance(prior.get("sha256"), str)
                or not _SHA.fullmatch(prior["sha256"])
            ):
                raise ValueError("Dispatcher no-write proof prior plan is outside publication state or invalid")
            # The runner revalidates this original hot path on later ticks.
            # Keep it even after resolution, or if older tooling moved it.
            bound.add(identifier)
    return bound


def _export_retention_intent(generation: Path) -> bool:
    path = generation / EXPORT_RETENTION_RECEIPT
    if not path.exists() and not path.is_symlink():
        return False
    receipt = _json(path)
    if receipt.get("phase") not in {"intent", "complete"}:
        raise ValueError("Publication export retention receipt has an unknown phase")
    return receipt["phase"] == "intent"


def preview_archive(state_dir: str | Path, output_dir: str | Path, *, dispatcher_state_dir: str | Path | None = None, max_generations: int = 1) -> dict:
    """Read-only selection; a later execution repeats all checks under the owner lock."""
    if type(max_generations) is not int or max_generations < 1:
        raise ValueError("max_generations must be a positive integer")
    state = Path(state_dir).absolute()
    output = Path(output_dir).absolute()
    gate = _load_gate(output)
    bound = _bound_generations(dispatcher_state_dir, state) if dispatcher_state_dir is not None else set()
    generations_root = state / "generations"
    if not generations_root.is_dir() or generations_root.is_symlink():
        raise ValueError("Publication generations directory is missing or linked")
    all_generations = sorted(
        path for path in generations_root.iterdir()
        if not (path.name == ".DS_Store" and _regular(path))
    )
    if any(path.is_symlink() or not path.is_dir() or not _GENERATION.fullmatch(path.name) for path in all_generations):
        raise ValueError("Publication generations directory has an unexpected entry")
    completed = {path.name: item for path in all_generations if (item := _completed(path)) is not None}
    newest = {
        name for name, _ in sorted(completed.items(), key=lambda item: item[1]["completed_at"], reverse=True)[:2]
    }
    protected = newest | {gate["generation_id"]} | bound
    entries = []
    for generation in all_generations:
        item = completed.get(generation.name)
        export_intent = _export_retention_intent(generation)
        if export_intent:
            protected.add(generation.name)
        reason = (
            "current_gate" if generation.name == gate["generation_id"] else
            "dispatcher_recovery_binding" if generation.name in bound else
            "export_retention_intent" if export_intent else
            "newest_two_complete" if generation.name in newest else
            "dispatcher_state_unavailable" if dispatcher_state_dir is None else
            "unresolved_or_unverified" if item is None else
            "eligible"
        )
        manifest_path = generation / MANIFEST
        if manifest_path.exists() or manifest_path.is_symlink():
            manifest = _manifest_metadata(generation)
            if item is None or manifest["objects"]["plan.json"]["sha256"] != item["result"]["plan_sha256"]:
                raise ValueError("Cold archive plan differs from completed result")
            sources = [generation / relative for relative in manifest["objects"]]
            reason = (
                "archive_resume" if any(path.exists() for path in sources) and generation.name not in protected
                else "already_archived"
            )
        elif reason == "eligible":
            plan_path = generation / "plan.json"
            if not _regular(plan_path):
                raise ValueError("Eligible publication plan is missing or linked")
            directory = generation / "beforeimages"
            if directory.exists() and (directory.is_symlink() or not directory.is_dir()):
                raise ValueError("Beforeimages directory is not a real directory")
            sources = [plan_path, *(sorted(directory.glob("*.json")) if directory.exists() else [])]
            if any(not _regular(path) for path in sources):
                raise ValueError("Eligible publication source is missing or linked")
        else:
            sources = []
        entries.append({
            "generation_id": generation.name,
            "reason": reason,
            "files": len(sources),
            "hot_bytes": sum(path.stat().st_size for path in sources if _regular(path)),
        })
    eligible = [entry["generation_id"] for entry in entries if entry["reason"] in {"eligible", "archive_resume"}]
    eligible.sort(key=lambda name: completed[name]["completed_at"])
    return {
        "schema_version": 1,
        "state_dir": str(state),
        "output_dir": str(output),
        "current_generation_id": gate["generation_id"],
        "dispatcher_state_dir": str(Path(dispatcher_state_dir).absolute()) if dispatcher_state_dir is not None else None,
        "dispatcher_recovery_generation_ids": sorted(bound),
        "protected_generation_ids": sorted(protected),
        "generations": entries,
        "eligible_generations": eligible[:max_generations],
        "remaining_eligible_generations": max(0, len(eligible) - max_generations),
    }


@contextmanager
def _owner(shared_lock: str | Path):
    lock = Path(shared_lock).absolute()
    if not _regular(lock):
        raise ValueError("Shared owner lock is missing or linked")
    before = lock.lstat()
    with lock.open("r+") as handle:
        opened = os.fstat(handle.fileno())
        if (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino):
            raise ValueError("Shared owner lock changed while opening")
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError("Shared owner lock is already held") from exc
        after = lock.lstat()
        if (after.st_dev, after.st_ino) != (opened.st_dev, opened.st_ino):
            raise ValueError("Shared owner lock changed after acquisition")
        yield


def _compress_object(source: Path, generation: Path, *, deadline_at: float | None = None) -> dict:
    source_sha, source_size = _digest(source, deadline_at=deadline_at)
    target = _object_path(generation, source_sha)
    _ensure_real_directory(_archive_root(generation))
    _ensure_real_directory(_archive_root(generation) / "objects")
    _ensure_real_directory(target.parent)
    if target.exists() or target.is_symlink():
        if not _regular(target):
            raise ValueError("Existing cold object is not a regular file")
    else:
        descriptor, temporary_name = tempfile.mkstemp(prefix=".cold-", suffix=".tmp", dir=target.parent)
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as raw, source.open("rb") as incoming:
                with gzip.GzipFile(fileobj=raw, mode="wb", filename="", mtime=0) as compressed:
                    for block in iter(lambda: incoming.read(_CHUNK), b""):
                        _check_deadline(deadline_at)
                        compressed.write(block)
                raw.flush()
                os.fsync(raw.fileno())
            if _digest(source, deadline_at=deadline_at) != (source_sha, source_size):
                raise ValueError("Source changed during cold compression")
            try:
                os.link(temporary, target)
                _sync_dir(target.parent)
            except FileExistsError:
                pass
        finally:
            temporary.unlink(missing_ok=True)
    object_sha, object_size = _digest(target, deadline_at=deadline_at)
    entry = {
        "sha256": source_sha,
        "size_bytes": source_size,
        "object_sha256": object_sha,
        "object_size_bytes": object_size,
        "object_path": str(target),
    }
    _verify_object(generation, str(source.relative_to(generation)), entry, deadline_at=deadline_at)
    return entry


def _write_manifest(path: Path, manifest: dict) -> None:
    if path.exists() or path.is_symlink():
        raise ValueError("Cold archive manifest already exists")
    descriptor, temporary_name = tempfile.mkstemp(prefix=".cold-manifest-", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            json.dump(manifest, output, sort_keys=True, separators=(",", ":"))
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        os.link(temporary, path)
        _sync_dir(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def _remove_verified_sources(generation: Path, manifest: dict, *, deadline_at: float | None = None) -> int:
    removed = 0
    for relative, entry in manifest["objects"].items():
        _check_deadline(deadline_at)
        path = generation / relative
        if not path.exists() and not path.is_symlink():
            continue
        if _digest(path, deadline_at=deadline_at) != (entry["sha256"], entry["size_bytes"]):
            raise ValueError(f"Source changed before cold removal: {relative}")
        path.unlink()
        _sync_dir(path.parent)
        removed += 1
    return removed


def archive_completed(state_dir: str | Path, output_dir: str | Path, shared_lock: str | Path, *, dispatcher_state_dir: str | Path | None = None, execute: bool = False, max_generations: int = 1, max_seconds: float | None = None) -> dict:
    """Archive selected generations. Defaults to a read-only preview."""
    if max_seconds is not None and max_seconds <= 0:
        raise ValueError("max_seconds must be positive")
    if not execute:
        return preview_archive(state_dir, output_dir, dispatcher_state_dir=dispatcher_state_dir, max_generations=max_generations)
    if dispatcher_state_dir is None:
        raise ValueError("Dispatcher state directory is required for archive execution")
    deadline_at = time.monotonic() + max_seconds if max_seconds is not None else None
    with _owner(shared_lock):
        selection = preview_archive(state_dir, output_dir, dispatcher_state_dir=dispatcher_state_dir, max_generations=max_generations)
        state = Path(state_dir).absolute()
        completed = []
        for identifier in selection["eligible_generations"]:
            _check_deadline(deadline_at)
            generation = state / "generations" / identifier
            result = _completed(generation)
            if result is None:
                raise ValueError("Completed generation changed during archive")
            existing = generation / MANIFEST
            if existing.exists() or existing.is_symlink():
                manifest = verify_archived_generation(generation, deadline_at=deadline_at)
                removed = _remove_verified_sources(generation, manifest, deadline_at=deadline_at)
                completed.append({"generation_id": identifier, "removed_hot_files": removed, "resumed": True})
                continue
            sources = _source_files(generation, result["result"]["plan_sha256"], deadline_at=deadline_at)
            objects = {
                str(source.relative_to(generation)): _compress_object(source, generation, deadline_at=deadline_at)
                for source in sources
            }
            manifest = {
                "schema_version": 1,
                "generation_id": identifier,
                "archive_root": str(_archive_root(generation)),
                "objects": objects,
            }
            _write_manifest(generation / MANIFEST, manifest)
            verify_archived_generation(generation, deadline_at=deadline_at)
            removed = _remove_verified_sources(generation, manifest, deadline_at=deadline_at)
            completed.append({"generation_id": identifier, "removed_hot_files": removed})
        return {**selection, "archived": completed}


def _restore_file(generation: Path, relative: str, entry: dict) -> None:
    target = generation / relative
    if target.exists() or target.is_symlink():
        if _digest(target) != (entry["sha256"], entry["size_bytes"]):
            raise ValueError(f"Existing restored file differs: {relative}")
        return
    _ensure_real_directory(target.parent)
    descriptor, temporary_name = tempfile.mkstemp(prefix=".cold-restore-", dir=target.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as output, gzip.open(_object_path(generation, entry["sha256"]), "rb") as source:
            for block in iter(lambda: source.read(_CHUNK), b""):
                output.write(block)
            output.flush()
            os.fsync(output.fileno())
        if _digest(temporary) != (entry["sha256"], entry["size_bytes"]):
            raise ValueError("Restored bytes differ before seal")
        os.link(temporary, target)
        _sync_dir(target.parent)
    finally:
        temporary.unlink(missing_ok=True)


def restore_generation(state_dir: str | Path, output_dir: str | Path, shared_lock: str | Path, generation_id: str, *, execute: bool = False) -> dict:
    """Restore exact plan and beforeimage bytes; defaults to a read-only preview."""
    if not _GENERATION.fullmatch(generation_id):
        raise ValueError("Invalid publication generation ID")
    generation = Path(state_dir).absolute() / "generations" / generation_id
    manifest = verify_archived_generation(generation)
    result = _completed(generation)
    if result is None or manifest["objects"]["plan.json"]["sha256"] != result["result"]["plan_sha256"]:
        raise ValueError("Cold archive has no matching completed publication")
    preview = {"generation_id": generation_id, "files": len(manifest["objects"]), "restored": False}
    if not execute:
        return preview
    with _owner(shared_lock):
        _load_gate(Path(output_dir).absolute())
        manifest = verify_archived_generation(generation)
        for relative, entry in manifest["objects"].items():
            _restore_file(generation, relative, entry)
        for relative, entry in manifest["objects"].items():
            if _digest(generation / relative) != (entry["sha256"], entry["size_bytes"]):
                raise ValueError("Restored publication file differs")
        (generation / MANIFEST).unlink()
        _sync_dir(generation)
    return {**preview, "restored": True}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("archive", "restore", "verify"):
        command = commands.add_parser(name)
        command.add_argument("--state-dir", type=Path, required=True)
        if name != "verify":
            command.add_argument("--output-dir", type=Path, required=True)
            command.add_argument("--shared-lock", type=Path, required=True)
            command.add_argument("--execute", action="store_true", help="Apply after read-only preview")
        if name == "archive":
            command.add_argument("--dispatcher-state-dir", type=Path, required=True)
            command.add_argument("--max-generations", type=int, default=1)
            command.add_argument("--max-seconds", type=float)
        if name != "archive":
            command.add_argument("--generation-id", required=True)
    args = parser.parse_args(argv)
    if args.command == "archive":
        result = archive_completed(args.state_dir, args.output_dir, args.shared_lock, execute=args.execute,
                                   dispatcher_state_dir=args.dispatcher_state_dir,
                                   max_generations=args.max_generations, max_seconds=args.max_seconds)
    elif args.command == "restore":
        result = restore_generation(args.state_dir, args.output_dir, args.shared_lock, args.generation_id, execute=args.execute)
    else:
        result = verify_archived_generation(args.state_dir / "generations" / args.generation_id)
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
