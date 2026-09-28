"""Read published listing observations without promoting them to full job details."""

from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
import json
import re
from pathlib import Path
import sqlite3
from urllib.parse import urlencode

_REQUIRED = {
    "live_listing_frames": {
        "source_id",
        "observed_at",
        "frame_sha256",
        "inventory_complete",
        "observed_count",
        "proof_json",
        "generation_id",
    },
    "live_listing_inventory": {
        "source_id",
        "external_id",
        "worker_job_key",
        "canonical_job_key",
        "title",
        "apply_url",
        "observed_at",
        "observed_in_latest_listing",
        "inventory_complete",
        "published_detail",
        "listing_json",
        "generation_id",
    },
    "jobs": {"job_key", "source_id", "external_id", "description", "org_id"},
}


def _unavailable(reason, limit, offset):
    return {
        "available": False,
        "reason": reason,
        "total": None,
        "counts": None,
        "sources": [],
        "listings": [],
        "limit": limit,
        "offset": offset,
        "next_offset": None,
        "completeness_certified": False,
    }


def _clock(value, now):
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            raise ValueError("Timezone missing")
        age = (now - stamp).total_seconds()
        return (None, "future_observation") if age < 0 else (age, "observed")
    except (TypeError, ValueError, AttributeError):
        return None, "invalid_observation_time"


def _public_evidence(raw):
    """Project typed facts and hashes only; internal paths/errors stay private."""
    proof = json.loads(raw)
    if not isinstance(proof, dict):
        return {}
    result = {}
    for key in ("complete", "verified_zero"):
        if type(proof.get(key)) is bool:
            result[key] = proof[key]
    for key in ("observed_count", "reported_total", "page_count"):
        if type(proof.get(key)) is int and proof[key] >= 0:
            result[key] = proof[key]
    method = proof.get("method")
    if isinstance(method, str) and re.fullmatch(r"[a-zA-Z0-9_]{1,80}", method):
        result["method"] = method
    captures = proof.get("capture_paths")
    if isinstance(captures, list):
        result["capture_sha256"] = [
            entry["sha256"] for entry in captures
            if isinstance(entry, dict) and isinstance(entry.get("sha256"), str)
            and re.fullmatch(r"[a-fA-F0-9]{64}", entry["sha256"])
        ]
    return result


