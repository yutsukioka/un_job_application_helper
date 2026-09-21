"""Identity-bound original-byte and extracted-text readback for publication."""

from __future__ import annotations

import hashlib
import json
import sqlite3


def digest(text):
    return hashlib.sha256((text or "").encode()).hexdigest()


def _blob_matches(conn, content_sha, cache):
    if content_sha not in cache:
        row = conn.execute(
            "SELECT content,size_bytes FROM attachment_blobs WHERE content_sha256=?", (content_sha,)
        ).fetchone()
        cache[content_sha] = bool(
            row
            and hashlib.sha256(row["content"]).hexdigest() == content_sha
            and len(row["content"]) == row["size_bytes"]
        )
    return cache[content_sha]


def document_readback(conn, job, document, blob_cache=None):
    """Verify one job/document association; a shared blob alone is insufficient."""
    checks = {
        "blob_hash_match": False,
        "association_match": False,
        "extracted_text_match": False,
        "parent_binding_match": False,
        "metadata_match": False,
        "raw_association_match": False,
        "verified": False,
    }
    result = {"checks": checks, "resolved_job_key": None, "errors": []}
    if conn is None:
        result["errors"].append("database_unavailable_or_not_requested")
        return result
    try:
        content_sha = document["content_sha256"]
        checks["blob_hash_match"] = _blob_matches(
            conn, content_sha, blob_cache if blob_cache is not None else {}
        )
        from jobagg.pipelines.live_publication import _resolve

        published = _resolve(conn, dict(job))
        if published is None:
            result["errors"].append("live_job_missing")
            return result
        key = published["job_key"]
        result["resolved_job_key"] = key
        identifier = hashlib.sha256(
            (key + "\n" + document["url"] + "\n" + content_sha).encode()
        ).hexdigest()
        association = conn.execute(
            "SELECT * FROM job_attachments WHERE attachment_id=? AND job_key=? "
            "AND source_id=? AND url=? AND content_sha256=?",
            (identifier, key, job["source_id"], document["url"], content_sha),
        ).fetchone()
        if association is None:
            result["errors"].append("live_document_association_missing")
            return result
        checks["association_match"] = True
        if (
            association["category"] != "unresolved"
            or association["required_for_complete_text"] != 1
        ):
            result["errors"].append("live_document_purpose_conflict_requires_review")
        metadata = json.loads(association["metadata_json"] or "{}")
        checks["extracted_text_match"] = association["extracted_text"] == document.get(
            "extracted_text", ""
        )
        checks["parent_binding_match"] = (
            metadata.get("parent_description_sha256")
            == document["parent_description_sha256"]
            == digest(published["description"])
        )
        # Publication changes these transport/association fields deliberately.
        # Every other captured metadata field must survive unchanged.
        rewritten = {
            "attachment_id",
            "job_key",
            "prepared_binary_path",
            "binary_ref",
            "status",
            "whole_job_complete",
            "fidelity_complete",
        }
        checks["metadata_match"] = (
            metadata.get("attachment_id") == identifier
            and metadata.get("job_key") == key
            and metadata.get("binary_ref")
            == {"table": "attachment_blobs", "key": content_sha, "column": "content"}
            and metadata.get("status") == association["status"] == "captured_unverified"
            and metadata.get("whole_job_complete") is False
            and metadata.get("fidelity_complete") is False
            and association["category"] == "unresolved"
            and association["required_for_complete_text"] == 1
            and association["final_url"] == document.get("final_url")
            and association["label"] == document.get("label")
            and all(
                metadata.get(field) == value
                for field, value in document.items()
                if field not in rewritten
            )
        )
        raw_documents = json.loads(published.get("raw_json") or "{}").get("attachments", [])
        checks["raw_association_match"] = (
            isinstance(raw_documents, list) and metadata in raw_documents
        )
        checks["verified"] = all(value for name, value in checks.items() if name != "verified")
        result["errors"].extend(
            name for name, value in checks.items() if not value and name != "verified"
        )
    except (KeyError, TypeError, ValueError, sqlite3.DatabaseError) as exc:
        result["errors"].append(type(exc).__name__ + ": " + str(exc))
    return result
