"""Explicit paged bootstrap of all reviewed legacy control roots.

Normal ticks consult one indexed unresolved reference, never enumerate run or
completed-generation history. Uncertain claims/generations remain durable repair
work. Only exact accepted/publication/export reconciliation can certify worker completion.
An exact wrapper duplicate may separately become a preserved reference; its
indexed worker remains independently gated.
This preparation API neither deploys code nor performs publication or requests.
"""

import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
from datetime import datetime
import time

from jobagg.publication_snapshot import _json_document, stable_file
from jobagg.runtime_source_head import owner

SCHEMA = "runtime-control-catalogue-1"
CONTROL_TABLES = {
    "jobagg_legacy_control_state",
    "jobagg_legacy_controls",
    "jobagg_legacy_dependencies",
    "jobagg_legacy_review_holds",
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
    conn.execute(
        "CREATE TABLE IF NOT EXISTS jobagg_legacy_review_holds(run_id TEXT PRIMARY KEY,review_json TEXT NOT NULL)"
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


def hold_relocated_publication(
    conn,
    *,
    run_id,
    original_control_root,
    owner_fd,
    owner_path,
    expected_inventory_sha256,
    deadline_at,
):
    """Explicit operator review; retain missing relocation proof, never bless it.

    Only an unprocessed record with the exact reviewed old acceptance locator
    qualifies. This cannot repair a hash contradiction or close a publication.
    """
    owner(owner_fd, owner_path)
    if conn.execute("SELECT 1 FROM sqlite_master WHERE name='jobagg_runtime_state'").fetchone():
        raise ValueError("legacy_review_hold_requires_inactive_runtime")
    row = conn.execute(
        "SELECT manifest_json,inventory_sha256,after_ordinal,complete FROM jobagg_legacy_control_state WHERE id=1"
    ).fetchone()
    if row is None or row[1] != expected_inventory_sha256 or row[3]:
        raise ValueError("legacy_review_hold_inventory_changed_or_complete")
    manifest = json.loads(row[0])
    selected = [(i, r) for i, r in enumerate(manifest["runs"]) if r["run_id"] == run_id]
    if len(selected) != 1 or selected[0][0] < row[2]:
        raise ValueError("legacy_review_hold_requires_unprocessed_record")
    supplied = selected[0][1]
    root, original = Path(supplied["path"]), Path(original_control_root)
    if (
        not original.is_absolute()
        or ".." in original.parts
        or original.name != run_id
        or original == root
        or supplied.get("kind", "worker_run") != "worker_run"
    ):
        raise ValueError("legacy_review_hold_exact_old_control_root_required")
    request, record = _json_document(root / "publication_request.json", deadline_at=deadline_at)
    intent, intent_record = _json_document(
        root / "publication_intent.json", deadline_at=deadline_at
    )
    if (
        record["sha256"] != supplied["controls"].get("publication_request.json")
        or intent_record["sha256"] != supplied["controls"].get("publication_intent.json")
        or intent.get("request_sha256") != record["sha256"]
        or request.get("worker_acceptance_path") != str(original / "acceptance.json")
        or request.get("worker_acceptance_sha256") != supplied["controls"].get("acceptance.json")
    ):
        raise ValueError("legacy_review_hold_original_binding_changed")
    review = {
        "run_id": run_id,
        "original_control_root": str(original),
        "reason": "legacy_publication_locator_relocated_requires_review",
        "request_sha256": record["sha256"],
        "inventory_sha256": row[1],
    }
    payload = json.dumps(review, sort_keys=True)
    prior = conn.execute(
        "SELECT review_json FROM jobagg_legacy_review_holds WHERE run_id=?", (run_id,)
    ).fetchone()
    if prior and prior[0] != payload:
        raise ValueError("legacy_review_hold_changed")
    conn.execute("INSERT OR IGNORE INTO jobagg_legacy_review_holds VALUES(?,?)", (run_id, payload))
    return review


def hold_publication_contradiction(
    conn, *, run_id, expected_failure, owner_fd, owner_path, expected_inventory_sha256, deadline_at
):
    """Record a reproduced legacy contradiction as quarantined operator work.

    Normal import still stops. This explicit review cannot certify publication,
    import acceptance, change source bytes, or make a held record fetch-eligible.
    """
    owner(owner_fd, owner_path)
    if conn.execute("SELECT 1 FROM sqlite_master WHERE name='jobagg_runtime_state'").fetchone():
        raise ValueError("legacy_review_hold_requires_inactive_runtime")
    row = conn.execute(
        "SELECT manifest_json,inventory_sha256,after_ordinal,complete FROM jobagg_legacy_control_state WHERE id=1"
    ).fetchone()
    if row is None or row[1] != expected_inventory_sha256 or row[3]:
        raise ValueError("legacy_review_hold_inventory_changed_or_complete")
    manifest = json.loads(row[0])
    selected = [(i, r) for i, r in enumerate(manifest["runs"]) if r["run_id"] == run_id]
    if (
        len(selected) != 1
        or selected[0][0] < row[2]
        or not expected_failure.startswith("Publication classification: ")
    ):
        raise ValueError("legacy_contradiction_review_exact_unprocessed_failure_required")
    supplied = selected[0][1]
    if supplied.get("kind", "worker_run") != "worker_run":
        raise ValueError("legacy_contradiction_worker_control_required")
    root = Path(supplied["path"])
    for name, sha in supplied["controls"].items():
        if stable_file(root / name, deadline_at=deadline_at)["sha256"] != sha:
            raise ValueError("legacy_review_hold_source_changed")
    from jobagg.terminal_publication import classify_run

    try:
        classify_run(root, deadline_at=deadline_at)
    except ValueError as error:
        if str(error) != expected_failure:
            raise ValueError("legacy_review_hold_failure_changed") from error
    else:
        raise ValueError("legacy_review_hold_failure_not_reproduced")
    review = {
        "kind": "publication_contradiction",
        "run_id": run_id,
        "reason": "legacy_publication_contradiction_requires_review",
        "observed_failure": expected_failure,
        "inventory_sha256": row[1],
    }
    payload = json.dumps(review, sort_keys=True)
    prior = conn.execute(
        "SELECT review_json FROM jobagg_legacy_review_holds WHERE run_id=?", (run_id,)
    ).fetchone()
    if prior and prior[0] != payload:
        raise ValueError("legacy_review_hold_changed")
    conn.execute("INSERT OR IGNORE INTO jobagg_legacy_review_holds VALUES(?,?)", (run_id, payload))
    return review


def _process_started_at(pid, deadline_at):
    remaining = deadline_at - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("runtime_control_import_deadline")
    if sys.platform == "darwin":
        # SDK sys/proc_info.h: PROC_PIDTBSDINFO / struct proc_bsdinfo.
        # Read the kernel identity directly: macOS will not exec setuid ps
        # inside the copy-only sandbox, and no executable fallback is needed.
        import ctypes

        class BSDInfo(ctypes.Structure):
            _fields_ = [
                ("prefix", ctypes.c_uint32 * 12),
                ("comm", ctypes.c_char * 16),
                ("name", ctypes.c_char * 32),
                ("suffix", ctypes.c_uint32 * 6),
                ("seconds", ctypes.c_uint64),
                ("microseconds", ctypes.c_uint64),
            ]

        library = ctypes.CDLL("/usr/lib/libproc.dylib", use_errno=True)
        query = library.proc_pidinfo
        query.argtypes = [
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_uint64,
            ctypes.c_void_p,
            ctypes.c_int,
        ]
        query.restype = ctypes.c_int
        info = BSDInfo()
        if (
            query(pid, 3, 0, ctypes.byref(info), ctypes.sizeof(info)) != ctypes.sizeof(info)
            or info.prefix[3] != pid
            or info.microseconds >= 1_000_000
        ):
            raise ValueError("legacy_process_start_identity_unavailable")
        return info.seconds + info.microseconds / 1_000_000
    result = subprocess.run(
        ["/bin/ps", "-p", str(pid), "-o", "lstart="],
        capture_output=True,
        text=True,
        timeout=min(2, remaining),
        env={**os.environ, "LC_ALL": "C"},
        check=False,
    )
    if result.returncode != 0:
        raise ValueError("legacy_process_start_identity_unavailable")
    try:
        return time.mktime(time.strptime(result.stdout.strip(), "%a %b %d %H:%M:%S %Y"))
    except ValueError as error:
        raise ValueError("legacy_process_start_identity_invalid") from error


def _boot_started_at():
    if sys.platform == "darwin":
        import ctypes

        library = ctypes.CDLL(None, use_errno=True)
        query = library.sysctlbyname
        query.argtypes = [
            ctypes.c_char_p,
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_size_t),
            ctypes.c_void_p,
            ctypes.c_size_t,
        ]
        query.restype = ctypes.c_int
        value = (ctypes.c_long * 2)()
        size = ctypes.c_size_t(ctypes.sizeof(value))
        if query(
            b"kern.boottime", value, ctypes.byref(size), None, 0
        ) != 0 or size.value != ctypes.sizeof(value):
            raise ValueError("legacy_boot_start_identity_unavailable")
        return value[0] + value[1] / 1_000_000
    if sys.platform.startswith("linux"):
        with Path("/proc/stat").open() as stream:
            for line in stream:
                if line.startswith("btime "):
                    return float(line.split()[1])
    raise ValueError("legacy_boot_start_identity_unavailable")


def _processes_absent(state, *, deadline_at):
    groups = set(state.get("process_groups", []))
    pids = groups | {
        state[key] for key in ("wrapper_pid", "worker_pid", "publication_pid") if state.get(key)
    }
    reconciled = []
    for pid in pids:
        if type(pid) is not int or pid <= 0:
            raise ValueError("legacy_process_identity_invalid")
        if pid in groups:
            try:
                os.killpg(pid, 0)
            except ProcessLookupError:
                pass
            except PermissionError:
                pass  # Present but not signalable; require kernel identity below.
            else:
                # A surviving group without its original leader cannot be
                # declared drained from a missing PID alone.
                try:
                    os.kill(pid, 0)
                except ProcessLookupError as error:
                    raise ValueError("legacy_process_group_identity_unresolved") from error
                except PermissionError:
                    pass
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            continue
        except PermissionError:
            pass  # EPERM proves neither drain nor ownership; inspect start identity.
        try:
            finished = datetime.fromisoformat(state["finished_at"])
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError("legacy_process_still_present") from error
        if finished.tzinfo is None:
            raise ValueError("legacy_process_still_present")
        boot = _boot_started_at()
        if boot > finished.timestamp():
            from jobagg.runtime_clock import domain

            reconciled.append(
                {
                    "pid": pid,
                    "historical_finished_at": state["finished_at"],
                    "boot_started_at_epoch": boot,
                    "boot_domain": domain(),
                    "disposition": "prior_boot_pid_reference",
                }
            )
            continue
        started = _process_started_at(pid, deadline_at)
        if started <= finished.timestamp():
            raise ValueError("legacy_process_still_present")
        # Observed start is later than the recorded end: this PID cannot be
        # the historical worker. Retain that evidence; never signal it.
        reconciled.append(
            {
                "pid": pid,
                "observed_started_at_epoch": started,
                "historical_finished_at": state["finished_at"],
                "disposition": "pid_reused",
            }
        )
    return reconciled


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
    from jobagg.acceptance_catalog import AcceptanceUnavailable, import_run, _bundle

    for ordinal in range(after, min(after + limit, len(manifest["runs"]))):
        if time.monotonic() >= deadline_at:
            raise TimeoutError("runtime_control_import_deadline")
        supplied = manifest["runs"][ordinal]
        root = Path(supplied["path"]).absolute()
        kind = supplied.get("kind", "worker_run")
        record_id = (
            root.name
            if kind == "worker_run"
            else "held-" + hashlib.sha256(str(root).encode()).hexdigest()
        )
        if (
            kind not in {"worker_run", "held_control_set"}
            or root.resolve() != root
            or record_id != supplied["run_id"]
        ):
            raise ValueError("runtime_control_root_aliased_or_wrong_identity")
        controls = {}
        values = {}
        for name, expected in supplied["controls"].items():
            if Path(name).name != name:
                raise ValueError("runtime_control_member_not_local")
            if kind == "held_control_set":
                data, proof = None, stable_file(root / name, deadline_at=deadline_at)
            else:
                if (root / name).stat().st_size > 16 * 1024**2:
                    raise ValueError("runtime_worker_control_oversized")
                data, proof = _json_document(root / name, deadline_at=deadline_at)
            if proof["sha256"] != expected:
                raise ValueError("runtime_control_import_source_changed")
            controls[str(root / name)] = proof["sha256"]
            values[name] = data
        process_proof = []
        if kind == "worker_run":
            process_proof = _processes_absent(
                values.get("state.json", values.get("outcome.json", {})), deadline_at=deadline_at
            )
        disposition = "repair_required"
        reason = "legacy_control_boundary_unproven"
        proof = {
            "run_id": record_id,
            "kind": kind,
            "controls": controls,
            "process_reconciliation": process_proof,
        }
        if kind == "held_control_set":
            # Standalone reports, browser/manual diagnostics and wrapper
            # outcomes have no exact worker exit bundle. Inventory them without
            # translating their success flags into acceptance or publication.
            reason = "legacy_auxiliary_controls_require_explicit_reconciliation"
        elif "publication_intent.json" in values:
            reviewed_hold = conn.execute(
                "SELECT review_json FROM jobagg_legacy_review_holds WHERE run_id=?", (record_id,)
            ).fetchone()
            if reviewed_hold:
                reviewed_hold = json.loads(reviewed_hold[0])
                if reviewed_hold.get("kind") == "publication_contradiction":
                    proof["publication_contradiction_review_hold"] = reviewed_hold
                elif reviewed_hold["request_sha256"] != controls.get(
                    str(root / "publication_request.json")
                ):
                    raise ValueError("legacy_review_hold_source_changed")
                else:
                    proof["publication_review_hold"] = reviewed_hold
                reason = reviewed_hold["reason"]
                classified = None
            else:
                classified = classify_run(root, deadline_at=deadline_at)
            if classified is not None:
                proof["publication"] = {
                    **classified,
                    "controls": [str(path) for path in classified["controls"]],
                }
                if classified["disposition"] == "terminal":
                    disposition = "publication_reconciliation_required"
                    reason = "exact_accepted_identity_and_current_exports_required"
                else:
                    disposition = "generation_pending"
                    reason = "exact_existing_generation_recovery_required"
                for path in classified["controls"]:
                    controls[str(path)] = classified["control_sha256"][str(path)]
        elif values.get("request.json"):
            disposition = "worker_claim_uncertain"
            reason = "worker_exit_or_independent_acceptance_reconciliation_required"
        # A publication result is not a substitute for exact worker acceptance.
        # Import successful exits even when an intent already exists. Missing
        # legacy proofs become explicit repair work; integrity errors still stop.
        if (
            kind == "worker_run"
            and "publication_contradiction_review_hold" not in proof
            and values.get("worker_result.json", {}).get("worker_exit_code") == 0
        ):
            try:
                review = proof.get("publication_review_hold")
                if review:
                    legacy_owner = str(
                        Path(review["original_control_root"]).parents[2]
                        / "remediation/manual-fetch-owner.lock"
                    )
                    if (
                        legacy_owner != str(Path(owner_path).absolute())
                        and values.get("acceptance.json", {}).get("binding", {}).get("shared_lock")
                        == legacy_owner
                    ):
                        # Validate original bytes/scope against their historical
                        # owner, with NO implementation admitted. This remains
                        # a review hold and inserts no accepted exit identity.
                        proof["acceptance_original_owner"] = legacy_owner
                        try:
                            _bundle(
                                root,
                                deadline_at=deadline_at,
                                owner_path=legacy_owner,
                                reviewed_implementations=set(),
                            )
                        except AcceptanceUnavailable as error:
                            raise AcceptanceUnavailable(
                                ["accepted_exit_legacy_owner_review_required", *error.reasons]
                            ) from error
                        raise ValueError("legacy_owner_hold_cannot_import_acceptance")
                accepted = import_run(
                    conn,
                    root,
                    owner_fd=owner_fd,
                    owner_path=owner_path,
                    reviewed_implementations=reviewed_implementations,
                    deadline_at=deadline_at,
                )
            except AcceptanceUnavailable as error:
                disposition = "repair_required"
                reason = ";".join(
                    ([reason] if "publication_review_hold" in proof else []) + list(error.reasons)
                )
                proof["acceptance_hold"] = list(error.reasons)
            else:
                proof["accepted_exit_sha256"] = accepted["sha256"]
                for item in accepted["controls"].values():
                    controls[item["path"]] = item["sha256"]
                if "publication_intent.json" not in values:
                    disposition = "accepted_pending"
                    reason = "accepted_work_has_no_publication_intent"
        unverified = supplied.get("unverified_files", {})
        if unverified:
            if kind != "held_control_set" or set(unverified) & set(supplied["controls"]):
                raise ValueError("runtime_unverified_control_scope_invalid")
            proof["unverified_controls"] = {}
            reason = "legacy_auxiliary_content_not_verified_requires_review"
            for name, expected in unverified.items():
                if time.monotonic() >= deadline_at:
                    raise TimeoutError("runtime_control_import_deadline")
                if Path(name).name != name:
                    raise ValueError("runtime_control_member_not_local")
                path = root / name
                if path.resolve() != path or not path.is_file():
                    raise ValueError("legacy_unverified_control_alias_or_missing")
                info = path.stat()
                identity = [
                    info.st_dev,
                    info.st_ino,
                    info.st_size,
                    info.st_mtime_ns,
                    info.st_ctime_ns,
                ]
                if identity != expected:
                    raise ValueError("legacy_unverified_control_identity_changed")
                proof["unverified_controls"][str(path)] = identity
                conn.execute(
                    "INSERT OR REPLACE INTO jobagg_legacy_dependencies VALUES(?,?,?,?)",
                    (str(path), record_id, None, json.dumps(identity)),
                )
        for path, sha in controls.items():
            record = stable_file(Path(path), deadline_at=deadline_at)
            if record["sha256"] != sha:
                raise ValueError("legacy_control_changed_before_checkpoint")
            stat = Path(path).stat()
            # Retain bytes and a cheap selected dependency identity. This is an
            # activation boundary; later recovery must advance its exact record.
            identity = [stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns]
            conn.execute(
                "INSERT OR REPLACE INTO jobagg_legacy_dependencies VALUES(?,?,?,?)",
                (path, record_id, sha, json.dumps(identity)),
            )
        conn.execute(
            "INSERT INTO jobagg_legacy_controls VALUES(?,?,?,?,?,?)",
            (record_id, ordinal, str(root), disposition, json.dumps(proof, sort_keys=True), reason),
        )
        previous = conn.execute(
            "SELECT chain_sha256 FROM jobagg_legacy_control_state WHERE id=1"
        ).fetchone()[0]
        chain = _digest([previous, record_id, ordinal, str(root), disposition, proof, reason])
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
    historical_index=None,
):
    """Compute exact accepted/export reconciliation here; never trust a boolean."""
    owner(owner_fd, owner_path)
    row = conn.execute(
        "SELECT control_root,proof_json,disposition FROM jobagg_legacy_controls WHERE run_id=?", (run_id,)
    ).fetchone()
    if row is None:
        raise ValueError("legacy_control_closure_run_unknown")
    proof = json.loads(row[1])
    if historical_index is not None:
        accepted = conn.execute(
            "SELECT proof_sha256 FROM jobagg_accepted_exit_runs WHERE run_id=?", (run_id,)
        ).fetchone()
        if (row[2] not in {"publication_reconciliation_required", "terminal_verified"}
                or accepted is None or accepted[0] != proof.get("accepted_exit_sha256")
                or proof.get("acceptance_hold") or proof.get("publication_review_hold")):
            raise ValueError("historical_index_requires_reviewed_accepted_terminal_run")
    if proof.get("publication_contradiction_review_hold"):
        raise ValueError("legacy_publication_contradiction_requires_separate_review_resolution")
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
        historical_index=historical_index,
    )
    if not reconciliation["activation_reconciled"]:
        raise ValueError("legacy_control_closure_requires_exact_identity_and_export_reconciliation")
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


