"""Additive-only owner-fenced preparation pages for historical representations.

Every original is retained. There is deliberately no source-removal option or
production activation. A completed page is not storage-admission headroom.
"""

from datetime import datetime
import os
from pathlib import Path
import re
import shutil
import time

from jobagg import historical_storage as h, storage_compression as storage
from jobagg.runtime_source_head import owner

SCHEMA = "historical-archive-page-1"
MAX_ITEMS = 64
MAX_CONTROL_BYTES = 64 * 1024**2
RESERVE = 20 * 1024**3


def _json(path, deadline):
    record = h.record(path, deadline_at=deadline, max_bytes=h.MAX_CONTROL)
    value, _ = h.document(path, record["sha256"], deadline_at=deadline)
    return value, record


def _option(config, flag):
    argv = config["publication_argv"]
    if argv.count(flag) != 1:
        raise ValueError("archive_configuration_path_ambiguous")
    return h.canonical(argv[argv.index(flag) + 1])


def _dirs(path, maximum, deadline):
    path = h.canonical(path)
    items = []
    with os.scandir(path) as entries:
        for entry in entries:
            h.check(deadline)
            if entry.name == ".DS_Store" and entry.is_file(follow_symlinks=False):
                continue
            if (
                len(items) >= maximum
                or entry.is_symlink()
                or not entry.is_dir(follow_symlinks=False)
            ):
                raise ValueError("archive_finite_canonical_directory_inventory_required")
            items.append(Path(entry.path))
    return sorted(items)


def protection(config, *, deadline_at):
    """Census current/previous, unfinished and dispatcher recovery inputs offline."""
    state, output = _option(config, "--state-dir"), _option(config, "--output-dir")
    gate, gate_record = _json(output / ".jobagg-publication-state.json", deadline_at)
    if (
        gate.get("state") != "complete"
        or gate.get("status") != "published"
        or gate.get("database_transactions_complete") is not True
        or gate.get("unresolved_publication") is True
        or not re.fullmatch("[a-f0-9]{32}", str(gate.get("generation_id")))
    ):
        raise ValueError("archive_complete_publication_gate_required")
    protected, completed, records = set(), [], [gate_record]
    generations = _dirs(state / "generations", 512, deadline_at)
    total = gate_record["size_bytes"]
    for generation in generations:
        h.check(deadline_at)
        if not re.fullmatch("[a-f0-9]{32}", generation.name):
            raise ValueError("archive_unknown_generation_identity")
        result = generation / "result.json"
        if not result.exists():
            protected.add(str(generation))
            continue
        value, record = _json(result, deadline_at)
        records.append(record)
        total += record["size_bytes"]
        if total > MAX_CONTROL_BYTES:
            raise ValueError("archive_protection_control_byte_bound")
        if (
            value.get("state") == "complete"
            and value.get("status") == "published"
            and value.get("database_transactions_complete") is True
            and value.get("generation_id") == generation.name
            and value.get("readback_verified", True) is True
            and value.get("plan_path") == str(generation / "plan.json")
        ):
            instant = datetime.fromisoformat(value["completed_at"].replace("Z", "+00:00"))
            if instant.tzinfo is None:
                raise ValueError("archive_generation_chronology_unknown")
            completed.append((instant.timestamp(), generation))
        else:
            protected.add(str(generation))
    if len(completed) < 2 or gate["generation_id"] not in {p.name for _, p in completed}:
        raise ValueError("archive_current_and_previous_complete_generations_required")
    protected.update(str(p) for _, p in sorted(completed, reverse=True)[:2])
    protected.add(str(state / "generations" / gate["generation_id"]))
    runs = _dirs(h.canonical(config["state_dir"]) / "runs", 10000, deadline_at)
    for run in runs:
        for name in ("publication_generation_binding.json", "publication_no_write_resolution.json"):
            path = run / name
            if not path.exists() and not path.is_symlink():
                continue
            value, record = _json(path, deadline_at)
            records.append(record)
            total += record["size_bytes"]
            if total > MAX_CONTROL_BYTES:
                raise ValueError("archive_protection_control_byte_bound")
            if name == "publication_generation_binding.json":
                identifier = value.get("generation_id")
            else:
                prior = value.get("proof", {}).get("prior_plan", {})
                prior_path = h.canonical(prior["path"])
                identifier = prior_path.parent.name
                if prior_path != state / "generations" / identifier / "plan.json":
                    raise ValueError("archive_recovery_journal_root_requires_review")
            if not re.fullmatch("[a-f0-9]{32}", str(identifier)):
                raise ValueError("archive_recovery_generation_unknown")
            protected.add(str(state / "generations" / identifier))
    return {
        "protected": sorted(protected),
        "records": records,
        "generations": [str(p) for p in generations],
        "runs": [str(p) for p in runs],
    }


