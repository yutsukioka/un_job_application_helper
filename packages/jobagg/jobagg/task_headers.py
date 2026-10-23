"""Indexed scheduling headers; payloads are hydrated only for the selected task."""

import hashlib
import json
from urllib.parse import urlsplit


def exists(conn):
    return bool(
        conn.execute("SELECT 1 FROM sqlite_master WHERE name='jobagg_task_headers'").fetchone()
    )


def configure(conn):
    def host(source, payload):
        value = json.loads(payload)
        if value.get("url"):
            return (urlsplit(value["url"]).hostname or "").lower()
        row = conn.execute(
            "SELECT host FROM jobagg_source_hosts WHERE source_id=?", (source,)
        ).fetchone()
        if row is None:
            raise ValueError("task_source_host_not_migrated")
        return row[0]

    conn.create_function("jobagg_task_host", 2, host)


def prepare(conn, source_hosts):
    """Explicit under-owner schema migration before runtime fence activation."""
    if exists(conn):
        if dict(conn.execute("SELECT source_id,host FROM jobagg_source_hosts")) != source_hosts:
            raise ValueError("task_host_contract_changed_review_required")
        return
    conn.execute("CREATE TABLE jobagg_source_hosts(source_id TEXT PRIMARY KEY,host TEXT NOT NULL)")
    conn.executemany("INSERT INTO jobagg_source_hosts VALUES(?,?)", sorted(source_hosts.items()))
    configure(conn)
    conn.execute("""CREATE TABLE jobagg_task_headers(task_id TEXT PRIMARY KEY,source_id TEXT,kind TEXT,
        host TEXT,status TEXT,eligible_at REAL,discovered_at REAL,last_attempt_at REAL)""")
    conn.execute("""INSERT INTO jobagg_task_headers SELECT t.task_id,t.source_id,t.kind,
        jobagg_task_host(t.source_id,t.payload),t.status,t.eligible_at,t.discovered_at,
        coalesce((SELECT max(started_at) FROM remediation_attempts a WHERE a.task_id=t.task_id),0)
        FROM remediation_tasks t""")
    conn.execute(
        "CREATE INDEX jobagg_header_order ON jobagg_task_headers(source_id,kind,host,status,last_attempt_at,discovered_at,task_id,eligible_at)"
    )
    conn.execute(
        "CREATE INDEX jobagg_header_groups ON jobagg_task_headers(status,source_id,kind,host,eligible_at)"
    )
    conn.execute("CREATE TABLE jobagg_kind_service(kind TEXT PRIMARY KEY,last_service REAL)")
    conn.execute(
        "INSERT INTO jobagg_kind_service SELECT kind,max(started_at) FROM remediation_attempts GROUP BY kind"
    )
    for op in ("INSERT", "UPDATE"):
        conn.execute(f"""CREATE TRIGGER jobagg_header_{op.lower()} AFTER {op} ON remediation_tasks BEGIN
        INSERT INTO jobagg_task_headers VALUES(new.task_id,new.source_id,new.kind,
        jobagg_task_host(new.source_id,new.payload),new.status,new.eligible_at,new.discovered_at,
        coalesce((SELECT last_attempt_at FROM jobagg_task_headers WHERE task_id=new.task_id),0))
        ON CONFLICT(task_id) DO UPDATE SET source_id=excluded.source_id,kind=excluded.kind,host=excluded.host,
        status=excluded.status,eligible_at=excluded.eligible_at,discovered_at=excluded.discovered_at; END""")
    conn.execute(
        "CREATE TRIGGER jobagg_header_delete AFTER DELETE ON remediation_tasks BEGIN DELETE FROM jobagg_task_headers WHERE task_id=old.task_id; END"
    )
    conn.execute("""CREATE TRIGGER jobagg_header_attempt AFTER INSERT ON remediation_attempts BEGIN
        UPDATE jobagg_task_headers SET last_attempt_at=max(last_attempt_at,new.started_at) WHERE task_id=new.task_id;
        INSERT INTO jobagg_kind_service VALUES(new.kind,new.started_at) ON CONFLICT(kind) DO UPDATE SET last_service=max(last_service,excluded.last_service); END""")
    conn.execute("CREATE TABLE jobagg_header_contract(id INTEGER PRIMARY KEY,sha256 TEXT)")
    conn.execute(
        "INSERT INTO jobagg_header_contract VALUES(1,?)",
        (hashlib.sha256(json.dumps(source_hosts, sort_keys=True).encode()).hexdigest(),),
    )


def candidates(conn, worker, now, *, excluded_sources=(), excluded_kinds=(), excluded_hosts=()):
    """One exact due frontier per actual source/kind/host, without payload scans.

    Ineligible source/host groups are eliminated before task lookup. SQL reads
    remain subject to the caller's deadline and statement-work ceiling.
    """
    from jobagg.runtime_holds import task_predicate
    predicate = task_predicate(conn, source="h.source_id", task="h.task_id", host="h.host")
    rows = []
    for source, kind, host in conn.execute(
        "SELECT source_id,kind,host FROM jobagg_task_headers WHERE status='pending' GROUP BY source_id,kind,host"
    ):
        worker.check_deadline(getattr(worker, "_sql_deadline", None))
        if (
            source not in worker.by_id
            or source in excluded_sources
            or kind in excluded_kinds
            or host in excluded_hosts
        ):
            continue
        row = conn.execute(
            "SELECT h.* FROM jobagg_task_headers h WHERE source_id=? AND kind=? AND host=? "
            "AND status='pending' AND eligible_at<=? AND " + predicate +
            " ORDER BY last_attempt_at,discovered_at,task_id LIMIT 1",
            (source, kind, host, now),
        ).fetchone()
        if row:
            rows.append({**dict(row), "_host": host})
    return rows
