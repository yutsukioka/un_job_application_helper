"""Explicit paged bootstrap of all reviewed legacy control roots.

Normal ticks consult one indexed unresolved reference, never enumerate run or
completed-generation history. Uncertain claims/generations remain durable repair
work. Only exact accepted/publication/export reconciliation can close a record.
This preparation API neither deploys code nor performs publication or requests.
"""

import hashlib
import json
import os
from pathlib import Path
import sqlite3
import time

from jobagg.publication_snapshot import _json_document
from jobagg.runtime_source_head import owner

SCHEMA = "runtime-control-catalogue-1"
CONTROL_TABLES = {
    "jobagg_legacy_control_state",
    "jobagg_legacy_controls",
    "jobagg_legacy_dependencies",
}


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def prepare(conn, *, manifest, owner_fd, owner_path):
    """Manifest is the separately reviewed complete root/writer inventory.

    Its SHA binds even terminal entries and every writer; omission cannot be
    repaired by a normal tick. The operator must supply the real closed boundary.
    """
    owner(owner_fd, owner_path)
    if (
        manifest.get("schema") != SCHEMA
        or manifest.get("complete_control_root_inventory") is not True
        or not manifest.get("epoch")
        or not manifest.get("writers")
        or any(row.get("fenced") is not True for row in manifest["writers"])
        or manifest.get("shared_owner") != str(Path(owner_path).absolute())
        or not isinstance(manifest.get("runs"), list)
    ):
        raise ValueError("reviewed_complete_runtime_root_writer_manifest_required")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS jobagg_legacy_control_state(id INTEGER PRIMARY KEY,schema TEXT,epoch TEXT,inventory_sha256 TEXT,manifest_json TEXT,after_ordinal INTEGER,complete INTEGER,receipt_json TEXT,chain_sha256 TEXT)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS jobagg_legacy_controls(run_id TEXT PRIMARY KEY,ordinal INTEGER UNIQUE,control_root TEXT,disposition TEXT,proof_json TEXT,repair_reason TEXT)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS legacy_control_unresolved ON jobagg_legacy_controls(disposition,ordinal)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS jobagg_legacy_dependencies(path TEXT PRIMARY KEY,run_id TEXT,sha256 TEXT,identity_json TEXT)"
    )
    sha = _digest(manifest)
    row = conn.execute(
        "SELECT inventory_sha256 FROM jobagg_legacy_control_state WHERE id=1"
    ).fetchone()
    if row and row[0] != sha:
        raise ValueError("runtime_control_import_inventory_changed")
    conn.execute(
        "INSERT OR IGNORE INTO jobagg_legacy_control_state VALUES(1,?,?,?,?,0,0,NULL,'')",
        (SCHEMA, manifest["epoch"], sha, json.dumps(manifest, sort_keys=True)),
    )
    return sha


def _processes_absent(state):
    pids = set(state.get("process_groups", [])) | {
        state[key] for key in ("wrapper_pid", "worker_pid", "publication_pid") if state.get(key)
    }
    for pid in pids:
        if type(pid) is not int or pid <= 0:
            raise ValueError("legacy_process_identity_invalid")
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            continue
        raise ValueError("legacy_process_still_present")


