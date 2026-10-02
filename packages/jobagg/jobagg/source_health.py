"""Current fetching health comes from durable attempts and queue state, not job counts."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import time
from urllib.parse import urlsplit
import hashlib
import math
from collections import Counter

from jobagg.pipelines.host_recovery import DEFAULT_RECOVERY_POLICY, host_eligibility
from jobagg.pipelines.sync_source import fetch_schedule_policy


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
        # Published/older generations remain readable without running a writer
        # migration or changing their immutable workspace binding.
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(remediation_sources)")}
        if "host" in columns:
            sources = conn.execute("SELECT source_id,host FROM remediation_sources")
        else:
            sources = conn.execute("SELECT source_id,NULL AS host FROM remediation_sources")
        result = {}
        for row in sources:
            source = row["source_id"]
            held = bool(holds.get(source)) or bool(
                conn.execute(
                    "SELECT 1 FROM source_circuit_breakers WHERE source_id=? AND state IN ('open','half_open')",
                    (source,),
                ).fetchone()
            )
            # The first request can use a different host from public listing
            # URLs. Check it even with an empty or fully completed task queue.
            # Pending/blocked documents can also use independent hosts.
            if host_holds:
                hosts = {row["host"]} if row["host"] else set()
                for task in conn.execute(
                    "SELECT payload FROM remediation_tasks WHERE source_id=? AND status IN ('pending','blocked')",
                    (source,),
                ):
                    payload = json.loads(task["payload"])
                    listing = payload.get("listing") or {}
                    document = payload.get("document") or {}
                    candidates = (
                        payload.get("source_url"), payload.get("apply_url"), payload.get("url"),
                        listing.get("source_url"), listing.get("apply_url"), document.get("url"),
                    )
                    hosts.update(urlsplit(value).hostname for value in candidates if value)
                held = held or any(
                    "host-" + hashlib.sha256(host.encode()).hexdigest()[:24] in host_holds
                    for host in hosts if host
                )
            result[source] = source_health(conn, source, now=now, hold=held)
        return result


def listing_recovery_pending(listing_state, host_state):
    """An audited release must obtain a fresh complete listing before details."""
    review = host_state.get("reviewed_source_recovery") or {}
    after = review.get("requires_listing_after")
    if after is None:
        return False
    if isinstance(after, bool) or not isinstance(after, (int, float)) or not math.isfinite(after) or after <= 0:
        raise ValueError("Malformed reviewed listing recovery time")
    listing_state = dict(listing_state or {})
    proof = json.loads(listing_state.get("listing_proof") or "{}")
    return (listing_state.get("last_list_at") is None or listing_state["last_list_at"] <= after
            or proof.get("complete") is not True)


def worker_source_health(worker, conn, source, listing_state, *, now, source_hold=None,
                  events=(), host_cache=None):
    """Describe the same persisted exclusions and due floors used by Worker.

    ``events`` must come from SharedPolicy.event_snapshot() once per report;
    passing it to policy_due avoids repeatedly scanning global attempt history.
    ``host_cache`` can likewise be shared across all sources in that snapshot.
    """
    cache = {} if host_cache is None else host_cache
    hosts = {}
    policy_due = {}
    kind_counts = {}
    pending_times = []
    source_reasons = []
    if source_hold:
        source_reasons.append("source_policy_hold")
    circuit = conn.execute(
        "SELECT state FROM source_circuit_breakers WHERE source_id=? AND state IN('open','half_open')",
        (source.id,),
    ).fetchone()
    if circuit:
        source_reasons.append("source_circuit_" + circuit["state"])

    def host_info(task):
        host = worker.task_host(task)
        if host not in cache:
            cache[host] = worker.host_state(host)
        state = cache[host]
        admission = host_eligibility(state, now)
        if host not in hosts:
            recovery = state.get("recovery")
            recent = [stamp for stamp in (recovery or {}).get("probe_attempts", [])
                      if stamp > now - DEFAULT_RECOVERY_POLICY.probe_window_seconds]
            hosts[host] = {
                "host": host,
                "stopped": state.get("stopped", False),
                "reason": state.get("reason"),
                "failure_category": state.get("failure_category"),
                "eligibility": admission["category"],
                "next_permitted_attempt_at": None if admission["category"] == "review"
                else max(now, admission["eligible_at"]),
                "recovery": None if recovery is None else {
                    "phase": recovery["phase"],
                    "failure_kind": recovery["failure_kind"],
                    "cooldown_until": recovery["eligible_at"],
                    "probe_until": recovery.get("probe_until"),
                    "recent_probe_count": len(recent),
                    "max_probes_per_window": DEFAULT_RECOVERY_POLICY.max_probes_per_window,
                    "probe_window_seconds": DEFAULT_RECOVERY_POLICY.probe_window_seconds,
                    "probe_budget_reset_at": recent[0] + DEFAULT_RECOVERY_POLICY.probe_window_seconds
                    if len(recent) >= DEFAULT_RECOVERY_POLICY.max_probes_per_window else None,
                },
            }
        return host, admission

    def admission_for(task, *, due):
        host, admission = host_info(task)
        reasons = list(source_reasons)
        if task["kind"] != "listing" and listing_recovery_pending(listing_state, cache[host]):
            reasons.append("awaiting_recovery_listing")
        if admission["category"] == "review":
            reasons.append("host_review_hold")
        if reasons:
            return {"host": host, "eligible": False, "next_permitted_attempt_at": None,
                    "reasons": reasons}
        kind = task["kind"]
        if kind not in policy_due:
            policy_due[kind] = worker.policy_due(source, kind, now, attempts=events)
        effective = max(now, due, admission["eligible_at"], policy_due[kind])
        if due > now:
            reasons.append("task_cooldown" if task["status"] == "pending" else "listing_schedule")
        if admission["eligible_at"] > now:
            reasons.append("host_cooldown")
            recovery = hosts[host]["recovery"]
            if recovery and recovery["probe_budget_reset_at"]:
                reasons.append("host_probe_budget")
        if policy_due[kind] > now:
            reasons.append("source_pacing_or_quota")
        return {"host": host, "eligible": effective <= now,
                "next_permitted_attempt_at": effective, "reasons": reasons}

    for row in conn.execute(
        "SELECT kind,status,count(*) AS n FROM remediation_tasks WHERE source_id=? GROUP BY kind,status",
        (source.id,),
    ):
        kind_counts.setdefault(row["kind"], {"states": {}, "eligible_pending": 0,
                                            "deferred_pending": 0, "held_pending": 0,
                                            "out_of_scope_pending": 0})["states"][row["status"]] = row["n"]
    rows = [dict(row) for row in conn.execute(
        "SELECT kind,status,eligible_at,payload,last_error FROM remediation_tasks "
        "WHERE source_id=? AND (status='pending' OR kind='listing')", (source.id,),
    )]
    pending_reasons = Counter()
    listing_task = None
    for task in rows:
        task["source_id"] = source.id
        if task["kind"] == "listing":
            listing_task = task
        if task["status"] != "pending":
            continue
        counts = kind_counts[task["kind"]]
        if task["kind"] == "document" and source.extra.get("fetch_attachments", True) is False:
            counts["out_of_scope_pending"] += 1
            continue
        admission = admission_for(task, due=task["eligible_at"])
        pending_reasons.update(admission["reasons"])
        if admission["eligible"]:
            counts["eligible_pending"] += 1
        elif admission["next_permitted_attempt_at"] is None:
            counts["held_pending"] += 1
        else:
            counts["deferred_pending"] += 1
        if admission["next_permitted_attempt_at"] is not None:
            pending_times.append(admission["next_permitted_attempt_at"])

    # A done listing becomes pending when seed_listings reaches next_list_at.
    # Blocked/interrupted tasks are preserved by enqueue and need review.
    task = listing_task or {"source_id": source.id, "kind": "listing", "status": "not_seeded", "payload": "{}"}
    status = task["status"]
    if status == "pending":
        listing = admission_for(task, due=task["eligible_at"])
    elif status in {"blocked", "interrupted", "inflight", "unavailable_pending_inventory", "listing_detail_conflict"}:
        host, _ = host_info(task)
        listing = {"host": host, "eligible": False, "next_permitted_attempt_at": None,
                   "reasons": ["listing_" + status, *source_reasons]}
        if hosts[host]["stopped"]:
            listing["reasons"].append("host_review_hold")
    else:
        listing = admission_for(task, due=listing_state["next_list_at"])
    listing.update(task_status=status, last_error=task.get("last_error"))
    last = listing_state["last_list_at"]
    interval = float(fetch_schedule_policy(source)["list_fetch_interval_minutes"]) * 60
    age = max(0, now - last) if last is not None else None
    stale = last is None or age >= interval
    return {
        "schema_version": 1,
        "observed_at": now,
        "listing_stale": stale,
        "listing_inventory_complete": json.loads(dict(listing_state).get("listing_proof") or "{}").get("complete") is True,
        "last_listing_observed_at": last,
        "listing_age_seconds": age,
        "listing_interval_seconds": interval,
        "listing_refresh_due_at": last + interval if last is not None else None,
        "next_listing_seed_at": listing_state["next_list_at"],
        "listing": listing,
        "task_eligibility": kind_counts,
        "eligible_pending_tasks": sum(counts["eligible_pending"] for counts in kind_counts.values()),
        "next_pending_attempt_at": min(pending_times) if pending_times else None,
        "pending_exclusion_reasons": dict(pending_reasons),
        "source_policy_hold": source_hold,
        "source_circuit": circuit["state"] if circuit else None,
        "hosts": list(hosts.values()),
        "scope": "Persisted task admission snapshot; request pacing, redirects, run budget and dispatch fairness are rechecked at execution.",
    }
