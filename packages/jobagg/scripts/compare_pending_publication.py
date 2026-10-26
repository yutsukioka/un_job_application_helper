#!/usr/bin/env python3
"""Step 2: compare a saved Step 1 inventory with current queue metadata; never write it."""

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import time

import inspect_pending_publication as inventory


MAX_REPORT_BYTES = 32 * 1024 * 1024
BINDINGS = ("runtime_schema", "epoch", "config_sha256", "publication_schema",
            "publication_epoch", "contract_sha256", "bootstrap_complete")


def load_inspection(path):
    with Path(path).open("rb") as handle:
        content = handle.read(MAX_REPORT_BYTES + 1)
    if len(content) > MAX_REPORT_BYTES:
        raise ValueError("Inspection exceeds the 32 MiB input limit")
    report = json.loads(content)
    if not isinstance(report, dict) or report.get("schema") != "jobagg-pending-inspection-2":
        raise ValueError("Step 1 schema 2 with saved unit/version records is required")
    units = report.get("units")
    if (not isinstance(units, list) or len(units) > 100000
            or len(units) != report.get("observed_pending_units")):
        raise ValueError("Inspection unit count is missing or inconsistent")
    seen = set()
    for row in units:
        if (not isinstance(row, dict) or not isinstance(row.get("unit"), str)
                or not row["unit"] or type(row.get("version")) is not int or row["version"] < 0
                or not isinstance(row.get("source_id"), (str, type(None)))
                or not isinstance(row.get("kind"), str) or row["unit"] in seen):
            raise ValueError("Inspection contains invalid or duplicate unit/version records")
        seen.add(row["unit"])
    boundary = report.get("boundary")
    if not isinstance(boundary, dict) or any(key not in boundary for key in BINDINGS):
        raise ValueError("Inspection authority bindings are missing")
    if (not report.get("database") or not isinstance(report.get("database_identity"), dict)
            or type(report.get("complete")) is not bool):
        raise ValueError("Inspection database identity or scope is missing")
    return report, hashlib.sha256(content).hexdigest()


def compare(inspection_path, *, max_units=10000, page_size=100, max_seconds=5):
    if not (1 <= max_units <= 100000 and 1 <= page_size <= 500 and 0 < max_seconds <= 30):
        raise ValueError("Limits: units 1..100000, page 1..500, seconds (0,30]")
    started = time.monotonic()
    deadline = started + max_seconds
    source, digest = load_inspection(inspection_path)
    path = Path(source["database"]).resolve(strict=True)
    report = {
        "schema": "jobagg-pending-comparison-1", "started_at_utc": inventory.utc_now(),
        "inspection_file": str(Path(inspection_path).resolve()), "inspection_sha256": digest,
        "database": str(path), "input_inventory_complete": source["complete"],
        "inspection_started_at_utc": source.get("started_at_utc"),
        "inspection_finished_at_utc": source.get("finished_at_utc"),
        "input_observed_units": len(source["units"]), "compared_units": 0,
        "scope": "saved Step 1 units only; current queue state, not destination content",
        "complete": False, "publication_verified": False, "acknowledgement_authorized": False,
        "global_backlog_empty_proven": False,
        "limits": {"max_units": max_units, "page_size": page_size, "max_seconds": max_seconds},
        "comparisons": [],
    }
    conn = None
    counts = Counter()
    try:
        if inventory.file_identity(path) != source["database_identity"]:
            raise ValueError("Worker database file identity differs from Step 1")
        conn = inventory.open_reader(path, deadline)
        report["reader_mode"] = "private_filesystem_clone"
        initial = None
        while True:
            if time.monotonic() >= deadline:
                report["stop_reason"] = "time_limit"
                break
            conn.execute("BEGIN")
            current = inventory.boundary(conn)
            if initial is None:
                initial = current
                report["boundary"] = initial
                if any(current[key] != source["boundary"][key] for key in BINDINGS):
                    raise ValueError("Authority epoch/configuration/contract changed since Step 1")
                if not current["bootstrap_complete"]:
                    raise ValueError("Publication bootstrap is incomplete")
            if current != initial or not inventory.reader_unchanged(conn):
                conn.rollback()
                report["stop_reason"] = "database_changed"
                break
            offset = report["compared_units"]
            rows = source["units"][offset:min(offset + page_size, max_units)]
            page = []
            for old in rows:
                new = conn.execute(
                    "SELECT version,source_id,kind,disposition,reason "
                    "FROM jobagg_dirty_current WHERE unit=?", (old["unit"],)
                ).fetchone()
                category = "missing_requires_review"
                if new:
                    if (new[1], new[2]) != (old["source_id"], old["kind"]):
                        category = "identity_changed_requires_review"
                    elif new[0] != old["version"]:
                        category = "version_changed_reinspect"
                    else:
                        category = {
                            "acknowledged": "already_acknowledged_in_native_queue",
                            "held": "held_requires_review",
                            "pending": "pending_requires_publication_or_verified_readback",
                        }.get(new[3], "unknown_disposition_requires_review")
                page.append({"unit": old["unit"], "inspected_version": old["version"],
                             "current_version": new[0] if new else None,
                             "source_id": old["source_id"], "kind": old["kind"],
                             "current_source_id": new[1] if new else None,
                             "current_kind": new[2] if new else None,
                             "current_disposition": new[3] if new else None,
                             "current_reason": new[4] if new else None, "category": category})
            conn.rollback()
            report["comparisons"].extend(page)
            report["compared_units"] += len(page)
            counts.update(row["category"] for row in page)
            if (inventory.boundary(conn) != initial or not inventory.reader_unchanged(conn)
                    or inventory.file_identity(path) != source["database_identity"]):
                report["stop_reason"] = "database_changed"
                break
            if time.monotonic() >= deadline:
                report["stop_reason"] = "time_limit"
                break
            if report["compared_units"] == len(source["units"]):
                report.update(complete=True, stop_reason="exhausted_input")
                break
            if report["compared_units"] >= max_units:
                report["stop_reason"] = "unit_limit"
                break
            time.sleep(min(0.025, max(0, deadline - time.monotonic())))
    except sqlite3.Error as exc:
        report.update(stop_reason="time_limit" if time.monotonic() >= deadline
                      else "database_error", error=str(exc))
    except ValueError as exc:
        report.update(stop_reason="binding_error", error=str(exc))
    finally:
        if conn is not None:
            conn.close()
    report["counts"] = dict(sorted(counts.items()))
    report["finished_at_utc"] = inventory.utc_now()
    report["elapsed_seconds"] = round(time.monotonic() - started, 6)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inspection", type=Path, required=True, help="Step 1 saved report.json")
    parser.add_argument("--max-units", type=int, default=10000)
    parser.add_argument("--page-size", type=int, default=100)
    parser.add_argument("--max-seconds", type=float, default=5)
    args = parser.parse_args(argv)
    try:
        report = compare(args.inspection, max_units=args.max_units,
                         page_size=args.page_size, max_seconds=args.max_seconds)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        report = {"schema": "jobagg-pending-comparison-1", "complete": False,
                  "finished_at_utc": inventory.utc_now(),
                  "stop_reason": "input_error", "error": str(exc)}
    try:
        inventory.print_saved_report(report, "jobagg-pending-comparison-")
    except OSError as exc:
        print(json.dumps({"complete": False, "stop_reason": "report_write_error",
                          "error": str(exc)}), flush=True)
        return 2
    return 0 if report["complete"] else 2


if __name__ == "__main__":
    sys.exit(main())
