"""M1 atomic dirty versions and bounded dependency admission.

This is the minimal publication authority, independent of storage compression.
Every affected unit is versioned in the same SQL transaction as its mutation.
Acknowledgement is conditional on the selected version; N cannot clear N+1.
Unknown operations remain held rather than turning a zero-task tick into no-op.
"""

import hashlib
import json
from pathlib import Path
import sqlite3
import time

SCHEMA = "publication-work-1"
EXCLUDED = {
    "jobagg_runtime_state",
    "semantic_history_state",
    "remediation_task_counts",
    "jobagg_task_headers",
    "jobagg_kind_service",
    "jobagg_source_hosts",
    "jobagg_header_contract",
    "jobagg_evidence_backfill",
}


def exists(conn):
    return bool(
        conn.execute("SELECT 1 FROM sqlite_master WHERE name='jobagg_dirty_state'").fetchone()
    )


def _dump(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def configure(conn):
    def units(table, payload):
        row = json.loads(payload)
        source = row.get("source_id")
        job = row.get("job_key")
        if row.get("_operation") == "DELETE":
            return _dump(
                [
                    {
                        "unit": "deleted:"
                        + table
                        + ":"
                        + str(job or source or row.get("task_id") or row.get("content_sha256")),
                        "kind": "unknown",
                        "source": source or "*",
                        "job": job,
                    }
                ]
            )
        if job and not source:
            match = conn.execute("SELECT source_id FROM jobs WHERE job_key=?", (job,)).fetchone()
            source = match[0] if match else None
        if job and source:
            return _dump([{"unit": "job:" + job, "kind": "job", "source": source, "job": job}])
        if table == "attachment_blobs":
            rows = conn.execute(
                "SELECT job_key FROM remediation_documents WHERE content_sha256=?",
                (row["content_sha256"],),
            ).fetchall()
            result = []
            for item in rows:
                match = conn.execute(
                    "SELECT source_id FROM jobs WHERE job_key=?", (item[0],)
                ).fetchone()
                if match:
                    result.append(
                        {
                            "unit": "job:" + item[0],
                            "kind": "job",
                            "source": match[0],
                            "job": item[0],
                        }
                    )
            if result:
                return _dump(result)
        if source:
            listing = table == "remediation_sources" and row.get("listing_changed", True)
            return _dump(
                [
                    {
                        "unit": ("listing:" if listing else "health:") + source,
                        "kind": "listing" if listing else "health",
                        "source": source,
                        "job": None,
                    }
                ]
            )
        return _dump([{"unit": "unknown:" + table, "kind": "unknown", "source": "*", "job": None}])

    conn.create_function("jobagg_dirty_units", 2, units)


def prepare(conn, *, contract_sha256, epoch):
    """Explicit staged schema preparation before runtime fence activation."""
    if exists(conn):
        return
    configure(conn)
    conn.execute(
        "CREATE TABLE jobagg_dirty_state(id INTEGER PRIMARY KEY,schema TEXT,epoch TEXT,contract_sha256 TEXT,next_transition_at REAL,bootstrap_complete INTEGER)"
    )
    conn.execute(
        "INSERT INTO jobagg_dirty_state VALUES(1,?,?,?,NULL,0)", (SCHEMA, epoch, contract_sha256)
    )
    conn.execute(
        "CREATE TABLE jobagg_dirty_versions(version INTEGER PRIMARY KEY AUTOINCREMENT,unit TEXT,source_id TEXT,job_key TEXT,kind TEXT,changed_at REAL,operation TEXT)"
    )
    conn.execute(
        "CREATE TABLE jobagg_dirty_current(unit TEXT PRIMARY KEY,version INTEGER,source_id TEXT,job_key TEXT,kind TEXT,first_changed_at REAL,last_changed_at REAL,disposition TEXT,reason TEXT,acknowledgement_sha256 TEXT)"
    )
    conn.execute(
        "CREATE INDEX jobagg_dirty_pending ON jobagg_dirty_current(disposition,first_changed_at,unit)"
    )
    conn.execute(
        "CREATE INDEX jobagg_dirty_source ON jobagg_dirty_current(source_id,disposition,first_changed_at,unit)"
    )
    conn.execute("CREATE INDEX jobagg_dirty_version_order ON jobagg_dirty_current(version)")
    conn.execute("CREATE TABLE jobagg_dirty_bootstrap(unit TEXT PRIMARY KEY)")
    conn.execute(
        "CREATE TABLE jobagg_dirty_cohorts(source_id TEXT PRIMARY KEY,last_service INTEGER NOT NULL)"
    )
    conn.execute(
        "CREATE TABLE jobagg_dirty_transitions(object_key TEXT PRIMARY KEY,source_id TEXT,job_key TEXT,due_at REAL,fired INTEGER)"
    )
    conn.execute(
        "CREATE INDEX jobagg_dirty_due ON jobagg_dirty_transitions(fired,due_at,object_key)"
    )
    for operation in ("INSERT", "UPDATE"):
        conn.execute(f"""CREATE TRIGGER jobagg_dirty_closing_{operation.lower()} AFTER {operation} ON jobs BEGIN
            INSERT INTO jobagg_dirty_transitions VALUES('closing:'||new.job_key,new.source_id,new.job_key,
                (julianday(new.closes_at)-2440587.5)*86400,0)
            ON CONFLICT(object_key) DO UPDATE SET source_id=excluded.source_id,due_at=excluded.due_at,
                fired=CASE WHEN due_at IS excluded.due_at THEN fired ELSE 0 END; END""")
    tables = [
        row[0]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
        if not row[0].startswith(("sqlite_", "jobagg_dirty_")) and row[0] not in EXCLUDED
    ]
    excluded = {
        row[1] for row in conn.execute("PRAGMA table_list") if row[2] in {"virtual", "shadow"}
    }
    for table in tables:
        if table in excluded:
            continue
        cols = [row[1] for row in conn.execute('PRAGMA table_info("' + table + '")')]
        for op in ("INSERT", "UPDATE", "DELETE"):
            prefix = "old" if op == "DELETE" else "new"
            fields = ",".join(
                "'" + col.replace("'", "''") + "'," + prefix + '."' + col + '"'
                for col in cols
                if col in {"source_id", "job_key", "content_sha256", "task_id"}
            )
            if table == "remediation_sources":
                changed = (
                    "1"
                    if op != "UPDATE"
                    else "(new.last_list_at IS NOT old.last_list_at OR new.listing_ids IS NOT old.listing_ids OR new.listing_proof IS NOT old.listing_proof)"
                )
                fields += ",'listing_changed'," + changed
            fields += ("," if fields else "") + "'_operation','" + op + "'"
            payload = "json_object(" + fields + ")"
            name = "jobagg_dirty_" + hashlib.sha256((table + op).encode()).hexdigest()
            conn.execute(f'''CREATE TRIGGER "{name}" AFTER {op} ON "{table}" BEGIN
                INSERT INTO jobagg_dirty_versions(unit,source_id,job_key,kind,changed_at,operation)
                SELECT json_extract(value,'$.unit'),json_extract(value,'$.source'),json_extract(value,'$.job'),json_extract(value,'$.kind'),
                    (julianday('now')-2440587.5)*86400,'{table}:{op}' FROM json_each(jobagg_dirty_units('{table}',{payload}));
                INSERT INTO jobagg_dirty_current
                SELECT unit,version,source_id,job_key,kind,changed_at,changed_at,'pending',NULL,NULL
                FROM jobagg_dirty_versions WHERE version>coalesce((SELECT max(version) FROM jobagg_dirty_current),0)
                ON CONFLICT(unit) DO UPDATE SET version=excluded.version,last_changed_at=excluded.last_changed_at,
                    first_changed_at=CASE WHEN disposition='acknowledged' THEN excluded.first_changed_at ELSE first_changed_at END,
                    disposition='pending',reason=NULL,acknowledgement_sha256=NULL; END''')


def _mark(conn, unit, source, job, kind, operation):
    at = time.time()
    version = conn.execute(
        "INSERT INTO jobagg_dirty_versions(unit,source_id,job_key,kind,changed_at,operation) VALUES(?,?,?,?,?,?)",
        (unit, source, job, kind, at, operation),
    ).lastrowid
    conn.execute(
        "INSERT INTO jobagg_dirty_current VALUES(?,?,?,?,?,?,?,'pending',NULL,NULL) ON CONFLICT(unit) DO UPDATE SET version=excluded.version,last_changed_at=excluded.last_changed_at,first_changed_at=CASE WHEN disposition='acknowledged' THEN excluded.first_changed_at ELSE first_changed_at END,disposition='pending',reason=NULL,acknowledgement_sha256=NULL",
        (unit, version, source, job, kind, at, at),
    )


def bootstrap(conn, *, limit, deadline_at):
    """Checkpoint every historical observation/frame; unknown acceptance is held.

    Complete legacy control/recovery reconciliation is a separate activation
    receipt. This import cannot infer publication from a later changed row.
    """
    remaining = limit
    for query, kind in (
        ("SELECT job_key,source_id FROM remediation_observations ORDER BY job_key", "job"),
        (
            "SELECT source_id AS job_key,source_id FROM remediation_sources WHERE last_list_at IS NOT NULL ORDER BY source_id",
            "listing",
        ),
    ):
        cursor = conn.execute(
            "SELECT * FROM ("
            + query
            + ") q WHERE NOT EXISTS(SELECT 1 FROM jobagg_dirty_bootstrap b WHERE b.unit=?||q.job_key) LIMIT ?",
            (kind + ":", remaining),
        )
        rows = cursor.fetchall()
        for key, source in rows:
            if time.monotonic() >= deadline_at:
                raise TimeoutError("publication_bootstrap_deadline")
            _mark(
                conn,
                kind + ":" + key,
                source,
                key if kind == "job" else None,
                kind,
                "verified_legacy_import",
            )
            conn.execute("INSERT INTO jobagg_dirty_bootstrap VALUES(?)", (kind + ":" + key,))
            if kind == "job":
                conn.execute(
                    "INSERT OR IGNORE INTO jobagg_dirty_transitions SELECT 'closing:'||job_key,source_id,job_key,(julianday(closes_at)-2440587.5)*86400,0 FROM jobs WHERE job_key=?",
                    (key,),
                )
        remaining -= len(rows)
        if remaining <= 0:
            return False
    conn.execute("UPDATE jobagg_dirty_state SET bootstrap_complete=1 WHERE id=1")
    return True


def vector(conn, *, contract_sha256, now):
    if not exists(conn):
        raise ValueError("publication_work_authority_missing")
    state = dict(
        zip(
            [
                item[0]
                for item in conn.execute("SELECT * FROM jobagg_dirty_state LIMIT 0").description
            ],
            conn.execute("SELECT * FROM jobagg_dirty_state WHERE id=1").fetchone(),
        )
    )
    if state["schema"] != SCHEMA or not state["bootstrap_complete"]:
        raise ValueError("publication_work_bootstrap_incomplete")
    due = conn.execute(
        "SELECT min(due_at) FROM jobagg_dirty_transitions WHERE fired=0 AND due_at IS NOT NULL"
    ).fetchone()[0]
    if due is not None:
        state["next_transition_at"] = (
            min(due, state["next_transition_at"])
            if state["next_transition_at"] is not None
            else due
        )
    reasons = []
    if state["contract_sha256"] != contract_sha256:
        reasons.append("contract_epoch_changed")
    if state["next_transition_at"] is not None and now >= state["next_transition_at"]:
        reasons.append("time_transition_due")
    pending = conn.execute(
        "SELECT unit,version,first_changed_at FROM jobagg_dirty_current WHERE disposition='pending' ORDER BY first_changed_at,unit LIMIT 1"
    ).fetchone()
    held = conn.execute(
        "SELECT unit,version,reason FROM jobagg_dirty_current WHERE disposition='held' ORDER BY first_changed_at,unit LIMIT 1"
    ).fetchone()
    return {
        "schema": SCHEMA,
        "epoch": state["epoch"],
        "empty": not pending and not held and not reasons,
        "pending": list(pending) if pending else None,
        "held": list(held) if held else None,
        "revalidation_reasons": reasons,
        "next_transition_at": state["next_transition_at"],
    }


def select(conn, *, policy, contract_sha256, output_root, source_paths, deadline_at):
    """Age-first cohort selection, finite candidates, exact fan-out union and bytes.

    Oversized first units are held with their estimate and do not starve smaller
    later cohorts. A complete listing frame is one indivisible unit.
    """
    required = (
        "max_candidates",
        "max_units",
        "max_sources",
        "max_targets",
        "max_bytes",
        "minimum_seconds",
        "measurement_sha256",
    )
    if any(key not in policy for key in required) or not policy["measurement_sha256"]:
        raise ValueError("measured_publication_admission_required")
    if (
        any(type(policy[key]) is not int or policy[key] <= 0 for key in required[:5])
        or policy["minimum_seconds"] <= 0
    ):
        raise ValueError("finite_publication_admission_required")
    work = vector(conn, contract_sha256=contract_sha256, now=time.time())
    if any(reason != "time_transition_due" for reason in work["revalidation_reasons"]):
        raise ValueError("publication_revalidation_requires_explicit_epoch_migration")
    cursor = conn.cursor()
    cursor.row_factory = sqlite3.Row
    cohorts = []
    for source in [*source_paths, "*"]:
        first = cursor.execute(
            "SELECT first_changed_at FROM jobagg_dirty_current WHERE source_id=? AND disposition='pending' ORDER BY first_changed_at,unit LIMIT 1",
            (source,),
        ).fetchone()
        if first:
            served = cursor.execute(
                "SELECT last_service FROM jobagg_dirty_cohorts WHERE source_id=?", (source,)
            ).fetchone()
            cohorts.append((served[0] if served else 0, first[0], source))
    rows = []
    for _, _, source in sorted(cohorts):
        available = policy["max_candidates"] - len(rows)
        if available <= 0:
            break
        rows.extend(
            cursor.execute(
                "SELECT c.*,v.operation FROM jobagg_dirty_current c JOIN jobagg_dirty_versions v ON v.version=c.version WHERE c.source_id=? AND c.disposition='pending' ORDER BY c.first_changed_at,c.unit LIMIT ?",
                (source, available),
            ).fetchall()
        )
    chosen = []
    sources = set()
    targets = set()
    total = 0
    holds = []
    for row in rows:
        if time.monotonic() + policy["minimum_seconds"] >= deadline_at:
            break
        source = row["source_id"]
        if source not in source_paths or row["kind"] == "unknown":
            holds.append(
                {
                    "unit": row["unit"],
                    "version": row["version"],
                    "reason": "unknown_or_disabled_scope_requires_review",
                }
            )
            continue
        if source not in sources and len(sources) >= policy["max_sources"]:
            continue
        paths = {
            str(Path(source_paths[source]).absolute()),
            str(Path(output_root).absolute() / "all_jobs.sqlite3"),
        }
        unit_bytes = 0
        if row["job_key"]:
            job = cursor.execute("SELECT * FROM jobs WHERE job_key=?", (row["job_key"],)).fetchone()
            unit_bytes = len(_dump(dict(job)).encode()) * 6 if job else 4096
            for blob in cursor.execute(
                "SELECT length(content) FROM attachment_blobs WHERE content_sha256 IN(SELECT content_sha256 FROM remediation_documents WHERE job_key=?)",
                (row["job_key"],),
            ):
                unit_bytes += blob[0] * 3
        elif row["kind"] == "listing":
            task = cursor.execute(
                "SELECT receipt FROM remediation_tasks WHERE source_id=? AND kind='listing' AND status='done'",
                (source,),
            ).fetchone()
            if task:
                receipt = json.loads(task[0])
                path = Path(receipt["frame_path"])
                unit_bytes = path.stat().st_size * 8  # Entire frame + maps/beforeimages.
        extra = sum(Path(path).stat().st_size * 8 for path in paths - targets)
        estimate = unit_bytes + extra
        if estimate > policy["max_bytes"]:
            holds.append(
                {
                    "unit": row["unit"],
                    "version": row["version"],
                    "reason": "oversized_unit_requires_reviewed_measured_path",
                    "estimated_bytes": estimate,
                }
            )
            continue
        if len(targets | paths) > policy["max_targets"] or total + estimate > policy["max_bytes"]:
            continue
        chosen.append(dict(row))
        sources.add(source)
        targets |= paths
        total += estimate
        if len(chosen) >= policy["max_units"]:
            break
    result = {
        "schema": SCHEMA,
        "epoch": work["epoch"],
        "contract_sha256": contract_sha256,
        "units": chosen,
        "held": holds,
        "source_ids": sorted(sources),
        "target_paths": sorted(targets),
        "estimated_bytes": total,
        "candidate_count": len(rows),
        "policy": policy,
        "all_work_selected": False,
    }
    result["sha256"] = hashlib.sha256(_dump(result).encode()).hexdigest()
    return result


def acknowledge(conn, selection, *, result_sha256, rejected=()):
    """Call only after exact per-target transactions, exports and readback."""
    rejected_jobs = {item.get("job_key") for item in rejected}
    for row in selection["units"]:
        disposition = (
            "held"
            if row["job_key"] in rejected_jobs or row["kind"] == "unknown"
            else "acknowledged"
        )
        conn.execute(
            "UPDATE jobagg_dirty_current SET disposition=?,reason=?,acknowledgement_sha256=? WHERE unit=? AND version=?",
            (
                disposition,
                "publication_rejected" if disposition == "held" else None,
                result_sha256,
                row["unit"],
                row["version"],
            ),
        )
    for source in {row["source_id"] for row in selection["units"]}:
        last = max(row["version"] for row in selection["units"] if row["source_id"] == source)
        conn.execute(
            "INSERT INTO jobagg_dirty_cohorts VALUES(?,?) ON CONFLICT(source_id) DO UPDATE SET last_service=max(last_service,excluded.last_service)",
            (source, last),
        )
    for row in selection["held"]:
        conn.execute(
            "UPDATE jobagg_dirty_current SET disposition='held',reason=? WHERE unit=? AND version=?",
            (row["reason"], row["unit"], row["version"]),
        )


def advance_time(conn, *, now, limit):
    rows = conn.execute(
        "SELECT object_key,source_id,job_key FROM jobagg_dirty_transitions WHERE fired=0 AND due_at<=? ORDER BY due_at,object_key LIMIT ?",
        (now, limit),
    ).fetchall()
    for key, source, job in rows:
        _mark(conn, "job:" + job, source, job, "job", "time_transition_revalidation")
        conn.execute("UPDATE jobagg_dirty_transitions SET fired=1 WHERE object_key=?", (key,))
    return len(rows)
