"""Explicit removal of verified historical originals in separate isolated copies.

No production mode, activation, native authority repair or capacity admission.
The additive installer remains unchanged. A lost removal receipt grants no credit.
"""

from contextlib import closing
from pathlib import Path
import shutil
import sqlite3
import time

from jobagg import archive_migration as migration, historical_storage as h
from jobagg import storage_compression as storage
from jobagg.isolated_control_repair import _drained

SCHEMA = "isolated-historical-removal-plan-1"
COPY_SCHEMA = "isolated-historical-artifact-copy-1"


def _directory(path):
    path = h.canonical(path)
    info = path.stat()
    if not path.is_dir():
        raise ValueError("isolated_archive_directory_required")
    return [info.st_dev, info.st_ino]


def _overlaps(a, b):
    return a.is_relative_to(b) or b.is_relative_to(a)


def _budget(deadline):
    h.check(deadline)
    if not 0 < deadline - time.monotonic() <= 900:
        raise ValueError("isolated_archive_finite_deadline_required")


def _context(additive_path, additive_sha, snapshot_path, snapshot_sha, owner_fd, deadline):
    _budget(deadline)
    additive, _ = h.document(additive_path, additive_sha, deadline_at=deadline)
    if additive.get("schema") != migration.SCHEMA or additive.get("originals_retained") is not True:
        raise ValueError("isolated_archive_additive_review_required")
    config = migration._fence(additive, owner_fd, deadline)
    root = h.canonical(additive["root"])
    snapshot, _ = h.document(snapshot_path, snapshot_sha, deadline_at=deadline)
    if (
        snapshot.get("schema") != COPY_SCHEMA
        or snapshot.get("snapshot_root") != str(root)
        or snapshot.get("copy_configuration_sha256") != additive["configuration_sha256"]
        or snapshot.get("complete") is not True
        or not isinstance(snapshot.get("files"), list)
        or not 0 < len(snapshot["files"]) <= migration.MAX_ITEMS
    ):
        raise ValueError("isolated_archive_exact_copy_manifest_required")
    source_root = h.canonical(snapshot["source_root"])
    if _overlaps(root, source_root) or _directory(root) != snapshot.get("copy_root_identity"):
        raise ValueError("isolated_archive_separate_copy_required")
    if _directory(source_root) != snapshot.get("source_root_identity"):
        raise ValueError("isolated_archive_source_root_changed")
    original, _ = h.document(
        snapshot["production_config"], snapshot["production_config_sha256"], deadline_at=deadline
    )
    outside = [source_root, h.canonical(snapshot["production_config"])]
    outside += [
        h.canonical(original[k]) for k in ("state_dir", "shared_lock_path", "maintenance_file")
    ]
    outside += [migration._option(original, flag) for flag in ("--state-dir", "--output-dir")]
    if original.get("publication_worker_database"):
        outside.append(h.canonical(original["publication_worker_database"]))
    if any(not path.is_relative_to(source_root) for path in outside[2:]):
        raise ValueError("isolated_archive_complete_production_root_required")
    if any(_overlaps(root, path) for path in outside):
        raise ValueError("isolated_archive_overlaps_production")
    controls = [
        h.canonical(additive_path),
        h.canonical(snapshot_path),
        h.canonical(additive["configuration_path"]),
    ]
    controls += [
        h.canonical(config[k]) for k in ("state_dir", "shared_lock_path", "maintenance_file")
    ]
    controls += [migration._option(config, flag) for flag in ("--state-dir", "--output-dir")]
    if any(not path.is_relative_to(root) or path == root for path in controls):
        raise ValueError("isolated_archive_copy_owned_controls_required")
    # Native accepted work must keep its original direct recovery inputs hot.
    # Read only a copy-owned database; no native state or source head is rewritten.
    if config.get("publication_worker_database"):
        database = h.canonical(config["publication_worker_database"])
        if not database.is_relative_to(root):
            raise ValueError("isolated_archive_copy_owned_database_required")
        with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True, timeout=0)) as conn:
            conn.set_progress_handler(lambda: int(time.monotonic() >= deadline), 256)
            from jobagg import runtime_authority

            if runtime_authority.state(conn) is not None:
                state = runtime_authority.validate(conn)
                if state["phase"] != "idle" or state["owner_path"] != config["shared_lock_path"]:
                    raise ValueError("isolated_archive_native_work_not_idle")
        h.check(deadline)
    marker = h.record(original["maintenance_file"], deadline_at=deadline, max_bytes=h.MAX_CONTROL)
    if marker != snapshot.get("production_fence"):
        raise ValueError("isolated_archive_production_fence_changed")
    rows = {}
    for row in snapshot["files"]:
        copied = h.canonical(row["copy"])
        source = h.canonical(row["source"])
        if (
            copied == root
            or not copied.is_relative_to(root)
            or source == source_root
            or not source.is_relative_to(source_root)
            or str(copied) in rows
        ):
            raise ValueError("isolated_archive_copy_inventory_scope")
        rows[str(copied)] = row
    _drained(deadline)
    return additive, config, root, rows


