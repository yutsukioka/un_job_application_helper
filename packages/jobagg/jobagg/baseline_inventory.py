"""Adopt retained live inventory without inventing fresh source observations.

Only the existing worker database is writable. Original live rows, associations
and verified bytes are archived separately; operational rows have no current-
membership or application-readiness certificate. Batches resume by job-key cursor.
"""

from __future__ import annotations

import argparse
from collections import Counter
from contextlib import closing, contextmanager
from datetime import UTC, datetime
import fcntl
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import stat
import time

from jobagg.detail_quality import DETAIL_QUALITY_COMPLETE, detail_quality_status
from jobagg.models import JobRecord
from jobagg.pipelines.sync_source import load_sources
from jobagg.publication_snapshot import _verify_owner, stable_file

VERSION = "live-baseline-inventory-1"
_TABLES = (
    "CREATE TABLE IF NOT EXISTS baseline_import_batches(batch_id TEXT PRIMARY KEY, imported_at TEXT NOT NULL, provenance_json TEXT NOT NULL, result_json TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS baseline_inventory_jobs(baseline_id TEXT PRIMARY KEY, batch_id TEXT NOT NULL, source_id TEXT NOT NULL, live_job_key TEXT NOT NULL, worker_job_key TEXT, row_sha256 TEXT NOT NULL, description_sha256 TEXT NOT NULL, row_json TEXT NOT NULL, aliases_json TEXT NOT NULL, disposition TEXT NOT NULL)",
    "CREATE INDEX IF NOT EXISTS baseline_inventory_source ON baseline_inventory_jobs(source_id,worker_job_key)",
    "CREATE TABLE IF NOT EXISTS baseline_inventory_links(worker_job_key TEXT PRIMARY KEY, baseline_id TEXT NOT NULL, source_id TEXT NOT NULL, external_id TEXT NOT NULL, description_sha256 TEXT NOT NULL, adopted_row_sha256 TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS baseline_inventory_beforeimages(worker_job_key TEXT NOT NULL,row_sha256 TEXT NOT NULL,row_json TEXT NOT NULL,PRIMARY KEY(worker_job_key,row_sha256))",
    "CREATE TABLE IF NOT EXISTS baseline_inventory_attachments(baseline_id TEXT NOT NULL,association_sha256 TEXT NOT NULL,row_json TEXT NOT NULL,content_sha256 TEXT,blob_status TEXT NOT NULL,evidence_json TEXT NOT NULL,PRIMARY KEY(baseline_id,association_sha256))",
    "CREATE TABLE IF NOT EXISTS baseline_inventory_blobs(content_sha256 TEXT PRIMARY KEY,media_type TEXT,size_bytes INTEGER NOT NULL,content BLOB NOT NULL)",
)
_UNCERTIFIED_COLUMNS = {
    "source_listed_current": 0,
    "trusted_current": 0,
    "application_ready": 0,
    "source_freshness_status": "baseline_unverified",
    "source_health_status": "baseline_unverified",
    "source_run_classification": "baseline_import",
    "source_publishability_classification": "baseline_unverified",
    "source_latest_observed_at": None,
    "canonical_job_key": None,
    "duplicate_of_job_key": None,
    "consolidation_status": None,
}
_IDENTITY = ("source_id", "external_id")


def _dump(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=True)


def _digest(value):
    return hashlib.sha256(_dump(value).encode()).hexdigest()


def _sha_text(value):
    return hashlib.sha256((value or "").encode()).hexdigest()


def _check(deadline):
    if time.monotonic() >= deadline:
        raise TimeoutError("Baseline batch deadline exhausted; transaction rolled back")


def _file(path):
    path = Path(path).absolute()
    value = path.lstat()
    if not stat.S_ISREG(value.st_mode):
        raise ValueError("Baseline input must be a regular file: " + str(path))
    return {
        "path": str(path),
        "device": value.st_dev,
        "inode": value.st_ino,
        "size": value.st_size,
        "mtime_ns": value.st_mtime_ns,
        "ctime_ns": value.st_ctime_ns,
    }


def _files(path):
    files = [_file(path)]
    wal = Path(str(path) + "-wal")
    if wal.exists() or wal.is_symlink():
        files.append(_file(wal))
    return files


