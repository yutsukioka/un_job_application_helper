"""Explicit containment of old control debt after current shared-state review.

A containment receipt never reconstructs an exit, publishes a historical
version, permits replay/retirement or earns empty-backlog credit. It allows only
new native work behind exact current task/unit gates. Ordinary entry reads a
single protected receipt; the complete review is an offline owner operation.
"""

from contextlib import closing
import hashlib
import json
import math
from pathlib import Path
import sqlite3
import time
from types import SimpleNamespace

from jobagg import runtime_authority, runtime_control_catalog as catalogue, runtime_holds
from jobagg.accepted_evidence import digest, selected_attempts, resolve_listing_event, resolve_event
from jobagg.publication_snapshot import _stamp, _record_stamp, stable_file
from jobagg.remediation_scheduling import task_key
from jobagg.policy_authority import PolicyAuthority

KEY = "runtime-history-containment"
SCHEMA = "native-history-containment-1"
KNOWN = {
    "terminal_verified",
    "reference_preserved",
    "repair_required",
    "accepted_pending",
    "worker_claim_uncertain",
    "publication_reconciliation_required",
}


def _check(deadline):
    if time.monotonic() >= deadline:
        raise TimeoutError("historical_containment_review_deadline")


def _read(path, expected, deadline, *, maximum=16 * 1024**2):
    path = Path(path)
    if not path.is_absolute() or path.resolve() != path or path.stat().st_size > maximum:
        raise ValueError("bounded_canonical_containment_review_required")
    record = stable_file(path, deadline_at=deadline)
    if record["sha256"] != expected:
        raise ValueError("containment_review_bytes_changed")
    value = json.loads(path.read_text())
    if tuple(_stamp(path.stat())) != _record_stamp(record):
        raise ValueError("containment_review_identity_changed")
    return value, record


def _policy(location, owner):
    root = Path(location["root"])
    if not root.is_absolute() or root.resolve() != root:
        raise ValueError("containment_policy_location_aliased")
    policy = SimpleNamespace(root=root, owner=Path(owner), native_epoch=location["epoch"])
    authority = PolicyAuthority(policy)
    authority.revision()
    return authority


def get(conn):
    if not runtime_holds.exists(conn):
        return None
    row = conn.execute(
        "SELECT review_json FROM jobagg_legacy_review_holds WHERE run_id=?", (KEY,)
    ).fetchone()
    return json.loads(row[0]) if row else None


def summary(conn):
    value = get(conn)
    if not value or value.get("schema") != SCHEMA:
        return None
    return {
        "historical_control_records_held": value["held_records"],
        "total_reviewed_control_records": value["total_records"],
        "original_successful_exits_reconstructed": False,
        "historical_replay_authorized": False,
        "retirement_authorized": False,
        "review_sha256": value["review_sha256"],
        "admission_review_valid": not bool(value.get("invalidated")),
        "invalidation_reason": value.get("invalidated"),
    }


def approved(conn, *, config_sha256, implementation_sha256, policy_location):
    value = get(conn)
    state = runtime_authority.state(conn)
    if not value or value.get("invalidated") or state is None:
        return False
    imported = catalogue.receipt(conn)
    if (
        value.get("schema") != SCHEMA
        or value.get("epoch") != state["epoch"]
        or value.get("worker_database") != conn.execute("PRAGMA database_list").fetchone()[2]
        or value.get("owner") != state["owner_path"]
        or value.get("config_sha256") != config_sha256
        or value.get("implementation_sha256") != implementation_sha256
        or value.get("catalogue_sha256") != imported["sha256"]
        or value.get("inventory_sha256") != imported["inventory_sha256"]
        or value.get("total_records") != imported["runs"]
        or value.get("policy_location") != policy_location
    ):
        return False
    _policy(policy_location, state["owner_path"])
    return True


def invalidate(conn, reason):
    value = get(conn)
    if value and not value.get("invalidated"):
        value = {**value, "invalidated": reason}
        conn.execute(
            "UPDATE jobagg_legacy_review_holds SET review_json=? WHERE run_id=?",
            (json.dumps(value, sort_keys=True), KEY),
        )


