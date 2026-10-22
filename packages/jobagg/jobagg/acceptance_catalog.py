"""Checkpointed legacy exit catalogue; 'done' SQL rows alone are not acceptance.

Only an exact successful exit/report/request bundle imports accepted attempts.
Selected import batches revalidate their small original control bundles; normal
runtime uses the fenced immutable accepted references produced by that import.
"""

import hashlib
import json
from pathlib import Path
import sqlite3
import time

from jobagg.publication_snapshot import _json_document
from jobagg.runtime_source_head import owner

SCHEMA = "accepted-worker-exit-1"


def prepare(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS jobagg_accepted_exits(attempt_id TEXT PRIMARY KEY,
        run_id TEXT,task_id TEXT,source_id TEXT,kind TEXT,proof_sha256 TEXT,controls_json TEXT)""")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS jobagg_accepted_exit_runs(run_id TEXT PRIMARY KEY,proof_sha256 TEXT,controls_json TEXT)"
    )


def _read(path, deadline_at):
    path = Path(path).absolute()
    if path.stat().st_size > 16 * 1024**2:
        raise ValueError("accepted_exit_control_oversized")
    return _json_document(path, deadline_at=deadline_at)


def _bundle(run, *, deadline_at, reviewed_implementations, owner_path=None):
    run = Path(run).absolute()
    if run.resolve() != run:
        raise ValueError("accepted_exit_control_root_alias")
    values = {}
    controls = {}
    for name in ("request.json", "acceptance.json", "worker_result.json"):
        value, record = _read(run / name, deadline_at)
        values[name] = value
        controls[name] = {"path": str(run / name), "sha256": record["sha256"]}
    request, report, result = (
        values[name] for name in ("request.json", "acceptance.json", "worker_result.json")
    )
    if (
        request.get("run_id") != run.name
        or report.get("run_id") != run.name
        or result.get("run_id") != run.name
        or type(result.get("worker_exit_code")) is not int
        or result["worker_exit_code"] != 0
        or result.get("report_sha256") != controls["acceptance.json"]["sha256"]
        or (
            result.get("request_sha256") is not None
            and result["request_sha256"] != controls["request.json"]["sha256"]
        )
        or report.get("source_manifest_sha256") != request.get("source_manifest_sha256")
        or report.get("status") not in {"complete", "incomplete"}
        or (
            owner_path is not None
            and report.get("binding", {}).get("shared_lock") != str(Path(owner_path).absolute())
        )
        or not isinstance(report.get("binding"), dict)
        or report["binding"].get("implementation_sha256") not in reviewed_implementations
    ):
        raise ValueError("accepted_exit_successful_bound_report_required")
    intervals = report.get("concurrency", {}).get("task_intervals")
    if not isinstance(intervals, list):
        raise ValueError("accepted_exit_attempt_identities_missing")
    expected = set(request.get("expected_source_ids", []))
    rows = []
    seen = set()
    for item in intervals:
        if time.monotonic() >= deadline_at:
            raise TimeoutError("accepted_exit_catalogue_deadline")
        kind = item.get("kind")
        outcome = item.get("outcome") or {}
        if {
            "listing": "listing_observed",
            "detail": "detail_stored",
            "document": "document_captured",
        }.get(kind) not in outcome:
            continue
        attempt = item.get("attempt_id")
        if (
            not isinstance(attempt, str)
            or not attempt
            or attempt in seen
            or item.get("source_id") not in expected
            or not item.get("task_id")
        ):
            raise ValueError("accepted_exit_attempt_scope_or_duplicate")
        seen.add(attempt)
        rows.append({key: item[key] for key in ("attempt_id", "task_id", "source_id", "kind")})
    proof = {
        "schema": SCHEMA,
        "run_id": run.name,
        "implementation_sha256": report["binding"]["implementation_sha256"],
        "controls": controls,
        "attempts": rows,
    }
    proof["sha256"] = hashlib.sha256(json.dumps(proof, sort_keys=True).encode()).hexdigest()
    return proof


def import_run(conn, run, *, owner_fd, owner_path, reviewed_implementations, deadline_at):
    owner(owner_fd, owner_path)
    prepare(conn)
    proof = _bundle(
        run,
        deadline_at=deadline_at,
        reviewed_implementations=reviewed_implementations,
        owner_path=owner_path,
    )
    previous = conn.execute(
        "SELECT proof_sha256 FROM jobagg_accepted_exit_runs WHERE run_id=?", (proof["run_id"],)
    ).fetchone()
    if previous and previous[0] != proof["sha256"]:
        raise ValueError("accepted_exit_conflicting_run_proof")
    payload = json.dumps(proof, sort_keys=True)
    for row in proof["attempts"]:
        old = conn.execute(
            "SELECT proof_sha256 FROM jobagg_accepted_exits WHERE attempt_id=?",
            (row["attempt_id"],),
        ).fetchone()
        if old and old[0] != proof["sha256"]:
            raise ValueError("accepted_exit_conflicting_occurrence_identity")
        conn.execute(
            "INSERT OR IGNORE INTO jobagg_accepted_exits VALUES(?,?,?,?,?,?,?)",
            (
                row["attempt_id"],
                proof["run_id"],
                row["task_id"],
                row["source_id"],
                row["kind"],
                proof["sha256"],
                payload,
            ),
        )
    conn.execute(
        "INSERT OR IGNORE INTO jobagg_accepted_exit_runs VALUES(?,?,?)",
        (proof["run_id"], proof["sha256"], payload),
    )
    return proof


def verified_attempt(conn, attempt, *, deadline_at, cache):
    if not conn.execute(
        "SELECT 1 FROM sqlite_master WHERE name='jobagg_accepted_exits'"
    ).fetchone():
        return None
    cursor = conn.cursor()
    cursor.row_factory = sqlite3.Row
    row = cursor.execute(
        "SELECT * FROM jobagg_accepted_exits WHERE attempt_id=?", (attempt["attempt_id"],)
    ).fetchone()
    if row is None:
        return None
    if any(row[key] != attempt[key] for key in ("task_id", "source_id", "kind")):
        raise ValueError("accepted_exit_immutable_identity_changed")
    proof = json.loads(row["controls_json"])
    if row["proof_sha256"] not in cache:
        actual = _bundle(
            Path(proof["controls"]["request.json"]["path"]).parent,
            deadline_at=deadline_at,
            reviewed_implementations={proof["implementation_sha256"]},
        )
        if actual != proof or actual["sha256"] != row["proof_sha256"]:
            raise ValueError("accepted_exit_import_control_changed")
        cache[row["proof_sha256"]] = True
    return {
        "kind": "verified_worker_exit",
        "proof_sha256": row["proof_sha256"],
        "run_id": row["run_id"],
    }