def _gate(path):
    evidence = stable_file(str(path))
    value = json.loads(path.read_bytes())
    if value.get("state") != "complete" or not value.get("generation_id"):
        raise ValueError("Live publication gate is not a completed generation")
    if hashlib.sha256(path.read_bytes()).hexdigest() != evidence["sha256"]:
        raise ValueError("Live publication gate changed during reading")
    return {
        "path": str(path),
        "sha256": evidence["sha256"],
        "generation_id": value["generation_id"],
    }


def _connect(path, *, write=False):
    _file(path)
    conn = sqlite3.connect(
        Path(path).absolute().as_uri() + ("?mode=rw" if write else "?mode=ro"), uri=True, timeout=1
    )
    conn.row_factory = sqlite3.Row
    if not write:
        conn.execute("PRAGMA query_only=ON")
    return conn


def _exists(conn, name):
    return bool(
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
        ).fetchone()
    )


def _rows(conn, query, args=()):
    return [dict(row) for row in conn.execute(query, args)]


def _raw(row):
    value = json.loads(row.get("raw_json") or "{}")
    if not isinstance(value, dict):
        raise ValueError("Job raw_json is not an object")
    return value


def _target_key(row):
    return JobRecord(
        source_id=row["source_id"],
        org_id=row["source_id"],
        ats_family=row["ats_family"],
        title=row["title"],
        apply_url=row["apply_url"],
        external_id=str(row["external_id"]),
    ).identity_key()


def _current(worker, row, key):
    exact = worker.execute("SELECT * FROM jobs WHERE job_key=?", (key,)).fetchone()
    matches = _rows(
        worker,
        "SELECT * FROM jobs WHERE source_id=? AND external_id=?",
        (row["source_id"], row["external_id"]),
    )
    if len(matches) > 1 or (
        exact and any(str(exact[field]) != str(row[field]) for field in _IDENTITY)
    ):
        raise ValueError("Worker identity is ambiguous or conflicts with desired job key")
    if exact and matches and exact["job_key"] != matches[0]["job_key"]:
        raise ValueError("Worker exact and native identities disagree")
    return dict(exact) if exact else (matches[0] if matches else None)


def _action(worker, row, source, fill_stubs):
    if source is None:
        return "archive_unknown_source", None, None
    if not source.enabled:
        return "archive_disabled_source", None, None
    if not row.get("external_id") or not str(row["external_id"]).strip():
        return "archive_missing_native_identity", None, None
    key = _target_key(row)
    current = _current(worker, row, key)
    if current is None:
        return "insert_missing", key, None
    key = current["job_key"]
    if (
        _exists(worker, "remediation_observations")
        and worker.execute(
            "SELECT 1 FROM remediation_observations WHERE job_key=?", (key,)
        ).fetchone()
    ):
        return "preserve_worker_observation", key, current
    if current.get("description") == row.get("description"):
        if (current.get("description") or "").strip():
            marker = _raw(current).get("_jobagg_baseline_inventory", {})
            linked = (
                worker.execute(
                    "SELECT baseline_id,description_sha256 FROM baseline_inventory_links WHERE worker_job_key=?",
                    (key,),
                ).fetchone()
                if _exists(worker, "baseline_inventory_links")
                else None
            )
            if (
                not linked
                or not isinstance(marker, dict)
                or marker.get("baseline_id") != linked["baseline_id"]
                or linked["description_sha256"] != _sha_text(current["description"])
            ):
                return "adopt_matching_worker_body", key, current
        return "preserve_worker_same_body", key, current
    # Filling a stub is opt-in; short or different substantive text is not a stub.
    stub = (
        not (current.get("description") or "").strip()
        or (current.get("description") or "").strip() == (current.get("title") or "").strip()
    )
    urls = {str(current.get(field)) for field in ("source_url", "apply_url") if current.get(field)}
    url_bound = bool(
        urls & {str(row.get(field)) for field in ("source_url", "apply_url") if row.get(field)}
    )
    rich = (
        detail_quality_status(title=row["title"], description=row.get("description"), raw=_raw(row))
        == DETAIL_QUALITY_COMPLETE
    )
    if fill_stubs and stub and url_bound and rich:
        return "fill_empty_stub", key, current
    return "preserve_worker_content", key, current