def reconcile_wrapper_reference(
    conn, run_id, *, expected_inventory_sha256, owner_fd, owner_path, deadline_at
):
    """Resolve only an exact diagnostic duplicate of an indexed worker outcome.

    This is explicit owner-held reconciliation, never a tick-time history scan.
    It neither approves that worker nor certifies acceptance/publication. Its
    independently indexed worker keeps its original disposition and proof.
    Wrappers with extra actions (including retention) cannot use this path.
    """
    owner(owner_fd, owner_path)
    imported = receipt(conn)
    if imported["inventory_sha256"] != expected_inventory_sha256:
        raise ValueError("wrapper_reference_inventory_changed")
    row = conn.execute(
        "SELECT control_root,disposition,proof_json FROM jobagg_legacy_controls WHERE run_id=?",
        (run_id,),
    ).fetchone()
    if row is None or row[1] not in {"repair_required", "reference_preserved"}:
        raise ValueError("wrapper_reference_requires_held_auxiliary")
    root, proof = Path(row[0]), json.loads(row[2])
    paths = {**proof.get("unverified_controls", {}), **proof.get("controls", {})}
    if proof.get("kind") != "held_control_set" or set(paths) != {
        str(root / "intent.json"), str(root / "outcome.json")
    }:
        raise ValueError("wrapper_reference_exact_two_controls_required")
    values, verified = {}, {}
    for name in ("intent.json", "outcome.json"):
        path = root / name
        dependency = conn.execute(
            "SELECT run_id,sha256,identity_json FROM jobagg_legacy_dependencies WHERE path=?",
            (str(path),),
        ).fetchone()
        value, record = _json_document(path, deadline_at=deadline_at)
        info = path.stat()
        identity = [info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns]
        if (
            dependency is None
            or dependency[0] != run_id
            or json.loads(dependency[2]) != identity
            or (dependency[1] is not None and dependency[1] != record["sha256"])
            or (str(path) in proof.get("controls", {})
                and proof["controls"][str(path)] != record["sha256"])
        ):
            raise ValueError("wrapper_reference_original_control_changed")
        values[name], verified[str(path)] = value, record["sha256"]
    intent, outcome = values["intent.json"], values["outcome.json"]
    result = outcome.get("result")
    if (
        set(intent) != {"schema_version", "attempt_id", "status", "started_at", "pid", "config"}
        or set(outcome) != set(intent) | {"finished_at", "result"}
        or intent.get("schema_version") != 1
        or outcome.get("schema_version") != 1
        or intent.get("status") != "started"
        or not isinstance(result, dict)
        or not result.get("run_id")
        or intent.get("attempt_id") != root.name
        or any(outcome.get(k) != intent.get(k) for k in
               ("attempt_id", "started_at", "pid", "config"))
        or type(intent.get("pid")) is not int
        or result.get("wrapper_pid") != intent["pid"]
        or outcome.get("status") != result.get("status")
    ):
        raise ValueError("wrapper_reference_binding_invalid")
    child = conn.execute(
        "SELECT control_root,proof_json FROM jobagg_legacy_controls WHERE run_id=?",
        (result["run_id"],),
    ).fetchone()
    if child is None:
        raise ValueError("wrapper_reference_worker_not_indexed")
    child_root, child_proof = Path(child[0]), json.loads(child[1])
    if (
        child_proof.get("kind") != "worker_run"
        or result.get("run_dir") != str(child_root)
        or result.get("request_path") != str(child_root / "request.json")
        or result.get("report_path") != str(child_root / "acceptance.json")
    ):
        raise ValueError("wrapper_reference_worker_scope_differs")
    child_path = child_root / "outcome.json"
    child_value, child_record = _json_document(child_path, deadline_at=deadline_at)
    if (
        child_proof.get("controls", {}).get(str(child_path)) != child_record["sha256"]
        or child_value != result
    ):
        raise ValueError("wrapper_reference_worker_outcome_differs")
    try:
        times = [datetime.fromisoformat(v) for v in (
            intent["started_at"], result["started_at"], result["finished_at"], outcome["finished_at"]
        )]
        if any(t.tzinfo is None for t in times) or times != sorted(times):
            raise ValueError("invalid wrapper chronology")
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("wrapper_reference_chronology_invalid") from error
    process = _processes_absent(
        {**result, "wrapper_pid": intent["pid"], "finished_at": outcome["finished_at"]},
        deadline_at=deadline_at,
    )
    verified[str(child_path)] = child_record["sha256"]
    for path, sha in verified.items():
        if stable_file(Path(path), deadline_at=deadline_at)["sha256"] != sha:
            raise ValueError("wrapper_reference_changed_before_checkpoint")
    reference = {
        "schema": "legacy-wrapper-reference-1",
        "inventory_sha256": expected_inventory_sha256,
        "worker_run_id": result["run_id"],
        "worker_outcome_sha256": child_record["sha256"],
        "wrapper_controls": {k: v for k, v in verified.items() if k != str(child_path)},
        "certifies_acceptance_or_publication": False,
    }
    if row[1] == "reference_preserved":
        if proof.get("wrapper_reference") != reference:
            raise ValueError("wrapper_reference_prior_resolution_changed")
        return reference
    proof["wrapper_reference"] = reference
    proof["wrapper_process_reconciliation"] = process
    for path, sha in reference["wrapper_controls"].items():
        conn.execute("UPDATE jobagg_legacy_dependencies SET sha256=? WHERE path=?", (sha, path))
    conn.execute(
        "UPDATE jobagg_legacy_controls SET disposition='reference_preserved',proof_json=?,repair_reason=NULL WHERE run_id=?",
        (json.dumps(proof, sort_keys=True), run_id),
    )
    return reference


