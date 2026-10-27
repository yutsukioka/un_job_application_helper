"""Explicit copy-only completion of a proved idle control rollback's token.

Never called by startup. The independent ahead head is not replaced or rewound.
The one permitted SQL mutation adopts that already attempted token, after an
operator-reviewed before-state, complete byte/integrity preview and both fences.
"""

from contextlib import closing
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import time

from jobagg import runtime_authority as authority, runtime_source_head as source
from jobagg.pending_publication import marker_path
from jobagg.publication_fence import publication_scope, verify_boundary
from jobagg.publication_snapshot import stable_file, _stamp, _record_stamp
from jobagg.verification_budget import scope as budget_scope

SCHEMA = "isolated-idle-control-rollback-plan-1"


def _dump(value):
    return json.dumps(value, sort_keys=True, allow_nan=False)


def _digest(value):
    return hashlib.sha256(_dump(value).encode()).hexdigest()


def _check(deadline):
    if not math.isfinite(deadline) or not 0 < deadline - time.monotonic() <= 900:
        raise TimeoutError("finite_isolated_control_repair_budget_required")


def _direct(path):
    path = Path(path)
    if not path.is_absolute() or path.resolve() != path:
        raise ValueError("isolated_repair_direct_path_required")
    return path


def _read(path, expected, deadline, maximum=16 * 1024**2):
    path = _direct(path)
    if path.stat().st_size > maximum or not re.fullmatch("[a-f0-9]{64}", str(expected)):
        raise ValueError("isolated_repair_bounded_pinned_control_required")
    pin = stable_file(path, deadline_at=deadline)
    if pin["sha256"] != expected:
        raise ValueError("isolated_repair_control_hash_changed")
    value = json.loads(path.read_text())
    if tuple(_stamp(path.stat())) != _record_stamp(pin):
        raise ValueError("isolated_repair_control_identity_changed")
    return value, pin


def _zero_wal(database):
    path = Path(str(database) + "-wal")
    if os.path.lexists(path) and (
        path.resolve() != path or not path.is_file() or path.stat().st_size != 0
    ):
        raise ValueError("isolated_repair_requires_checkpointed_copy")


def _unchanged(pins, deadline):
    _check(deadline)
    for pin in pins:
        if tuple(_stamp(Path(pin["path"]).stat())) != _record_stamp(pin):
            raise ValueError("isolated_repair_preview_dependency_changed")


def _drained(deadline):
    _check(deadline)
    result = subprocess.run(
        ["ps", "-axo", "pid=,command="],
        capture_output=True,
        text=True,
        check=True,
        timeout=min(5, deadline - time.monotonic()),
    )
    if len(result.stdout) > 2 * 1024**2:
        raise ValueError("isolated_repair_process_census_bound")
    pattern = r"(?:^|\s)(?:-m\s+)?jobagg\.(?:remediation_worker|publish_worker)(?:\s|$)|/proposed_runner/runner\.py(?:\s|$)"
    if any(re.search(pattern, line) for line in result.stdout.splitlines()):
        raise ValueError("isolated_repair_writer_or_publisher_present")
    return {
        "matching_processes": 0,
        "process_census_sha256": hashlib.sha256(result.stdout.encode()).hexdigest(),
    }