def step(conn, *, owner_fd, owner_path, limit, deadline_at, reviewed_implementations):
    owner(owner_fd, owner_path)
    if type(limit) is not int or limit <= 0:
        raise ValueError("finite_legacy_control_import_page_required")
    value = conn.execute(
        "SELECT manifest_json,after_ordinal,complete FROM jobagg_legacy_control_state WHERE id=1"
    ).fetchone()
    if value is None:
        raise ValueError("runtime_control_import_not_prepared")
    manifest = json.loads(value[0])
    after = value[1]
    if value[2]:
        return {"complete": True, "processed": 0}
    processed = 0
    from jobagg.terminal_publication import classify_run
    from jobagg.acceptance_catalog import import_run

    for ordinal in range(after, min(after + limit, len(manifest["runs"]))):
        if time.monotonic() >= deadline_at:
            raise TimeoutError("runtime_control_import_deadline")
        supplied = manifest["runs"][ordinal]
        root = Path(supplied["path"]).absolute()
        if root.resolve() != root or root.name != supplied["run_id"]:
            raise ValueError("runtime_control_root_aliased_or_wrong_identity")
        controls = {}
        values = {}
        for name, expected in supplied["controls"].items():
            if Path(name).name != name:
                raise ValueError("runtime_control_member_not_local")
            data, proof = _json_document(root / name, deadline_at=deadline_at)
            if proof["sha256"] != expected:
                raise ValueError("runtime_control_import_source_changed")
            controls[str(root / name)] = proof["sha256"]
            values[name] = data
        _processes_absent(values.get("state.json", values.get("outcome.json", {})))
        disposition = "repair_required"
        reason = "legacy_control_boundary_unproven"
        proof = {"run_id": root.name, "controls": controls}
        if "publication_intent.json" in values:
            classified = classify_run(root, deadline_at=deadline_at)
            proof["publication"] = classified
            if classified["disposition"] == "terminal":
                # Canonical terminal classification preserves original/recovery
                # bindings. It alone does not prove current export consistency.
                disposition = "publication_reconciliation_required"
                reason = "exact_accepted_identity_and_current_exports_required"
            else:
                disposition = "generation_pending"
                reason = "exact_existing_generation_recovery_required"
            for item in classified.get("controls", []):
                controls[item["path"]] = item["sha256"]
        elif values.get("worker_result.json", {}).get("worker_exit_code") == 0:
            accepted = import_run(
                conn,
                root,
                owner_fd=owner_fd,
                owner_path=owner_path,
                reviewed_implementations=reviewed_implementations,
                deadline_at=deadline_at,
            )
            proof["accepted_exit_sha256"] = accepted["sha256"]
            for item in accepted["controls"].values():
                controls[item["path"]] = item["sha256"]
            disposition = "accepted_pending"
            reason = "accepted_work_has_no_publication_intent"
        elif values.get("request.json"):
            disposition = "worker_claim_uncertain"
            reason = "worker_exit_or_independent_acceptance_reconciliation_required"
        for path, sha in controls.items():
            actual, record = _json_document(Path(path), deadline_at=deadline_at)
            if record["sha256"] != sha:
                raise ValueError("legacy_control_changed_before_checkpoint")
            stat = Path(path).stat()
            # Retain bytes and a cheap selected dependency identity. This is an
            # activation boundary; later recovery must advance its exact record.
            identity = [stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns]
            conn.execute(
                "INSERT OR REPLACE INTO jobagg_legacy_dependencies VALUES(?,?,?,?)",
                (path, root.name, sha, json.dumps(identity)),
            )
        conn.execute(
            "INSERT INTO jobagg_legacy_controls VALUES(?,?,?,?,?,?)",
            (root.name, ordinal, str(root), disposition, json.dumps(proof, sort_keys=True), reason),
        )
        previous = conn.execute(
            "SELECT chain_sha256 FROM jobagg_legacy_control_state WHERE id=1"
        ).fetchone()[0]
        chain = _digest([previous, root.name, ordinal, str(root), disposition, proof, reason])
        conn.execute(
            "UPDATE jobagg_legacy_control_state SET after_ordinal=?,chain_sha256=? WHERE id=1",
            (ordinal + 1, chain),
        )
        processed += 1
    complete = after + processed == len(manifest["runs"])
    if complete:
        proof = {
            "schema": SCHEMA,
            "epoch": manifest["epoch"],
            "inventory_sha256": _digest(manifest),
            "runs": len(manifest["runs"]),
            "shared_owner": manifest["shared_owner"],
            "controls_sha256": conn.execute(
                "SELECT chain_sha256 FROM jobagg_legacy_control_state WHERE id=1"
            ).fetchone()[0],
        }
        proof["sha256"] = _digest(proof)
        conn.execute(
            "UPDATE jobagg_legacy_control_state SET complete=1,receipt_json=? WHERE id=1",
            (json.dumps(proof, sort_keys=True),),
        )
    return {"complete": complete, "processed": processed}