def prepare(conn, *, review_path, review_sha256, owner_fd, deadline_at):
    """Complete exact control inventory + current evidence/charge containment.

    Caller holds the actual owner and native controller on an idle copied source.
    All records remain auditable and no original disposition is rewritten here.
    """
    if not math.isfinite(deadline_at) or not 0 < deadline_at - time.monotonic() <= 300:
        raise ValueError("finite_historical_containment_review_required")
    _check(deadline_at)
    state = runtime_authority.validate(conn)
    from jobagg.runtime_source_head import owner

    owner(owner_fd, state["owner_path"])
    if state["phase"] != "idle":
        raise ValueError("containment_requires_idle_owner")
    review, review_record = _read(review_path, review_sha256, deadline_at)
    from jobagg.remediation_worker import implementation_hash

    if review.get("implementation_sha256") != implementation_hash():
        raise ValueError("containment_reviewed_implementation_changed")
    imported = catalogue.receipt(conn)
    if (
        review.get("schema") != "current-shared-state-containment-review-1"
        or review.get("epoch") != state["epoch"]
        or review.get("inventory_sha256") != imported["inventory_sha256"]
        or review.get("catalogue_sha256") != imported["sha256"]
    ):
        raise ValueError("containment_catalogue_review_changed")
    root = Path(review["snapshot_root"])
    database = Path(conn.execute("PRAGMA database_list").fetchone()[2])
    if root.resolve() != root or database != root / "worker/jobs.sqlite3":
        raise ValueError("containment_exact_staged_copy_required")
    bootstrap = json.loads(state["bootstrap_json"])
    snapshot, _ = _read(
        root / "snapshot.json", bootstrap["source_sha256"], deadline_at, maximum=64 * 1024**2
    )
    config, _ = _read(
        snapshot["production_config"], snapshot["production_config_sha256"], deadline_at
    )
    if (
        snapshot.get("snapshot_root") != str(root)
        or snapshot.get("complete") is not True
        or database.samefile(config["publication_worker_database"])
        or state["owner_path"] != config["shared_lock_path"]
    ):
        raise ValueError("containment_source_boundary_changed")
    marker = stable_file(Path(config["maintenance_file"]), deadline_at=deadline_at)
    if marker["sha256"] != snapshot["fence_sha256"]:
        raise ValueError("containment_production_fence_changed")
    candidate, _ = _read(review["configuration_path"], review["config_sha256"], deadline_at)
    if candidate.get("policy_authority_location") != review["policy_location"]:
        raise ValueError("containment_policy_not_bound_by_configuration")
    from jobagg.publication_fence import verify_boundary, publication_scope, source_paths

    registry_path = Path(review["registry_path"])
    if (
        registry_path.resolve() != registry_path
        or registry_path.stat().st_size > 1024**2
        or stable_file(registry_path, deadline_at=deadline_at)["sha256"]
        != review["registry_sha256"]
    ):
        raise ValueError("containment_registry_changed")
    known_sources = set(source_paths(review["registry_path"], root / "output"))
    scope = publication_scope(review["registry_path"], root / "output")
    boundary = runtime_authority.publication_control(conn).get("publication_cursor")
    if not verify_boundary(boundary, expected_scope=scope):
        raise ValueError("containment_current_export_boundary_unproven")
    if conn.execute("SELECT 1 FROM jobagg_dirty_current WHERE kind='unknown' LIMIT 1").fetchone():
        raise ValueError("containment_unknown_current_operation")
    for original in (
        Path(config["publication_worker_database"]).parent,
        Path(config["shared_lock_path"]).parent,
        Path(config["state_dir"]),
    ):
        if root == original or root in original.parents or original in root.parents:
            raise ValueError("containment_overlaps_production_root")
    if any(
        source not in known_sources
        for (source,) in conn.execute("SELECT DISTINCT source_id FROM jobagg_dirty_current")
    ):
        raise ValueError("containment_unknown_current_source")
    policy = _policy(review["policy_location"], state["owner_path"])
    if policy.policy.root != root / "policy":
        raise ValueError("containment_requires_isolated_policy_copy")
    with closing(sqlite3.connect(policy.path.as_uri() + "?mode=ro", uri=True)) as accounting:
        accounting.set_progress_handler(lambda: int(time.monotonic() >= deadline_at), 256)
        accounting.execute("ATTACH DATABASE ? AS worker", (database.as_uri() + "?mode=ro",))
        if accounting.execute(
            "SELECT 1 FROM worker.remediation_attempts a LEFT JOIN main.attempts p ON p.attempt_id=a.attempt_id WHERE p.attempt_id IS NULL OR p.source_id!=a.source_id OR p.kind!=a.kind LIMIT 1"
        ).fetchone():
            raise ValueError("containment_unaccounted_charge_or_identity")
        if accounting.execute(
            "SELECT 1 FROM worker.remediation_tasks t WHERE t.attempts!=(SELECT count(*) FROM worker.remediation_attempts a WHERE a.task_id=t.task_id) LIMIT 1"
        ).fetchone():
            raise ValueError("containment_task_attempt_counter_gap")
    cursor = conn.cursor()
    cursor.row_factory = sqlite3.Row
    controls = cursor.execute("SELECT * FROM jobagg_legacy_controls ORDER BY ordinal").fetchall()
    supplied = review.get("controls")
    if (
        not isinstance(supplied, list)
        or len(supplied) != len(controls)
        or len(controls) != imported["runs"]
        or len(controls) > 100000
    ):
        raise ValueError("containment_control_inventory_incomplete")
    held = []
    for actual, expected in zip(controls, supplied):
        _check(deadline_at)
        if (
            actual["run_id"] != expected.get("run_id")
            or actual["disposition"] != expected.get("disposition")
            or hashlib.sha256(actual["proof_json"].encode()).hexdigest()
            != expected.get("proof_sha256")
            or actual["disposition"] not in KNOWN
        ):
            raise ValueError("containment_control_review_changed_or_partial_generation")
        if actual["disposition"] not in {"terminal_verified", "reference_preserved"}:
            if expected.get("action") != "retain_history_hold_no_replay" or not expected.get(
                "next_condition"
            ):
                raise ValueError("containment_history_hold_disposition_required")
            held.append(expected)
    evidence = {v["job_key"]: v for v in review.get("current_details", [])}
    rows = cursor.execute(
        "SELECT j.*,o.proof AS current_proof FROM jobs j JOIN remediation_observations o ON o.job_key=j.job_key ORDER BY j.job_key"
    ).fetchall()
    if (
        len(evidence) != len(review.get("current_details", []))
        or len(evidence) != len(rows)
        or len(rows) > 100000
    ):
        raise ValueError("containment_current_evidence_inventory_incomplete")
    dependencies = [review_record, marker]
    for row in rows:
        _check(deadline_at)
        expected = evidence[row["job_key"]]
        record, pin = _read(expected["path"], expected["sha256"], deadline_at)
        dependencies.append(pin)
        proof = json.loads(row["current_proof"])
        checkpoint = conn.execute(
            "SELECT observation_sha256,disposition FROM jobagg_evidence_backfill WHERE job_key=?",
            (row["job_key"],),
        ).fetchone()
        if (
            record["job_key"] != row["job_key"]
            or record["observation_sha256"] != digest(proof)
            or checkpoint is None
            or checkpoint[0] != digest(proof)
            or record["disposition"] != checkpoint[1]
        ):
            raise ValueError("containment_current_observation_changed")
        if checkpoint[1] == "matched" and not record["retained_evidence"]:
            raise ValueError("containment_current_retained_bytes_unverified")
        for result in record["retained_evidence"]:
            if result["disposition"] != "retained_detail_bytes_and_task_crosswalk_verified":
                if checkpoint[1] == "matched":
                    raise ValueError("containment_current_retained_bytes_unverified")
                continue
            attempt = conn.execute(
                "SELECT evidence,source_id,kind,status FROM remediation_attempts WHERE attempt_id=?",
                (result["attempt_id"],),
            ).fetchone()
            if attempt is None or (attempt[1], attempt[2], attempt[3]) != (
                row["source_id"],
                "detail",
                "done",
            ):
                raise ValueError("containment_current_attempt_identity_changed")
            original = json.loads(attempt[0])
            if result["artifact"]["sha256"] != original["detail_sha256"] or len(
                result["captures"]
            ) != len(proof["captures"]):
                raise ValueError("containment_current_artifact_binding_changed")
            if any(
                c["metadata"]["sha256"] != r["sha256"]
                for c, r in zip(result["captures"], proof["captures"])
            ):
                raise ValueError("containment_original_capture_binding_changed")
            files = [result["artifact"]]
            for capture in result["captures"]:
                files.append(capture["metadata"])
                if capture.get("compressed_body"):
                    files.append(capture["compressed_body"])
            for dependency in files:
                path = Path(dependency["path"])
                if path.resolve() != path or tuple(_stamp(path.stat())) != _record_stamp(
                    dependency
                ):
                    raise ValueError("containment_current_evidence_identity_changed")
                dependencies.append(dependency)
        if checkpoint[1] == "matched":
            if not selected_attempts(conn, row, proof):
                raise ValueError("containment_independent_acceptance_missing")
            from jobagg.remediation_worker import PUBLIC_FIELDS

            fields = digest({field: row[field] for field in PUBLIC_FIELDS})
            body = hashlib.sha256(row["description"].encode()).hexdigest()
            if not conn.execute(
                "SELECT 1 FROM jobagg_accepted_evidence WHERE job_key=? AND proof_sha256=? AND public_fields_sha256=? AND body_sha256=? LIMIT 1",
                (row["job_key"], digest(proof), fields, body),
            ).fetchone():
                raise ValueError("containment_current_public_fields_changed")
        elif (
            runtime_holds.find(conn, unit="job:" + row["job_key"]) is None
            or runtime_holds.find(
                conn, task=task_key(row["source_id"], "detail", str(row["external_id"]))
            )
            is None
        ):
            raise ValueError("containment_current_rejection_not_scoped")
    for source in known_sources:
        _check(deadline_at)
        try:
            resolve_listing_event(conn, source_id=source)
        except ValueError:
            if (
                runtime_holds.find(conn, unit="listing:" + source) is None
                or runtime_holds.find(conn, task=task_key(source, "listing")) is None
            ):
                raise ValueError("containment_unscoped_listing_head")
    for doc in cursor.execute("SELECT * FROM remediation_documents"):
        _check(deadline_at)
        try:
            resolve_event(
                conn,
                kind="document",
                source_id=doc["source_id"],
                task_id=doc["task_id"],
                evidence_key=digest(json.loads(doc["manifest"])),
            )
        except ValueError:
            if runtime_holds.find(conn, task=doc["task_id"]) is None:
                raise ValueError("containment_unscoped_document")
            if (
                json.loads(doc["manifest"]).get("purpose_state")
                not in {"supplementary", "supplementary_excluded"}
                and runtime_holds.find(conn, unit="job:" + doc["job_key"]) is None
            ):
                raise ValueError("containment_unscoped_required_document")
    for attempt in cursor.execute(
        "SELECT task_id FROM remediation_attempts WHERE finished_at IS NULL OR status='reserved'"
    ):
        if runtime_holds.find(conn, task=attempt["task_id"]) is None:
            raise ValueError("containment_unscoped_interrupted_claim")
    for dependency in dependencies:
        _check(deadline_at)
        if tuple(_stamp(Path(dependency["path"]).stat())) != _record_stamp(dependency):
            raise ValueError("containment_review_dependency_changed")
    if runtime_authority.validate(conn) != state:
        raise ValueError("containment_native_source_changed")
    for item in held:
        payload = {
            "schema": "held-historical-control-1",
            **item,
            "historical_exit_reconstructed": False,
            "replay_authorized": False,
            "retirement_authorized": False,
            "review_sha256": review_sha256,
        }
        conn.execute(
            "INSERT OR REPLACE INTO jobagg_legacy_review_holds VALUES(?,?)",
            ("history-hold/" + item["run_id"], json.dumps(payload, sort_keys=True)),
        )
    value = {
        "schema": SCHEMA,
        "epoch": state["epoch"],
        "worker_database": str(database),
        "owner": state["owner_path"],
        "config_sha256": review["config_sha256"],
        "implementation_sha256": review["implementation_sha256"],
        "catalogue_sha256": imported["sha256"],
        "inventory_sha256": imported["inventory_sha256"],
        "total_records": len(controls),
        "held_records": len(held),
        "review_sha256": review_sha256,
        "policy_location": review["policy_location"],
        "current_observations_reviewed": len(rows),
        "historical_replay_authorized": False,
        "retirement_authorized": False,
    }
    previous = get(conn)
    if previous:
        conn.execute(
            "INSERT OR IGNORE INTO jobagg_legacy_review_holds VALUES(?,?)",
            (
                "history-containment-review/" + digest(previous),
                json.dumps(previous, sort_keys=True),
            ),
        )
    conn.execute(
        "INSERT OR REPLACE INTO jobagg_legacy_review_holds VALUES(?,?)",
        (KEY, json.dumps(value, sort_keys=True)),
    )
    return value