def _grant(additive, additive_sha, ordinal, deadline):
    item = additive["items"][ordinal]
    checkpoint = (
        Path(additive["root"])
        / storage.STORAGE_DIRECTORY
        / "migrations"
        / additive_sha
        / f"{ordinal:04}.complete.json"
    )
    value, _ = migration._json(checkpoint, deadline)
    if (
        value.get("review_sha256") != additive_sha
        or value.get("item") != item
        or value.get("originals_retained") is not True
    ):
        raise ValueError("isolated_archive_completed_additive_item_required")
    grant = h.load_grant(value["grant_path"], value["grant_sha256"], deadline_at=deadline)
    if (
        grant.path != Path(item["path"])
        or grant.value["kind"] != item["kind"]
        or grant.value["review_sha256"] != additive_sha
    ):
        raise ValueError("isolated_archive_grant_scope_changed")
    grant.check(
        deadline_at=deadline, expected_sha256=item["sha256"], original_identity=item["identity"]
    )
    return grant


def _source(item, row, deadline):
    if row.get("sha256") != item["sha256"] or row.get("copy_identity") != item["identity"]:
        raise ValueError("isolated_archive_original_copy_binding_changed")
    source = h.record(row["source"], deadline_at=deadline, max_bytes=h.MAX_ARTIFACT)
    if source["sha256"] != item["sha256"] or source["identity"] != row.get("source_identity"):
        raise ValueError("isolated_archive_retained_source_changed")
    if source["identity"][:2] == item["identity"][:2]:
        raise ValueError("isolated_archive_hardlinked_source")


def _native_dependency(config, item, deadline):
    if not config.get("publication_worker_database"):
        return
    path = h.canonical(config["publication_worker_database"])
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=0)) as conn:
        conn.set_progress_handler(lambda: int(time.monotonic() >= deadline), 256)
        if conn.execute(
            "SELECT 1 FROM sqlite_master WHERE name='jobagg_legacy_dependencies'"
        ).fetchone():
            if conn.execute(
                "SELECT 1 FROM jobagg_legacy_dependencies WHERE path=?", (item["path"],)
            ).fetchone():
                # An offline grant alone does not migrate ordinary catalogue
                # consumers. Preserve those original files pending that review.
                raise ValueError("isolated_archive_native_dependency_consumer_review_required")
    h.check(deadline)


def _producer(config, root, item, deadline):
    _native_dependency(config, item, deadline)
    record = migration._producer(config, root, item, deadline)
    if record["identity"] != item["producer_identity"]:
        raise ValueError("isolated_archive_original_producer_changed")
    controls = []
    if item["kind"] == "snapshot":
        # Reuse existing derived-snapshot retention eligibility. Recovery variants
        # remain hot until separately reviewed; do not follow arbitrary controls.
        run = Path(item["path"]).parent
        if (run / "publication_resolution.json").exists() or (run / "publication_resolution.json").is_symlink():
            raise ValueError("isolated_archive_recovered_snapshot_requires_review")
        for name in (
            "publication_request.json",
            "acceptance.json",
            "outcome.json",
            "publication_result.json",
        ):
            controls.append(h.record(run / name, deadline_at=deadline, max_bytes=h.MAX_CONTROL))
        from jobagg.storage_retention import _run_group

        group, _ = _run_group(run, deadline_at=deadline)
        if group is None:
            raise ValueError("isolated_archive_unresolved_snapshot_protected")
        if any(h.identity(row["path"]) != row["identity"] for row in controls):
            raise ValueError("isolated_archive_retention_control_changed")
    return {"producer": record, "terminal_controls": controls}


