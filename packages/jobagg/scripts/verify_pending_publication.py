#!/usr/bin/env python3
"""Step 3: verify retained native readback evidence and preview exact-version matches."""

import argparse
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import stat
import sys
import time

import compare_pending_publication as comparison
import inspect_pending_publication as inventory


def load_json(path):
    with Path(path).open("rb") as handle:
        content = handle.read(comparison.MAX_REPORT_BYTES + 1)
    if len(content) > comparison.MAX_REPORT_BYTES:
        raise ValueError("JSON input exceeds 32 MiB")
    value = json.loads(content)
    if not isinstance(value, dict):
        raise ValueError("JSON input must be an object")
    return value, hashlib.sha256(content).hexdigest()


def require_digest(value, compact=False):
    unsigned = {key: item for key, item in value.items() if key != "sha256"}
    options = {"sort_keys": True}
    if compact:
        options["separators"] = (",", ":")
    if hashlib.sha256(json.dumps(unsigned, **options).encode()).hexdigest() != value.get("sha256"):
        raise ValueError("Native receipt digest differs")


def export_identity(path):
    if path.resolve(strict=True) != path:
        raise ValueError("Export aliases are not accepted")
    value = path.stat()
    if not stat.S_ISREG(value.st_mode) or value.st_mode & 0o222:
        raise ValueError("Export is not a sealed regular file")
    return {"path": str(path), "device": value.st_dev, "inode": value.st_ino,
            "size": value.st_size, "mtime_ns": value.st_mtime_ns,
            "ctime_ns": value.st_ctime_ns, "mode": value.st_mode, "links": value.st_nlink}


def check_time(deadline):
    if time.monotonic() >= deadline:
        raise TimeoutError("Step 3 verification time limit")