def _operational(row, key, baseline_id, columns, current=None, *, marker_only=False):
    value = {field: item for field, item in row.items() if field in columns}
    value.update({field: item for field, item in _UNCERTIFIED_COLUMNS.items() if field in columns})
    value.update(job_key=key)
    raw = _raw(row)
    # Preserve original certifications in the archived live row, not as current
    # worker assertions. Negative evidence remains visible in the archive too.
    for field in ("_deterministic_fetch_observation", "attachments", "required_attachment_urls"):
        raw.pop(field, None)
    for field in ("_jobagg_main_text_verification", "_jobagg_attachment_verification"):
        if isinstance(raw.get(field), dict) and raw[field].get("complete") is not False:
            raw.pop(field, None)
    raw["_jobagg_baseline_inventory"] = {
        "version": VERSION,
        "baseline_id": baseline_id,
        "description_sha256": _sha_text(row.get("description")),
        "live_job_key": row["job_key"],
        "fresh_source_observation": False,
        "current_membership_certified": False,
        "completeness_certified": False,
        "original_attachment_associations_table": "baseline_inventory_attachments",
        "original_attachment_bytes_table": "baseline_inventory_blobs",
    }
    if current:
        # Keep the current listing/queue's metadata. Only proven empty prose is
        # filled, with complete original live metadata still in baseline tables.
        value = {
            **current,
            "description": row["description"],
            "raw_json": _dump(
                {**_raw(current), "_jobagg_baseline_inventory": raw["_jobagg_baseline_inventory"]}
            ),
        }
        if marker_only:
            return value
        if "application_ready" in columns:
            value["application_ready"] = 0
        value["normalized_hash"] = _digest(
            {
                "baseline_id": baseline_id,
                "prior_hash": current.get("normalized_hash"),
                "description": row["description"],
            }
        )
    else:
        value["raw_json"] = _dump(raw)
    return value


def retain_baseline_for_listing(conn, listing):
    """Worker hook: keep adopted details while recording fresh listing separately."""
    if not _exists(conn, "baseline_inventory_links"):
        return None
    key = listing.identity_key()
    link = conn.execute(
        "SELECT * FROM baseline_inventory_links WHERE worker_job_key=?", (key,)
    ).fetchone()
    if not link:
        return None
    current = conn.execute("SELECT * FROM jobs WHERE job_key=?", (key,)).fetchone()
    stored = conn.execute(
        "SELECT * FROM baseline_inventory_jobs WHERE baseline_id=?", (link["baseline_id"],)
    ).fetchone()
    if (
        not current
        or not stored
        or link["source_id"] != listing.source_id
        or str(link["external_id"]) != str(listing.external_id)
        or current["source_id"] != listing.source_id
        or str(current["external_id"]) != str(listing.external_id)
    ):
        raise ValueError("Imported baseline identity binding changed")
    original = json.loads(stored["row_json"])
    if (
        _digest(original) != stored["row_sha256"]
        or original["source_id"] != listing.source_id
        or str(original["external_id"]) != str(listing.external_id)
        or _sha_text(original.get("description")) != link["description_sha256"]
    ):
        raise ValueError("Imported baseline row evidence changed")
    marker = _raw(dict(current)).get("_jobagg_baseline_inventory", {})
    if (
        marker.get("baseline_id") != link["baseline_id"]
        or _sha_text(current["description"]) != link["description_sha256"]
    ):
        # A later detail can legitimately replace the adopted body.
        return None
    if not (current["description"] or "").strip():
        return None
    return {
        "job_key": key,
        "baseline_id": link["baseline_id"],
        "retained_description_sha256": link["description_sha256"],
        "scope": "Retained live baseline; fresh listing recorded separately and detail remains due",
        "fresh_source_observation": False,
        "current_membership_certified": False,
        "completeness_certified": False,
    }


