"""Read-only evidence survival checks after loss of historical run controls.

A byte/task crosswalk is neither a reconstructed exit nor a completeness,
publication, fresh-provider or retirement certificate. No native state changes.
"""

import gzip
import hashlib
import json
import math
from pathlib import Path
import sqlite3
import time

from jobagg.publication_snapshot import _json_document, stable_file, _record_stamp, _stamp


def _check(deadline):
    if time.monotonic() >= deadline:
        raise TimeoutError("retained_artifact_deadline")


def _control(path, deadline):
    _check(deadline)
    p = Path(path).absolute()
    if p.resolve() != p or p.stat().st_size > 16 * 1024**2:
        raise ValueError("retained_control_aliased_or_oversized")
    return _json_document(p, deadline_at=deadline)


def _capture(reference, source, external, deadline, remaining):
    metadata, record = _control(reference["path"], deadline)
    if record["sha256"] != reference["sha256"]:
        raise ValueError("retained_capture_metadata_changed")
    size = metadata.get("body_bytes")
    if (
        type(size) is not int
        or not 0 <= size <= remaining
        or metadata.get("status_code") != 200
        or metadata.get("body_captured") is not True
        or metadata.get("body_complete") is False
        or metadata.get("storage_limited") is True
    ):
        raise ValueError("retained_capture_incomplete_or_byte_bound")
    binding = metadata.get("source_binding", {})
    if binding.get("source_id", source) != source:
        raise ValueError("retained_capture_source_differs")
    path = Path(metadata["artifact"]).absolute()
    if path.resolve() != path or path.stat().st_size > remaining:
        raise ValueError("retained_body_aliased_or_oversized")
    compressed = stable_file(path, deadline_at=deadline)
    digest, total = hashlib.sha256(), 0
    with gzip.open(path, "rb") as stream:
        while block := stream.read(min(1024**2, remaining + 1 - total)):
            _check(deadline)
            total += len(block)
            if total > remaining:
                raise ValueError("retained_body_decompressed_byte_bound")
            digest.update(block)
    _check(deadline)
    if (
        digest.hexdigest() != metadata.get("body_sha256")
        or total != size
        or tuple(_stamp(path.stat())) != _record_stamp(compressed)
        or tuple(_stamp(Path(reference["path"]).stat())) != _record_stamp(record)
    ):
        raise ValueError("retained_capture_body_changed")
    phase = metadata.get("phase", {})
    return {
        "metadata": record,
        "compressed_body": compressed,
        "body_sha256": digest.hexdigest(),
        "body_bytes": total,
        "identity_bound_detail": phase.get("kind") == "detail"
        and str(phase.get("job_id")) == external,
    }


def assess(conn, attempt_id, *, deadline_at, max_body_bytes=64 * 1024**2):
    if (
        not isinstance(attempt_id, str)
        or not attempt_id
        or type(max_body_bytes) is not int
        or not 0 < max_body_bytes <= 128 * 1024**2
        or not math.isfinite(deadline_at)
    ):
        raise ValueError("finite_exact_retained_artifact_review_required")
    _check(deadline_at)
    cursor = conn.cursor()
    cursor.row_factory = sqlite3.Row
    row = cursor.execute(
        "SELECT * FROM remediation_attempts WHERE attempt_id=?", (attempt_id,)
    ).fetchone()
    if row is None:
        raise ValueError("retained_attempt_unknown")
    row = dict(row)
    result = {
        "attempt_id": attempt_id,
        "source_id": row["source_id"],
        "kind": row["kind"],
        "historical_worker_exit_verified": False,
        "publication_verified": False,
        "completeness_certified": False,
        "authorizes_recovery_or_retirement": False,
    }
    try:
        if row["status"] != "done" or row["finished_at"] is None:
            raise ValueError("retained_attempt_not_completed")
        task = cursor.execute(
            "SELECT source_id,kind,external_id FROM remediation_tasks WHERE task_id=?",
            (row["task_id"],),
        ).fetchone()
        if task is None or any(task[k] != row[k] for k in ("source_id", "kind")):
            raise ValueError("retained_task_crosswalk_differs")
        receipt = json.loads(row["evidence"])
        path_key, hash_key = {
            "detail": ("detail_path", "detail_sha256"),
            "listing": ("frame_path", "frame_sha256"),
            "document": ("manifest", "sha256"),
        }[row["kind"]]
        artifact, record = _control(receipt[path_key], deadline_at)
        if record["sha256"] != receipt[hash_key]:
            raise ValueError("retained_artifact_hash_changed")
        parsed = artifact["job"] if row["kind"] == "detail" else artifact
        if parsed.get("source_id") != row["source_id"]:
            raise ValueError("retained_artifact_source_differs")
        result["artifact"] = record
        if row["kind"] == "detail":
            body = parsed.get("description")
            proof = artifact["proof"]
            if (
                not isinstance(body, str)
                or not body.strip()
                or str(parsed.get("external_id")) != str(task["external_id"])
                or proof.get("source_id") != row["source_id"]
                or str(proof.get("external_id")) != str(task["external_id"])
                or hashlib.sha256(body.encode()).hexdigest()
                != proof.get("parsed_source_text_sha256")
            ):
                raise ValueError("retained_detail_identity_or_text_changed")
            captures = proof.get("captures")
            if not isinstance(captures, list) or not 0 < len(captures) <= 64:
                raise ValueError("retained_capture_inventory_missing_or_unbounded")
            checked, total = [], 0
            for reference in captures:
                entry = _capture(
                    reference,
                    row["source_id"],
                    str(task["external_id"]),
                    deadline_at,
                    max_body_bytes - total,
                )
                checked.append(entry)
                total += entry["body_bytes"]
            if not any(c["identity_bound_detail"] for c in checked):
                raise ValueError("retained_identity_bound_detail_capture_missing")
            result["captures"] = checked
            result["body_bytes_read"] = total
            result["disposition"] = "retained_detail_bytes_and_task_crosswalk_verified"
        else:
            # These bytes are preserved but enumeration/primary-document and
            # destination proofs need their own selected verifier review.
            result["disposition"] = "retained_control_bytes_verified_further_review_required"
        if (
            stable_file(Path(receipt[path_key]), deadline_at=deadline_at)["sha256"]
            != record["sha256"]
        ):
            raise ValueError("retained_artifact_changed_before_receipt")
    except FileNotFoundError as error:
        result.update(
            disposition="missing_evidence",
            reason="referenced_file_missing",
            missing_path=error.filename,
        )
    except (KeyError, TypeError, ValueError, OSError, EOFError) as error:
        result.update(disposition="held", reason=str(error))
    _check(deadline_at)
    return result