def _original(item, *, absent_allowed):
    path = h.canonical(item["path"])
    if not path.exists() and not path.is_symlink():
        if not absent_allowed:
            raise ValueError("isolated_archive_missing_original_without_intent")
        return False
    if h.identity(path) != item["identity"] or path.stat().st_nlink != 1:
        raise ValueError("isolated_archive_original_changed_or_linked")
    return True


def _verify(grant, scratch, deadline):
    if grant.value["kind"] == "plan":
        from jobagg.historical_plans import read_plan

        read_plan(
            grant.path,
            expected_sha256=grant.value["logical_sha256"],
            deadline_at=deadline,
            max_bytes=h.MAX_ARTIFACT,
            archive=grant,
        )
    else:
        grant.verify(deadline_at=deadline)
    if grant.value["kind"] == "snapshot":
        with grant.restore_snapshot(
            scratch_parent=scratch, deadline_at=deadline, reserve_bytes=migration.RESERVE
        ):
            pass


def prepare(
    additive_path, additive_sha, *, snapshot_path, snapshot_sha, ordinals, owner_fd, deadline_at
):
    """Compile an isolated-only removal after additive verification and copy review."""
    additive, config, root, rows = _context(
        additive_path, additive_sha, snapshot_path, snapshot_sha, owner_fd, deadline_at
    )
    if (
        not isinstance(ordinals, list)
        or not 0 < len(ordinals) <= migration.MAX_ITEMS
        or len(set(ordinals)) != len(ordinals)
        or any(type(i) is not int or not 0 <= i < len(additive["items"]) for i in ordinals)
    ):
        raise ValueError("isolated_archive_finite_unique_selection_required")
    dependency, _ = h.document(
        additive["dependency_review_path"],
        additive["dependency_review_sha256"],
        deadline_at=deadline_at,
    )
    protected = [
        h.canonical(p) for p in additive["protection"]["protected"] + dependency["protected_paths"]
    ]
    # A legacy producer is still read directly by the additive compiler. Retain it
    # even if its own compressed representation exists; no reader waiver here.
    producers = {i["producer_path"] for i in additive["items"]}
    selected = []
    for ordinal in ordinals:
        item = additive["items"][ordinal]
        path = h.canonical(item["path"])
        if (
            str(path) not in rows
            or str(path) in producers
            or any(path.is_relative_to(p) for p in protected)
        ):
            raise ValueError("isolated_archive_protected_or_unmapped_input")
        eligibility = _producer(config, root, item, deadline_at)
        _source(item, rows[str(path)], deadline_at)
        _original(item, absent_allowed=False)
        grant = _grant(additive, additive_sha, ordinal, deadline_at)
        grant.verify(deadline_at=deadline_at)
        selected.append(
            {
                "ordinal": ordinal,
                "item": item,
                "copy": rows[str(path)],
                "eligibility": eligibility,
                "grant_path": grant.control_path,
                "grant_sha256": grant.sha256,
            }
        )
    if sum(row["item"]["size_bytes"] for row in selected) > 8 * 1024**3:
        raise ValueError("isolated_archive_page_byte_bound")
    return {
        "schema": SCHEMA,
        "additive_path": str(h.canonical(additive_path)),
        "additive_sha256": additive_sha,
        "snapshot_path": str(h.canonical(snapshot_path)),
        "snapshot_sha256": snapshot_sha,
        "root": str(root),
        "items": selected,
        "production_mode": False,
    }


def _checkpoint(path, expected, deadline):
    if not path.exists() and not path.is_symlink():
        return None
    value, _ = migration._json(path, deadline)
    if any(value.get(k) != v for k, v in expected.items()):
        raise ValueError("isolated_archive_checkpoint_binding_changed")
    return value


