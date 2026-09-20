"""Captured listing-frame publication, preserving all existing job detail text.

New listings without accepted details are visible in live_listing_inventory with
published_detail=0. Positive observations update existing row presence; absences
are asserted only for independently complete configured frames. No closure is
inferred. Consumers must also inspect the generation publication gate.
"""

from __future__ import annotations

import json
from pathlib import Path

from jobagg.models import JobRecord
from jobagg.pipelines.inventory_checks import verify_listing


def plan_frames(worker, consolidated, sources, targets, *, limit, deadline_at=None):
    from jobagg.pipelines.live_publication import (
        _capture,
        _digest,
        _epoch,
        _receipt_exists,
        _resolve,
        _ro,
        _sha,
    )
    from contextlib import closing
    import time

    frames, rejected = [], []
    if not worker.execute(
        "SELECT 1 FROM sqlite_master WHERE name='remediation_sources'"
    ).fetchone():
        return frames, rejected
    source_rows = worker.execute(
        "SELECT * FROM remediation_sources WHERE last_list_at IS NOT NULL ORDER BY last_list_at,source_id"
    ).fetchall()
    for source_row in source_rows:
        if len(frames) >= limit or (deadline_at and time.monotonic() >= deadline_at):
            break
        source_id = source_row["source_id"]
        try:
            if source_id not in targets or not targets[source_id].is_file():
                raise ValueError("listing_source_database_unavailable")
            task = worker.execute(
                "SELECT receipt FROM remediation_tasks WHERE source_id=? AND kind='listing' AND status='done'",
                (source_id,),
            ).fetchone()
            if not task:
                continue
            receipt = json.loads(task["receipt"])
            frame_path = Path(receipt["frame_path"])
            if _sha(frame_path) != receipt["frame_sha256"]:
                raise ValueError("listing_frame_hash_mismatch")
            frame = json.loads(frame_path.read_text())
            if (
                frame.get("source_id") != source_id
                or _epoch(frame["observed_at"]) != source_row["last_list_at"]
            ):
                raise ValueError("listing_frame_source_or_time_mismatch")
            if _epoch(frame["observed_at"]) > time.time() + 300:
                raise ValueError("listing_frame_clock_in_future")
            jobs = []
            for item in frame["jobs"]:
                if item.get("source_id") != source_id or not item.get("external_id"):
                    raise ValueError("listing_native_identity_missing_or_wrong_source")
                record = JobRecord(
                    source_id=source_id,
                    org_id=item["org_id"],
                    ats_family=item["ats_family"],
                    title=item["title"],
                    apply_url=item["apply_url"],
                    external_id=item["external_id"],
                    raw=item.get("raw", {}),
                )
                jobs.append((record.identity_key(), item))
            keys = [key for key, _ in jobs]
            if len(keys) != len(set(keys)) or set(keys) != set(
                json.loads(source_row["listing_ids"])
            ):
                raise ValueError("listing_saved_population_mismatch")
            proof = json.loads(source_row["listing_proof"])
            if proof != receipt.get("enumeration"):
                raise ValueError("listing_enumeration_proof_mismatch")
            capture_paths = sorted((frame_path.parent / "http").glob("*.json"))
            meta = [_capture(path) for path in capture_paths]
            if not any(item and item.get("phase", {}).get("kind") == "listing" for item in meta):
                raise ValueError("listing_successful_capture_missing")
            # Re-run the independent configured-scope census before allowing
            # absence updates. Unsupported families remain positive-only.
            records = [
                JobRecord(
                    source_id=source_id,
                    org_id=item["org_id"],
                    ats_family=item["ats_family"],
                    title=item["title"],
                    apply_url=item["apply_url"],
                    external_id=item["external_id"],
                    raw=item.get("raw", {}),
                )
                for _, item in jobs
            ]
            verified = verify_listing(sources[source_id], records, capture_paths)
            complete = proof.get("complete") is True and verified.get("complete") is True
            key = "listing:" + _digest(
                {"source": source_id, "frame_sha256": receipt["frame_sha256"], "proof": proof}
            )
            target = targets[source_id]
            with closing(_ro(target)) as source_conn:
                if _receipt_exists(source_conn, key) and _receipt_exists(consolidated, key):
                    continue
                destinations = []
                for conn, path in (
                    (source_conn, target),
                    (
                        consolidated,
                        Path(consolidated.execute("PRAGMA database_list").fetchone()[2]),
                    ),
                ):
                    exists = conn.execute(
                        "SELECT 1 FROM sqlite_master WHERE name='live_listing_frames'"
                    ).fetchone()
                    previous = (
                        conn.execute(
                            "SELECT observed_at FROM live_listing_frames WHERE source_id=?",
                            (source_id,),
                        ).fetchone()
                        if exists
                        else None
                    )
                    if previous and _epoch(previous[0]) > _epoch(frame["observed_at"]):
                        raise ValueError("listing_frame_older_than_published_frame")
                    mapping = {}
                    for job_key, item in jobs:
                        row = {
                            "job_key": job_key,
                            "source_id": source_id,
                            "external_id": item["external_id"],
                        }
                        live = _resolve(conn, row)
                        mapping[job_key] = live["job_key"] if live else None
                    before = []
                    for live in conn.execute(
                        "SELECT job_key,raw_json,source_listed_current,trusted_current FROM jobs WHERE source_id=?",
                        (source_id,),
                    ):
                        if complete or live["job_key"] in set(mapping.values()):
                            before.append(dict(live))
                    destinations.append(
                        {"path": str(path), "mapping": mapping, "before_listing_rows": before}
                    )
                frames.append(
                    {
                        "publication_key": key,
                        "source_id": source_id,
                        "observed_at": frame["observed_at"],
                        "frame_path": str(frame_path),
                        "frame_sha256": receipt["frame_sha256"],
                        "inventory_complete": complete,
                        "proof": proof,
                        "jobs": [{"job_key": job_key, **item} for job_key, item in jobs],
                        "destinations": destinations,
                    }
                )
        except (ValueError, KeyError, OSError) as exc:
            rejected.append({"source_id": source_id, "reason": str(exc)})
    return frames, rejected


