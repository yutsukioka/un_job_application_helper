"""Reviewed queue-only recovery. Preview is default; never fetches or edits policy.

A new plan binds each task, its original successful listing attempt/frame and all
supporting files. Apply holds the shared owner and changes only task payload,
status and eligibility, with an atomic repair journal; attempts are immutable.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import sqlite3
import time

from jobagg.db import JobDatabase
from jobagg.hashing import ensure_job_hash, posting_fingerprint
from jobagg.remediation_worker import (
    PUBLIC_FIELDS,
    dump,
    frame_listing,
    implementation_hash,
    record,
    sha,
    shared_owner,
    task_key,
    utc,
)


def digest_value(value):
    return hashlib.sha256(dump(value).encode()).hexdigest()


def reference(path):
    path = Path(path)
    return {"path": str(path), "sha256": sha(path)}


@contextmanager
def connection(workspace, *, write=False):
    path = workspace / "jobs.sqlite3"
    if path.resolve() != path or not path.is_file():
        raise ValueError("Repair requires an existing unaliased worker database")
    conn = sqlite3.connect(path.as_uri() + ("?mode=rw" if write else "?mode=ro"), uri=True)
    conn.row_factory = sqlite3.Row
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def original_listing(conn, workspace, task):
    payload = json.loads(task["payload"])
    frame_path = Path(payload["frame_path"])
    resolved = frame_path.resolve()
    if (
        not resolved.is_relative_to((workspace / "captures").resolve())
        or resolved.name != "listing.json"
    ):
        raise ValueError("Queue frame escapes worker captures")
    if sha(frame_path) != payload["frame_sha256"]:
        raise ValueError("Queue frame bytes changed")
    attempt = conn.execute(
        "SELECT * FROM remediation_attempts WHERE attempt_id=?", (resolved.parent.name,)
    ).fetchone()
    if (
        not attempt
        or attempt["kind"] != "listing"
        or attempt["source_id"] != task["source_id"]
        or attempt["status"] != "done"
    ):
        raise ValueError("Frame has no successful source-bound listing attempt")
    evidence = json.loads(attempt["evidence"])
    if (
        Path(evidence["frame_path"]).resolve() != resolved
        or evidence["frame_sha256"] != payload["frame_sha256"]
    ):
        raise ValueError("Listing attempt does not bind queue frame")
    frame = json.loads(frame_path.read_text())
    found = [
        x
        for x in frame.get("jobs", [])
        if x.get("source_id") == task["source_id"]
        and str(x.get("external_id")) == task["external_id"]
    ]
    if frame.get("source_id") != task["source_id"] or len(found) != 1:
        raise ValueError("Source/job frame identity is ambiguous")
    fixed = {**payload, "listing": found[0]}
    job = frame_listing(fixed, task["source_id"], task["external_id"])
    if task["task_id"] != task_key(task["source_id"], "detail", task["external_id"]):
        raise ValueError("Task identity does not match frame")
    return payload, fixed, job, [reference(frame_path)], dict(attempt)


def projected_legacy_payload(frame_job, current):
    job = deepcopy(frame_job)
    if current is not None:
        object.__new__(JobDatabase)._merge_existing_detail_fields(job, current)
    ensure_job_hash(job)
    if job.posting_fingerprint is None:
        job.posting_fingerprint = posting_fingerprint(job)
    return json.loads(dump(asdict(job)))


def listing_marker_evidence(conn, workspace, marker, source_id, external_id):
    if not isinstance(marker, dict):
        raise ValueError("Retained listing marker missing")
    path = Path(marker["evidence_ref"])
    if (
        not path.resolve().is_relative_to((workspace / "captures").resolve())
        or path.name != "listing.json"
    ):
        raise ValueError("Retained listing marker frame escapes worker captures")
    frame = json.loads(path.read_text())
    attempt = conn.execute(
        "SELECT * FROM remediation_attempts WHERE attempt_id=?", (path.resolve().parent.name,)
    ).fetchone()
    if (
        not attempt
        or attempt["source_id"] != source_id
        or attempt["kind"] != "listing"
        or attempt["status"] != "done"
    ):
        raise ValueError("Retained listing marker lacks original successful attempt")
    evidence = json.loads(attempt["evidence"])
    if (
        Path(evidence["frame_path"]).resolve() != path.resolve()
        or evidence["frame_sha256"] != sha(path)
        or frame.get("source_id") != source_id
    ):
        raise ValueError("Retained listing marker original frame differs")
    seen = any(
        x.get("source_id") == source_id and str(x.get("external_id")) == str(external_id)
        for x in frame["jobs"]
    )
    expected = {
        "version": 1,
        "source_id": source_id,
        "observed_at": frame["observed_at"],
        "observed_in_latest_listing": seen,
        "inventory_complete": evidence["enumeration"]["complete"],
        "evidence_ref": marker["evidence_ref"],
        "reason": "observed" if seen else "not_observed_in_latest_frame",
    }
    if marker != expected:
        raise ValueError("Retained listing marker is not reproduced by its own original frame")
    return reference(path)


def known_mutation(payload, fixed, listing, current, conn, workspace):
    if payload == fixed:
        return None
    old, fresh = payload.get("listing"), fixed["listing"]
    generated = {"normalized_hash", "posting_fingerprint"}
    if isinstance(old, dict) and {k: v for k, v in old.items() if k not in generated} == {
        k: v for k, v in fresh.items() if k not in generated
    }:
        # Values must still be the deterministic generated hashes, not arbitrary edits.
        if old == projected_legacy_payload(listing, None):
            return "generated_listing_hash_fields", []
    projected = projected_legacy_payload(listing, current)
    if old == projected:
        return "reproduced_database_listing_merge", []
    if isinstance(old, dict) and isinstance(old.get("raw"), dict):
        stripped_old, stripped_projected = deepcopy(old), deepcopy(projected)
        old_marker = stripped_old["raw"].pop("_jobagg_listing_verification", None)
        new_marker = stripped_projected["raw"].pop("_jobagg_listing_verification", None)
        if stripped_old == stripped_projected:
            refs = [
                listing_marker_evidence(
                    conn, workspace, marker, listing.source_id, listing.external_id
                )
                for marker in (old_marker, new_marker)
            ]
            return "reproduced_merge_with_bound_historical_listing_marker", refs
    return None


def failure_artifacts(workspace, task):
    receipt = json.loads(task["receipt"] or "{}")
    target = Path(receipt.get("capture_directory", "/missing"))
    if (
        not target.resolve().is_relative_to((workspace / "captures").resolve())
        or target.name != task["claim"]
    ):
        raise ValueError("Failure capture directory does not bind durable claim")
    paths = sorted((target / "http").glob("*.json"))
    return target, paths


def metadata_conflict(workspace, task, current):
    prefix = "ValueError: Atomic detail readback differs: "
    error = task["last_error"] or ""
    if not error.startswith(prefix) or error[len(prefix) :] not in {
        "department",
        "employment_type",
    }:
        return None
    target, paths = failure_artifacts(workspace, task)
    artifact_path = target / "detail.json"
    artifact = json.loads(artifact_path.read_text())
    parsed, proof = artifact["job"], artifact["proof"]
    field = error[len(prefix) :]
    if parsed.get(field) is not None or not current or current[field] is None:
        return None
    if (
        parsed.get("source_id") != task["source_id"]
        or str(parsed.get("external_id")) != task["external_id"]
    ):
        return None
    if hashlib.sha256((parsed.get("description") or "").encode()).hexdigest() != proof.get(
        "parsed_source_text_sha256"
    ):
        return None
    projected = record(parsed)
    object.__new__(JobDatabase)._merge_existing_detail_fields(projected, current)
    for key in PUBLIC_FIELDS:
        if (
            parsed.get(key) is not None
            and json.loads(dump(asdict(projected))).get(key) != parsed[key]
        ):
            return None
    refs = [reference(artifact_path)]
    captured = False
    for ref in proof.get("captures", []):
        path = Path(ref["path"])
        if path not in paths or sha(path) != ref["sha256"]:
            raise ValueError("Failed detail capture proof changed")
        meta = json.loads(path.read_text())
        if (
            meta.get("status_code") == 200
            and meta.get("body_captured") is True
            and meta.get("phase", {}).get("kind") == "detail"
            and str(meta.get("phase", {}).get("job_id")) == task["external_id"]
        ):
            refs.extend([reference(path), reference(meta["artifact"])])
            captured = True
    return refs if captured else None


def policy_state(workspace, shared_lock, source, urls):
    from jobagg.pipelines.worker_policy import SharedPolicy

    policy = SharedPolicy(shared_lock, [source])
    policy.inspect()
    paths = [
        policy.root / "source_holds.json",
        policy.marker,
        policy.root / "bootstrap.json",
        policy.root / "attempt_index.json",
    ]
    holds = json.loads(paths[0].read_text())
    if holds.get(source):
        raise ValueError("Source policy remains held")
    due = 0
    for url in urls:
        from urllib.parse import urlsplit

        host = (urlsplit(url).hostname or "").lower()
        path = (
            policy.root
            / "hosts"
            / ("host-" + hashlib.sha256(host.encode()).hexdigest()[:24] + ".json")
        )
        if path.exists():
            paths.append(path)
            state = json.loads(path.read_text())
            if state.get("stopped"):
                raise ValueError("Host policy remains stopped: " + host)
            due = max(due, float(state.get("eligible_at", 0)))
    return due, [reference(p) for p in paths if p.exists()]


def transient_failure(workspace, task):
    _, paths = failure_artifacts(workspace, task)
    if not paths:
        return None
    path = paths[-1]
    meta = json.loads(path.read_text())
    status = meta.get("status_code")
    known = meta.get("error_type") in {
        "TimeoutError",
        "ConnectionResetError",
        "ConnectionAbortedError",
        "ConnectionRefusedError",
        "BrokenPipeError",
    }
    known |= meta.get("error_type") == "HTTPError" and status in {408, 429, 500, 502, 503, 504}
    if (
        not known
        or meta.get("state") != "failed"
        or meta.get("body_captured") is not False
        or meta.get("phase", {}).get("kind") != "detail"
        or str(meta.get("phase", {}).get("job_id")) != task["external_id"]
        or not (task["last_error"] or "").startswith(meta["error_type"] + ":")
        or not meta.get("finished_at")
        or not meta.get("started_at")
        or status not in {None, 408, 429, 500, 502, 503, 504}
    ):
        return None
    return [reference(path)], meta["url"]


def classify(conn, workspace, shared_lock, task):
    payload, fixed, listing, refs, listing_attempt = original_listing(conn, workspace, task)
    current = conn.execute(
        "SELECT * FROM jobs WHERE job_key=?", (listing.identity_key(),)
    ).fetchone()
    mutation = known_mutation(payload, fixed, listing, current, conn, workspace)
    if payload != fixed and not mutation:
        return None
    changed = mutation[0] if mutation else None
    if mutation:
        refs.extend(mutation[1])
    error = task["last_error"] or ""
    category = None
    if changed and task["status"] == "pending":
        category = changed
    elif changed and task["status"] == "blocked":
        _, captures = failure_artifacts(workspace, task)
        if not captures and any(
            label in error
            for label in (
                "externalPath",
                "No usable public detail",
                "public detail URL",
                "listing differs",
                "detail URL",
                "source identifier",
                "Public detail is unavailable or returned a different job identity",
            )
        ):
            category = changed
    if task["status"] == "blocked" and (metadata := metadata_conflict(workspace, task, current)):
        category = "false_absent_metadata_readback"
        refs.extend(metadata)
    retry_url = None
    if task["status"] == "blocked" and (retry := transient_failure(workspace, task)):
        category = "guarded_transient_transport"
        extra, retry_url = retry
        refs.extend(extra)
    if not category:
        return None
    circuit = conn.execute(
        "SELECT 1 FROM source_circuit_breakers WHERE source_id=? AND state IN ('open','half_open')",
        (task["source_id"],),
    ).fetchone()
    if circuit:
        raise ValueError("Source database circuit remains held")
    due, policy_refs = policy_state(
        workspace,
        shared_lock,
        task["source_id"],
        [url for url in [listing.source_url, listing.apply_url, retry_url] if url],
    )
    refs.extend(policy_refs)
    attempt = (
        conn.execute(
            "SELECT * FROM remediation_attempts WHERE attempt_id=?", (task["claim"],)
        ).fetchone()
        if task["claim"]
        else None
    )
    return {
        "category": category,
        "task_before": task,
        "task_before_sha256": digest_value(task),
        "new_payload": dump(fixed),
        "eligible_at_floor": max(task["eligible_at"], due),
        "job_before_sha256": digest_value(dict(current)) if current else None,
        "attempt_before": dict(attempt) if attempt else None,
        "listing_attempt": listing_attempt,
        "evidence": refs,
    }


def prepare(workspace, shared_lock):
    workspace, shared_lock = Path(workspace).absolute(), Path(shared_lock).absolute()
    if workspace.resolve() != workspace or shared_lock.resolve() != shared_lock:
        raise ValueError("Repair workspace/owner paths must be direct")
    marker_path = workspace / "worker_workspace.json"
    marker = json.loads(marker_path.read_text())
    if (
        marker.get("shared_lock") != str(shared_lock)
        or marker.get("implementation_sha256") != implementation_hash()
    ):
        raise ValueError("Repair requires current reviewed worker workspace binding")
    candidates, held = [], []
    with connection(workspace) as conn:
        for row in conn.execute(
            "SELECT * FROM remediation_tasks WHERE kind='detail' AND status IN ('pending','blocked') ORDER BY task_id"
        ):
            task = dict(row)
            try:
                candidate = classify(conn, workspace, shared_lock, task)
                if candidate:
                    candidates.append(candidate)
            except (ValueError, KeyError, OSError) as exc:
                held.append({"task_id": task["task_id"], "reason": str(exc)})
    return {
        "schema_version": 1,
        "kind": "reviewed_queue_only_repair",
        "created_at": utc(),
        "workspace": str(workspace),
        "shared_lock": str(shared_lock),
        "implementation_sha256": implementation_hash(),
        "workspace_marker": reference(marker_path),
        "candidates": candidates,
        "held": held,
        "network_requests": 0,
        "database_writes": 0,
    }


def apply(plan):
    if plan.get("schema_version") != 1 or plan.get("kind") != "reviewed_queue_only_repair":
        raise ValueError("Unsupported queue repair plan")
    workspace, lock = Path(plan["workspace"]), Path(plan["shared_lock"])
    plan_sha = digest_value(plan)
    if not lock.is_file() or lock.resolve() != lock:
        raise ValueError("Repair requires the existing direct shared owner")
    keys = [item["task_before"]["task_id"] for item in plan["candidates"]]
    if not keys or len(keys) != len(set(keys)):
        raise ValueError("Repair plan requires distinct nonempty task scope")
    with shared_owner(lock):
        with connection(workspace) as conn:
            journal = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='remediation_queue_repairs'"
            ).fetchone()
            prior = (
                conn.execute(
                    "SELECT * FROM remediation_queue_repairs WHERE plan_sha256=?", (plan_sha,)
                ).fetchall()
                if journal
                else []
            )
            if prior:
                if {row["task_id"] for row in prior} != set(keys):
                    raise ValueError("Partial/conflicting queue repair journal")
                original = {
                    item["task_before"]["task_id"]: item["task_before"]
                    for item in plan["candidates"]
                }
                for row in prior:
                    actual = conn.execute(
                        "SELECT * FROM remediation_tasks WHERE task_id=?", (row["task_id"],)
                    ).fetchone()
                    if (
                        json.loads(row["before_json"]) != original[row["task_id"]]
                        or not actual
                        or dict(actual) != json.loads(row["after_json"])
                    ):
                        raise ValueError("Repair journal or later task state differs")
                return {
                    "status": "already_applied",
                    "plan_sha256": plan_sha,
                    "tasks_requeued": 0,
                    "attempts_changed": 0,
                    "policy_changes": 0,
                    "jobs_changed": 0,
                    "network_requests": 0,
                }
        fresh = prepare(workspace, lock)
        fresh_by_key = {x["task_before"]["task_id"]: x for x in fresh["candidates"]}
        if (
            plan["implementation_sha256"] != fresh["implementation_sha256"]
            or plan["workspace_marker"] != fresh["workspace_marker"]
        ):
            raise ValueError("Repair code/workspace binding changed")
        with connection(workspace, write=True) as conn:
            conn.execute("BEGIN IMMEDIATE")
            for item in plan["candidates"]:
                key = item["task_before"]["task_id"]
                if fresh_by_key.get(key) != item:
                    raise ValueError("Repair task or its proof changed: " + key)
                actual = dict(
                    conn.execute(
                        "SELECT * FROM remediation_tasks WHERE task_id=?", (key,)
                    ).fetchone()
                )
                if actual != item["task_before"]:
                    raise ValueError("Task changed before repair transaction")
            conn.execute(
                "CREATE TABLE IF NOT EXISTS remediation_queue_repairs(plan_sha256 TEXT,task_id TEXT,applied_at TEXT,before_json TEXT,after_json TEXT,PRIMARY KEY(plan_sha256,task_id))"
            )
            for item in plan["candidates"]:
                key = item["task_before"]["task_id"]
                due = max(time.time(), item["eligible_at_floor"])
                conn.execute(
                    "UPDATE remediation_tasks SET payload=?,status='pending',eligible_at=? WHERE task_id=?",
                    (item["new_payload"], due, key),
                )
                after = dict(
                    conn.execute(
                        "SELECT * FROM remediation_tasks WHERE task_id=?", (key,)
                    ).fetchone()
                )
                conn.execute(
                    "INSERT INTO remediation_queue_repairs VALUES(?,?,?,?,?)",
                    (plan_sha, key, utc(), dump(item["task_before"]), dump(after)),
                )
    return {
        "status": "applied",
        "plan_sha256": plan_sha,
        "tasks_requeued": len(plan["candidates"]),
        "attempts_changed": 0,
        "policy_changes": 0,
        "jobs_changed": 0,
        "network_requests": 0,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path)
    parser.add_argument("--shared-lock", type=Path)
    parser.add_argument("--plan", type=Path)
    parser.add_argument("--plan-sha256")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.output.exists():
        raise ValueError("Output must be a new receipt path")
    if args.execute:
        if not args.plan or sha(args.plan) != args.plan_sha256:
            raise ValueError("Execute requires the exact reviewed plan path and SHA")
        result = apply(json.loads(args.plan.read_text()))
    else:
        result = prepare(args.workspace, args.shared_lock)
    with args.output.open("x") as stream:
        stream.write(dump(result) + "\n")
    print(
        dump(
            {
                "output": str(args.output),
                "sha256": sha(args.output),
                "candidates": len(result.get("candidates", [])),
                "status": result.get("status", "preview"),
            }
        )
    )


if __name__ == "__main__":
    main()
