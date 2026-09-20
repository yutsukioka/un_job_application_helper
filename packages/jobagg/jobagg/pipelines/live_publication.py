"""Incremental, evidence-bound publication without an LLM or network access.

The caller owns the worker's shared lock. Source and consolidated transactions
are individually atomic, not a multi-file transaction. The public API must honor
.jobagg-publication-state.json: every non-complete state is unavailable. Immutable
plans, row beforeimages, copied blobs and transaction markers permit roll-forward
recovery after a crash. Exports become current only when the gate is complete.
"""

from __future__ import annotations

from contextlib import closing
from copy import deepcopy
from dataclasses import fields
from datetime import UTC, datetime
import gzip
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import time
from uuid import uuid4

from jobagg.publication_deadline import expired
from jobagg.atomic_files import atomic_write_text
from jobagg.accepted_detail_lineage import matching_detail_attempts
from jobagg.classification import classify_and_store
from jobagg.db import JobDatabase
from jobagg.detail_quality import DETAIL_QUALITY_COMPLETE, detail_quality_status
from jobagg.models import JobRecord
from jobagg.pipelines.bundles import source_output_paths, source_output_slug
from jobagg.pipelines.publication_exports import publish_exports
from jobagg.pipelines.document_readback import document_readback
from jobagg.pipelines.sync_source import load_sources

VERSION = "incremental-live-publication-1"
GATE_NAME = ".jobagg-publication-state.json"
_DATE_FIELDS = {"first_seen_at", "last_seen_at", "posted_at", "closes_at"}
_JOURNAL = "jobagg_publication_receipts"


def _dump(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=True, default=str)


def _digest(value):
    return hashlib.sha256(_dump(value).encode()).hexdigest()


def _sha(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _save(path, value):
    atomic_write_text(path, _dump(value) + "\n")


def _now():
    return datetime.now(UTC).isoformat()


def _epoch(value):
    if not value:
        return 0.0
    if isinstance(value, (float, int)):
        return float(value)
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("Observation time lacks timezone")
    return result.timestamp()


def _ro(path):
    connection = sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def _row(conn, key):
    result = conn.execute("SELECT * FROM jobs WHERE job_key=?", (key,)).fetchone()
    return dict(result) if result else None


def _raw(row):
    return json.loads((row or {}).get("raw_json") or "{}")


def _resolve(conn, row):
    """Only same-source, unique native identity aliases may route updates."""
    current = _row(conn, row["job_key"])
    if current and (
        current["source_id"] != row["source_id"]
        or str(current["external_id"]) != str(row["external_id"])
    ):
        raise ValueError("exact_key_has_different_source_or_native_identity")
    alias = conn.execute(
        "SELECT * FROM consolidated_job_aliases WHERE duplicate_job_key=?", (row["job_key"],)
    ).fetchone()
    if alias:
        if alias["canonical_source_id"] != row["source_id"]:
            raise ValueError("cross_source_alias_requires_review")
        current = _row(conn, alias["canonical_job_key"])
        if not current or str(current["external_id"]) != str(row["external_id"]):
            raise ValueError("alias_native_identity_conflict")
    if not current:
        matches = conn.execute(
            "SELECT * FROM jobs WHERE source_id=? AND external_id=?",
            (row["source_id"], row["external_id"]),
        ).fetchall()
        if len(matches) > 1:
            raise ValueError("ambiguous_source_native_identity")
        current = dict(matches[0]) if matches else None
    return dict(current) if current else None


def _capture(path, expected_sha=None):
    path = Path(path)
    if expected_sha and _sha(path) != expected_sha:
        raise ValueError("capture_metadata_hash_mismatch")
    meta = json.loads(path.read_text())
    if meta.get("status_code") != 200 or meta.get("body_captured") is not True:
        return None
    artifact = Path(meta["artifact"])
    with gzip.open(artifact, "rb") as source:
        digest = hashlib.sha256()
        size = 0
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
            size += len(block)
    if digest.hexdigest() != meta.get("body_sha256") or size != meta.get("body_bytes"):
        raise ValueError("captured_response_bytes_differ")
    return meta


def _accepted_detail(conn, row, observation):
    proof = json.loads(observation["proof"])
    body_hash = hashlib.sha256((row["description"] or "").encode()).hexdigest()
    if (
        proof.get("source_id") != row["source_id"]
        or str(proof.get("external_id")) != str(row["external_id"])
        or body_hash != observation["source_description_sha256"]
        or body_hash != observation["database_description_sha256"]
        or body_hash != proof.get("parsed_source_text_sha256")
    ):
        raise ValueError("detail_identity_or_text_hash_mismatch")
    if (
        detail_quality_status(title=row["title"], description=row["description"], raw=_raw(row))
        != DETAIL_QUALITY_COMPLETE
    ):
        raise ValueError("detail_parser_quality_rejected")
    # Refresh status is scheduling state, not accepted evidence. Never replace
    # a successful immutable attempt with the mutable task's latest receipt.
    bound_attempts = matching_detail_attempts(conn, row, proof)
    if not bound_attempts:
        raise ValueError("completed_detail_artifact_not_bound")
    public_fields = (
        "title",
        "apply_url",
        "source_url",
        "description",
        "location",
        "department",
        "employment_type",
        "posted_at",
        "closes_at",
        "closes_at_local",
        "closes_tz",
    )
    for binding in bound_attempts:
        parsed = binding["artifact"]["job"]
        if any(parsed.get(field) != row.get(field) for field in public_fields):
            raise ValueError("worker_public_fields_differ_from_completed_detail_artifact")
    captures = [_capture(item["path"], item["sha256"]) for item in proof.get("captures", [])]
    if not any(
        meta
        and meta.get("phase", {}).get("kind") == "detail"
        and str(meta.get("phase", {}).get("job_id")) == str(row["external_id"])
        for meta in captures
    ):
        raise ValueError("no_successful_identity_bound_detail_capture")
    return proof


def _documents(conn, row, proof):
    documents, rejected = [], []
    body_sha = proof["parsed_source_text_sha256"]
    for record in conn.execute(
        "SELECT * FROM remediation_documents WHERE job_key=? ORDER BY task_id", (row["job_key"],)
    ):
        try:
            doc = json.loads(record["manifest"])
            if (
                record["source_id"] != row["source_id"]
                or doc.get("job_key") != row["job_key"]
                or doc.get("source_id") != row["source_id"]
                or doc.get("url") != record["url"]
                or doc.get("parent_description_sha256") != body_sha
                or doc.get("content_sha256") != record["content_sha256"]
                or hashlib.sha256(doc.get("extracted_text", "").encode()).hexdigest()
                != record["text_sha256"]
            ):
                raise ValueError("document_identity_parent_or_hash_conflict")
            task = conn.execute(
                "SELECT receipt,payload,status FROM remediation_tasks WHERE task_id=?",
                (record["task_id"],),
            ).fetchone()
            if not task or task["status"] != "done":
                raise ValueError("document_task_not_complete")
            payload, receipt = json.loads(task["payload"]), json.loads(task["receipt"])
            if payload.get("url") != record["url"] or payload.get("job_key") != row["job_key"]:
                raise ValueError("document_task_association_differs")
            path = Path(receipt["manifest"])
            if _sha(path) != receipt.get("sha256") or json.loads(path.read_text()) != doc:
                raise ValueError("document_manifest_not_bound")
            binary = Path(doc["binary_path"])
            if _sha(binary) != record["content_sha256"]:
                raise ValueError("document_binary_hash_conflict")
            blob = conn.execute(
                "SELECT size_bytes,content FROM attachment_blobs WHERE content_sha256=?",
                (record["content_sha256"],),
            ).fetchone()
            if (
                not blob
                or hashlib.sha256(blob["content"]).hexdigest() != record["content_sha256"]
                or len(blob["content"]) != blob["size_bytes"]
            ):
                raise ValueError("worker_blob_hash_conflict")
            captures = [_capture(path) for path in doc.get("capture_paths", [])]
            if not any(
                meta and meta.get("body_sha256") == record["content_sha256"] for meta in captures
            ):
                raise ValueError("document_capture_bytes_not_bound")
            documents.append(doc)
        except (ValueError, KeyError, OSError, sqlite3.DatabaseError) as exc:
            rejected.append({"task_id": record["task_id"], "reason": str(exc)})
    return documents, rejected


def _publication_key(row, observation, documents):
    return _digest(
        {
            "observation": dict(observation),
            "source": row["source_id"],
            "external_id": row["external_id"],
            "documents": documents,
        }
    )


def _receipt_exists(conn, key):
    exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE name=? AND type='table'", (_JOURNAL,)
    ).fetchone()
    return bool(
        exists
        and conn.execute(f"SELECT 1 FROM {_JOURNAL} WHERE publication_key=?", (key,)).fetchone()
    )


