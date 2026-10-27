#!/usr/bin/env python3
"""Owner-held, copy-only reconciliation against a sealed historical plan index.

No production database opens, provider calls, path guessing, task resets or
retirement. Failed or partial records remain held; receipts never certify census
completeness. Resume the same finite reviewed set after interruption.
"""

from contextlib import closing
import argparse
import json
import math
import os
from pathlib import Path
import sqlite3
import time

from m1_stage_snapshot import disjoint, fenced_owner, sha
from jobagg import (
    runtime_authority,
    runtime_control_catalog,
    runtime_reconciliation,
    publication_fence,
)
from jobagg import historical_destinations, historical_occurrences


def save(path, value):
    with path.open("x") as stream:
        json.dump(value, stream, sort_keys=True, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    descriptor = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def reconcile(
    root,
    operation,
    review_path,
    review_sha256,
    *,
    seconds,
    max_records,
    record_seconds=5,
    resume=False,
):
    started = time.monotonic()
    if (
        not math.isfinite(seconds)
        or not 0 < seconds <= 300
        or type(max_records) is not int
        or not 0 < max_records <= 64
    ):
        raise ValueError("finite_historical_reconciliation_bounds_required")
    if not math.isfinite(record_seconds) or not 0 < record_seconds <= min(seconds, 60):
        raise ValueError("finite_historical_record_deadline_required")
    deadline = started + seconds
    root, operation, review_path = map(lambda p: Path(p).absolute(), (root, operation, review_path))
    if (
        root.resolve() != root
        or operation.resolve() != operation
        or operation.parent != root
        or review_path.resolve() != review_path
        or not review_path.is_relative_to(root)
        or review_path.stat().st_size > 1024**2
        or sha(review_path) != review_sha256
    ):
        raise ValueError("exact_copy_local_historical_review_required")
    review = json.loads(review_path.read_text())
    ids = review.get("run_ids")
    if (
        review.get("schema") != "historical-occurrence-reconciliation-review-1"
        or not isinstance(ids, list)
        or not 0 < len(ids) <= max_records
        or any(not isinstance(v, str) or Path(v).name != v or v in {".", ".."} for v in ids)
        or len(set(ids)) != len(ids)
    ):
        raise ValueError("finite_unique_historical_run_set_required")
    snapshot = json.loads((root / "snapshot.json").read_text())
    config_path = Path(snapshot["production_config"])
    if (
        snapshot.get("complete") is not True
        or snapshot.get("snapshot_root") != str(root)
        or config_path.resolve() != config_path
        or sha(config_path) != snapshot["production_config_sha256"]
    ):
        raise ValueError("historical_reconciliation_original_boundary_changed")
    config = json.loads(config_path.read_text())
    database = root / "worker/jobs.sqlite3"
    production = Path(config["publication_worker_database"])
    disjoint(
        root,
        [
            production.parent,
            Path(config["state_dir"]),
            Path(config["attempt_state_dir"]),
            Path(config["shared_lock_path"]).parent,
        ],
    )
    if (
        database.resolve() != database
        or database.samefile(production)
        or database.stat().st_nlink != 1
    ):
        raise ValueError("historical_reconciliation_copy_aliases_production")
    index_root = Path(review["index_root"])
    if index_root.resolve() != index_root or index_root.parent != root:
        raise ValueError("historical_reconciliation_index_outside_copy")
    receipt_path = index_root / "index-receipt.json"
    if receipt_path.stat().st_size > 1024**2 or sha(receipt_path) != review["index_receipt_sha256"]:
        raise ValueError("historical_index_receipt_changed")
    receipt = json.loads(receipt_path.read_text())
    boundary_path = Path(review["boundary_path"])
    if (
        boundary_path.resolve() != boundary_path
        or not boundary_path.is_relative_to(root)
        or boundary_path.stat().st_size > 1024**2
        or sha(boundary_path) != review["boundary_file_sha256"]
    ):
        raise ValueError("historical_reconciliation_boundary_changed")
    boundary = json.loads(boundary_path.read_text())
    mapping_receipt = json.loads((index_root / "mapping.json").read_text())
    contract = {"review_path": str(review_path), "review_sha256": review_sha256}
    if resume:
        if json.loads((operation / "review.json").read_text()) != contract:
            raise ValueError("historical_reconciliation_resume_review_changed")
    else:
        operation.mkdir(mode=0o700, exist_ok=False)
        save(operation / "review.json", contract)
    result = {
        "schema": "historical-occurrence-reconciliation-1",
        "review_sha256": review_sha256,
        "production_activated": False,
        "payloads_copied": 0,
        "records": [],
    }
    with fenced_owner(config, snapshot["fence_sha256"]) as fd:
        with closing(sqlite3.connect(database)) as conn:
            conn.set_progress_handler(lambda: int(time.monotonic() >= deadline), 256)
            state = runtime_authority.validate(conn)
            if state["phase"] != "idle" or state["owner_path"] != config["shared_lock_path"]:
                raise ValueError("idle_native_copy_with_actual_owner_required")
            imported = runtime_control_catalog.receipt(conn)
            if imported["inventory_sha256"] != review["inventory_sha256"]:
                raise ValueError("historical_reconciliation_inventory_changed")
            mapping = historical_destinations.Destinations(
                conn, mapping_receipt, boundary=boundary, owner_fd=fd, deadline_at=deadline
            )
            if mapping_receipt["sha256"] != receipt["mapping_sha256"]:
                raise ValueError("historical_index_mapping_receipt_changed")
            index = historical_occurrences.Index(
                index_root / "occurrences.sqlite3",
                expected_sha256=receipt["database_sha256"],
                mapping=mapping,
                deadline_at=deadline,
            )
            registry = Path(review["registry_path"])
            if (
                registry.resolve() != registry
                or registry.stat().st_size > 1024**2
                or sha(registry) != review["registry_sha256"]
            ):
                raise ValueError("historical_reconciliation_registry_changed")
            sources = publication_fence.source_paths(registry, root / "output")
            aggregate = str(root / "output/all_jobs.sqlite3")
            source_paths = {source: [str(path), aggregate] for source, path in sources.items()}
            if {path for paths in source_paths.values() for path in paths} != {
                row["copy"] for row in mapping.rows.values()
            }:
                raise ValueError("historical_reconciliation_registry_mapping_incomplete")
            result["before"] = dict(
                conn.execute(
                    "SELECT disposition,count(*) FROM jobagg_legacy_controls GROUP BY disposition"
                )
            )
            for run_id in ids:
                if time.monotonic() >= deadline:
                    raise TimeoutError("historical_reconciliation_page_deadline")
                row = conn.execute(
                    "SELECT control_root,disposition FROM jobagg_legacy_controls WHERE run_id=?",
                    (run_id,),
                ).fetchone()
                if not row or row[1] not in {
                    "publication_reconciliation_required",
                    "terminal_verified",
                }:
                    raise ValueError("reviewed_terminal_publication_record_required")
                record_path = operation / (run_id + ".json")
                if record_path.exists():
                    previous = json.loads(record_path.read_text())
                    if previous["run_id"] != run_id or previous["resolved"] != (
                        row[1] == "terminal_verified"
                    ):
                        raise ValueError("historical_reconciliation_checkpoint_differs_from_native")
                    result["records"].append(previous)
                    continue
                record_deadline = min(deadline, time.monotonic() + record_seconds)
                try:
                    assessment = runtime_reconciliation.reconcile_run(
                        row[0],
                        database,
                        source_paths,
                        deadline_at=record_deadline,
                        publication_boundary=boundary,
                        expected_publication_scope=mapping_receipt["scope"],
                        historical_index=index,
                        stop_on_unresolved=True,
                    )
                except (TimeoutError, sqlite3.OperationalError) as error:
                    if not isinstance(error, TimeoutError) and time.monotonic() < record_deadline:
                        raise
                    assessment = {
                        "activation_reconciled": False,
                        "unresolved": [],
                        "assessment_exhaustive": False,
                        "reason": "record_assessment_deadline",
                    }
                except FileNotFoundError:
                    assessment = {
                        "activation_reconciled": False,
                        "unresolved": [],
                        "assessment_exhaustive": False,
                        "reason": "referenced_artifact_missing",
                    }
                entry = {
                    "run_id": run_id,
                    "resolved": assessment["activation_reconciled"],
                    "unresolved": assessment.get("unresolved", []),
                    "reason": assessment.get("reason"),
                    "assessment_exhaustive": assessment.get("assessment_exhaustive", False),
                    "record_budget_seconds": record_seconds,
                }
                if assessment["activation_reconciled"]:
                    with runtime_authority.controller(conn, run_id=None, owner_fd=fd):
                        with conn:
                            runtime_control_catalog.close_reconciled(
                                conn,
                                run_id,
                                worker_database=database,
                                destinations=source_paths,
                                publication_boundary=boundary,
                                expected_publication_scope=mapping_receipt["scope"],
                                owner_fd=fd,
                                owner_path=config["shared_lock_path"],
                                deadline_at=deadline,
                                historical_index=index,
                            )
                # Native proof is the durable checkpoint. A crash before this
                # receipt is safe: replay recomputes proof, including exports.
                record_path = operation / (run_id + ".json")
                if record_path.exists():
                    if json.loads(record_path.read_text()) != entry:
                        raise ValueError("historical_reconciliation_prior_result_changed")
                else:
                    save(record_path, entry)
                result["records"].append(entry)
            result["after"] = dict(
                conn.execute(
                    "SELECT disposition,count(*) FROM jobagg_legacy_controls GROUP BY disposition"
                )
            )
            result["oldest_unresolved"] = runtime_control_catalog.oldest_unresolved(conn)
            runtime_authority.validate(conn)
    result["elapsed_seconds"] = time.monotonic() - started
    if not (operation / "receipt.json").exists():
        save(operation / "receipt.json", result)
    return result


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    for name in ("snapshot", "operation", "review"):
        p.add_argument("--" + name, type=Path, required=True)
    p.add_argument("--review-sha256", required=True)
    p.add_argument("--seconds", type=float, required=True)
    p.add_argument("--max-records", type=int, required=True)
    p.add_argument("--resume", action="store_true")
    p.add_argument("--record-seconds", type=float, required=True)
    a = p.parse_args()
    print(
        json.dumps(
            reconcile(
                a.snapshot,
                a.operation,
                a.review,
                a.review_sha256,
                seconds=a.seconds,
                max_records=a.max_records,
                resume=a.resume,
                record_seconds=a.record_seconds,
            )
        )
    )