def apply_frames(conn, frames, generation, *, changed_keys=()):
    from jobagg.pipelines.live_publication import _JOURNAL, _dump, _now, _receipt_exists

    conn.execute(
        """CREATE TABLE IF NOT EXISTS live_listing_frames(source_id TEXT PRIMARY KEY,observed_at TEXT NOT NULL,frame_sha256 TEXT NOT NULL,inventory_complete INTEGER NOT NULL,observed_count INTEGER NOT NULL,proof_json TEXT NOT NULL,generation_id TEXT NOT NULL)"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS live_listing_inventory(source_id TEXT NOT NULL,external_id TEXT NOT NULL,worker_job_key TEXT NOT NULL,canonical_job_key TEXT,title TEXT NOT NULL,apply_url TEXT NOT NULL,observed_at TEXT NOT NULL,observed_in_latest_listing INTEGER,inventory_complete INTEGER NOT NULL,published_detail INTEGER NOT NULL,listing_json TEXT NOT NULL,generation_id TEXT NOT NULL,PRIMARY KEY(source_id,external_id))"""
    )
    for frame, destination in frames:
        if _receipt_exists(conn, frame["publication_key"]):
            continue
        for before in destination["before_listing_rows"]:
            actual = conn.execute(
                "SELECT job_key,raw_json,source_listed_current,trusted_current FROM jobs WHERE job_key=?",
                (before["job_key"],),
            ).fetchone()
            if before["job_key"] not in changed_keys and (not actual or dict(actual) != before):
                raise ValueError("live_listing_row_preimage_changed")
        # NULL means absent from an incomplete frame: public absence is unknown.
        conn.execute(
            "UPDATE live_listing_inventory SET observed_in_latest_listing=?,inventory_complete=?,generation_id=? WHERE source_id=?",
            (
                0 if frame["inventory_complete"] else None,
                int(frame["inventory_complete"]),
                generation,
                frame["source_id"],
            ),
        )
        observed_live = set()
        for item in frame["jobs"]:
            canonical = destination["mapping"].get(item["job_key"])
            if canonical is None:
                match = conn.execute(
                    "SELECT job_key,description FROM jobs WHERE source_id=? AND external_id=?",
                    (frame["source_id"], str(item["external_id"])),
                ).fetchall()
                canonical = match[0]["job_key"] if len(match) == 1 else None
            row = (
                conn.execute(
                    "SELECT description FROM jobs WHERE job_key=?", (canonical,)
                ).fetchone()
                if canonical
                else None
            )
            if canonical:
                observed_live.add(canonical)
            conn.execute(
                "INSERT OR REPLACE INTO live_listing_inventory VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    frame["source_id"],
                    str(item["external_id"]),
                    item["job_key"],
                    canonical,
                    item["title"],
                    item["apply_url"],
                    frame["observed_at"],
                    1,
                    int(frame["inventory_complete"]),
                    int(bool(row and row[0])),
                    _dump(item),
                    generation,
                ),
            )
        for row in conn.execute(
            "SELECT job_key,raw_json FROM jobs WHERE source_id=?", (frame["source_id"],)
        ).fetchall():
            present = row["job_key"] in observed_live
            if not present and not frame["inventory_complete"]:
                continue
            raw = json.loads(row["raw_json"] or "{}")
            raw["_jobagg_listing_verification"] = {
                "source_id": frame["source_id"],
                "observed_at": frame["observed_at"],
                "observed_in_latest_listing": present,
                "inventory_complete": frame["inventory_complete"],
                "evidence_ref": frame["frame_path"],
                "frame_sha256": frame["frame_sha256"],
                "generation_id": generation,
            }
            conn.execute(
                "UPDATE jobs SET raw_json=?,source_listed_current=?,trusted_current=CASE WHEN ?=0 THEN 0 ELSE trusted_current END WHERE job_key=?",
                (_dump(raw), int(present), int(present), row["job_key"]),
            )
        conn.execute(
            "INSERT OR REPLACE INTO live_listing_frames VALUES(?,?,?,?,?,?,?)",
            (
                frame["source_id"],
                frame["observed_at"],
                frame["frame_sha256"],
                int(frame["inventory_complete"]),
                len(frame["jobs"]),
                _dump(frame["proof"]),
                generation,
            ),
        )
        conn.execute(
            f"INSERT INTO {_JOURNAL} VALUES(?,?,?,?)",
            (frame["publication_key"], generation, "source:" + frame["source_id"], _now()),
        )
