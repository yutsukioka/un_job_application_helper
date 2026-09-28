"""Small deterministic publication snapshots, excluding retained baseline bulk.

This schema is a publication read contract, not a copy of the worker. Missing
jobs, tasks and blobs remain missing so the publisher reports the same failures.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time

from jobagg.accepted_detail_lineage import matching_detail_attempts

VERSION = "publication-projection-2"
METADATA_TABLE = "jobagg_publication_projection"
LEGACY_FILTERS = {
    "jobs": "job_key IN (SELECT job_key FROM remediation_observations)",
    "remediation_observations": "all_rows",
    "remediation_documents": "all_rows",
    "remediation_tasks": "all_rows",
    "remediation_sources": "all_rows",
    "attachment_blobs": "content_sha256 IN (SELECT content_sha256 FROM remediation_documents)",
}
FILTERS = {
    **LEGACY_FILTERS,
    "remediation_attempts": "successful_detail_attempts_bound_to_current_observations_v1",
}


def _dump(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(",", ":"))


def _sha(value):
    return hashlib.sha256(_dump(value).encode()).hexdigest()


def _check(deadline):
    if deadline is not None and time.monotonic() >= deadline:
        raise TimeoutError("Publication projection deadline exhausted")


def _exists(conn, table):
    return conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()


def _row_digest(row):
    return _sha(
        [
            {"sqlite_blob_sha256": hashlib.sha256(value).hexdigest(), "bytes": len(value)}
            if isinstance(value, bytes)
            else value
            for value in row
        ]
    )


def validate_manifest(manifest):
    expected_filters = (
        LEGACY_FILTERS
        if isinstance(manifest, dict) and manifest.get("version") == "publication-projection-1"
        else FILTERS
    )
    if (
        not isinstance(manifest, dict)
        or manifest.get("version") not in {VERSION, "publication-projection-1"}
        or manifest.get("filters") != expected_filters
    ):
        raise ValueError("Unknown publication projection schema or filters")
    if set(manifest.get("tables", {})) != set(expected_filters):
        raise ValueError("Publication projection table manifest differs")
    diagnostics = manifest.get("original_diagnostics", {})
    if any(
        type(diagnostics.get(key)) is not int or diagnostics[key] < 0
        for key in ("worker_jobs", "jobs_without_observations", "baseline_archived_jobs")
    ):
        raise ValueError("Publication projection original diagnostics are missing")
    for table, record in manifest["tables"].items():
        if record.get("present") is not True:
            if record != {"present": False}:
                raise ValueError("Invalid missing-table publication projection record")
            continue
        if (
            type(record.get("rows")) is not int
            or record["rows"] < 0
            or type(record.get("source_rows")) is not int
            or record["source_rows"] < record["rows"]
            or not isinstance(record.get("columns"), list)
            or not isinstance(record.get("sha256"), str)
            or len(record["sha256"]) != 64
        ):
            raise ValueError("Invalid publication projection table binding: " + table)
    return manifest


def _accepted_attempt_ids(origin, deadline_at):
    """Retain accepted evidence, not every historical refresh attempt."""
    if not _exists(origin, "remediation_attempts") or not _exists(origin, "remediation_tasks"):
        return set()
    cursor = origin.cursor()
    cursor.row_factory = sqlite3.Row
    identifiers = set()
    for row in cursor.execute(
        "SELECT j.*,o.proof AS accepted_proof FROM jobs j "
        "JOIN remediation_observations o ON o.job_key=j.job_key ORDER BY j.job_key"
    ):
        _check(deadline_at)
        try:
            proof = json.loads(row["accepted_proof"])
        except (TypeError, ValueError):
            continue  # The unchanged bad observation is still copied and rejected.
        for binding in matching_detail_attempts(origin, row, proof):
            identifiers.add(binding["attempt"]["attempt_id"])
    return identifiers


def build_projection(origin, destination, *, deadline_at=None):
    """Copy only declared read dependencies within a single pinned source TX."""
    origin.execute("BEGIN")
    origin.set_progress_handler(
        lambda: int(deadline_at is not None and time.monotonic() >= deadline_at), 1000
    )
    destination.execute("PRAGMA journal_mode=DELETE")
    destination.execute("PRAGMA synchronous=FULL")
    destination.execute("BEGIN")
    manifest = {
        "version": VERSION,
        "filters": FILTERS,
        "tables": {},
        "original_diagnostics": {},
        "freshness_or_completeness_certified": False,
    }
    try:
        if not _exists(origin, "jobs") or not _exists(origin, "remediation_observations"):
            raise ValueError("Publication projection requires jobs and observations schema")
        manifest["original_diagnostics"] = {
            "worker_jobs": origin.execute("SELECT count(*) FROM jobs").fetchone()[0],
            "jobs_without_observations": origin.execute(
                "SELECT count(*) FROM jobs j LEFT JOIN remediation_observations o ON o.job_key=j.job_key WHERE o.job_key IS NULL"
            ).fetchone()[0],
            "baseline_archived_jobs": origin.execute(
                "SELECT count(*) FROM baseline_inventory_jobs"
            ).fetchone()[0]
            if _exists(origin, "baseline_inventory_jobs")
            else 0,
        }
        accepted_attempt_ids = _accepted_attempt_ids(origin, deadline_at)
        for table, predicate in FILTERS.items():
            _check(deadline_at)
            schema = _exists(origin, table)
            if schema is None:
                manifest["tables"][table] = {"present": False}
                continue
            # Only explicit table DDL is copied: no triggers, application views,
            # unrelated tables, virtual tables or executable extension schemas.
            sql = schema[0]
            if not sql or not sql.lstrip().upper().startswith("CREATE TABLE"):
                raise ValueError("Unsupported publication table schema: " + table)
            destination.execute(sql)
            columns_info = list(origin.execute('PRAGMA table_info("' + table + '")'))
            columns = [column[1] for column in columns_info]
            quoted = ",".join('"' + name.replace('"', '""') + '"' for name in columns)
            primary = [
                column[1]
                for column in sorted(columns_info, key=lambda column: column[5])
                if column[5]
            ]
            order = primary or columns
            order_sql = ",".join('"' + name.replace('"', '""') + '"' for name in order)
            where = ""
            if predicate != "all_rows":
                if table == "remediation_attempts":
                    where = " WHERE kind='detail' AND status='done' AND finished_at IS NOT NULL"
                elif table == "attachment_blobs" and not _exists(origin, "remediation_documents"):
                    where = " WHERE 0"
                else:
                    where = " WHERE " + predicate
            query = "SELECT " + quoted + ' FROM "' + table + '"' + where + " ORDER BY " + order_sql
            digest, count = hashlib.sha256(), 0
            insert = (
                'INSERT INTO "'
                + table
                + '"('
                + quoted
                + ") VALUES("
                + ",".join("?" for _ in columns)
                + ")"
            )
            for row in origin.execute(query):
                _check(deadline_at)
                values = tuple(row)
                if (
                    table == "remediation_attempts"
                    and values[columns.index("attempt_id")] not in accepted_attempt_ids
                ):
                    continue
                destination.execute(insert, values)
                digest.update((_row_digest(values) + "\n").encode())
                count += 1
            actual_digest, actual_count = hashlib.sha256(), 0
            for row in destination.execute(
                "SELECT " + quoted + ' FROM "' + table + '" ORDER BY ' + order_sql
            ):
                _check(deadline_at)
                actual_digest.update((_row_digest(tuple(row)) + "\n").encode())
                actual_count += 1
            if actual_count != count or actual_digest.hexdigest() != digest.hexdigest():
                raise ValueError("Publication projection readback differs: " + table)
            manifest["tables"][table] = {
                "present": True,
                "schema_sha256": hashlib.sha256(sql.encode()).hexdigest(),
                "columns": columns,
                "rows": count,
                "source_rows": origin.execute('SELECT count(*) FROM "' + table + '"').fetchone()[0],
                "sha256": digest.hexdigest(),
            }
            for index in origin.execute(
                "SELECT sql FROM sqlite_master WHERE type='index' AND tbl_name=? AND sql IS NOT NULL ORDER BY name",
                (table,),
            ):
                destination.execute(index[0])
        validate_manifest(manifest)
        manifest_sha = _sha(manifest)
        destination.execute(
            "CREATE TABLE jobagg_publication_projection(id INTEGER PRIMARY KEY CHECK(id=1),manifest_json TEXT NOT NULL,manifest_sha256 TEXT NOT NULL)"
        )
        destination.execute(
            "INSERT INTO jobagg_publication_projection VALUES(1,?,?)",
            (_dump(manifest), manifest_sha),
        )
        _check(deadline_at)
        destination.commit()
        return {"manifest": manifest, "sha256": manifest_sha}
    except BaseException:
        destination.rollback()
        raise
    finally:
        origin.set_progress_handler(None, 0)
        origin.rollback()


def projection_diagnostics(conn):
    """Return original worker counts for a projected DB; full snapshots use None."""
    if not _exists(conn, METADATA_TABLE):
        return None
    values = conn.execute(
        "SELECT manifest_json,manifest_sha256 FROM jobagg_publication_projection ORDER BY id"
    ).fetchall()
    if len(values) != 1:
        raise ValueError("Publication projection metadata is missing or duplicated")
    manifest = validate_manifest(json.loads(values[0][0]))
    if _sha(manifest) != values[0][1]:
        raise ValueError("Publication projection metadata hash differs")
    for table, record in manifest["tables"].items():
        if bool(_exists(conn, table)) != record["present"]:
            raise ValueError("Publication projection table presence differs")
        if (
            record["present"]
            and conn.execute('SELECT count(*) FROM "' + table + '"').fetchone()[0] != record["rows"]
        ):
            raise ValueError("Publication projection table count differs")
    return {
        **manifest["original_diagnostics"],
        "projection_version": manifest["version"],
        "manifest_sha256": values[0][1],
    }