def _producer(config, root, item, deadline):
    path, kind = h.canonical(item["path"]), item["kind"]
    state = _option(config, "--state-dir")
    if kind == "beforeimage":
        # Large plans are parsed with the existing bounded projection, not loaded
        # as an unbounded JSON control. Every skipped byte is still validated.
        from jobagg.historical_plans import read_plan

        plan, binding = read_plan(
            h.canonical(item["producer_path"]),
            expected_sha256=item["producer_sha256"],
            deadline_at=deadline,
            max_bytes=h.MAX_ARTIFACT,
        )
        producer = {
            "path": item["producer_path"],
            "sha256": binding["sha256"],
            "identity": binding["identity"],
            "size_bytes": binding["bytes"],
        }
        source = plan
    else:
        source, producer = _json(h.canonical(item["producer_path"]), deadline)
        if producer["sha256"] != item["producer_sha256"]:
            raise ValueError("archive_original_producer_changed")
    expected, size = item["sha256"], item["size_bytes"]
    if kind == "snapshot":
        run = path.parent
        if (
            path.name != "publication_snapshot.sqlite3"
            or run.parent != h.canonical(config["state_dir"]) / "runs"
            or Path(item["producer_path"]) != run / "publication_request.json"
        ):
            raise ValueError("archive_snapshot_scope_invalid")
        seal = source.get("publication_snapshot", {})
        if (
            seal.get("path") != str(path)
            or seal.get("sha256") != expected
            or seal.get("size_bytes") != size
        ):
            raise ValueError("archive_snapshot_original_seal_required")
        storage._check_sqlite_candidate(path, sealed=True)
    else:
        generation = path.parent if kind == "plan" else path.parent.parent
        if generation.parent != state / "generations" or not re.fullmatch(
            "[a-f0-9]{32}", generation.name
        ):
            raise ValueError("archive_generation_artifact_scope_invalid")
        if kind == "plan":
            if (
                path.name != "plan.json"
                or Path(item["producer_path"]) != generation / "result.json"
                or source.get("plan_path") != str(path)
                or source.get("plan_sha256") != expected
            ):
                raise ValueError("archive_plan_original_producer_required")
        elif kind == "beforeimage":
            if (
                path.parent.name != "beforeimages"
                or path.suffix != ".json"
                or Path(item["producer_path"]) != generation / "plan.json"
            ):
                raise ValueError("archive_beforeimage_scope_invalid")
            matches = [
                d
                for field in ("changes", "listing_frames")
                for c in source.get(field, [])
                for d in c.get("destinations", [])
                if d.get("beforeimage_path") == str(path)
            ]
            if not matches or any(d.get("beforeimage_sha256") != expected for d in matches):
                raise ValueError("archive_beforeimage_original_producer_required")
        elif kind == "export":
            if (
                path.parent.name not in {"exports-v2", "before_exports"}
                or Path(item["producer_path"]) != generation / "export-checkpoints.json"
            ):
                raise ValueError("archive_export_scope_invalid")
            matches = []
            for row in source.get("entries", {}).values():
                if (
                    row.get("phase") != "replaced_verified"
                    or row.get("unverified_backup_candidates")
                    or row.get("legacy_backup")
                ):
                    continue
                for locator, binding in (("prepared", "new"), ("backup_path", "old")):
                    if row.get(locator) == str(path) and (
                        binding != "old" or row.get("backup_status") == "verified"
                    ):
                        matches.append(row.get(binding))
            if not matches or any(v != {"sha256": expected, "size": size} for v in matches):
                raise ValueError("archive_export_original_producer_required")
        else:
            raise ValueError("archive_unknown_artifact_kind")
    if not path.is_relative_to(root):
        raise ValueError("archive_artifact_outside_root")
    return producer