def _summary(plan, plan_sha, directory, deadline):
    confirmed, unknown, complete = 0, 0, 0
    for number, selected in enumerate(plan["items"]):
        binding = {"plan_sha256": plan_sha, "index": number, "selection": selected}
        removed_path = directory / f"{number:04}.removed.json"
        removed = _checkpoint(removed_path, binding, deadline)
        done = _checkpoint(directory / f"{number:04}.complete.json", binding, deadline)
        intent = _checkpoint(directory / f"{number:04}.intent.json", binding, deadline)
        if removed:
            if (
                intent is None
                or type(removed.get("absent_after_open_intent")) is not bool
                or type(removed.get("removed_bytes")) is not int
                or removed["removed_bytes"]
                != (0 if removed["absent_after_open_intent"] else selected["item"]["size_bytes"])
            ):
                raise ValueError("isolated_archive_invalid_removal_credit")
            confirmed += removed["removed_bytes"]
            unknown += selected["item"]["size_bytes"] if removed["absent_after_open_intent"] else 0
        if done:
            if not removed or hash_manifest(removed_path, deadline) != done["removed_sha256"]:
                raise ValueError("isolated_archive_removal_receipt_changed")
            h.load_grant(selected["grant_path"], selected["grant_sha256"], deadline_at=deadline)
            complete += 1
    return confirmed, unknown, complete