def _latest_detail(row, conn):
    if not row:
        return 0.0
    raw = _raw(row)
    clocks = [
        raw.get("_deterministic_fetch_observation", {}).get("observed_at"),
        raw.get("_jobagg_main_text_verification", {}).get("observed_at"),
    ]
    backlog = conn.execute(
        "SELECT last_success_at FROM detail_backlog WHERE job_key=?", (row["job_key"],)
    ).fetchone()
    if backlog:
        clocks.append(backlog[0])
    # last_seen_at is deliberately conservative when older manual observations
    # do not carry an independently named detail clock. It never rewrites history.
    clocks.append(row.get("last_seen_at"))
    return max((_epoch(value) for value in clocks if value), default=0.0)


def plan_publication(worker_database, registry_path, output_dir, *, max_jobs=20, deadline_at=None, job_keys=None):
    _validate_targets(worker_database, output_dir)
    output = Path(output_dir).resolve()
    sources = {source.id: source for source in load_sources(registry_path)}
    targets = {
        key: output / f"{source_output_slug(source)}_jobs.sqlite3"
        for key, source in sources.items()
        if source.enabled
    }
    duplicates = {path for path in targets.values() if list(targets.values()).count(path) > 1}
    plan = {
        "version": VERSION,
        "created_at": _now(),
        "output_dir": str(output),
        "worker_database": str(Path(worker_database).resolve()),
        "registry_sha256": _sha(registry_path),
        "changes": [],
        "listing_frames": [],
        "rejected": [],
        "unchanged": 0,
        "listing_only": 0,
        "disabled_sources": sorted(key for key, source in sources.items() if not source.enabled),
        "completeness_certified": False,
        "budget_exhausted": False,
    }
    with (
        closing(_ro(worker_database)) as worker,
        closing(_ro(output / "all_jobs.sqlite3")) as consolidated,
    ):
        worker.execute("BEGIN")
        plan["listing_only"] = worker.execute(
            "SELECT count(*) FROM jobs j LEFT JOIN remediation_observations o ON o.job_key=j.job_key WHERE o.job_key IS NULL"
        ).fetchone()[0]
        from jobagg.publication_projection import projection_diagnostics

        projected = projection_diagnostics(worker)
        if projected is not None:
            plan["listing_only"] = projected["jobs_without_observations"]
            plan["publication_projection"] = projected
        observations = worker.execute(
            "SELECT * FROM remediation_observations ORDER BY checked_at,job_key"
        ).fetchall()
        from jobagg.pipelines.live_inventory import plan_frames

        if job_keys is None:
            plan["listing_frames"], listing_rejected = plan_frames(
                worker, consolidated, sources, targets, limit=5, deadline_at=deadline_at
            )
        else:
            selected = set(job_keys)
            observations = [row for row in observations if row["job_key"] in selected]
            plan["selected_job_keys"] = sorted(selected)
            listing_rejected = []
        plan["rejected"].extend(listing_rejected)
        plan["observation_set_sha256"] = _digest(
            {
                "details": [dict(row) for row in observations],
                "listing_frames": [
                    {"source_id": frame["source_id"], "frame_sha256": frame["frame_sha256"]}
                    for frame in plan["listing_frames"]
                ],
                "documents": [
                    dict(row)
                    for row in worker.execute(
                        "SELECT task_id,manifest FROM remediation_documents ORDER BY task_id"
                    )
                ],
            }
        )
        for observation in observations:
            if len(plan["changes"]) >= max_jobs or (
                expired(deadline_at)
            ):
                plan["budget_exhausted"] = True
                break
            row = _row(worker, observation["job_key"])
            try:
                if not row or row["source_id"] not in targets:
                    raise ValueError("source_missing_or_disabled")
                target = targets[row["source_id"]]
                if target in duplicates or not target.is_file():
                    raise ValueError("canonical_source_database_missing_or_ambiguous")
                attachments_in_scope = sources[row["source_id"]].extra.get("fetch_attachments", True) is not False
                document_manifest_rows = [
                    dict(item)
                    for item in worker.execute(
                        "SELECT task_id,manifest FROM remediation_documents WHERE job_key=? ORDER BY task_id",
                        (row["job_key"],),
                    )
                ] if attachments_in_scope else []
                key = _publication_key(row, observation, [])
                with closing(_ro(target)) as source_conn:
                    if (
                        not document_manifest_rows
                        and _receipt_exists(source_conn, key)
                        and _receipt_exists(consolidated, key)
                    ):
                        plan["unchanged"] += 1
                        continue
                proof = _accepted_detail(worker, row, observation)
                documents, document_rejected = (_documents(worker, row, proof)
                    if attachments_in_scope else ([], []))
                key = _publication_key(row, observation, documents)
                plan["rejected"].extend(
                    {"job_key": row["job_key"], **item} for item in document_rejected
                )
                with closing(_ro(target)) as source_conn:
                    source_row = _resolve(source_conn, row)
                    all_row = _resolve(consolidated, row)
                    if not attachments_in_scope and all(
                        previous and _raw(previous).get("_deterministic_fetch_observation") == proof
                        and all(previous.get(field) == row.get(field) for field in _PUBLIC_FIELDS)
                        for previous in (source_row, all_row)
                    ):
                        plan["unchanged"] += 1
                        continue
                    receipts = [
                        _receipt_exists(connection, key)
                        for connection in (source_conn, consolidated)
                    ]
                    reconciliation = []
                    if documents:
                        for connection, path, previous in (
                            (source_conn, target, source_row),
                            (consolidated, output / "all_jobs.sqlite3", all_row),
                        ):
                            cache = {}
                            reconciliation.append(
                                {
                                    "destination": str(path),
                                    "row_sha256": _digest(previous),
                                    "documents": [
                                        document_readback(connection, row, document, cache)
                                        for document in documents
                                    ],
                                }
                            )
                    document_readback_ok = all(
                        result["checks"]["verified"]
                        for destination in reconciliation
                        for result in destination["documents"]
                    )
                    if any(
                        "live_document_purpose_conflict_requires_review" in result["errors"]
                        for destination in reconciliation
                        for result in destination["documents"]
                    ):
                        raise ValueError("live_document_purpose_conflict_requires_review")
                    if all(receipts) and document_readback_ok:
                        plan["unchanged"] += 1
                        continue
                    repair_of_key = None
                    if any(receipts) and not document_readback_ok:
                        # Preserve old receipts. A new, deterministic repair is
                        # bound to both row before-images and failed readbacks;
                        # recovery still uses the normal atomic journal path.
                        repair_of_key = key
                        key = _digest(
                            {"repair_of_publication_key": key, "readback": reconciliation}
                        )
                    observed_at = _epoch(proof["observed_at"])
                    if observed_at > time.time() + 300:
                        raise ValueError("detail_observation_clock_is_in_the_future")
                    shorter_text_regression_exceptions = []
                    if not proof.get("independent_whole_public_text_verified"):
                        for baseline, destination_path in (
                            (source_row, target),
                            (all_row, output / "all_jobs.sqlite3"),
                        ):
                            if baseline and len(" ".join(row["description"].split())) < len(
                                " ".join((baseline["description"] or "").split())
                            ):
                                from jobagg.pipelines.public_text_regression import (
                                    taleo_date_metadata_only_change,
                                )

                                date_header_check = taleo_date_metadata_only_change(baseline, row)
                                if date_header_check.get("accepted") is not True:
                                    from jobagg.pipelines.reviewed_text_changes import reviewed_text_change
                                    date_header_check = reviewed_text_change(baseline, row, proof)
                                if date_header_check.get("accepted") is not True:
                                    raise ValueError(
                                        "shorter_than_retained_live_text_requires_full_source_contract"
                                    )
                                shorter_text_regression_exceptions.append(
                                    {
                                        "destination": str(destination_path),
                                        "before_description_sha256": hashlib.sha256(
                                            baseline["description"].encode()
                                        ).hexdigest(),
                                        "incoming_description_sha256": proof[
                                            "parsed_source_text_sha256"
                                        ],
                                        "check": date_header_check,
                                    }
                                )
                    document_only = bool(documents) and all(
                        old
                        and old["description"] == row["description"]
                        and _raw(old).get("_deterministic_fetch_observation") == proof
                        for old in (source_row, all_row)
                    )
                    if not document_only and observed_at < max(
                        _latest_detail(source_row, source_conn),
                        _latest_detail(all_row, consolidated),
                    ):
                        raise ValueError("older_than_live_observation")
                    change = {
                        "publication_key": key,
                        "worker_row": row,
                        "proof": proof,
                        "documents": documents,
                        "attachments_in_scope": attachments_in_scope,
                        "repair_of_publication_key": repair_of_key,
                        "document_readback_before": reconciliation if repair_of_key else [],
                        "document_only": document_only,
                        "shorter_text_regression_exceptions": shorter_text_regression_exceptions,
                        "destinations": [
                            {"path": str(target), "before": source_row},
                            {"path": str(output / "all_jobs.sqlite3"), "before": all_row},
                        ],
                    }
                    for destination, connection in zip(
                        change["destinations"], (source_conn, consolidated)
                    ):
                        destination["expected_public_fields"] = _preview_merge(
                            change, destination["before"], connection
                        )
                    plan["changes"].append(change)
            except (ValueError, KeyError, OSError, sqlite3.DatabaseError) as exc:
                plan["rejected"].append({"job_key": observation["job_key"], "reason": str(exc)})
    return plan