def _attachments(live, row, remaining, deadline, already_verified):
    associations, blobs = [], {}
    if not _exists(live, "job_attachments"):
        return associations, blobs, remaining
    for association in _rows(
        live,
        "SELECT * FROM job_attachments WHERE job_key=? ORDER BY attachment_id",
        (row["job_key"],),
    ):
        _check(deadline)
        content_sha = association.get("content_sha256")
        item = {
            "row": association,
            "association_sha256": _digest(association),
            "blob_status": "unresolved_missing_reference",
        }
        if content_sha and _exists(live, "attachment_blobs"):
            metadata = live.execute(
                "SELECT media_type,size_bytes,length(content) AS actual_size FROM attachment_blobs WHERE content_sha256=?",
                (content_sha,),
            ).fetchone()
            if metadata:
                item["blob_metadata"] = dict(metadata)
                if metadata["actual_size"] != metadata["size_bytes"]:
                    item["blob_status"] = "unresolved_size_mismatch"
                elif content_sha in already_verified or content_sha in blobs:
                    item["blob_status"] = "verified_original_bytes"
                elif metadata["actual_size"] > remaining:
                    item["blob_status"] = "unresolved_batch_byte_budget"
                else:
                    content = live.execute(
                        "SELECT content FROM attachment_blobs WHERE content_sha256=?",
                        (content_sha,),
                    ).fetchone()[0]
                    if hashlib.sha256(content).hexdigest() != content_sha:
                        item["blob_status"] = "unresolved_content_hash_mismatch"
                    else:
                        item["blob_status"] = "verified_original_bytes"
                        blobs[content_sha] = {
                            "media_type": metadata["media_type"],
                            "content": content,
                        }
                        remaining -= len(content)
        associations.append(item)
    return associations, blobs, remaining


