#!/usr/bin/env python3
"""Bounded publication benchmark on a local SQLite backup, never the live DB.

Run with the candidate JobAgg package on PYTHONPATH. Immutable accepted artifacts
are read from their existing paths. The benchmark changes only its output folder.
"""

from __future__ import annotations

import argparse
from collections import Counter
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import time

from jobagg import accepted_detail_lineage as lineage
from jobagg.publication_projection import build_projection, projection_diagnostics


def serialized(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def sha(value):
    return hashlib.sha256(serialized(value).encode()).hexdigest()


@contextmanager
def artifact_profile():
    original = lineage._sha
    stats = {"hash_calls": 0, "hashed_bytes": 0, "hash_seconds": 0.0}

    def measured(path, *, deadline_at=None):
        started = time.monotonic()
        try:
            return original(path, deadline_at=deadline_at)
        finally:
            stats["hash_calls"] += 1
            stats["hashed_bytes"] += path.stat().st_size if path.is_file() else 0
            stats["hash_seconds"] += time.monotonic() - started

    lineage._sha = measured
    try:
        yield stats
    finally:
        lineage._sha = original


def write_report(folder, report):
    (folder / "benchmark.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")


def summarize(stage, report, folder):
    write_report(folder, report)
    print(json.dumps({"stage": stage, "result": report[stage]}, sort_keys=True), flush=True)


def plans(conn, samples):
    return [
        {
            "source_id": row["source_id"],
            "external_id": row["external_id"],
            "plan": [list(item) for item in conn.execute(
                "EXPLAIN QUERY PLAN " + lineage.DETAIL_ATTEMPTS_QUERY,
                (row["source_id"], str(row["external_id"])),
            )],
        }
        for row in samples[:3]
    ]


def sample_phase(conn, samples, *, expected_rows=None, expected_lineage=None, seconds=180):
    deadline = time.monotonic() + seconds
    conn.set_progress_handler(lambda: int(time.monotonic() >= deadline), 1000)
    query_rows, matching = [], []
    query_seconds = 0.0
    query_counts = []
    try:
        for row in samples:
            started = time.monotonic()
            values = [tuple(item) for item in conn.execute(
                lineage.DETAIL_ATTEMPTS_QUERY,
                (row["source_id"], str(row["external_id"])),
            )]
            query_seconds += time.monotonic() - started
            query_rows.append(values)
            query_counts.append(len(values))
        matching_started = time.monotonic()
        with artifact_profile() as artifacts:
            for row in samples:
                result = lineage.matching_detail_attempts(
                    conn, row, json.loads(row["accepted_proof"]), deadline_at=deadline,
                )
                matching.append(serialized(result))
        matching_seconds = time.monotonic() - matching_started
    finally:
        conn.set_progress_handler(None, 0)
    if expected_rows is not None:
        assert query_rows == expected_rows, "Ordered SQL rows changed after indexing"
    if expected_lineage is not None:
        assert matching == expected_lineage, "Accepted lineage results changed after indexing"
    return {
        "identities": len(samples),
        "source_counts": dict(sorted(Counter(row["source_id"] for row in samples).items())),
        "sql_seconds": query_seconds,
        "sql_rows": sum(query_counts),
        "matching_detail_attempts_seconds": matching_seconds,
        "matching_identities": sum(value != "[]" for value in matching),
        "artifacts": artifacts,
        "ordered_sql_rows_sha256": sha(query_rows),
        "serialized_matching_results_sha256": sha(matching),
        "query_plans": plans(conn, samples),
        "equivalence_checked": expected_rows is not None and expected_lineage is not None,
    }, query_rows, matching


def full_projection(conn, target, seconds):
    started = time.monotonic()
    destination = sqlite3.connect(target)
    try:
        with artifact_profile() as artifacts:
            try:
                result = build_projection(conn, destination, deadline_at=started + seconds)
            except TimeoutError as error:
                return {
                    "completed": False,
                    "seconds": time.monotonic() - started,
                    "deadline_seconds": seconds,
                    "error": str(error),
                    "artifacts": artifacts,
                }
        assert destination.execute("PRAGMA quick_check").fetchall() == [("ok",)]
        diagnostics = projection_diagnostics(destination)
        return {
            "completed": True,
            "seconds": time.monotonic() - started,
            "deadline_seconds": seconds,
            "database_bytes": target.stat().st_size,
            "manifest_sha256": result["sha256"],
            "manifest": result["manifest"],
            "timings": result.get("timings", {}),
            "diagnostics": diagnostics,
            "artifacts": artifacts,
            "sqlite_quick_check": "ok",
            "row_readback_verified": True,
        }
    finally:
        destination.close()


def markdown(report):
    before, after = report["sample_baseline"], report["sample_indexed"]
    baseline, indexed = report["projection_baseline"], report["projection_indexed"]
    lines = [
        "# Publication lineage benchmark", "",
        "All database mutations occurred on a local SQLite online backup. The live",
        "worker database was opened with mode=ro and query_only=ON. Accepted artifact",
        "files were read at their immutable existing paths. The backup pins one database",
        "state while the scheduled worker may continue operating.", "",
        f"- Source: `{report['source']}`",
        f"- Backup completed: `{report['backup']['finished_at_utc']}`",
        f"- Backup elapsed: {report['backup']['seconds']:.3f} seconds",
        f"- Snapshot size: {report['backup']['database_bytes']:,} bytes",
        f"- Observed jobs: {report['selection']['observed_jobs']:,}", "",
        "## Representative lookups", "",
        f"Deterministic evenly spaced sample of {before['identities']} observed identities.",
        "Exact ordered SQL rows and exact serialized matching_detail_attempts results",
        "were asserted equal before and after the migration.", "",
        "| Measurement | Before | Indexed |",
        "|---|---:|---:|",
        f"| SQL seconds | {before['sql_seconds']:.6f} | {after['sql_seconds']:.6f} |",
        f"| Matching helper seconds | {before['matching_detail_attempts_seconds']:.6f} | {after['matching_detail_attempts_seconds']:.6f} |",
        f"| Artifact SHA-256 seconds | {before['artifacts']['hash_seconds']:.6f} | {after['artifacts']['hash_seconds']:.6f} |",
        f"| SQL result rows | {before['sql_rows']:,} | {after['sql_rows']:,} |", "",
        f"Index construction: {report['index_migration']['seconds']:.3f} seconds;",
        f"database file growth: {report['index_migration']['database_bytes_added']:,} bytes.",
        "Free pages may be reused, so file growth and index page occupancy differ.",
        f"Index page bytes: `{report['index_migration'].get('index_page_bytes')}`.", "",
        "### Query plan before", "", "```text",
        *[row[-1] for row in before["query_plans"][0]["plan"]], "```", "",
        "### Query plan indexed", "", "```text",
        *[row[-1] for row in after["query_plans"][0]["plan"]], "```", "",
        "## Complete projection", "",
    ]
    for name, result in (("Before", baseline), ("Indexed", indexed)):
        lines.append(f"- {name}: {'completed' if result['completed'] else 'did not complete'} in {result['seconds']:.3f} seconds (deadline {result['deadline_seconds']} seconds).")
        if not result["completed"]:
            lines.append(f"  Result: `{result['error']}`")
        else:
            lines.append(f"  Manifest/readback and SQLite quick_check passed; projection size {result['database_bytes']:,} bytes.")
            lines.append(f"  Manifest SHA-256: `{result['manifest_sha256']}`")
    lines += ["", f"Complete manifest equivalence: {report['full_manifest_equivalence']}.", "",
        "Timing instrumentation separates accepted-lineage resolution, artifact hashing,",
        "and each table's row copy, hash/readback and index creation. Artifact hashing is",
        "a component of accepted-lineage resolution, so these times must not be added.", "",
        "This is one isolated before/after run on the same snapshot, not a controlled",
        "cold-cache benchmark. The indexed run benefits from earlier filesystem reads.",
        "SQL query plans and exact output equivalence establish the index mechanism;",
        "elapsed times are specific to this snapshot and machine. Production catch-up",
        "and source freshness are not certified by a projection benchmark.", "",
        "The complete timings, table row counts, query plans, digests and source sample",
        "distribution are in `benchmark.json`.", ""]
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--samples", type=int, default=250)
    parser.add_argument("--backup-seconds", type=int, default=120)
    parser.add_argument("--sample-seconds", type=int, default=180)
    parser.add_argument("--projection-seconds", type=int, default=360)
    args = parser.parse_args()
    for name in ("samples", "backup_seconds", "sample_seconds", "projection_seconds"):
        if getattr(args, name) <= 0:
            parser.error(name.replace("_", "-") + " must be positive")
    source = args.source.resolve()
    folder = args.output.resolve()
    if folder == source.parent or source.is_relative_to(folder):
        parser.error("Output must be a separate local directory")
    if folder.exists():
        parser.error("Output must be a new directory that does not already exist")
    folder.mkdir(parents=True, exist_ok=False)
    snapshot = folder / "source.sqlite3"
    report = {
        "source": str(source),
        "started_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "query": lineage.DETAIL_ATTEMPTS_QUERY,
        "runtime": {"python": sys.version, "sqlite": sqlite3.sqlite_version},
        "limitations": ["single run", "warm filesystem cache after baseline", "no production writes", "no freshness certification"],
    }
    live = sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)
    live.execute("PRAGMA query_only=ON")
    live.execute("BEGIN")
    live.execute("SELECT count(*) FROM sqlite_master").fetchone()
    conn = sqlite3.connect(snapshot)
    started = time.monotonic()
    try:
        def progress(status, remaining, total):
            if time.monotonic() - started >= args.backup_seconds:
                raise TimeoutError("Read-only online backup deadline exhausted")
        live.backup(conn, pages=256, progress=progress, sleep=0.01)
    finally:
        live.rollback()
        live.close()
    conn.execute("PRAGMA journal_mode=DELETE")
    assert conn.execute("PRAGMA quick_check").fetchall() == [("ok",)]
    report["backup"] = {
        "seconds": time.monotonic() - started,
        "finished_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "database_bytes": snapshot.stat().st_size,
        "sqlite_quick_check": "ok",
    }
    summarize("backup", report, folder)
    index_names = ("idx_remediation_tasks_detail_identity", "idx_remediation_attempts_detail_lineage")
    report["initial_candidate_indexes"] = [dict(zip(("name", "sql"), row)) for row in conn.execute(
        "SELECT name,sql FROM sqlite_master WHERE type='index' AND name IN (?,?) ORDER BY name", index_names,
    )]
    # Existing candidate indexes are removed only from the isolated backup to form
    # an unindexed baseline. Their presence in the source is explicitly recorded.
    for name in index_names:
        conn.execute('DROP INDEX IF EXISTS "' + name + '"')
    conn.commit()
    cursor = conn.cursor()
    cursor.row_factory = sqlite3.Row
    observed = list(cursor.execute(
        "SELECT j.*,o.proof AS accepted_proof FROM jobs j JOIN remediation_observations o ON o.job_key=j.job_key ORDER BY j.job_key"
    ))
    valid = []
    for row in observed:
        try:
            json.loads(row["accepted_proof"])
        except (TypeError, ValueError):
            continue
        valid.append(row)
    if not valid:
        raise ValueError("No observed identities with parseable proof")
    count = min(args.samples, len(valid))
    samples = [valid[(index * len(valid)) // count] for index in range(count)]
    report["selection"] = {"observed_jobs": len(observed), "parseable_proof_jobs": len(valid), "samples": len(samples), "method": "evenly spaced over job_key ORDER BY"}
    report["sample_baseline"], expected_rows, expected_lineage = sample_phase(conn, samples, seconds=args.sample_seconds)
    summarize("sample_baseline", report, folder)
    report["projection_baseline"] = full_projection(conn, folder / "projection-baseline.sqlite3", args.projection_seconds)
    summarize("projection_baseline", report, folder)
    before_bytes = snapshot.stat().st_size
    started = time.monotonic()
    lineage.ensure_detail_lineage_indexes(conn)
    conn.commit()
    report["index_migration"] = {
        "seconds": time.monotonic() - started,
        "database_bytes_before": before_bytes,
        "database_bytes_after": snapshot.stat().st_size,
        "database_bytes_added": snapshot.stat().st_size - before_bytes,
        "indexes": [dict(zip(("name", "sql"), row)) for row in conn.execute(
            "SELECT name,sql FROM sqlite_master WHERE type='index' AND name IN (?,?) ORDER BY name", index_names,
        )],
    }
    try:
        report["index_migration"]["index_page_bytes"] = {
            name: page_bytes for name, page_bytes in conn.execute(
                "SELECT name,sum(pgsize) FROM dbstat WHERE name IN (?,?) GROUP BY name ORDER BY name", index_names,
            )
        }
    except sqlite3.OperationalError:
        report["index_migration"]["index_page_bytes"] = None
    summarize("index_migration", report, folder)
    report["sample_indexed"], _, _ = sample_phase(conn, samples, expected_rows=expected_rows, expected_lineage=expected_lineage, seconds=args.sample_seconds)
    summarize("sample_indexed", report, folder)
    report["projection_indexed"] = full_projection(conn, folder / "projection-indexed.sqlite3", args.projection_seconds)
    summarize("projection_indexed", report, folder)
    if report["projection_baseline"]["completed"] and report["projection_indexed"]["completed"]:
        assert report["projection_baseline"]["manifest"] == report["projection_indexed"]["manifest"], "Complete projection manifest changed"
        report["full_manifest_equivalence"] = "exact manifests equal"
    else:
        report["full_manifest_equivalence"] = "not established: baseline or indexed full projection did not complete"
    conn.close()
    report["finished_at_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    write_report(folder, report)
    (folder / "benchmark.md").write_text(markdown(report))
    print(json.dumps({"stage": "complete", "report": str(folder / "benchmark.md")}), flush=True)


if __name__ == "__main__":
    main()