def _context(conn, review_path, review_sha256, owner_fd, deadline):
    """All non-integrity predicates are repeated before update and on resume."""
    _check(deadline)
    review, reviewed = _read(review_path, review_sha256, deadline)
    from jobagg.remediation_worker import implementation_hash

    if (
        review.get("schema") != "isolated-idle-control-rollback-review-1"
        or review.get("control_only_rollback_reviewed") is not True
        or review.get("implementation_sha256") != implementation_hash()
    ):
        raise ValueError("isolated_repair_explicit_matching_review_required")
    root = _direct(review["snapshot_root"])
    database = root / "worker/jobs.sqlite3"
    if source._database(conn) != review["database"] or source._database(conn)["path"] != str(
        database
    ):
        raise ValueError("isolated_repair_database_identity_changed")
    current = authority._validate_structure(conn)
    source.owner(owner_fd, current["owner_path"])
    before, before_pin = _read(review["before_path"], review["before_sha256"], deadline)
    expected = before["source_state"]
    if expected["phase"] != "idle" or expected["run_id"] is not None:
        raise ValueError("isolated_repair_only_initial_idle_control_rollback")
    bootstrap = json.loads(expected["bootstrap_json"])
    snapshot, snapshot_pin = _read(
        root / "snapshot.json", bootstrap["source_sha256"], deadline, 64 * 1024**2
    )
    original, original_pin = _read(
        snapshot["production_config"], snapshot["production_config_sha256"], deadline
    )
    if (
        snapshot.get("snapshot_root") != str(root)
        or snapshot.get("complete") is not True
        or current["owner_path"] != original["shared_lock_path"]
        or database.samefile(original["publication_worker_database"])
    ):
        raise ValueError("isolated_repair_requires_exact_separate_copy")
    for name in ("publication_worker_database", "state_dir", "shared_lock_path"):
        production = _direct(original[name]).parent
        if root == production or root in production.parents or production in root.parents:
            raise ValueError("isolated_repair_overlaps_production")
    config, config_pin = _read(
        review["configuration_path"], before["configuration_sha256"], deadline
    )
    if (
        config["publication_worker_database"] != str(database)
        or config["shared_lock_path"] != current["owner_path"]
    ):
        raise ValueError("isolated_repair_configuration_copy_changed")
    state_dir = _direct(config["state_dir"])
    copy_marker = _direct(config["maintenance_file"])
    if not state_dir.is_relative_to(root) or not copy_marker.is_relative_to(root):
        raise ValueError("isolated_repair_requires_copy_owned_controls")
    production_marker = stable_file(_direct(original["maintenance_file"]), deadline_at=deadline)
    copy_pin = stable_file(copy_marker, deadline_at=deadline)
    if (
        production_marker["sha256"] != snapshot["fence_sha256"]
        or copy_pin["sha256"] != before["copied_marker_sha256"]
    ):
        raise ValueError("isolated_repair_maintenance_fence_changed")
    failure, failure_pin = _read(review["failure_path"], review["failure_sha256"], deadline)
    if (
        failure.get("configuration_sha256") != before["configuration_sha256"]
        or failure.get("production_activated") is not False
        or failure.get("production_fence_removed") is not False
        or failure.get("copy_marker_restored") is not True
        or failure.get("failure") != "OperationalError('disk I/O error')"
        or "begin_publication_only" not in failure.get("traceback", "")
        or failure.get("worker_dispatched", False) is not False
        or failure.get("publisher_dispatched", False) is not False
        or failure.get("interrupted_admission") is not None
    ):
        raise ValueError("isolated_repair_failed_pre_admission_proof_required")
    if os.path.lexists(marker_path(state_dir)):
        raise ValueError("isolated_repair_accepted_marker_present")
    # No new run existed at this control-only failure boundary.
    if (state_dir / "runs").exists() and next((state_dir / "runs").iterdir(), None) is not None:
        raise ValueError("isolated_repair_run_or_publication_controls_present")
    gate_path = root / "output/.jobagg-publication-state.json"
    gate_pin = stable_file(gate_path, deadline_at=deadline)
    gate, _ = _read(gate_path, gate_pin["sha256"], deadline)
    if gate.get("state") != "complete" or gate.get("unresolved_publication") is True:
        raise ValueError("isolated_repair_unresolved_generation")
    registry_path = _direct(review["registry_path"])
    if registry_path.stat().st_size > 1024**2:
        raise ValueError("isolated_repair_registry_bound")
    registry_pin = stable_file(registry_path, deadline_at=deadline)
    if registry_pin["sha256"] != review["registry_sha256"]:
        raise ValueError("isolated_repair_registry_changed")
    boundary = json.loads(expected["control_json"]).get("publication_cursor")
    if not verify_boundary(
        boundary, expected_scope=publication_scope(review["registry_path"], root / "output")
    ):
        raise ValueError("isolated_repair_export_boundary_changed")
    head_path = _direct(bootstrap["restore_head_path"])
    if not bootstrap.get("restore_head_root") or not _direct(
        bootstrap["restore_head_root"]
    ).is_relative_to(root):
        raise ValueError("isolated_repair_requires_copy_owned_external_head")
    ahead, head_pin = _read(head_path, review["head_sha256"], deadline, 65536)
    after = {**expected, "source_transaction_id": ahead["transaction_id"]}
    if (
        ahead["minimum_revision"] != expected["revision"]
        or ahead["transaction_id"] == expected["source_transaction_id"]
    ):
        raise ValueError("isolated_repair_unknown_or_domain_transaction")
    source.validate(conn, after)
    if current not in (expected, after):
        raise ValueError("isolated_repair_state_changed_or_partial_commit")
    if current == expected:
        _zero_wal(database)
    pins = [
        reviewed,
        before_pin,
        snapshot_pin,
        original_pin,
        config_pin,
        production_marker,
        copy_pin,
        failure_pin,
        gate_pin,
        registry_pin,
        head_pin,
    ]
    _unchanged(pins, deadline)
    return {
        "review": review,
        "before": expected,
        "after": after,
        "pins": pins,
        "database": database,
        "current": current,
    }