def reconcile_inventory(
    live_database,
    worker_database,
    registry,
    shared_lock,
    *,
    owner_fd,
    execute=False,
    after_key="",
    max_jobs=250,
    max_bytes=128 * 1024 * 1024,
    max_metadata_bytes=64 * 1024 * 1024,
    max_seconds=120,
    fill_empty_stubs=False,
    expected_preview_sha256=None,
    fault=None,
):
    """One bounded, atomic worker batch; caller retains the shared owner FD."""
    if (
        type(max_jobs) is not int
        or not 1 <= max_jobs <= 5000
        or max_bytes < 0
        or max_metadata_bytes <= 0
        or max_seconds <= 0
    ):
        raise ValueError("Invalid baseline batch bounds")
    deadline = time.monotonic() + max_seconds
    live_path, worker_path, registry_path, lock_path = [
        Path(p).absolute() for p in (live_database, worker_database, registry, shared_lock)
    ]
    live_stamp, worker_stamp = _file(live_path), _file(worker_path)
    if (live_stamp["device"], live_stamp["inode"]) == (
        worker_stamp["device"],
        worker_stamp["inode"],
    ):
        raise ValueError("Live and worker databases alias the same file")
    if worker_path.stat().st_nlink != 1:
        raise ValueError("Writable worker database has hardlink aliases")
    _verify_owner(owner_fd, lock_path)
    gate_path = live_path.parent / ".jobagg-publication-state.json"
    gate = _gate(gate_path)
    origin_files = _files(live_path)
    registry_sha = stable_file(str(registry_path), deadline_at=deadline)["sha256"]
    sources = {source.id: source for source in load_sources(registry_path)}
    with (
        closing(_connect(live_path)) as live,
        closing(_connect(worker_path, write=execute)) as worker,
    ):
        live.execute("BEGIN")
        if execute:
            worker.execute("PRAGMA foreign_keys=ON")
            worker.execute("PRAGMA synchronous=FULL")
        worker.execute("BEGIN IMMEDIATE" if execute else "BEGIN")
        worker.set_progress_handler(lambda: int(time.monotonic() >= deadline), 1000)
        live.set_progress_handler(lambda: int(time.monotonic() >= deadline), 1000)
        try:
            schema = _rows(
                live,
                "SELECT type,name,tbl_name,sql FROM sqlite_master WHERE sql IS NOT NULL ORDER BY type,name",
            )
            # SQLite can create an empty WAL when opening a WAL-mode database
            # read-only. Accept only that specific side effect under the owner;
            # every pre-existing file must remain byte-container-identical.
            pinned_files = _files(live_path)
            original_by_path = {item["path"]: item for item in origin_files}
            pinned_by_path = {item["path"]: item for item in pinned_files}
            if any(
                pinned_by_path.get(path) != item for path, item in original_by_path.items()
            ) or any(
                item["path"] != str(live_path) + "-wal" or item["size"] != 0
                for path, item in pinned_by_path.items()
                if path not in original_by_path
            ):
                raise ValueError("Live files changed while pinning the read-only snapshot")
            origin_files = pinned_files
            columns = {row["name"] for row in worker.execute("PRAGMA table_info(jobs)")}
            rows = _rows(
                live,
                "SELECT * FROM jobs WHERE job_key>? ORDER BY job_key LIMIT ?",
                (after_key, max_jobs + 1),
            )
            has_more = len(rows) > max_jobs
            rows = rows[:max_jobs]
            changes, all_blobs, remaining = [], {}, max_bytes
            metadata_bytes = 0
            duplicate_identities = {
                (item["source_id"], str(item["external_id"]))
                for item in live.execute(
                    "SELECT source_id,external_id FROM jobs WHERE external_id IS NOT NULL GROUP BY source_id,external_id HAVING count(*)>1"
                )
            }
            for row in rows:
                _check(deadline)
                baseline_id = _digest({"origin": str(live_path), "row_sha256": _digest(row)})
                rejection = None
                try:
                    disposition, key, before = _action(
                        worker, row, sources.get(row["source_id"]), fill_empty_stubs
                    )
                    # Multiple live rows must not claim one native worker identity.
                    if (row["source_id"], str(row.get("external_id"))) in duplicate_identities:
                        raise ValueError("Live native identity is duplicated")
                except (ValueError, TypeError) as exc:
                    disposition, key, before, rejection = (
                        "archive_identity_conflict",
                        None,
                        None,
                        str(exc),
                    )
                aliases = (
                    _rows(
                        live,
                        "SELECT * FROM consolidated_job_aliases WHERE canonical_job_key=? OR duplicate_job_key=? ORDER BY duplicate_job_key",
                        (row["job_key"], row["job_key"]),
                    )
                    if _exists(live, "consolidated_job_aliases")
                    else []
                )
                baseline_id = _digest(
                    {
                        "origin": str(live_path),
                        "row_sha256": _digest(row),
                        "aliases_sha256": _digest(aliases),
                    }
                )
                associations, blobs, remaining = _attachments(
                    live, row, remaining, deadline, all_blobs
                )
                change = {
                    "baseline_id": baseline_id,
                    "row": row,
                    "aliases": aliases,
                    "worker_job_key": key,
                    "disposition": disposition,
                    "worker_before_sha256": _digest(before) if before else None,
                    "worker_before": before,
                    "attachments": associations,
                    "rejection": rejection,
                }
                if disposition in {
                    "insert_missing",
                    "fill_empty_stub",
                    "adopt_matching_worker_body",
                }:
                    change["operational"] = _operational(
                        row,
                        key,
                        baseline_id,
                        columns,
                        before,
                        marker_only=disposition == "adopt_matching_worker_body",
                    )
                change_size = len(_dump(change).encode())
                if metadata_bytes + change_size > max_metadata_bytes:
                    if not changes:
                        raise ValueError(
                            "One baseline record exceeds max_metadata_bytes; increase the explicit batch bound"
                        )
                    has_more = True
                    break
                metadata_bytes += change_size
                all_blobs.update(blobs)
                changes.append(change)
            rows = [change["row"] for change in changes]
            scoped_snapshot = {
                "origin_path": str(live_path),
                "gate": gate,
                "origin_files": origin_files,
                "schema_sha256": _digest(schema),
                "registry_sha256": registry_sha,
                "after_key": after_key,
                "through_key": rows[-1]["job_key"] if rows else after_key,
                "live_rows_sha256": _digest(
                    [{k: c[k] for k in ("row", "aliases", "attachments")} for c in changes]
                ),
                "provenance_kind": "pinned_read_transaction_with_scoped_logical_hashes_not_whole_database_hash",
            }
            preview_sha = _digest({"snapshot": scoped_snapshot, "changes": changes})
            if expected_preview_sha256 and preview_sha != expected_preview_sha256:
                raise ValueError("Reviewed baseline preview or worker preimages changed")
            counts = dict(Counter(c["disposition"] for c in changes))
            blob_counts = dict(Counter(a["blob_status"] for c in changes for a in c["attachments"]))
            result = {
                "version": VERSION,
                "mode": "execute" if execute else "preview",
                "status": "prepared",
                "preview_sha256": preview_sha,
                "snapshot": scoped_snapshot,
                "jobs_examined": len(changes),
                "dispositions": counts,
                "inserted_with_text": sum(
                    c["disposition"] == "insert_missing"
                    and bool((c["row"].get("description") or "").strip())
                    for c in changes
                ),
                "inserted_without_text": sum(
                    c["disposition"] == "insert_missing"
                    and not (c["row"].get("description") or "").strip()
                    for c in changes
                ),
                "worker_observations_retained": counts.get("preserve_worker_observation", 0),
                "attachment_statuses": blob_counts,
                "verified_distinct_blob_bytes": sum(len(b["content"]) for b in all_blobs.values()),
                "prepared_metadata_bytes": metadata_bytes,
                "max_metadata_bytes": max_metadata_bytes,
                "has_more": has_more,
                "next_cursor": rows[-1]["job_key"] if has_more and rows else None,
                "disabled_sources": sorted(s.id for s in sources.values() if not s.enabled),
                "rejections": [
                    {"live_job_key": c["row"]["job_key"], "reason": c["rejection"]}
                    for c in changes
                    if c["rejection"]
                ],
                "fresh_source_observations_created": 0,
                "queue_tasks_changed": 0,
                "live_database_writes": 0,
                "current_membership_certified": False,
                "completeness_certified": False,
            }
            _check(deadline)
            if _files(live_path) != origin_files or _gate(gate_path) != gate:
                raise ValueError("Live baseline changed during the pinned read")
            if execute:
                for sql in _TABLES:
                    worker.execute(sql)
                batch_id = _digest(scoped_snapshot)
                for content_sha, blob in all_blobs.items():
                    prior = worker.execute(
                        "SELECT size_bytes,content FROM baseline_inventory_blobs WHERE content_sha256=?",
                        (content_sha,),
                    ).fetchone()
                    if prior and (
                        prior["size_bytes"] != len(blob["content"])
                        or hashlib.sha256(prior["content"]).hexdigest() != content_sha
                    ):
                        raise ValueError(
                            "Existing archived attachment bytes conflict with their hash"
                        )
                    worker.execute(
                        "INSERT OR IGNORE INTO baseline_inventory_blobs VALUES(?,?,?,?)",
                        (content_sha, blob["media_type"], len(blob["content"]), blob["content"]),
                    )
                for change in changes:
                    _check(deadline)
                    row, key = change["row"], change["worker_job_key"]
                    worker.execute(
                        "INSERT OR IGNORE INTO baseline_inventory_jobs VALUES(?,?,?,?,?,?,?,?,?,?)",
                        (
                            change["baseline_id"],
                            batch_id,
                            row["source_id"],
                            row["job_key"],
                            key,
                            _digest(row),
                            _sha_text(row.get("description")),
                            _dump(row),
                            _dump(change["aliases"]),
                            change["disposition"],
                        ),
                    )
                    archived = worker.execute(
                        "SELECT row_sha256,row_json,aliases_json FROM baseline_inventory_jobs WHERE baseline_id=?",
                        (change["baseline_id"],),
                    ).fetchone()
                    if (
                        archived["row_sha256"] != _digest(row)
                        or json.loads(archived["row_json"]) != row
                        or json.loads(archived["aliases_json"]) != change["aliases"]
                    ):
                        raise ValueError("Archived baseline row conflicts with prepared evidence")
                    if "operational" in change:
                        if change["worker_before"]:
                            worker.execute(
                                "INSERT OR IGNORE INTO baseline_inventory_beforeimages VALUES(?,?,?)",
                                (
                                    key,
                                    change["worker_before_sha256"],
                                    _dump(change["worker_before"]),
                                ),
                            )
                        value = change["operational"]
                        names = list(value)
                        if change["disposition"] == "insert_missing":
                            worker.execute(
                                "INSERT INTO jobs("
                                + ",".join(names)
                                + ") VALUES("
                                + ",".join("?" for _ in names)
                                + ")",
                                [value[name] for name in names],
                            )
                        else:
                            worker.execute(
                                "UPDATE jobs SET "
                                + ",".join(name + "=?" for name in names if name != "job_key")
                                + " WHERE job_key=?",
                                [value[name] for name in names if name != "job_key"] + [key],
                            )
                        actual = dict(
                            worker.execute("SELECT * FROM jobs WHERE job_key=?", (key,)).fetchone()
                        )
                        if any(actual[name] != value[name] for name in value):
                            raise ValueError("Adopted worker row differs from prepared baseline")
                        worker.execute(
                            "INSERT OR REPLACE INTO baseline_inventory_links VALUES(?,?,?,?,?,?)",
                            (
                                key,
                                change["baseline_id"],
                                row["source_id"],
                                str(row["external_id"]),
                                _sha_text(row.get("description")),
                                _digest(actual),
                            ),
                        )
                    for association in change["attachments"]:
                        content_sha = association["row"].get("content_sha256")
                        worker.execute(
                            "INSERT INTO baseline_inventory_attachments VALUES(?,?,?,?,?,?) ON CONFLICT(baseline_id,association_sha256) DO UPDATE SET blob_status=CASE WHEN excluded.blob_status='verified_original_bytes' THEN excluded.blob_status ELSE baseline_inventory_attachments.blob_status END,evidence_json=CASE WHEN excluded.blob_status='verified_original_bytes' THEN excluded.evidence_json ELSE baseline_inventory_attachments.evidence_json END",
                            (
                                change["baseline_id"],
                                association["association_sha256"],
                                _dump(association["row"]),
                                content_sha,
                                association["blob_status"],
                                _dump(association),
                            ),
                        )
                if fault:
                    fault("before_commit", worker)
                _check(deadline)
                _verify_owner(owner_fd, lock_path)
                if _files(live_path) != origin_files or _gate(gate_path) != gate:
                    raise ValueError("Live baseline changed before worker commit")
                result["status"] = "imported"
                result["batch_id"] = batch_id
                worker.execute(
                    "INSERT OR IGNORE INTO baseline_import_batches VALUES(?,?,?,?)",
                    (
                        batch_id,
                        datetime.now(UTC).isoformat(),
                        _dump(scoped_snapshot),
                        _dump(result),
                    ),
                )
                worker.commit()
            else:
                worker.rollback()
            return result
        except BaseException:
            worker.set_progress_handler(None, 0)
            worker.rollback()
            raise


