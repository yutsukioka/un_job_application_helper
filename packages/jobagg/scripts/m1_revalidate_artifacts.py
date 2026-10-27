#!/usr/bin/env python3
"""Prepare read-only surviving-artifact evidence on an idle fenced copy.

No native database changes, recovery admissions or historical run closures.
"""

from contextlib import closing
import argparse
import json
import math
from pathlib import Path
import sqlite3
import time

from m1_stage_snapshot import disjoint, fenced_owner, sha
from m1_reconcile_history import save
from jobagg import (
    historical_artifacts,
    runtime_authority,
    runtime_control_catalog,
    runtime_source_head,
)


def revalidate(
    root, review_path, review_sha256, operation, *, seconds, max_records, record_seconds
):
    if (
        not math.isfinite(seconds)
        or not 0 < seconds <= 300
        or not math.isfinite(record_seconds)
        or not 0 < record_seconds <= min(60, seconds)
        or type(max_records) is not int
        or not 0 < max_records <= 64
    ):
        raise ValueError("finite_surviving_artifact_bounds_required")
    started = time.monotonic()
    deadline = started + seconds
    root, review_path, operation = map(lambda p: Path(p).absolute(), (root, review_path, operation))
    if (
        root.resolve() != root
        or review_path.resolve() != review_path
        or operation.resolve() != operation
        or operation.parent != root
        or not review_path.is_relative_to(root)
        or review_path.stat().st_size > 1024**2
        or sha(review_path) != review_sha256
    ):
        raise ValueError("exact_copy_local_artifact_review_required")
    review = json.loads(review_path.read_text())
    ids = review.get("attempt_ids")
    if (
        review.get("schema") != "surviving-artifact-review-1"
        or not isinstance(ids, list)
        or not 0 < len(ids) <= max_records
        or len(set(ids)) != len(ids)
        or any(not isinstance(v, str) or not v for v in ids)
    ):
        raise ValueError("finite_exact_surviving_attempt_inventory_required")
    snapshot = json.loads((root / "snapshot.json").read_text())
    config_path = Path(snapshot["production_config"])
    if (
        snapshot.get("complete") is not True
        or snapshot.get("snapshot_root") != str(root)
        or config_path.resolve() != config_path
        or sha(config_path) != snapshot["production_config_sha256"]
    ):
        raise ValueError("surviving_artifact_original_boundary_changed")
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
        raise ValueError("surviving_artifact_copy_aliases_production")
    operation.mkdir(mode=0o700, exist_ok=False)
    save(operation / "review.json", {"path": str(review_path), "sha256": review_sha256})
    records = []
    with fenced_owner(config, snapshot["fence_sha256"]) as fd:
        with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as conn:
            state = runtime_authority.validate(conn)
            runtime_source_head.owner(fd, state["owner_path"])
            if state["phase"] != "idle" or state["owner_path"] != config["shared_lock_path"]:
                raise ValueError("idle_fenced_artifact_copy_required")
            if (
                runtime_control_catalog.receipt(conn)["inventory_sha256"]
                != review["inventory_sha256"]
            ):
                raise ValueError("surviving_artifact_catalogue_changed")
            for ordinal, attempt in enumerate(ids):
                if time.monotonic() >= deadline:
                    raise TimeoutError("surviving_artifact_page_deadline")
                record_deadline = min(deadline, time.monotonic() + record_seconds)
                conn.set_progress_handler(lambda: int(time.monotonic() >= record_deadline), 256)
                try:
                    result = historical_artifacts.assess(
                        conn,
                        attempt,
                        deadline_at=record_deadline,
                        max_body_bytes=review["max_body_bytes_per_record"],
                    )
                except (TimeoutError, sqlite3.OperationalError) as error:
                    if not isinstance(error, TimeoutError) and time.monotonic() < record_deadline:
                        raise
                    result = {
                        "attempt_id": attempt,
                        "disposition": "held",
                        "reason": "record_deadline",
                        "historical_worker_exit_verified": False,
                        "publication_verified": False,
                        "completeness_certified": False,
                        "authorizes_recovery_or_retirement": False,
                    }
                save(operation / (str(ordinal) + ".json"), result)
                records.append(result)
            conn.set_progress_handler(None, 0)
            if runtime_authority.validate(conn) != state:
                raise ValueError("surviving_artifact_native_revision_changed")
    result = {
        "schema": "surviving-artifact-revalidation-1",
        "review_sha256": review_sha256,
        "records": records,
        "worker_revision": state["revision"],
        "record_seconds": record_seconds,
        "production_activated": False,
        "payloads_copied": 0,
        "native_rows_changed": 0,
        "historical_runs_resolved": 0,
        "elapsed_seconds": time.monotonic() - started,
    }
    save(operation / "receipt.json", result)
    return result


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    for name in ("snapshot", "review", "operation"):
        p.add_argument("--" + name, type=Path, required=True)
    p.add_argument("--review-sha256", required=True)
    p.add_argument("--seconds", type=float, required=True)
    p.add_argument("--record-seconds", type=float, required=True)
    p.add_argument("--max-records", type=int, required=True)
    a = p.parse_args()
    print(
        json.dumps(
            revalidate(
                a.snapshot,
                a.review,
                a.review_sha256,
                a.operation,
                seconds=a.seconds,
                max_records=a.max_records,
                record_seconds=a.record_seconds,
            )
        )
    )
