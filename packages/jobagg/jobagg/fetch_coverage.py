"""Read-only source census: current listings, fresh full details and live readback.

This reports observed coverage and exact persistence separately from claims about
provider scope or extraction fidelity. It never turns successful transport into
a promise that the public website contains no additional jobs or text.
"""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
import sqlite3
import time

from jobagg.pipelines.sync_source import load_sources
from jobagg.pipelines.bundles import source_output_slug
from jobagg.pipelines.live_publication import GATE_NAME, _resolve
from jobagg.pipelines.document_readback import _blob_matches, document_readback

PUBLIC_METADATA = (
    "title",
    "source_url",
    "apply_url",
    "location",
    "department",
    "employment_type",
    "posted_at",
    "closes_at",
    "closes_at_local",
    "closes_tz",
)


def digest(text):
    return hashlib.sha256((text or "").encode()).hexdigest()


def connect(path):
    conn = sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    conn.execute("BEGIN")
    return conn


def census(registry, worker_database, live_database=None, now=None, *, source_output_dir=None):
    now = time.time() if now is None else now
    sources = load_sources(registry)
    source_output_dir = (
        Path(source_output_dir)
        if source_output_dir
        else (Path(live_database).parent if live_database else None)
    )
    gate_path = Path(live_database).parent / GATE_NAME if live_database else None
    gate_before = json.loads(gate_path.read_text()) if gate_path and gate_path.is_file() else None
    report = {
        "schema_version": 2,
        "generated_at": datetime.fromtimestamp(now, UTC).isoformat(),
        "worker_database": str(worker_database),
        "live_database": str(live_database) if live_database else None,
        "source_output_dir": str(source_output_dir) if source_output_dir else None,
        "enabled_sources": [],
        "disabled_sources": [],
        "completeness_certified": False,
        "method": "Census of every currently enumerated job ID; exact SHA-256 text and blob readback. This is not a sample.",
        "limitations": [
            "Enumeration certificates cover the configured endpoint and filters only",
            "Public-text and document extraction fidelity require source contracts and visual checks",
            "A matching database hash proves preservation of fetched text, not absence of unfetched text",
            "Database snapshots are individually consistent; use the shared owner lock for a coordinated cross-database census",
            "API downloads and exported files require separate readback; this command performs no network calls",
        ],
        "metric_definitions": {
            "current_live_document_blobs_match": "Task count whose captured binary hash exists anywhere in consolidated storage; not job association or completeness proof",
            "current_document_blobs_match": "Task count whose original captured binary hash matches worker storage",
            "current_documents_pending_or_blocked": "Compatibility metric: all current required document tasks not done, including pending, blocked and interrupted",
            "current_live_documents_verified": "Current done tasks with matching consolidated binary, exact job association, extracted text, parent description hash, manifest metadata and jobs.raw_json association",
            "current_source_documents_verified": "Same checks in the canonical source database",
            "document_content_hash_counts": "Distinct binary hashes, deduplicated across tasks and across sources in report totals",
        },
    }
    worker = connect(worker_database)
    live = connect(live_database) if live_database else None
    all_hashes = {
        key: set()
        for key in (
            "current_done",
            "worker_matching",
            "live_blob_matching",
            "source_blob_matching",
            "live_verified",
            "source_verified",
        )
    }
    source_connections = []
    live_blob_cache = {}
    worker_blob_cache = {}
    try:
        for source in sources:
            if not source.enabled:
                report["disabled_sources"].append(
                    {
                        "source_id": source.id,
                        "name": source.name,
                        "reason": source.extra.get("coverage_note") or "Disabled in registry",
                    }
                )
                continue
            state = worker.execute(
                "SELECT * FROM remediation_sources WHERE source_id=?", (source.id,)
            ).fetchone()
            ids = json.loads(state["listing_ids"] or "[]") if state else []
            source_path = (
                source_output_dir / f"{source_output_slug(source)}_jobs.sqlite3"
                if source_output_dir
                else None
            )
            source_live, source_error = None, None
            if source_path:
                try:
                    source_live = connect(source_path)
                    source_connections.append(source_live)
                except (OSError, sqlite3.DatabaseError) as exc:
                    source_error = type(exc).__name__ + ": " + str(exc)
            source_blob_cache = {}
            rows = {
                row["job_key"]: row
                for row in worker.execute(
                    "SELECT job_key,source_id,external_id,description,"
                    + ",".join(PUBLIC_METADATA)
                    + " FROM jobs WHERE source_id=?",
                    (source.id,),
                )
            }
            observations = {
                row["job_key"]: row
                for row in worker.execute(
                    "SELECT * FROM remediation_observations WHERE source_id=?", (source.id,)
                )
            }
            counts = {
                "listed_current": len(ids),
                "worker_rows": 0,
                "fresh_detail_hash_match": 0,
                "live_text_match": 0,
                "live_public_fields_match": 0,
                "current_document_tasks": 0,
                "current_document_done_tasks": 0,
                "current_documents_pending_or_blocked": 0,
                "current_document_blobs_match": 0,
                "current_live_document_blobs_match": 0,
            }
            for destination in ("live", "source"):
                for metric in (
                    "document_associations_match",
                    "document_extracted_text_match",
                    "document_parent_bindings_match",
                    "document_metadata_match",
                    "document_raw_associations_match",
                    "documents_verified",
                ):
                    counts[f"current_{destination}_{metric}"] = 0
            counts["current_source_document_blobs_match"] = 0
            counts["current_document_manifest_matches"] = 0
            hashes = {key: set() for key in all_hashes}
            document_reconciliation = []
            gaps = []
            for key in ids:
                job, observed = rows.get(key), observations.get(key)
                if job is None:
                    gaps.append({"job_key": key, "gap": "listing_without_worker_row"})
                    continue
                counts["worker_rows"] += 1
                fresh = observed is not None and observed["checked_at"] >= now - 86400
                exact = (
                    observed is not None
                    and digest(job["description"])
                    == observed["database_description_sha256"]
                    == observed["source_description_sha256"]
                )
                if fresh and exact:
                    counts["fresh_detail_hash_match"] += 1
                else:
                    gaps.append(
                        {
                            "job_key": key,
                            "gap": "full_detail_missing_stale_or_changed",
                            "fresh": fresh,
                            "exact": exact,
                        }
                    )
                if live:
                    try:
                        published = _resolve(live, dict(job))
                    except (ValueError, sqlite3.DatabaseError) as exc:
                        published = None
                        gaps.append(
                            {
                                "job_key": key,
                                "gap": "live_identity_resolution_failed",
                                "reason": str(exc),
                            }
                        )
                    if (
                        published
                        and job["description"]
                        and digest(published["description"]) == digest(job["description"])
                    ):
                        counts["live_text_match"] += 1
                        if all(published[field] == job[field] for field in PUBLIC_METADATA):
                            counts["live_public_fields_match"] += 1
                    elif observed:
                        gaps.append({"job_key": key, "gap": "live_text_missing_or_different"})
            attachments_in_scope = source.extra.get("fetch_attachments", True) is not False
            current_hashes = {
                key: digest(row["description"]) for key, row in rows.items() if key in set(ids)
            }
            for task in worker.execute(
                "SELECT * FROM remediation_tasks WHERE source_id=? AND kind='document'",
                (source.id,),
            ):
                if not attachments_in_scope:
                    continue
                payload = json.loads(task["payload"])
                key = payload["job_key"]
                if (
                    payload.get("parent_description_sha256") != current_hashes.get(key)
                    or task["status"] == "not_required"
                ):
                    continue
                counts["current_document_tasks"] += 1
                if task["status"] != "done":
                    counts["current_documents_pending_or_blocked"] += 1
                    gaps.append(
                        {
                            "job_key": key,
                            "gap": "current_document_unfinished",
                            "status": task["status"],
                            "task_id": task["task_id"],
                        }
                    )
                    continue
                counts["current_document_done_tasks"] += 1
                document = worker.execute(
                    "SELECT * FROM remediation_documents WHERE task_id=?",
                    (task["task_id"],),
                ).fetchone()
                item = {
                    "task_id": task["task_id"],
                    "source_id": source.id,
                    "job_key": key,
                    "url": payload.get("url"),
                    "worker_manifest_match": False,
                    "worker_blob_hash_match": False,
                }
                document_reconciliation.append(item)
                if document is None:
                    gaps.append({**item, "gap": "completed_document_record_missing"})
                    continue
                try:
                    manifest = json.loads(document["manifest"])
                    content_sha = document["content_sha256"]
                    item["content_sha256"] = content_sha
                    hashes["current_done"].add(content_sha)
                    manifest_ok = (
                        document["source_id"] == manifest.get("source_id") == source.id
                        and document["job_key"] == manifest.get("job_key") == key
                        and document["url"] == manifest.get("url") == payload.get("url")
                        and manifest.get("content_sha256") == content_sha
                        and manifest.get("parent_description_sha256") == current_hashes[key]
                        and digest(manifest.get("extracted_text", "")) == document["text_sha256"]
                    )
                    item["worker_manifest_match"] = manifest_ok
                    counts["current_document_manifest_matches"] += manifest_ok
                    worker_ok = _blob_matches(worker, content_sha, worker_blob_cache)
                    item["worker_blob_hash_match"] = worker_ok
                    counts["current_document_blobs_match"] += worker_ok
                    if worker_ok:
                        hashes["worker_matching"].add(content_sha)
                    for name, conn, cache in (
                        ("live", live, live_blob_cache),
                        ("source", source_live, source_blob_cache),
                    ):
                        result = document_readback(conn, rows[key], manifest, cache)
                        item[name] = result
                        checks = result["checks"]
                        # Invalid worker evidence can never count as publication verification.
                        checks["verified"] = checks["verified"] and manifest_ok and worker_ok
                        mapping = {
                            "blob_hash_match": "document_blobs_match",
                            "association_match": "document_associations_match",
                            "extracted_text_match": "document_extracted_text_match",
                            "parent_binding_match": "document_parent_bindings_match",
                            "metadata_match": "document_metadata_match",
                            "raw_association_match": "document_raw_associations_match",
                            "verified": "documents_verified",
                        }
                        for check, metric in mapping.items():
                            counts[f"current_{name}_{metric}"] += checks[check]
                        if checks["blob_hash_match"]:
                            hashes[f"{name}_blob_matching"].add(content_sha)
                        if checks["verified"]:
                            hashes[f"{name}_verified"].add(content_sha)
                    if (
                        not manifest_ok
                        or not worker_ok
                        or (live is not None and not item["live"]["checks"]["verified"])
                        or (source_path is not None and not item["source"]["checks"]["verified"])
                    ):
                        gaps.append(
                            {
                                "job_key": key,
                                "task_id": task["task_id"],
                                "gap": "current_document_publication_unverified",
                                "content_sha256": content_sha,
                            }
                        )
                except (KeyError, TypeError, ValueError, sqlite3.DatabaseError) as exc:
                    item["error"] = type(exc).__name__ + ": " + str(exc)
                    gaps.append(
                        {
                            "job_key": key,
                            "task_id": task["task_id"],
                            "gap": "completed_document_evidence_invalid",
                            "reason": item["error"],
                        }
                    )
            proof = json.loads(state["listing_proof"] or "{}") if state else {}
            report["enabled_sources"].append(
                {
                    "source_id": source.id,
                    "name": source.name,
                    "attachments_in_scope": attachments_in_scope,
                    "retained_document_task_counts": [dict(row) for row in worker.execute(
                        "SELECT status,count(*) AS count FROM remediation_tasks WHERE source_id=? AND kind='document' GROUP BY status", (source.id,))],
                    "listing_observed_at": state["last_list_at"] if state else None,
                    "listing_fresh_3h": bool(
                        state and state["last_list_at"] and state["last_list_at"] >= now - 10800
                    ),
                    "enumeration": proof,
                    "counts": counts,
                    "source_live_database": str(source_path) if source_path else None,
                    "source_live_database_available": source_live is not None,
                    "source_live_database_error": source_error,
                    "document_content_hash_counts": {
                        key: len(value) for key, value in hashes.items()
                    },
                    "document_reconciliation": document_reconciliation,
                    "gaps": gaps,
                    "task_counts": [
                        dict(row)
                        for row in worker.execute(
                            "SELECT kind,status,count(*) AS n FROM remediation_tasks WHERE source_id=? GROUP BY kind,status",
                            (source.id,),
                        )
                    ],
                }
            )
            for metric in all_hashes:
                all_hashes[metric].update(hashes[metric])
        report["totals"] = (
            {
                metric: sum(s["counts"][metric] for s in report["enabled_sources"])
                for metric in report["enabled_sources"][0]["counts"]
            }
            if report["enabled_sources"]
            else {}
        )
        report["enabled_count"] = len(report["enabled_sources"])
        report["disabled_count"] = len(report["disabled_sources"])
        report["document_content_hash_counts"] = {
            key: len(value) for key, value in all_hashes.items()
        }
        gate_after = (
            json.loads(gate_path.read_text()) if gate_path and gate_path.is_file() else None
        )
        report["publication_generation"] = gate_before.get("generation_id") if gate_before else None
        report["publication_gate_complete"] = bool(
            gate_before and gate_before.get("state") == "complete"
        )
        report["publication_gate_unchanged_during_census"] = (
            gate_before == gate_after if gate_before is not None else None
        )
        return report
    finally:
        worker.close()
        if live:
            live.close()
        for conn in source_connections:
            conn.close()


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--registry", type=Path, required=True)
    p.add_argument("--worker-database", type=Path, required=True)
    p.add_argument("--live-database", type=Path)
    p.add_argument("--source-output-dir", type=Path)
    p.add_argument("--report", type=Path, required=True)
    a = p.parse_args(argv)
    report = census(
        a.registry, a.worker_database, a.live_database, source_output_dir=a.source_output_dir
    )
    a.report.parent.mkdir(parents=True, exist_ok=True)
    a.report.write_text(json.dumps(report, indent=2) + "\n")
    print(
        json.dumps(
            {
                "report": str(a.report),
                "enabled_count": report["enabled_count"],
                "disabled_count": report["disabled_count"],
                "totals": report["totals"],
                "completeness_certified": False,
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
