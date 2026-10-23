"""Bounded, read-only historical acceptance reconciliation for M1 preparation.

A changed later row does not erase older acceptance. Unproven listing/document/
export destinations remain explicit unresolved work, never an empty-backlog seal.
This diagnostic does not activate an authority or dispatch fetching/publication.
"""

import hashlib
import json
from pathlib import Path
import sqlite3
import time
from contextlib import closing


def _check(deadline):
    if time.monotonic() >= deadline:
        raise TimeoutError("historical reconciliation budget exhausted")


def _control(path, deadline):
    from jobagg.publication_snapshot import _json_document

    _check(deadline)
    path = Path(path)
    if path.is_symlink() or path.stat().st_size > 16 * 1024**2:
        raise ValueError("reconciliation control absent/aliased/oversized")
    value, record = _json_document(path, deadline_at=deadline)
    _check(deadline)
    return value, record["sha256"]


def reconcile_run(*args, deadline_at, **kwargs):
    from jobagg.verification_budget import scope

    with scope(deadline_at):
        return _reconcile_run(*args, deadline_at=deadline_at, **kwargs)


def _reconcile_run(
    run,
    worker_database,
    destinations,
    *,
    deadline_at,
    publication_boundary=None,
    expected_publication_scope=None,
    generation_references=(),
    reviewed_generation_sha256=(),
):
    run = Path(run).absolute()
    request, request_sha = _control(run / "request.json", deadline_at)
    report, report_sha = _control(run / "acceptance.json", deadline_at)
    worker, worker_sha = _control(run / "worker_result.json", deadline_at)
    if (
        request.get("run_id") != run.name
        or report.get("run_id") != run.name
        or worker.get("run_id") != run.name
        or worker.get("worker_exit_code") != 0
        or worker.get("report_sha256") != report_sha
    ):
        raise ValueError("historical worker acceptance lacks exact successful exit/report proof")
    intervals = report.get("concurrency", {}).get("task_intervals")
    if not isinstance(intervals, list):
        return {
            "run_id": run.name,
            "reconciled": False,
            "activation_reconciled": False,
            "exports_reconciled": False,
            "reason": "accepted_attempt_identities_missing",
            "acceptance_sha256": report_sha,
        }
    records = []
    unresolved = []
    with closing(
        sqlite3.connect(Path(worker_database).absolute().as_uri() + "?mode=ro", uri=True, timeout=1)
    ) as conn:
        conn.row_factory = sqlite3.Row
        conn.set_progress_handler(lambda: int(time.monotonic() >= deadline_at), 256)
        for interval in intervals:
            _check(deadline_at)
            outcome = interval.get("outcome", {})
            if not any(
                key in outcome for key in ("detail_stored", "listing_observed", "document_captured")
            ):
                continue
            attempt_id = interval.get("attempt_id")
            row = conn.execute(
                "SELECT * FROM remediation_attempts WHERE attempt_id=?", (attempt_id,)
            ).fetchone()
            if (
                not row
                or row["status"] != "done"
                or row["finished_at"] is None
                or any(row[key] != interval.get(key) for key in ("task_id", "source_id", "kind"))
            ):
                unresolved.append(
                    {
                        "attempt_id": attempt_id,
                        "reason": "immutable_acceptance_identity_missing_or_changed",
                    }
                )
                continue
            receipt = json.loads(row["evidence"])
            target_paths = (
                destinations.get(row["source_id"], [])
                if isinstance(destinations, dict)
                else destinations
            )
            if row["kind"] != "detail":
                record = _reconcile_other(conn, row, receipt, target_paths, deadline_at)
                records.append(record)
                if not record["exact_readback"]:
                    if not _historical_occurrence(
                        row,
                        receipt,
                        generation_references,
                        reviewed_generation_sha256,
                        target_paths,
                        deadline_at,
                    ):
                        unresolved.append(
                            {
                                "attempt_id": attempt_id,
                                "kind": row["kind"],
                                "reason": "exact_listing_document_identity_unproven_or_superseded",
                            }
                        )
                continue
            artifact, digest = _control(Path(receipt["detail_path"]), deadline_at)
            if digest != receipt.get("detail_sha256"):
                unresolved.append({"attempt_id": attempt_id, "reason": "accepted_artifact_changed"})
                continue
            job = artifact["job"]
            task = conn.execute(
                "SELECT source_id,kind,external_id FROM remediation_tasks WHERE task_id=?",
                (row["task_id"],),
            ).fetchone()
            if (
                not task
                or task["source_id"] != job["source_id"]
                or task["kind"] != "detail"
                or str(task["external_id"]) != str(job["external_id"])
            ):
                unresolved.append(
                    {"attempt_id": attempt_id, "reason": "immutable_task_crosswalk_differs"}
                )
                continue
            targets = []
            for destination in target_paths:
                _check(deadline_at)
                with closing(
                    sqlite3.connect(
                        Path(destination).absolute().as_uri() + "?mode=ro", uri=True, timeout=1
                    )
                ) as live:
                    live.row_factory = sqlite3.Row
                    live.set_progress_handler(lambda: int(time.monotonic() >= deadline_at), 256)
                    rows = live.execute(
                        "SELECT source_id,external_id,description,raw_json FROM jobs WHERE source_id=? AND external_id=?",
                        (job["source_id"], str(job["external_id"])),
                    ).fetchall()
                    exact = (
                        len(rows) == 1
                        and rows[0]["description"] == job["description"]
                        and json.loads(rows[0]["raw_json"]).get("_deterministic_fetch_observation")
                        == artifact["proof"]
                    )
                    targets.append(
                        {"path": str(destination), "exact_current_identity_readback": exact}
                    )
            record = {
                "attempt_id": attempt_id,
                "task_id": row["task_id"],
                "source_id": row["source_id"],
                "external_id": str(job["external_id"]),
                "artifact_sha256": digest,
                "description_sha256": hashlib.sha256(job["description"].encode()).hexdigest(),
                "destinations": targets,
            }
            records.append(record)
            if (
                len(targets) < 2
                or not all(item["exact_current_identity_readback"] for item in targets)
            ) and not _historical_occurrence(
                row,
                receipt,
                generation_references,
                reviewed_generation_sha256,
                target_paths,
                deadline_at,
            ):
                unresolved.append(
                    {
                        "attempt_id": attempt_id,
                        "reason": "exact_published_identity_unproven_or_superseded",
                    }
                )
    from jobagg.publication_fence import verify_boundary

    exports_verified = (
        publication_boundary is not None
        and expected_publication_scope is not None
        and verify_boundary(publication_boundary, expected_scope=expected_publication_scope)
        and set(
            map(
                str,
                [path for paths in destinations.values() for path in paths]
                if isinstance(destinations, dict)
                else destinations,
            )
        ).issubset({item["path"] for item in publication_boundary["databases"]})
    )
    return {
        "run_id": run.name,
        "request_sha256": request_sha,
        "acceptance_sha256": report_sha,
        "worker_result_sha256": worker_sha,
        "accepted_details": records,
        "unresolved": unresolved,
        "reconciled": not unresolved,
        "exports_reconciled": exports_verified,
        "activation_reconciled": not unresolved and exports_verified,
        "publication_boundary": publication_boundary,
        "expected_publication_scope": expected_publication_scope,
        "verification_deadline_at": deadline_at,
        "scope": "Exact accepted occurrence/current readback or reviewed generation proof; current full-scope export cursor is required independently.",
    }