def prepare(
    config_path,
    *,
    config_sha256,
    root,
    artifacts,
    dependency_review_path,
    dependency_review_sha256,
    implementation_sha256,
    owner_fd,
    deadline_at,
):
    """Return a reviewed finite additive page; no source removal can be requested."""
    h.check(deadline_at)
    if not 0 < deadline_at - time.monotonic() <= 900 or not 0 < len(artifacts) <= MAX_ITEMS:
        raise ValueError("archive_finite_page_required")
    root = h.canonical(root)
    config, _ = h.document(config_path, config_sha256, deadline_at=deadline_at)
    owner(owner_fd, h.canonical(config["shared_lock_path"]))
    dependency, _ = h.document(
        dependency_review_path, dependency_review_sha256, deadline_at=deadline_at
    )
    if (
        dependency.get("schema") != "historical-archive-dependency-review-1"
        or dependency.get("configuration_sha256") != config_sha256
        or dependency.get("complete_writer_inventory") is not True
        or dependency.get("reader_compatibility_verified") is not True
        or not isinstance(dependency.get("protected_paths"), list)
    ):
        raise ValueError("archive_explicit_dependency_reader_review_required")
    from jobagg.remediation_worker import implementation_hash

    if implementation_sha256 != implementation_hash():
        raise ValueError("archive_reviewed_implementation_changed")
    marker = h.record(config["maintenance_file"], deadline_at=deadline_at, max_bytes=h.MAX_CONTROL)
    if marker["sha256"] != dependency.get("maintenance_sha256"):
        raise ValueError("archive_operation_owned_fence_required")
    protected = protection(config, deadline_at=deadline_at)
    prefixes = [h.canonical(p) for p in protected["protected"] + dependency["protected_paths"]]
    items, total = [], 0
    for supplied in artifacts:
        path = h.canonical(supplied["path"])
        if any(path.is_relative_to(p) for p in prefixes):
            raise ValueError("archive_protected_dependency")
        producer = _producer(config, root, supplied, deadline_at)
        original = h.record(path, deadline_at=deadline_at, max_bytes=h.MAX_ARTIFACT)
        if (
            original["sha256"] != supplied["sha256"]
            or original["size_bytes"] != supplied["size_bytes"]
        ):
            raise ValueError("archive_original_content_changed")
        total += original["size_bytes"]
        if total > 8 * 1024**3:
            raise ValueError("archive_page_byte_bound")
        items.append(
            {
                **supplied,
                "identity": original["identity"],
                "producer_identity": producer["identity"],
            }
        )
    if len({x["path"] for x in items}) != len(items):
        raise ValueError("archive_duplicate_logical_path")
    return {
        "schema": SCHEMA,
        "root": str(root),
        "configuration_path": str(h.canonical(config_path)),
        "configuration_sha256": config_sha256,
        "shared_owner": config["shared_lock_path"],
        "maintenance": marker,
        "dependency_review_path": str(h.canonical(dependency_review_path)),
        "dependency_review_sha256": dependency_review_sha256,
        "implementation_sha256": implementation_sha256,
        "protection": protected,
        "items": items,
        "originals_retained": True,
        "source_bytes": total,
    }


def _fence(review, owner_fd, deadline):
    h.check(deadline)
    owner(owner_fd, review["shared_owner"])
    from jobagg.remediation_worker import implementation_hash

    if implementation_hash() != review["implementation_sha256"]:
        raise ValueError("archive_reviewed_implementation_changed")
    config, _ = h.document(
        review["configuration_path"], review["configuration_sha256"], deadline_at=deadline
    )
    if (
        config["shared_lock_path"] != review["shared_owner"]
        or config["maintenance_file"] != review["maintenance"]["path"]
    ):
        raise ValueError("archive_writer_boundary_changed")
    if (
        h.record(config["maintenance_file"], deadline_at=deadline, max_bytes=h.MAX_CONTROL)
        != review["maintenance"]
    ):
        raise ValueError("archive_operation_owned_fence_changed")
    h.document(
        review["dependency_review_path"], review["dependency_review_sha256"], deadline_at=deadline
    )
    if protection(config, deadline_at=deadline) != review["protection"]:
        raise ValueError("archive_protected_boundary_changed")
    return config