@contextmanager
def _owner(path, descriptor=None):
    if descriptor is not None:
        _verify_owner(descriptor, Path(path).absolute())
        yield descriptor
    else:
        with Path(path).open("r+") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            _verify_owner(handle.fileno(), Path(path).absolute())
            yield handle.fileno()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for flag in ("live-database", "worker-database", "registry", "shared-lock"):
        parser.add_argument("--" + flag, required=True)
    parser.add_argument("--execute", action="store_true", help="Default is a read-only preview")
    parser.add_argument("--owner-fd", type=int)
    parser.add_argument("--after-key", default="")
    parser.add_argument("--max-jobs", type=int, default=250)
    parser.add_argument("--max-bytes", type=int, default=128 * 1024 * 1024)
    parser.add_argument("--max-metadata-bytes", type=int, default=64 * 1024 * 1024)
    parser.add_argument("--max-seconds", type=float, default=120)
    parser.add_argument("--fill-empty-stubs", action="store_true")
    parser.add_argument("--expected-preview-sha256")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args(argv)
    descriptor = args.owner_fd
    if descriptor is None and os.environ.get("JOBAGG_SHARED_LOCK_FD"):
        descriptor = int(os.environ["JOBAGG_SHARED_LOCK_FD"])
    with _owner(args.shared_lock, descriptor) as owned:
        result = reconcile_inventory(
            args.live_database,
            args.worker_database,
            args.registry,
            args.shared_lock,
            owner_fd=owned,
            execute=args.execute,
            after_key=args.after_key,
            max_jobs=args.max_jobs,
            max_bytes=args.max_bytes,
            max_metadata_bytes=args.max_metadata_bytes,
            max_seconds=args.max_seconds,
            fill_empty_stubs=args.fill_empty_stubs,
            expected_preview_sha256=args.expected_preview_sha256,
        )
    if args.report:
        from jobagg.atomic_files import atomic_write_text

        atomic_write_text(args.report, json.dumps(result, indent=2) + "\n")
    print(_dump(result))


if __name__ == "__main__":
    main()