def _reconcile_other(worker, attempt, receipt, destinations, deadline):
    kind = attempt["kind"]
    source = attempt["source_id"]
    targets = []
    path_key, hash_key = (
        ("frame_path", "frame_sha256") if kind == "listing" else ("manifest", "sha256")
    )
    artifact, sha = _control(receipt[path_key], deadline)
    if sha != receipt[hash_key] or artifact.get("source_id") != source:
        return {
            "attempt_id": attempt["attempt_id"],
            "kind": kind,
            "exact_readback": False,
            "reason": "accepted_artifact_changed",
        }
    task = worker.execute(
        "SELECT source_id,kind,external_id FROM remediation_tasks WHERE task_id=?",
        (attempt["task_id"],),
    ).fetchone()
    if task is None or task["source_id"] != source or task["kind"] != kind:
        raise ValueError("accepted_listing_document_task_crosswalk_differs")
    for destination in destinations:
        _check(deadline)
        with closing(
            sqlite3.connect(Path(destination).absolute().as_uri() + "?mode=ro", uri=True, timeout=1)
        ) as live:
            live.row_factory = sqlite3.Row
            live.set_progress_handler(lambda: int(time.monotonic() >= deadline), 256)
            exact = False
            try:
                if kind == "listing":
                    frame = live.execute(
                        "SELECT * FROM live_listing_frames WHERE source_id=?", (source,)
                    ).fetchone()
                    entries = live.execute(
                        "SELECT * FROM live_listing_inventory WHERE source_id=?", (source,)
                    ).fetchall()
                    current = {
                        row["external_id"]: row
                        for row in entries
                        if row["observed_in_latest_listing"] == 1
                    }
                    expected = {str(item["external_id"]): item for item in artifact["jobs"]}
                    exact = (
                        len(expected) == len(artifact["jobs"])
                        and frame is not None
                        and frame["frame_sha256"] == sha
                        and frame["observed_at"] == artifact["observed_at"]
                        and json.loads(frame["proof_json"]) == receipt["enumeration"]
                        and set(current) == set(expected)
                        and all(
                            {
                                key: value
                                for key, value in json.loads(current[key]["listing_json"]).items()
                                if key != "job_key"
                            }
                            == item
                            for key, item in expected.items()
                        )
                    )
                else:
                    from jobagg.pipelines.document_readback import document_readback

                    job = worker.execute(
                        "SELECT * FROM jobs WHERE job_key=?", (artifact["job_key"],)
                    ).fetchone()
                    binary = Path(artifact["binary_path"])
                    from jobagg.accepted_detail_lineage import _sha

                    exact = (
                        job is not None
                        and _sha(binary, deadline_at=deadline) == artifact["content_sha256"]
                        and document_readback(live, dict(job), artifact, {})["checks"]["verified"]
                    )
            except (KeyError, ValueError, sqlite3.DatabaseError):
                exact = False
            targets.append(
                {"path": str(destination), "exact_current_identity_readback": bool(exact)}
            )
    return {
        "attempt_id": attempt["attempt_id"],
        "kind": kind,
        "artifact_sha256": sha,
        "destinations": targets,
        "exact_readback": len(targets) >= 2
        and all(row["exact_current_identity_readback"] for row in targets),
    }