def execute(
    plan_path,
    plan_sha,
    *,
    owner_fd,
    scratch_parent,
    deadline_at,
    start=0,
    max_items=4,
    max_bytes=2 * 1024**3,
    fault=lambda _: None,
):
    """Remove only mapped isolated originals; resume without inventing byte credit."""
    _budget(deadline_at)
    plan, _ = h.document(plan_path, plan_sha, deadline_at=deadline_at)
    if plan.get("schema") != SCHEMA or plan.get("production_mode") is not False:
        raise ValueError("isolated_archive_no_production_mode")
    additive, config, root, rows = _context(
        plan["additive_path"],
        plan["additive_sha256"],
        plan["snapshot_path"],
        plan["snapshot_sha256"],
        owner_fd,
        deadline_at,
    )
    if (
        plan["root"] != str(root)
        or not 0 < len(plan["items"]) <= migration.MAX_ITEMS
        or type(start) is not int
        or not 0 <= start < len(plan["items"])
        or type(max_items) is not int
        or not 0 < max_items <= migration.MAX_ITEMS
        or type(max_bytes) is not int
        or not 0 < max_bytes <= 8 * 1024**3
    ):
        raise ValueError("isolated_archive_finite_execution_page_required")
    scratch = h.canonical(scratch_parent)
    if (
        not scratch.is_dir()
        or scratch == root
        or not scratch.is_relative_to(root)
        or not h.canonical(plan_path).is_relative_to(root)
    ):
        raise ValueError("isolated_archive_copy_owned_scratch_required")
    directory = root / storage.STORAGE_DIRECTORY / "isolated-removals" / plan_sha
    h.canonical(directory)
    directory.mkdir(parents=True, mode=0o700, exist_ok=True)
    _summary(plan, plan_sha, directory, deadline_at)
    visited, consumed, new_credit, index = 0, 0, 0, start
    for index in range(start, len(plan["items"])):
        selected = plan["items"][index]
        ordinal, item = selected["ordinal"], selected["item"]
        if visited >= max_items or consumed + item["size_bytes"] > max_bytes:
            break
        _context(
            plan["additive_path"],
            plan["additive_sha256"],
            plan["snapshot_path"],
            plan["snapshot_sha256"],
            owner_fd,
            deadline_at,
        )
        if additive["items"][ordinal] != item or rows.get(item["path"]) != selected["copy"]:
            raise ValueError("isolated_archive_item_changed")
        if _producer(config, root, item, deadline_at) != selected["eligibility"]:
            raise ValueError("isolated_archive_eligibility_changed")
        _source(item, selected["copy"], deadline_at)
        grant = _grant(additive, plan["additive_sha256"], ordinal, deadline_at)
        if grant.control_path != selected["grant_path"] or grant.sha256 != selected["grant_sha256"]:
            raise ValueError("isolated_archive_grant_changed")
        _verify(grant, scratch, deadline_at)
        binding = {"plan_sha256": plan_sha, "index": index, "selection": selected}
        done_path = directory / f"{index:04}.complete.json"
        done = _checkpoint(done_path, binding, deadline_at)
        intent_path = directory / f"{index:04}.intent.json"
        intent = _checkpoint(intent_path, binding, deadline_at)
        present = _original(item, absent_allowed=intent is not None)
        for anchor in (root, scratch, grant.path.parent):
            if shutil.disk_usage(anchor).free < migration.RESERVE + 2 * 1024**2:
                raise ValueError("isolated_archive_physical_reserve_unavailable")
        if done is not None:
            if present:
                raise ValueError("isolated_archive_completed_original_reappeared")
        else:
            if intent is None:
                h.exclusive(intent_path, h.dump(binding))
            fault("after_intent")
            removal_path = directory / f"{index:04}.removed.json"
            removal = _checkpoint(removal_path, binding, deadline_at)
            if removal is not None:
                if present:
                    raise ValueError("isolated_archive_receipted_original_reappeared")
            else:
                fault("before_removal")
                _context(
                    plan["additive_path"],
                    plan["additive_sha256"],
                    plan["snapshot_path"],
                    plan["snapshot_sha256"],
                    owner_fd,
                    deadline_at,
                )
                present = _original(item, absent_allowed=intent is not None)
                if _producer(config, root, item, deadline_at) != selected["eligibility"]:
                    raise ValueError("isolated_archive_eligibility_changed")
                _source(item, selected["copy"], deadline_at)
                _verify(grant, scratch, deadline_at)
                manifest = root / storage.STORAGE_DIRECTORY / storage.MANIFEST_NAME
                with storage._store_lock(
                    manifest, wait_seconds=min(5, max(0, deadline_at - time.monotonic()))
                ):
                    current = storage._read_manifest(manifest, root)
                    entry = current["artifacts"].get(str(grant.path.relative_to(root)))
                    if (
                        entry is None
                        or entry["logical_sha256"] != item["sha256"]
                        or entry["logical_bytes"] != item["size_bytes"]
                    ):
                        raise ValueError("isolated_archive_manifest_binding_changed")
                    if not any(
                        r["physical_path"] == str(grant.physical.relative_to(root))
                        and r["physical_sha256"] == grant.value["physical_sha256"]
                        and r["physical_bytes"] == grant.value["physical_identity"][2]
                        for r in entry["representations"]
                    ):
                        raise ValueError("isolated_archive_manifest_representation_changed")
                    if present and (entry["legacy"]["retired"] or entry["legacy"]["removed_bytes"]):
                        raise ValueError("isolated_archive_prior_retirement_changed")
                    with storage.stream_artifact(
                        root,
                        grant.path,
                        expected_sha256=item["sha256"],
                        expected_bytes=item["size_bytes"],
                        deadline_at=deadline_at,
                        manifest_path=grant.manifest,
                    ) as stream:
                        descriptor = stream.descriptor
                        stream.abandon()
                    if not present:
                        # The earlier unlink may have preceded a failed directory
                        # sync. Make observed absence durable before checkpointing.
                        storage._sync_dir(grant.path.parent)
                    removed, manifest_sha = storage._retire_legacy(
                        root,
                        {"manifest": manifest},
                        descriptor,
                        manifest_sha=hash_manifest(manifest, deadline_at),
                        deadline_at=deadline_at,
                    )
                fault("after_removal")
                removal = {
                    **binding,
                    "removed_bytes": removed if present else 0,
                    "absent_after_open_intent": not present,
                    "manifest_sha256": manifest_sha,
                }
                h.check(deadline_at)
                h.exclusive(removal_path, h.dump(removal))
                new_credit += removal["removed_bytes"]
            fault("after_removal_receipt")
            _context(
                plan["additive_path"],
                plan["additive_sha256"],
                plan["snapshot_path"],
                plan["snapshot_sha256"],
                owner_fd,
                deadline_at,
            )
            _verify(grant, scratch, deadline_at)
            if _original(item, absent_allowed=True):
                raise ValueError("isolated_archive_removal_readback_failed")
            h.exclusive(
                done_path,
                h.dump({**binding, "removed_sha256": hash_manifest(removal_path, deadline_at)}),
            )
            fault("after_checkpoint")
        visited += 1
        consumed += item["size_bytes"]
    else:
        index = len(plan["items"])
    confirmed, unknown, complete = _summary(plan, plan_sha, directory, deadline_at)
    return {
        "visited": visited,
        "next_index": index,
        "complete": complete == len(plan["items"]),
        "completed": complete,
        "new_confirmed_removed_bytes": new_credit,
        "confirmed_removed_bytes": confirmed,
        "uncredited_absent_bytes": unknown,
        "headroom_granted": False,
        "production_readiness_granted": False,
        "production_activated": False,
    }


def hash_manifest(path, deadline):
    return h.record(path, deadline_at=deadline, max_bytes=32 * 1024**2)["sha256"]