def preview(*, review_path, review_sha256, owner_fd, deadline_at):
    """Read-only bounded integrity/byte preview. A timeout grants no repair."""
    _check(deadline_at)
    review, _ = _read(review_path, review_sha256, deadline_at)
    database = _direct(review["database"]["path"])
    with (
        budget_scope(deadline_at),
        closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True, timeout=1)) as conn,
    ):
        conn.set_progress_handler(lambda: int(time.monotonic() >= deadline_at), 256)
        context = _context(conn, review_path, review_sha256, owner_fd, deadline_at)
        if context["current"] != context["before"]:
            raise ValueError("isolated_repair_preview_requires_original_uncertainty")
        processes = _drained(deadline_at)
        integrity = [tuple(row) for row in conn.execute("PRAGMA quick_check(1)")]
        if integrity != [("ok",)]:
            raise ValueError("isolated_repair_integrity_unproven")
        database_pin = stable_file(database, deadline_at=deadline_at)
        _unchanged(context["pins"], deadline_at)
        source.owner(owner_fd, context["before"]["owner_path"])
        if authority._validate_structure(conn) != context["before"]:
            raise ValueError("isolated_repair_state_changed_during_preview")
    value = {
        "schema": SCHEMA,
        "review_path": str(review_path),
        "review_sha256": review_sha256,
        "snapshot_root": review["snapshot_root"],
        "before": context["before"],
        "after": context["after"],
        "database_pin": database_pin,
        "dependencies": context["pins"],
        "integrity": "ok",
        "processes": processes,
        "production_activated": False,
    }
    value["sha256"] = _digest(value)
    return value


def _exclusive(path, value):
    path = _direct(path)
    payload = (_dump(value) + "\n").encode()
    if os.path.lexists(path):
        if (
            not path.is_file()
            or path.stat().st_size != len(payload)
            or path.read_bytes() != payload
        ):
            raise ValueError("isolated_repair_existing_intent_or_receipt_differs")
        return
    descriptor = os.open(
        path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600
    )
    with os.fdopen(descriptor, "wb") as out:
        out.write(payload)
        out.flush()
        os.fsync(out.fileno())
    directory = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def _cas(conn, before, after):
    changed = conn.execute(
        "UPDATE jobagg_runtime_state SET source_transaction_id=? WHERE id=1 AND source_transaction_id=? AND epoch=? AND revision=? AND phase='idle' AND run_id IS NULL",
        (
            after["source_transaction_id"],
            before["source_transaction_id"],
            before["epoch"],
            before["revision"],
        ),
    ).rowcount
    if changed != 1:
        raise ValueError("isolated_repair_compare_and_set_changed")