def execute_page(
    review_path,
    review_sha256,
    *,
    owner_fd,
    scratch_parent,
    deadline_at,
    max_items=4,
    max_bytes=2 * 1024**3,
    fault=lambda _: None,
):
    """Install additive verified representations; checkpoint and resume exactly.

    Every original stays in place. Completion grants no storage headroom, source
    removal, accepted evidence, historical closure or production activation.
    """
    h.check(deadline_at)
    if (
        type(max_items) is not int
        or not 0 < max_items <= MAX_ITEMS
        or type(max_bytes) is not int
        or not 0 < max_bytes <= 8 * 1024**3
        or not 0 < deadline_at - time.monotonic() <= 900
    ):
        raise ValueError("archive_finite_execution_page_required")
    review, _ = h.document(review_path, review_sha256, deadline_at=deadline_at)
    if (
        review.get("schema") != SCHEMA
        or review.get("originals_retained") is not True
        or not 0 < len(review["items"]) <= MAX_ITEMS
    ):
        raise ValueError("archive_review_schema")
    config = _fence(review, owner_fd, deadline_at)
    root, scratch = h.canonical(review["root"]), h.canonical(scratch_parent)
    if not scratch.is_dir() or scratch == root or not scratch.is_relative_to(root):
        raise ValueError("archive_registered_copy_owned_scratch_required")
    directory = root / storage.STORAGE_DIRECTORY / "migrations" / review_sha256
    h.canonical(directory)
    directory.mkdir(parents=True, mode=0o700, exist_ok=True)
    h.canonical(directory)
    completed, processed, consumed = [], 0, 0
    for ordinal, item in enumerate(review["items"]):
        h.check(deadline_at)
        path = h.canonical(item["path"])
        if not path.is_relative_to(root) or any(
            path.is_relative_to(h.canonical(p)) for p in review["protection"]["protected"]
        ):
            raise ValueError("archive_protected_dependency")
        producer = _producer(config, root, item, deadline_at)
        if producer["identity"] != item["producer_identity"]:
            raise ValueError("archive_original_producer_identity_changed")
        done = directory / (f"{ordinal:04}.complete.json")
        if done.exists():
            value, _ = _json(done, deadline_at)
            if value.get("review_sha256") != review_sha256 or value.get("item") != item:
                raise ValueError("archive_completed_checkpoint_changed")
            grant = h.load_grant(
                value["grant_path"], value["grant_sha256"], deadline_at=deadline_at
            )
            grant.check(
                deadline_at=deadline_at,
                expected_sha256=item["sha256"],
                original_identity=item["identity"],
            )
            grant.verify(deadline_at=deadline_at)
            if h.identity(item["path"]) != item["identity"]:
                raise ValueError("archive_retained_original_changed")
            completed.append(value)
            continue
        if processed >= max_items or consumed + item["size_bytes"] > max_bytes:
            break
        # Includes preview raw+compressed coexistence, staging and controls.
        required = 3 * item["size_bytes"] + 2 * 1024**2
        for anchor in (root, scratch):
            if shutil.disk_usage(anchor).free < RESERVE + required:
                raise ValueError("archive_physical_reserve_unavailable")
        _fence(review, owner_fd, deadline_at)
        path = h.canonical(item["path"])
        if h.identity(path) != item["identity"]:
            raise ValueError("archive_original_identity_changed")
        intent_path = directory / (f"{ordinal:04}.intent.json")
        intent = {"review_sha256": review_sha256, "ordinal": ordinal, "item": item}
        if not intent_path.exists():
            h.exclusive(intent_path, h.dump(intent))
        elif _json(intent_path, deadline_at)[0] != intent:
            raise ValueError("archive_open_intent_changed")
        installed_path = directory / (f"{ordinal:04}.installed.json")
        if installed_path.exists():
            installed, _ = _json(installed_path, deadline_at)
            if installed.get("review_sha256") != review_sha256:
                raise ValueError("archive_install_checkpoint_changed")
            grant = h.load_grant(
                installed["grant_path"], installed["grant_sha256"], deadline_at=deadline_at
            )
            grant.check(
                deadline_at=deadline_at,
                expected_sha256=item["sha256"],
                original_identity=item["identity"],
            )
            grant.verify(deadline_at=deadline_at)
        else:
            producer = _producer(config, root, item, deadline_at)
            if producer["identity"] != item["producer_identity"]:
                raise ValueError("archive_original_producer_identity_changed")
            storage.compress_artifact(
                root,
                path,
                codec="zstd",
                level=3,
                execute=True,
                shared_lock=review["shared_owner"],
                owner_fd=owner_fd,
                retire_legacy=False,
                sealed_sqlite=item["kind"] == "snapshot",
                min_bytes=0,
                max_measure_bytes=item["size_bytes"],
                max_object_bytes=item["size_bytes"],
                deadline_at=deadline_at,
                scratch_parent=scratch,
            )
            fault("after_install")
            grant = h.seal_grant(
                root,
                path,
                kind=item["kind"],
                expected_sha256=item["sha256"],
                expected_bytes=item["size_bytes"],
                original_identity=item["identity"],
                review_sha256=review_sha256,
                shared_lock=review["shared_owner"],
                owner_fd=owner_fd,
                deadline_at=deadline_at,
            )
            installed = {
                "review_sha256": review_sha256,
                "grant_path": grant.control_path,
                "grant_sha256": grant.sha256,
            }
            h.exclusive(installed_path, h.dump(installed))
        fault("after_grant")
        _fence(review, owner_fd, deadline_at)
        if h.identity(path) != item["identity"]:
            raise ValueError("archive_retained_original_changed")
        result = {
            **intent,
            **installed,
            "originals_retained": True,
            "removed_bytes": 0,
            "headroom_granted": False,
        }
        h.exclusive(done, h.dump(result))
        completed.append(result)
        processed += 1
        consumed += item["size_bytes"]
        fault("after_checkpoint")
    return {
        "processed": processed,
        "completed": len(completed),
        "complete": len(completed) == len(review["items"]),
        "source_bytes_this_page": consumed,
        "removed_bytes": 0,
        "headroom_granted": False,
        "production_readiness_granted": False,
        "checkpoints": completed,
        "originals_retained": True,
    }
