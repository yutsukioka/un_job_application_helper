"""Current fetching health comes from durable attempts and queue state, not job counts."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import time
from urllib.parse import urlsplit
import hashlib


def source_health(conn, source_id, *, now=None, hold=None, max_age=21600):
    now = time.time() if now is None else now
    latest = conn.execute(
        "SELECT status,started_at,finished_at FROM remediation_attempts WHERE source_id=? "
        "ORDER BY started_at DESC,attempt_id DESC LIMIT 1",
        (source_id,),
    ).fetchone()
    success = conn.execute(
        "SELECT max(finished_at) FROM remediation_attempts WHERE source_id=? AND status='done'",
        (source_id,),
    ).fetchone()[0]
    counts = {
        row["status"]: row["n"]
        for row in conn.execute(
            "SELECT status,count(*) n FROM remediation_tasks WHERE source_id=? GROUP BY status",
            (source_id,),
        )
    }
    retry_due = conn.execute(
        "SELECT min(eligible_at) FROM remediation_tasks WHERE source_id=? AND status='pending' AND last_error IS NOT NULL",
        (source_id,),
    ).fetchone()[0]
    if hold:
        status = "held"
    elif any(
        counts.get(key)
        for key in ("blocked", "dead_letter", "interrupted", "listing_detail_conflict")
    ):
        status = "degraded"
    elif retry_due is not None:
        status = "retry_pending"
    elif latest is None:
        status = "not_checked"
    elif latest["finished_at"] is None:
        status = "fetching"
    elif not 0 <= now - latest["finished_at"] <= max_age:
        status = "stale"
    elif latest["status"] == "done":
        status = "ok"
    else:
        status = "degraded"

    def iso(value):
        return (
            datetime.fromtimestamp(value, timezone.utc).isoformat() if value is not None else None
        )

    return {
        "health_status": status,
        "fetch_status": status,
        "observed_at": iso(latest["finished_at"] or latest["started_at"]) if latest else None,
        "last_attempt_status": latest["status"] if latest else None,
        "last_success_at": iso(success),
        "retry_due_at": iso(retry_due),
        "active_hold": bool(hold),
        "task_counts": counts,
        "coverage_status": "incomplete",
        "health_basis": "deterministic_worker_queue",
    }


def read_worker_health(database, *, now=None):
    """Read only; never initialize an absent database or expose capture contents."""
    database = Path(database).resolve(strict=True)
    marker = json.loads((database.parent / "worker_workspace.json").read_text())
    policy = Path(marker["shared_lock"] + ".worker-policy")
    holds = json.loads((policy / "source_holds.json").read_text())
    host_holds = set()
    for path in (policy / "hosts").glob("*.json"):
        state = json.loads(path.read_text())
        if state.get("stopped"):
            host_holds.add(path.stem)
    with sqlite3.connect(database.as_uri() + "?mode=ro", uri=True) as conn:
        conn.row_factory = sqlite3.Row
        result = {}
        for row in conn.execute("SELECT source_id FROM remediation_sources"):
            source = row["source_id"]
            held = bool(holds.get(source)) or bool(
                conn.execute(
                    "SELECT 1 FROM source_circuit_breakers WHERE source_id=? AND state IN ('open','half_open')",
                    (source,),
                ).fetchone()
            )
            # A blocked task already produces degraded health. Inspect pending
            # listing/detail payloads too, so an active host hold takes priority.
            if host_holds:
                for task in conn.execute(
                    "SELECT payload FROM remediation_tasks WHERE source_id=? AND status IN ('pending','blocked')",
                    (source,),
                ):
                    payload = json.loads(task["payload"])
                    listing = payload.get("listing") or {}
                    for key in ("source_url", "apply_url"):
                        host = urlsplit(listing.get(key) or "").hostname
                        if (
                            host
                            and "host-" + hashlib.sha256(host.encode()).hexdigest()[:24]
                            in host_holds
                        ):
                            held = True
            result[source] = source_health(conn, source, now=now, hold=held)
        return result