def verify(comparison_path, output_root, *, max_seconds=60, max_bytes=4 * 1024**3):
    if not (0 < max_seconds <= 300 and 0 < max_bytes <= 4 * 1024**3):
        raise ValueError("Limits: seconds (0,300], export bytes (0,4 GiB]")
    started = time.monotonic()
    deadline = started + max_seconds
    prior, prior_digest = load_json(comparison_path)
    if (prior.get("schema") != "jobagg-pending-comparison-1" or prior.get("complete") is not True
            or not isinstance(prior.get("comparisons"), list)
            or len(prior["comparisons"]) != prior.get("compared_units")
            or len(prior["comparisons"]) > 100000):
        raise ValueError("Step 3 requires a complete scoped Step 2 comparison")
    original, original_digest = comparison.load_inspection(prior["inspection_file"])
    if original_digest != prior["inspection_sha256"]:
        raise ValueError("Step 1 input changed after comparison")
    expected_units = {row["unit"]: row for row in original["units"]}
    if (len({row["unit"] for row in prior["comparisons"]}) != len(prior["comparisons"])
            or any(row["unit"] not in expected_units
                   or row["inspected_version"] != expected_units[row["unit"]]["version"]
                   for row in prior["comparisons"])):
        raise ValueError("Step 2 unit scope differs from Step 1")
    root = Path(output_root).absolute()
    if root.resolve(strict=True) != root:
        raise ValueError("Output root aliases are not accepted")
    gate_path = root / ".jobagg-publication-state.json"
    worker_path = Path(original["database"]).resolve(strict=True)
    report = {
        "schema": "jobagg-pending-preview-1", "started_at_utc": inventory.utc_now(),
        "comparison_file": str(Path(comparison_path).resolve()), "comparison_sha256": prior_digest,
        "scope": "saved comparison units and the latest native completed publication boundary",
        "input_inventory_complete": original["complete"],
        "complete": False, "destination_boundary_verified": False,
        "export_hashes_verified": False, "acknowledgement_authorized": False,
        "owner_held_recheck_required": True, "global_backlog_empty_proven": False,
        "limits": {"max_seconds": max_seconds, "max_export_bytes": max_bytes},
        "checked_stores": 0, "checked_exports": 0, "export_bytes_read": 0,
        "unit_count": len(prior["comparisons"]), "preview": [], "units": [],
    }
    worker = None
    store_signatures = []
    checked_exports = []
    try:
        if inventory.file_identity(worker_path) != original["database_identity"]:
            raise ValueError("Worker file identity differs from Step 1")
        worker = inventory.open_reader(worker_path, deadline)
        state = inventory.boundary(worker)
        if (any(state[key] != prior["boundary"][key] for key in comparison.BINDINGS)
                or not state["bootstrap_complete"]):
            raise ValueError("Authority bindings changed since comparison")
        report["boundary"] = state
        gate, gate_digest = load_json(gate_path)
        report["publication_state"] = gate.get("state")
        if (gate.get("state") != "complete" or gate.get("status") != "published"
                or gate.get("readback_verified") is not True
                or gate.get("database_transactions_complete") is not True):
            raise ValueError("Latest publication is not a completed verified generation")
        report.update(generation_id=gate["generation_id"], publication_gate_sha256=gate_digest)
        selection = gate["publication_selection"]
        boundary = gate["publication_boundary"]
        plan_path = Path(gate["plan_path"]).absolute()
        if (plan_path.resolve(strict=True) != plan_path or plan_path.name != "plan.json"
                or plan_path.parent.name != gate["generation_id"]):
            raise ValueError("Native generation plan path differs")
        plan, plan_digest = load_json(plan_path)
        if (plan_digest != gate["plan_sha256"] or plan.get("generation_id") != gate["generation_id"]
                or plan.get("publication_selection") != selection
                or plan.get("processed_units") != gate["processed_units"]
                or plan.get("output_dir") != str(root)):
            raise ValueError("Native generation plan digest/scope differs")
        require_digest(selection, compact=True)
        require_digest(boundary)
        if (selection.get("schema") != "publication-work-3"
                or selection.get("epoch") != state["publication_epoch"]
                or selection.get("contract_sha256") != state["contract_sha256"]
                or boundary.get("schema") != "publication-boundary-1"
                or boundary.get("output_root") != str(root)):
            raise ValueError("Generation/boundary authority scope differs")
        chosen = {(row["unit"], row["version"]): row for row in selection["units"]}
        processed = {(row["unit"], row["version"]) for row in gate["processed_units"]}
        if not processed <= chosen.keys():
            raise ValueError("Processed versions are outside native selection")
        rejected_jobs = {row.get("job_key") for row in gate.get("rejected", []) if row.get("job_key")}
        rejected_sources = {row.get("source_id") for row in gate.get("rejected", [])
                            if not row.get("job_key")}
        stores, exports = boundary["databases"], boundary["exports"]
        if (not 1 <= len(stores) <= 64 or not 1 <= len(exports) <= 256
                or len({row["path"] for row in stores}) != len(stores)
                or len({row["path"] for row in exports}) != len(exports)):
            raise ValueError("Missing, duplicate or oversized destination/export scope")
        store_paths = {row["path"] for row in stores}
        expected_exports = {
            str(root / (Path(row["path"]).name.removesuffix("_jobs.sqlite3") + "_jobs_" + suffix))
            for row in stores for suffix in ("current.json", "current.csv", "history.json", "history.csv")
        }
        if (str(root / "all_jobs.sqlite3") not in store_paths
                or not set(selection["target_paths"]) <= store_paths
                or {row["path"] for row in exports} != expected_exports):
            raise ValueError("Destination/export coverage differs")
        required_receipts = {}
        for entry in [*plan.get("changes", []), *plan.get("listing_frames", [])]:
            for target in entry["destinations"]:
                if target["path"] not in store_paths:
                    raise ValueError("Plan destination missing from boundary")
                required_receipts.setdefault(target["path"], set()).add(entry["publication_key"])
        report.update(boundary_store_count=len(stores), boundary_export_count=len(exports),
                      checked_publication_receipts=0)
        for expected in stores:
            check_time(deadline)
            path = Path(expected["path"])
            if path.parent != root or path.resolve(strict=True) != path:
                raise ValueError("Destination outside output root or aliased")
            signature = inventory.source_signature(path)
            conn = inventory.open_reader(path, deadline,
                                         tables={"jobagg_publication_revision", "sqlite_master",
                                                 "jobagg_publication_receipts"})
            try:
                cursor = conn.execute("SELECT * FROM jobagg_publication_revision WHERE id=1")
                row = cursor.fetchone()
                if row is None:
                    raise ValueError("Missing destination revision")
                actual = dict(zip((column[0] for column in cursor.description), row))
                schema = list(conn.execute("SELECT name,type,sql FROM sqlite_master ORDER BY name"))
                schema_digest = hashlib.sha256(json.dumps(schema).encode()).hexdigest()
                binding = {"path": str(path), **inventory.file_identity(path),
                           **{key: actual[key] for key in
                              ("schema", "epoch", "store_id", "revision", "schema_sha256")}}
                if (actual["schema"] != "publication-revision-2" or binding != expected
                        or schema_digest != actual["schema_sha256"] or not inventory.reader_unchanged(conn)):
                    raise ValueError("Destination schema/revision binding differs")
                for key in required_receipts.get(str(path), set()):
                    receipt = conn.execute(
                        "SELECT generation_id FROM jobagg_publication_receipts WHERE publication_key=?",
                        (key,)).fetchone()
                    if not receipt or not receipt[0]:
                        raise ValueError("Destination publication receipt missing")
                    report["checked_publication_receipts"] += 1
            finally:
                conn.close()
            store_signatures.append((path, signature))
            report["checked_stores"] += 1
        report["destination_boundary_verified"] = True
        for expected in exports:
            check_time(deadline)
            path = Path(expected["path"])
            if path.parent != root:
                raise ValueError("Export outside output root")
            identity = {key: value for key, value in expected.items() if key != "sha256"}
            if export_identity(path) != identity:
                raise ValueError("Export identity differs from native readback boundary")
            digest = hashlib.sha256()
            with path.open("rb") as handle:
                before = handle.fileno()
                if inventory.file_identity(Path(expected["path"])) != {
                        "device": os.fstat(before).st_dev,
                        "inode": os.fstat(before).st_ino}:
                    raise ValueError("Export changed while opening")
                while True:
                    check_time(deadline)
                    chunk = handle.read(min(1024**2, max_bytes - report["export_bytes_read"] + 1))
                    if not chunk:
                        break
                    report["export_bytes_read"] += len(chunk)
                    if report["export_bytes_read"] > max_bytes:
                        raise ValueError("Export verification byte limit")
                    digest.update(chunk)
            if digest.hexdigest() != expected["sha256"] or export_identity(path) != identity:
                raise ValueError("Export bytes/identity differ from native readback boundary")
            checked_exports.append((path, identity))
            report["checked_exports"] += 1
        report["export_hashes_verified"] = True
        for old in prior["comparisons"]:
            check_time(deadline)
            row = worker.execute("SELECT version,source_id,kind,disposition FROM jobagg_dirty_current "
                                 "WHERE unit=?", (old["unit"],)).fetchone()
            category = "needs_fresh_inspection"
            pair = (old["unit"], old["inspected_version"])
            if row and tuple(row[:3]) == (pair[1], old["source_id"], old["kind"]):
                if row[3] != "pending":
                    category = "already_acknowledged" if row[3] == "acknowledged" else "held_or_unknown_retain"
                elif pair not in processed:
                    category = "no_exact_version_readback_proof_retain_pending"
                else:
                    selected = chosen[pair]
                    if (selected["source_id"] != old["source_id"] or selected["kind"] != old["kind"]
                            or selected.get("job_key") in rejected_jobs
                            or selected["source_id"] in rejected_sources or selected["kind"] == "unknown"):
                        category = "rejected_or_identity_uncertain_retain_pending"
                    else:
                        category = "exact_native_readback_match_preview_only"
                        report["preview"].append({"unit": pair[0], "version": pair[1],
                                                  "generation_id": gate["generation_id"]})
            report["units"].append({"unit": pair[0], "version": pair[1], "category": category})
        if (not inventory.reader_unchanged(worker)
                or any(inventory.source_signature(path) != signature for path, signature in store_signatures)
                or any(export_identity(path) != identity for path, identity in checked_exports)
                or load_json(gate_path)[1] != gate_digest
                or load_json(plan_path)[1] != plan_digest):
            raise ValueError("Publication/worker boundary changed during verification")
        report.update(complete=True, stop_reason="verified_scoped_boundary")
    except (OSError, sqlite3.Error, ValueError, KeyError, TypeError) as exc:
        report.update(stop_reason="time_limit" if isinstance(exc, TimeoutError) or time.monotonic() >= deadline
                      else "publication_in_progress" if report.get("publication_state") in {"publishing", "exporting"}
                      else "verification_incomplete", error=str(exc), preview=[])
    finally:
        if worker is not None:
            worker.close()
    report["preview_count"] = len(report["preview"])
    report["counts"] = dict(Counter(row["category"] for row in report["units"]))
    report["finished_at_utc"] = inventory.utc_now()
    report["elapsed_seconds"] = round(time.monotonic() - started, 6)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--comparison", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--max-seconds", type=float, default=60)
    parser.add_argument("--max-bytes", type=int, default=4 * 1024**3)
    args = parser.parse_args(argv)
    try:
        report = verify(args.comparison, args.output_root,
                        max_seconds=args.max_seconds, max_bytes=args.max_bytes)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        report = {"schema": "jobagg-pending-preview-1", "complete": False,
                  "stop_reason": "input_error", "error": str(exc)}
    try:
        inventory.print_saved_report(report, "jobagg-pending-preview-")
    except OSError as exc:
        print(json.dumps({"complete": False, "stop_reason": "report_write_error", "error": str(exc)}))
        return 2
    return 0 if report["complete"] else 2


if __name__ == "__main__":
    sys.exit(main())