def _validate_targets(worker_database, output_dir):
    output = Path(output_dir)
    worker = Path(worker_database)
    paths = [
        worker,
        *output.glob("*_jobs.sqlite3"),
        *output.glob("*_jobs_*.json"),
        *output.glob("*_jobs_*.csv"),
    ]
    seen = {}
    if output.is_symlink() or worker.is_symlink():
        raise ValueError("publication_symlink_target_rejected")
    for path in paths:
        if path.is_symlink():
            raise ValueError("publication_symlink_target_rejected")
        if path.exists():
            stat = path.stat()
            identity = (stat.st_dev, stat.st_ino)
            if identity in seen:
                raise ValueError("publication_targets_share_inode")
            seen[identity] = str(path)


def _model(row):
    values = {field.name: row.get(field.name) for field in fields(JobRecord) if field.name in row}
    values["raw"] = _raw(row)
    for key in _DATE_FIELDS:
        if values.get(key):
            values[key] = datetime.fromisoformat(values[key].replace("Z", "+00:00"))
    values["normalized_hash"] = None
    values["posting_fingerprint"] = None
    return JobRecord(**values)


def _merged_model(change, before, generation):
    if change.get("document_only") and before:
        retained = deepcopy(before)
        raw = _raw(retained)
        raw["_deterministic_publication"] = {
            "version": VERSION,
            "generation_id": generation,
            "publication_key": change["publication_key"],
            "completeness_certified": False,
        }
        retained["raw_json"] = _dump(raw)
        return _model(retained)
    incoming = deepcopy(change["worker_row"])
    previous = _raw(before)
    public = _raw(incoming)
    from jobagg.adapters.imo_public import (
        bound_public_date_fields,
        public_claims,
        restore_raw_public_claims,
    )

    imo_calendar_fields = bound_public_date_fields(public, incoming)
    source_owned_fields = set(imo_calendar_fields)
    raw = {
        **previous,
        **{key: value for key, value in public.items() if value not in (None, "", [], {})},
    }
    if imo_calendar_fields:
        restore_raw_public_claims(raw, public_claims(public))
    from jobagg.adapters.eu_primary_metadata import FIELDS as EU_FIELDS, PREVIOUS as EU_PREVIOUS
    from jobagg.eu_primary_metadata_observation import bound_public_metadata
    from jobagg.eu_primary_observation import MARKER as EU_TEXT_MARKER

    eu_metadata_bound = bound_public_metadata(public, incoming)
    eu_legacy_bound = incoming.get("source_id") == "eu_careers_static" and JobDatabase._eu_bound_public_detail(public, incoming)
    if eu_metadata_bound:
        source_owned_fields.update(EU_FIELDS)
    if eu_metadata_bound or eu_legacy_bound:
        retired = {
            key: deepcopy(previous[key])
            for key in (EU_PREVIOUS, EU_TEXT_MARKER, "_eu_reviewed_primary_extraction", "_eu_cached_recovery_capture_bindings")
            if key in previous and key not in public
        }
        if retired:
            # A historical PDF certificate cannot become evidence for a newly
            # captured body just because the raw dictionaries were merged.
            # Retain its exact contents with its old body hash, outside active
            # parser marker positions; the ordinary source validator still runs.
            history = deepcopy(previous.get("_deterministic_publication_historical_proofs", []))
            if not isinstance(history, list):
                raise ValueError("historical_publication_proofs_not_a_list")
            archived = {
                "scope": "historical_not_evidence_for_current_body",
                "description_sha256": hashlib.sha256(
                    (before.get("description") or "").encode()
                ).hexdigest(),
                "proofs": retired,
            }
            if archived not in history:
                history.append(archived)
            raw["_deterministic_publication_historical_proofs"] = history
            for key in retired:
                raw.pop(key, None)
    # Existing document associations and every unknown/negative evidence field
    # survive; new document associations are appended after original bytes verify.
    raw["attachments"] = deepcopy(previous.get("attachments", []))
    if before and before.get("description") != incoming.get("description"):
        for key, value in list(raw.items()):
            if (
                "verification" in key
                and key != "_jobagg_listing_verification"
                and isinstance(value, dict)
                and value.get("complete") is True
            ):
                raw[key] = {
                    **value,
                    "complete": False,
                    "invalidated_reason": "Public content changed; historical proof retained in generation beforeimage",
                }
    raw["_deterministic_fetch_observation"] = change["proof"]
    raw["_jobagg_main_text_verification"] = {
        "complete": False,
        "observed_at": change["proof"]["observed_at"],
        "reason": "Parser output/capture equality; independent whole public text contract unverified",
        "description_sha256": change["proof"]["parsed_source_text_sha256"],
        "previous_verification": previous.get("_jobagg_main_text_verification"),
    }
    raw["attachment_verification"] = {
        **previous.get("attachment_verification", {}),
        "complete": False,
        "discovery_complete": False,
        "excluded_by_scope": not change.get("attachments_in_scope", True),
        "reason": ("Current public document scope/fidelity unverified"
                   if change.get("attachments_in_scope", True) else
                   "Supplementary attachments excluded by user configuration"),
    }
    raw["_deterministic_publication"] = {
        "version": VERSION,
        "generation_id": generation,
        "publication_key": change["publication_key"],
        "completeness_certified": False,
    }
    if before:
        incoming["org_id"] = before["org_id"]
        incoming["source_id"] = before["source_id"]
        incoming["first_seen_at"] = before["first_seen_at"]
        # Missing parser values mean unknown, never proof of removal.
        for field in fields(JobRecord):
            if (
                field.name != "raw"
                and field.name not in source_owned_fields
                and incoming.get(field.name) in (None, "")
            ):
                incoming[field.name] = before.get(field.name)
    incoming["last_seen_at"] = change["proof"]["observed_at"]
    incoming["raw_json"] = _dump(raw)
    return _model(incoming)