def _historical_occurrence(attempt, receipt, references, reviewed, destinations, deadline):
    """Explicit finite reviewed receipts; never discover history or choose latest.

    A producer's exact completed readback/transaction receipt proves a superseded
    occurrence only with its immutable plan and each actual destination journal.
    Current export consistency is a separate mandatory activation check.
    """
    kind = attempt["kind"]
    field, hash_field = {
        "detail": ("detail_path", "detail_sha256"),
        "listing": ("frame_path", "frame_sha256"),
        "document": ("manifest", "sha256"),
    }[kind]
    artifact, sha = _control(receipt[field], deadline)
    if sha != receipt[hash_field]:
        return False
    for reference in references:
        _check(deadline)
        if reference.get("result_sha256") not in reviewed:
            continue
        result, result_sha = _control(reference["result_path"], deadline)
        plan, plan_sha = _control(reference["plan_path"], deadline)
        if (
            result_sha != reference["result_sha256"]
            or plan_sha != reference["plan_sha256"]
            or result.get("plan_sha256") != plan_sha
            or result.get("generation_id") != plan.get("generation_id")
            or result.get("status") != "published"
            or result.get("state") != "complete"
            or result.get("readback_verified") is not True
            or result.get("database_transactions_complete") is not True
            or Path(plan["generation_root"]) != Path(reference["plan_path"]).parent
        ):
            raise ValueError("reviewed_generation_reconciliation_binding_changed")
        candidates = (
            plan.get("listing_frames", []) if kind == "listing" else plan.get("changes", [])
        )
        for candidate in candidates:
            matched = (
                (
                    candidate.get("frame_sha256") == sha
                    and candidate.get("source_id") == attempt["source_id"]
                )
                if kind == "listing"
                else (
                    candidate.get("proof") == artifact.get("proof")
                    and candidate.get("worker_row", {}).get("description")
                    == artifact.get("job", {}).get("description")
                    and candidate.get("worker_row", {}).get("source_id") == attempt["source_id"]
                    and str(candidate.get("worker_row", {}).get("external_id"))
                    == str(artifact.get("job", {}).get("external_id"))
                )
                if kind == "detail"
                else artifact in candidate.get("documents", [])
            )
            if not matched:
                continue
            paths = {item["path"] for item in candidate["destinations"]}
            if not set(map(str, destinations)).issubset(paths):
                continue
            all_journalled = True
            for path in destinations:
                _check(deadline)
                with closing(
                    sqlite3.connect(
                        Path(path).absolute().as_uri() + "?mode=ro", uri=True, timeout=1
                    )
                ) as conn:
                    conn.set_progress_handler(lambda: int(time.monotonic() >= deadline), 256)
                    row = conn.execute(
                        "SELECT generation_id FROM jobagg_publication_receipts WHERE publication_key=?",
                        (candidate["publication_key"],),
                    ).fetchone()
                    all_journalled &= bool(row and row[0] == plan["generation_id"])
            if all_journalled:
                return True
    return False