def execute(*, plan_path, plan_sha256, intent_path, owner_fd, deadline_at, fault=lambda _: None):
    """Complete one reviewed SQL token handoff; preserve the ahead head bytes."""
    _check(deadline_at)
    plan, _ = _read(plan_path, plan_sha256, deadline_at, 16 * 1024**2)
    if (
        plan.get("schema") != SCHEMA
        or plan.get("sha256") != _digest({k: v for k, v in plan.items() if k != "sha256"})
        or plan.get("integrity") != "ok"
    ):
        raise ValueError("isolated_repair_matching_integrity_preview_required")
    root = _direct(plan["snapshot_root"])
    intent_path = _direct(intent_path)
    if not intent_path.is_relative_to(root) or not _direct(plan_path).is_relative_to(root):
        raise ValueError("isolated_repair_journal_must_remain_in_copy")
    intent = {
        "schema": "isolated-idle-control-rollback-intent-1",
        "plan_sha256": plan_sha256,
        "review_sha256": plan["review_sha256"],
        "before": plan["before"],
        "after": plan["after"],
    }
    existed = os.path.lexists(intent_path)
    if existed:
        _exclusive(intent_path, intent)
    database = _direct(plan["database_pin"]["path"])
    completion_path = intent_path.with_name(intent_path.name + ".completed.json")
    with (
        budget_scope(deadline_at),
        closing(
            sqlite3.connect(
                database.as_uri() + "?mode=rw",
                uri=True,
                timeout=1,
                isolation_level=None,
                cached_statements=0,
            )
        ) as conn,
    ):
        conn.set_progress_handler(lambda: int(time.monotonic() >= deadline_at), 256)
        context = _context(conn, plan["review_path"], plan["review_sha256"], owner_fd, deadline_at)
        if context["before"] != plan["before"] or context["after"] != plan["after"]:
            raise ValueError("isolated_repair_preview_state_or_head_changed")
        _drained(deadline_at)
        if context["current"] == context["after"]:
            if not existed:
                raise ValueError("isolated_repair_committed_token_requires_original_intent")
        else:
            _unchanged(plan["dependencies"], deadline_at)
            try:
                _unchanged([plan["database_pin"]], deadline_at)
            except ValueError:
                # The same exact intent may have rolled back its token-only SQL.
                # A physical replacement is still refused by the native identity.
                if (
                    not existed
                    or stable_file(database, deadline_at=deadline_at)["sha256"]
                    != plan["database_pin"]["sha256"]
                ):
                    raise ValueError("isolated_repair_database_changed_after_preview")
            _exclusive(intent_path, intent)
            fault("before_update")
            committing = False

            def guard():
                _check(deadline_at)
                source.owner(owner_fd, context["before"]["owner_path"])
                _unchanged(context["pins"], deadline_at)

            def authorize(action, first, second, database_name, trigger):
                if action == sqlite3.SQLITE_UPDATE:
                    return (
                        sqlite3.SQLITE_OK
                        if (first, second, database_name, trigger)
                        == ("jobagg_runtime_state", "source_transaction_id", "main", None)
                        else sqlite3.SQLITE_DENY
                    )
                if action == sqlite3.SQLITE_TRANSACTION:
                    if first == "COMMIT":
                        try:
                            guard()
                            if not committing:
                                return sqlite3.SQLITE_DENY
                        except (ValueError, OSError, TimeoutError):
                            return sqlite3.SQLITE_DENY
                    return sqlite3.SQLITE_OK
                if action in (sqlite3.SQLITE_READ, sqlite3.SQLITE_SELECT, sqlite3.SQLITE_FUNCTION):
                    return sqlite3.SQLITE_OK
                if (
                    action == sqlite3.SQLITE_PRAGMA
                    and first in ("table_list", "database_list")
                    and second is None
                ):
                    return sqlite3.SQLITE_OK
                return sqlite3.SQLITE_DENY

            def mutation():
                guard()
                return int(authority.state(conn) == context["before"])

            conn.create_function("jobagg_runtime_mutation_allowed", 0, mutation)
            conn.set_authorizer(authorize)
            try:
                conn.execute("BEGIN IMMEDIATE")
                guard()
                if authority._validate_structure(conn) != context["before"]:
                    raise ValueError("isolated_repair_state_changed_before_update")
                _cas(conn, context["before"], context["after"])
                fault("before_commit")
                if authority.validate(conn) != context["after"]:
                    raise ValueError("isolated_repair_token_readback_failed")
                committing = True
                conn.execute("COMMIT")
                fault("after_commit")
            finally:
                if conn.in_transaction:
                    conn.rollback()
                conn.set_authorizer(None)
                conn.create_function("jobagg_runtime_mutation_allowed", 0, lambda: 0)
        if authority.validate(conn) != context["after"]:
            raise ValueError("isolated_repair_committed_state_differs")
        _unchanged(context["pins"], deadline_at)
        receipt = {
            "schema": "isolated-idle-control-rollback-completed-1",
            "plan_sha256": plan_sha256,
            "review_sha256": plan["review_sha256"],
            "state": context["after"],
            "independent_head_rewritten": False,
            "domain_writes_permitted": False,
            "production_activated": False,
        }
        _exclusive(completion_path, receipt)
        return receipt