def listing_inventory(
    database, *, source=None, pending_only=False, limit=100, offset=0, now=None
):
    """Return latest-frame positives; report unknown/absent historical rows separately.

    ``published_detail`` means text exists in the live job store. It does not
    certify that every current public field or attachment has been captured.
    All counts and page rows share one read-only SQLite snapshot.
    """
    if (
        type(limit) is not int
        or not 1 <= limit <= 1000
        or type(offset) is not int
        or offset < 0
    ):
        raise ValueError("Use a limit of 1–1000 and a nonnegative offset")
    if source is not None and (
        not isinstance(source, str) or not 1 <= len(source) <= 200
    ):
        raise ValueError("Source must be an exact nonempty source ID")
    path = Path(database).resolve()
    if not path.is_file():
        return _unavailable("listing_database_missing", limit, offset)
    now = now or datetime.now(timezone.utc)
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("BEGIN")
        for table, expected in _REQUIRED.items():
            actual = {
                row["name"] for row in conn.execute(f"PRAGMA table_info({table})")
            }
            if not expected.issubset(actual):
                return _unavailable(
                    "listing_publication_schema_unavailable", limit, offset
                )
        # Optional filters remain bound parameters, never SQL fragments.
        params = [source, source]
        count_row = conn.execute(
            """SELECT count(*) AS tracked,
            COALESCE(sum(observed_in_latest_listing=1),0) AS observed_current,
            COALESCE(sum(observed_in_latest_listing=1 AND published_detail=0),0) AS detail_pending,
            COALESCE(sum(observed_in_latest_listing=1 AND published_detail=1),0) AS detail_published,
            COALESCE(sum(observed_in_latest_listing=0),0) AS absent_from_complete_frame,
            COALESCE(sum(observed_in_latest_listing IS NULL),0) AS presence_unknown
            FROM live_listing_inventory WHERE (? IS NULL OR source_id=?)""",
            params,
        ).fetchone()
        frames = [
            dict(row)
            for row in conn.execute(
                "SELECT * FROM live_listing_frames WHERE (? IS NULL OR source_id=?) ORDER BY source_id", params
            )
        ]
        rows = conn.execute(
            """SELECT i.*,
            j.job_key AS matched_job_key, j.org_id AS matched_org,
            CASE WHEN length(trim(COALESCE(j.description,'')))>0 THEN 1 ELSE 0 END AS matched_text
            FROM live_listing_inventory i
            LEFT JOIN jobs j ON j.job_key=i.canonical_job_key
                AND j.source_id=i.source_id AND j.external_id=i.external_id
            WHERE i.observed_in_latest_listing=1
                AND (? IS NULL OR i.source_id=?)
                AND (?=0 OR i.published_detail=0)
            ORDER BY i.source_id,i.external_id LIMIT ? OFFSET ?""",
            [*params, int(pending_only), limit, offset],
        ).fetchall()
        per_source = {
            row["source_id"]: dict(row)
            for row in conn.execute(
                """SELECT source_id,
                COALESCE(sum(observed_in_latest_listing=1),0) AS observed_current,
                COALESCE(sum(observed_in_latest_listing=1 AND published_detail=0),0) AS detail_pending,
                COALESCE(sum(observed_in_latest_listing=1 AND published_detail=1),0) AS detail_published,
                COALESCE(sum(observed_in_latest_listing IS NULL),0) AS presence_unknown
                FROM live_listing_inventory WHERE (? IS NULL OR source_id=?) GROUP BY source_id""",
                params,
            )
        }
        listings = []
        for row in rows:
            listing = json.loads(row["listing_json"])
            matched = bool(row["matched_job_key"] and row["matched_text"])
            declared = row["published_detail"] == 1
            listings.append(
                {
                    "source_id": row["source_id"],
                    "source_name": row["matched_org"] or listing.get("org_id"),
                    "external_id": row["external_id"],
                    "worker_job_key": row["worker_job_key"],
                    "canonical_job_key": row["matched_job_key"],
                    "title": row["title"],
                    "apply_url": row["apply_url"],
                    "observed_at": row["observed_at"],
                    "observed_in_latest_listing": True,
                    "inventory_complete": row["inventory_complete"] == 1,
                    "published_detail": declared,
                    "detail_available": matched,
                    "detail_status": (
                        "available_unverified"
                        if declared
                        else "retained_text_available"
                    )
                    if matched
                    else ("publication_binding_conflict" if declared else "pending"),
                    "detail_url": "/api/job-detail?"
                    + urlencode({"job_key": row["matched_job_key"]})
                    if matched
                    else None,
                    "generation_id": row["generation_id"],
                    "completeness_certified": False,
                }
            )
        sources = []
        for frame in frames:
            age, clock_status = _clock(frame["observed_at"], now)
            counts = per_source.get(frame["source_id"], {})
            sources.append(
                {
                    "source_id": frame["source_id"],
                    "observed_at": frame["observed_at"],
                    "age_seconds": age,
                    "observation_clock_status": clock_status,
                    "freshness_certified": False,
                    "inventory_complete": frame["inventory_complete"] == 1,
                    "observed_count": frame["observed_count"],
                    "current_inventory_rows": counts.get("observed_current", 0),
                    "detail_pending": counts.get("detail_pending", 0),
                    "detail_published": counts.get("detail_published", 0),
                    "presence_unknown": counts.get("presence_unknown", 0),
                    "frame_sha256": frame["frame_sha256"],
                    "evidence": _public_evidence(frame["proof_json"]),
                    "generation_id": frame["generation_id"],
                    "completeness_certified": False,
                }
            )
        total = count_row["detail_pending" if pending_only else "observed_current"]
        return {
            "available": True,
            "source": source,
            "pending_only": pending_only,
            "total": total,
            "counts": dict(count_row),
            "sources": sources,
            "listings": listings,
            "limit": limit,
            "offset": offset,
            "next_offset": offset + len(listings)
            if offset + len(listings) < total
            else None,
            "scope": "Observed listings in each source's latest published frame; absent from an incomplete frame means unknown.",
            "detail_scope": "Published text availability is separate from full public text and attachment verification.",
            "completeness_certified": False,
        }