_PUBLIC_FIELDS = (
    "title",
    "description",
    "apply_url",
    "source_url",
    "location",
    "department",
    "employment_type",
    "posted_at",
    "closes_at",
    "closes_at_local",
    "closes_tz",
)


def _preview_merge(change, before, source_connection):
    """Run the real merge and classifier in a small in-memory candidate first."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    db = JobDatabase(":memory:")
    db._persistent_conn = conn
    try:
        db.initialize()
        if before:
            columns = list(before)
            conn.execute(
                f"INSERT INTO jobs ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})",
                tuple(before[column] for column in columns),
            )
            for item in source_connection.execute(
                "SELECT * FROM classification_overrides WHERE vacancy_id=?", (before["job_key"],)
            ):
                columns = list(item.keys())
                conn.execute(
                    f"INSERT INTO classification_overrides ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})",
                    tuple(item),
                )
        model = _merged_model(change, before, "preparation")
        db.upsert_job(model)
        actual = db.get_job(model.identity_key())
        if actual["description"] != change["worker_row"]["description"]:
            raise ValueError("production_merge_cannot_accept_exact_fresh_description")
        classify_and_store(actual, db)
        return {field: actual.get(field) for field in _PUBLIC_FIELDS}
    finally:
        db._persistent_conn = None
        conn.close()


def _backup_row(conn, row, target):
    if not row:
        return {"job": None, "related": {}}
    related = {}
    for table, column in (
        ("job_attachments", "job_key"),
        ("detail_backlog", "job_key"),
        ("vacancy_source_features", "vacancy_id"),
        ("vacancy_classifications", "vacancy_id"),
        ("classification_overrides", "vacancy_id"),
        ("vacancy_locations", "vacancy_id"),
        ("change_events", "job_key"),
        ("vacancy_snapshots", "job_key"),
    ):
        related[table] = [
            dict(item)
            for item in conn.execute(f"SELECT * FROM {table} WHERE {column}=?", (row["job_key"],))
        ]
    for document in related["job_attachments"]:
        digest = document.get("content_sha256")
        if digest:
            blob = conn.execute(
                "SELECT content FROM attachment_blobs WHERE content_sha256=?", (digest,)
            ).fetchone()
            if not blob or hashlib.sha256(blob[0]).hexdigest() != digest:
                raise ValueError("live_beforeimage_document_blob_conflict")
            path = target / "before_blobs" / digest
            path.parent.mkdir(parents=True, exist_ok=True)
            if not path.exists():
                with path.open("xb") as handle:
                    handle.write(blob[0])
                    handle.flush()
                    os.fsync(handle.fileno())
    return {"job": row, "related": related}


def _prepare(plan, state_dir):
    generation = uuid4().hex
    root = Path(state_dir).resolve() / "generations" / generation
    root.mkdir(parents=True)
    plan = deepcopy(plan)
    plan["generation_id"] = generation
    plan["generation_root"] = str(root)
    for change in plan["changes"]:
        for destination in change["destinations"]:
            with closing(_ro(destination["path"])) as conn:
                before = destination["before"]
                if before and _row(conn, before["job_key"]) != before:
                    raise ValueError("live_baseline_changed_during_preparation")
                backup = _backup_row(conn, before, root)
                name = _digest([destination["path"], change["publication_key"]]) + ".json"
                path = root / "beforeimages" / name
                _save(path, backup)
                destination["beforeimage_path"] = str(path)
                destination["beforeimage_sha256"] = _sha(path)
        for document in change["documents"]:
            source = Path(document["binary_path"])
            path = root / "blobs" / document["content_sha256"]
            path.parent.mkdir(exist_ok=True)
            if not path.exists():
                shutil.copyfile(source, path)
                with path.open("rb") as handle:
                    os.fsync(handle.fileno())
            if _sha(path) != document["content_sha256"]:
                raise ValueError("prepared_document_binary_conflict")
            document["prepared_binary_path"] = str(path)
    for frame in plan["listing_frames"]:
        for destination in frame["destinations"]:
            with closing(_ro(destination["path"])) as conn:
                previous = {}
                for table in ("live_listing_frames", "live_listing_inventory"):
                    exists = conn.execute(
                        "SELECT 1 FROM sqlite_master WHERE name=?", (table,)
                    ).fetchone()
                    previous[table] = (
                        [
                            dict(row)
                            for row in conn.execute(
                                f"SELECT * FROM {table} WHERE source_id=?", (frame["source_id"],)
                            )
                        ]
                        if exists
                        else []
                    )
                previous["job_presence_rows"] = destination["before_listing_rows"]
                before_path = (
                    root
                    / "beforeimages"
                    / (_digest([destination["path"], frame["publication_key"]]) + ".json")
                )
                _save(before_path, previous)
                destination["beforeimage_path"] = str(before_path)
                destination["beforeimage_sha256"] = _sha(before_path)
    path = root / "plan.json"
    _save(path, plan)
    return plan, path


def _attach_documents(db, conn, change, actual_key):
    row = db.get_job(actual_key)
    raw = row["raw"]
    old_documents = raw.get("attachments", [])
    if not isinstance(old_documents, list):
        raise ValueError("live_attachment_metadata_is_not_a_list")
    new_docs = []
    for doc in change["documents"]:
        data = Path(doc["prepared_binary_path"]).read_bytes()
        digest = doc["content_sha256"]
        if hashlib.sha256(data).hexdigest() != digest:
            raise ValueError("prepared_attachment_bytes_changed")
        old = conn.execute(
            "SELECT content FROM attachment_blobs WHERE content_sha256=?", (digest,)
        ).fetchone()
        if old and bytes(old[0]) != data:
            raise ValueError("live_attachment_hash_collision_or_corruption")
        conn.execute(
            "INSERT OR IGNORE INTO attachment_blobs(content_sha256,media_type,size_bytes,content) VALUES(?,?,?,?)",
            (digest, doc.get("media_type"), len(data), data),
        )
        identifier = hashlib.sha256(
            (actual_key + "\n" + doc["url"] + "\n" + digest).encode()
        ).hexdigest()
        published = {key: value for key, value in doc.items() if key != "prepared_binary_path"}
        published.update(
            attachment_id=identifier,
            job_key=actual_key,
            binary_ref={"table": "attachment_blobs", "key": digest, "column": "content"},
            status="captured_unverified",
            whole_job_complete=False,
            fidelity_complete=False,
        )
        old_association = conn.execute(
            "SELECT job_key,source_id,url,content_sha256 FROM job_attachments WHERE attachment_id=?",
            (identifier,),
        ).fetchone()
        if old_association and tuple(old_association) != (
            actual_key,
            change["worker_row"]["source_id"],
            doc["url"],
            digest,
        ):
            raise ValueError("existing_document_association_identity_conflict")
        conn.execute(
            """INSERT INTO job_attachments
            (attachment_id,job_key,source_id,url,final_url,label,category,required_for_complete_text,status,content_sha256,extracted_text,metadata_json)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(attachment_id) DO UPDATE SET
            final_url=excluded.final_url,label=excluded.label,status=excluded.status,
            extracted_text=excluded.extracted_text,metadata_json=excluded.metadata_json""",
            (
                identifier,
                actual_key,
                change["worker_row"]["source_id"],
                doc["url"],
                doc.get("final_url"),
                doc.get("label"),
                "unresolved",
                1,
                "captured_unverified",
                digest,
                doc.get("extracted_text", ""),
                _dump(published),
            ),
        )
        stored = conn.execute(
            "SELECT extracted_text,metadata_json FROM job_attachments WHERE attachment_id=?",
            (identifier,),
        ).fetchone()
        if (
            stored["extracted_text"] != doc.get("extracted_text", "")
            or json.loads(stored["metadata_json"]) != published
        ):
            raise ValueError("document_association_text_readback_failed")
        new_docs.append(published)
    by_key = {
        (item.get("attachment_id"), item.get("content_sha256")): item
        for item in old_documents
        if isinstance(item, dict)
    }
    for doc in new_docs:
        by_key[(doc["attachment_id"], doc["content_sha256"])] = doc
    raw["attachments"] = list(by_key.values())
    conn.execute(
        "UPDATE jobs SET raw_json=?, application_ready=0 WHERE job_key=?", (_dump(raw), actual_key)
    )


def _apply_database(path, changes, generation, fault=None, frames=()):
    db = JobDatabase(path)
    with db.connection_scope() as conn:
        conn.execute("PRAGMA synchronous=FULL")
        conn.execute(
            f"CREATE TABLE IF NOT EXISTS {_JOURNAL}(publication_key TEXT PRIMARY KEY,generation_id TEXT NOT NULL,job_key TEXT NOT NULL,committed_at TEXT NOT NULL)"
        )
        for change, destination in changes:
            if _receipt_exists(conn, change["publication_key"]):
                continue
            before = destination["before"]
            if _sha(destination["beforeimage_path"]) != destination["beforeimage_sha256"]:
                raise ValueError("beforeimage_hash_changed")
            model = _merged_model(change, before, generation)
            current = _row(conn, model.identity_key())
            if current != before:
                raise ValueError("live_row_preimage_changed_before_commit")
            db.upsert_job(model)
            actual = db.get_job(model.identity_key())
            if {field: actual.get(field) for field in _PUBLIC_FIELDS} != destination[
                "expected_public_fields"
            ]:
                raise ValueError("production_merge_differs_from_prepared_public_fields")
            _attach_documents(db, conn, change, model.identity_key())
            classify_and_store(db.get_job(model.identity_key()), db)
            conn.execute(
                "UPDATE jobs SET application_ready=0,detail_quality_status=?,canonical_job_key=COALESCE(canonical_job_key,job_key) WHERE job_key=?",
                (DETAIL_QUALITY_COMPLETE, model.identity_key()),
            )
            conn.execute(
                f"INSERT INTO {_JOURNAL} VALUES(?,?,?,?)",
                (change["publication_key"], generation, model.identity_key(), _now()),
            )
            if fault:
                fault("before_database_commit", str(path))
        if frames:
            from jobagg.pipelines.live_inventory import apply_frames

            for _, destination in frames:
                if _sha(destination["beforeimage_path"]) != destination["beforeimage_sha256"]:
                    raise ValueError("listing_beforeimage_hash_changed")
            changed_keys = [
                _merged_model(change, destination["before"], generation).identity_key()
                for change, destination in changes
            ]
            apply_frames(conn, frames, generation, changed_keys=changed_keys)
        if conn.execute(
            "SELECT 1 FROM sqlite_master WHERE name='live_listing_inventory'"
        ).fetchone():
            for change, destination in changes:
                model = _merged_model(change, destination["before"], generation)
                conn.execute(
                    "UPDATE live_listing_inventory SET canonical_job_key=?,published_detail=1 WHERE source_id=? AND external_id=?",
                    (model.identity_key(), model.source_id, str(model.external_id)),
                )
    if fault:
        fault("after_database_commit", str(path))


def _exports(plan):
    seen = set()
    for change in [*plan["changes"], *plan["listing_frames"]]:
        for destination in change["destinations"]:
            path = Path(destination["path"])
            if path in seen:
                continue
            seen.add(path)
            slug = path.name.removesuffix("_jobs.sqlite3")
            paths = source_output_paths(path.parent, slug)
            for name in ("current_json", "current_csv", "history_json", "history_csv"):
                yield {
                    "database": str(path),
                    "path": str(paths[name]),
                    "format": name.rsplit("_", 1)[1],
                    "status": "open" if name.startswith("current") else None,
                    "history_only": slug == "all" and name.startswith("history"),
                }


def _finish_generation(plan, gate, gate_path, deadline_at, fault=None):
    grouped = {}
    for change in plan["changes"]:
        for destination in change["destinations"]:
            grouped.setdefault(destination["path"], []).append((change, destination))
    grouped_frames = {}
    for frame in plan["listing_frames"]:
        for destination in frame["destinations"]:
            grouped_frames.setdefault(destination["path"], []).append((frame, destination))
            grouped.setdefault(destination["path"], [])
    # Sources first, consolidated last. Marker + domain writes share each TX.
    for path in sorted(grouped, key=lambda value: (Path(value).name == "all_jobs.sqlite3", value)):
        if expired(deadline_at):
            return {
                **gate,
                "status": "publication_pending",
                "reason": "deadline_before_database_transaction",
            }
        _apply_database(
            path, grouped[path], plan["generation_id"], fault, frames=grouped_frames.get(path, [])
        )
    gate["database_transactions_complete"] = True
    gate["state"] = "exporting"
    _save(gate_path, gate)
    exported = publish_exports(plan, _exports(plan), deadline_at=deadline_at, fault=fault)
    if not exported["complete"]:
        return {**gate, "status": "publication_pending", "reason": exported["reason"]}
    # Exact body and attachment byte reads after all transactions/exports.
    for path, changes in grouped.items():
        with closing(_ro(path)) as conn:
            for change, destination in changes:
                model = _merged_model(change, destination["before"], plan["generation_id"])
                actual = _row(conn, model.identity_key())
                if (
                    not actual
                    or actual["description"] != change["worker_row"]["description"]
                    or not _receipt_exists(conn, change["publication_key"])
                ):
                    raise ValueError("live_generation_readback_failed")
                for doc in change["documents"]:
                    blob = conn.execute(
                        "SELECT content FROM attachment_blobs WHERE content_sha256=?",
                        (doc["content_sha256"],),
                    ).fetchone()
                    if not blob or hashlib.sha256(blob[0]).hexdigest() != doc["content_sha256"]:
                        raise ValueError("live_document_readback_failed")
                    readback = document_readback(conn, change["worker_row"], doc)
                    if not readback["checks"]["verified"]:
                        raise ValueError(
                            "live_document_association_readback_failed: "
                            + ",".join(readback["errors"])
                        )
    for path, frames in grouped_frames.items():
        with closing(_ro(path)) as conn:
            for frame, _ in frames:
                current = conn.execute(
                    "SELECT frame_sha256,observed_at,generation_id FROM live_listing_frames WHERE source_id=?",
                    (frame["source_id"],),
                ).fetchone()
                count = conn.execute(
                    "SELECT count(*) FROM live_listing_inventory WHERE source_id=? AND observed_in_latest_listing=1",
                    (frame["source_id"],),
                ).fetchone()[0]
                if (
                    not current
                    or tuple(current)
                    != (frame["frame_sha256"], frame["observed_at"], plan["generation_id"])
                    or count != len(frame["jobs"])
                    or not _receipt_exists(conn, frame["publication_key"])
                ):
                    raise ValueError("live_listing_inventory_readback_failed")
    gate.update(
        state="complete",
        status="published",
        completed_at=_now(),
        changed_jobs=len(plan["changes"]),
        published_listing_frames=len(plan["listing_frames"]),
        exports=exported["exports"],
        export_recovery_evidence=exported["legacy_rollback_evidence"],
        completeness_certified=False,
    )
    _save(Path(plan["generation_root"]) / "result.json", gate)
    _save(gate_path, gate)
    return gate


def publish_incremental(
    worker_database,
    registry_path,
    output_dir,
    state_dir,
    *,
    max_jobs=20,
    deadline_at=None,
    execute=False,
    fault=None,
    request_identity=None,
    job_keys=None,
):
    """Publish while the caller holds the shared owner; never acquires it twice.

    A read-only preview does not create files or open writable SQLite handles.
    ``deadline_at`` is a monotonic clock. An individual transaction/export is a
    bounded indivisible unit; the caller must provide a hard outer deadline.
    """
    _validate_targets(worker_database, output_dir)
    gate_path = Path(output_dir).resolve() / GATE_NAME
    gate = json.loads(gate_path.read_text()) if gate_path.exists() else None
    if gate and gate.get("state") != "complete":
        if not execute:
            return {"status": "recovery_required", "gate": gate, "completeness_certified": False}
        plan_path = Path(gate["plan_path"])
        if _sha(plan_path) != gate["plan_sha256"]:
            raise ValueError("recovery_plan_hash_changed")
        plan = json.loads(plan_path.read_text())
        if plan["output_dir"] != str(Path(output_dir).resolve()):
            raise ValueError("recovery_output_mismatch")
        return _finish_generation(plan, gate, gate_path, deadline_at, fault)
    plan = plan_publication(
        worker_database, registry_path, output_dir, max_jobs=max_jobs, deadline_at=deadline_at, job_keys=job_keys
    )
    if not execute:
        return {"status": "preview", "plan": plan}
    if not plan["changes"] and not plan["listing_frames"]:
        return {
            "status": "deferred" if plan["budget_exhausted"] else "no_changes",
            "generation_not_started": True,
            "unchanged": plan["unchanged"],
            "rejected": plan["rejected"],
            "listing_only": plan["listing_only"],
            "budget_exhausted": plan["budget_exhausted"],
            "observation_set_sha256": plan["observation_set_sha256"],
            "completeness_certified": False,
        }
    if expired(deadline_at):
        return {
            "status": "deferred",
            "generation_not_started": True,
            "reason": "preparation_deadline",
            "observation_set_sha256": plan["observation_set_sha256"],
            "completeness_certified": False,
        }
    if request_identity is not None:
        plan["request_identity_sha256"] = request_identity
    prepared, path = _prepare(plan, state_dir)
    if expired(deadline_at):
        return {
            "status": "deferred",
            "generation_not_started": True,
            "reason": "deadline_after_preparation_before_gate",
            "prepared_plan": str(path),
            "observation_set_sha256": plan["observation_set_sha256"],
            "completeness_certified": False,
        }
    gate = {
        "version": VERSION,
        "state": "publishing",
        "generation_id": prepared["generation_id"],
        "started_at": _now(),
        **({"request_identity_sha256": request_identity} if request_identity is not None else {}),
        "plan_path": str(path),
        "plan_sha256": _sha(path),
        "database_transactions_complete": False,
        "completeness_certified": False,
        "observation_set_sha256": prepared["observation_set_sha256"],
    }
    _save(gate_path, gate)
    return _finish_generation(prepared, gate, gate_path, deadline_at, fault)