def can_close_scoped_noop(
    conn,
    run_id,
    *,
    accepted_progress,
    contract_sha256,
    config_sha256,
    implementation_sha256,
    policy_location,
):
    state = runtime_authority.validate(conn)
    if (
        state["run_id"] != run_id
        or state["phase"] != "worker"
        or type(accepted_progress) is not int
        or accepted_progress != 0
        or not approved(
            conn,
            config_sha256=config_sha256,
            implementation_sha256=implementation_sha256,
            policy_location=policy_location,
        )
    ):
        return False
    from jobagg.publication_work import vector
    from jobagg.publication_fence import verify_boundary

    if conn.execute(
        "SELECT 1 FROM jobagg_accepted_events WHERE run_id=? LIMIT 1", (run_id,)
    ).fetchone():
        return False
    work = vector(conn, contract_sha256=contract_sha256, now=time.time())
    control = json.loads(state["control_json"])
    return (
        work["pending"] is None
        and not work["revalidation_reasons"]
        and not work.get("unscoped_held")
        and bool(work["held"] or (summary(conn) or {}).get("historical_control_records_held"))
        and control.get("publication_scope") is not None
        and verify_boundary(
            control.get("publication_cursor"), expected_scope=control["publication_scope"]
        )
    )


def close_scoped_noop(conn, run_id, **proof):
    if not can_close_scoped_noop(conn, run_id, **proof):
        raise ValueError("scoped_noop_requires_current_native_boundary_and_zero_acceptance")
    # Current batch termination leaves every held unit and old control intact.
    runtime_authority.finish_publication(conn, run_id)