def review_accepted_exit(
    conn, run_id, *, reviewed_implementations, review_sha256,
    expected_inventory_sha256, owner_fd, owner_path, deadline_at
):
    """Resolve only an implementation hold using an explicit historical review.

    Source provenance is reviewed off-line by the operator. Original controls
    and successful exact task identities are reverified here. This API does not
    close publication, an old-owner hold, a missing interval or a contradiction.
    """
    owner(owner_fd, owner_path)
    if receipt(conn)['inventory_sha256'] != expected_inventory_sha256:
        raise ValueError('accepted_review_inventory_changed')
    if (not isinstance(review_sha256, str) or len(review_sha256) != 64
            or any(c not in '0123456789abcdef' for c in review_sha256)):
        raise ValueError('accepted_review_receipt_required')
    row = conn.execute(
        'SELECT control_root,disposition,proof_json FROM jobagg_legacy_controls WHERE run_id=?',
        (run_id,),
    ).fetchone()
    if row is None:
        raise ValueError('accepted_review_run_unknown')
    proof = json.loads(row[2])
    if (row[1] != 'repair_required' or proof.get('kind') != 'worker_run'
            or proof.get('acceptance_hold') != ['accepted_exit_implementation_review_required']
            or proof.get('publication_review_hold') or proof.get('publication_contradiction_review_hold')):
        raise ValueError('accepted_review_requires_only_implementation_hold')
    for name, expected in proof['controls'].items():
        if stable_file(Path(name), deadline_at=deadline_at)['sha256'] != expected:
            raise ValueError('accepted_review_original_controls_changed')
    publication = proof.get('publication', {}).get('disposition')
    if publication == 'terminal':
        disposition, reason = 'publication_reconciliation_required', 'exact_accepted_identity_and_current_exports_required'
    elif publication == 'unresolved':
        disposition, reason = 'generation_pending', 'exact_existing_generation_recovery_required'
    elif publication is None:
        disposition, reason = 'accepted_pending', 'accepted_work_has_no_publication_intent'
    else:
        raise ValueError('accepted_review_publication_class_unknown')
    from jobagg.acceptance_catalog import import_run

    accepted = import_run(conn, Path(row[0]), owner_fd=owner_fd, owner_path=owner_path,
                          reviewed_implementations=reviewed_implementations, deadline_at=deadline_at)
    proof['accepted_exit_sha256'] = accepted['sha256']
    proof['acceptance_review'] = {'review_sha256': review_sha256,
                                'original_hold': proof.pop('acceptance_hold'),
                                'implementation_sha256': accepted['implementation_sha256']}
    conn.execute('UPDATE jobagg_legacy_controls SET disposition=?,proof_json=?,repair_reason=? WHERE run_id=?',
                 (disposition, json.dumps(proof, sort_keys=True), reason, run_id))
    return {'run_id': run_id, 'disposition': disposition, 'accepted_exit_sha256': accepted['sha256']}