def receipt(conn):
    if not conn.execute(
        "SELECT 1 FROM sqlite_master WHERE name='jobagg_legacy_control_state'"
    ).fetchone():
        raise ValueError("runtime_control_catalogue_missing_explicit_migration_required")
    row = conn.execute(
        "SELECT schema,complete,receipt_json FROM jobagg_legacy_control_state WHERE id=1"
    ).fetchone()
    if row is None or row[0] != SCHEMA or row[1] != 1:
        raise ValueError("runtime_control_import_incomplete")
    value = json.loads(row[2])
    unsigned = {key: item for key, item in value.items() if key != "sha256"}
    if _digest(unsigned) != value.get("sha256"):
        raise ValueError("runtime_control_import_receipt_changed")
    return value


def oldest_unresolved(conn):
    if not conn.execute(
        "SELECT 1 FROM sqlite_master WHERE name='jobagg_legacy_control_state'"
    ).fetchone():
        return None
    receipt(conn)
    cursor = conn.cursor()
    cursor.row_factory = sqlite3.Row
    rows = []
    for disposition in (
        "repair_required",
        "generation_pending",
        "accepted_pending",
        "worker_claim_uncertain",
        "publication_reconciliation_required",
    ):
        row = cursor.execute(
            "SELECT run_id,control_root,disposition,repair_reason,ordinal FROM jobagg_legacy_controls WHERE disposition=? ORDER BY ordinal LIMIT 1",
            (disposition,),
        ).fetchone()
        if row:
            rows.append(dict(row))
    return min(rows, key=lambda row: row["ordinal"]) if rows else None


def close_reconciled(
    conn,
    run_id,
    *,
    worker_database,
    destinations,
    publication_boundary,
    expected_publication_scope,
    owner_fd,
    owner_path,
    deadline_at,
    generation_references=(),
    reviewed_generation_sha256=(),
):
    """Compute exact accepted/export reconciliation here; never trust a boolean."""
    owner(owner_fd, owner_path)
    row = conn.execute(
        "SELECT control_root,proof_json FROM jobagg_legacy_controls WHERE run_id=?", (run_id,)
    ).fetchone()
    if row is None:
        raise ValueError("legacy_control_closure_run_unknown")
    from jobagg.runtime_reconciliation import reconcile_run

    reconciliation = reconcile_run(
        Path(row[0]),
        worker_database,
        destinations,
        deadline_at=deadline_at,
        publication_boundary=publication_boundary,
        expected_publication_scope=expected_publication_scope,
        generation_references=generation_references,
        reviewed_generation_sha256=reviewed_generation_sha256,
    )
    if not reconciliation["activation_reconciled"]:
        raise ValueError("legacy_control_closure_requires_exact_identity_and_export_reconciliation")
    proof = json.loads(row[1])
    for path, sha in proof["controls"].items():
        # Mutable process status is diagnostic; immutable worker acceptance and
        # original outcomes/intents still have to match their imported bytes.
        if Path(path).name == "state.json":
            continue
        _, actual = _json_document(Path(path), deadline_at=deadline_at)
        if actual["sha256"] != sha:
            raise ValueError("legacy_control_closure_original_evidence_changed")
    proof["reconciliation"] = reconciliation
    conn.execute(
        "UPDATE jobagg_legacy_controls SET disposition='terminal_verified',proof_json=?,repair_reason=NULL WHERE run_id=?",
        (json.dumps(proof, sort_keys=True), run_id),
    )
    return reconciliation
