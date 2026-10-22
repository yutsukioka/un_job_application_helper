"""Explicit, owner-held quota import. Never called by ordinary worker startup.

Cataloguing is a separately budgeted metadata pass. Payload import, replay and
final evidence verification commit finite pages. The cutover receipt precedes
rename, so interruption cannot recreate an empty authority or lose old charges.
"""

from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import time

from jobagg.atomic_files import atomic_write_text
from jobagg.policy_authority import (
    PolicyAuthority,
    SCHEMA,
    _dump,
    _insert,
    _schema_hash,
    _signature,
)
from jobagg.publication_fence import _owner, _file_identity

MIGRATION = "jobagg-policy-import-2"
DDL = """
CREATE TABLE IF NOT EXISTS authority(id INTEGER PRIMARY KEY,epoch TEXT,revision INTEGER,transaction_id TEXT,audit_cursor TEXT,audit_completed_at REAL);
CREATE TABLE IF NOT EXISTS sources(source_id TEXT PRIMARY KEY,pause REAL,n INTEGER);
CREATE TABLE IF NOT EXISTS attempts(attempt_id TEXT PRIMARY KEY,source_id TEXT,kind TEXT,sequence INTEGER,started_at REAL,effective_end REAL,batch_break INTEGER,event_json TEXT);
CREATE INDEX IF NOT EXISTS policy_sequence ON attempts(source_id,kind,sequence DESC);
CREATE INDEX IF NOT EXISTS policy_batch_break ON attempts(source_id,kind,batch_break,sequence DESC);
CREATE INDEX IF NOT EXISTS policy_window ON attempts(source_id,kind,effective_end DESC);
CREATE TABLE IF NOT EXISTS evidence(path TEXT PRIMARY KEY,sha256 TEXT);
CREATE TABLE IF NOT EXISTS migration_receipt(id INTEGER PRIMARY KEY,receipt_json TEXT);
CREATE TABLE IF NOT EXISTS import_state(id INTEGER PRIMARY KEY,phase TEXT,verified_ordinal INTEGER);
CREATE TABLE IF NOT EXISTS import_files(ordinal INTEGER PRIMARY KEY,path TEXT UNIQUE,sha256 TEXT,event_json TEXT,started_at REAL,imported INTEGER DEFAULT 0);
CREATE INDEX IF NOT EXISTS import_order ON import_files(imported,started_at,ordinal);
CREATE INDEX IF NOT EXISTS import_unread ON import_files(sha256,ordinal);
CREATE TABLE IF NOT EXISTS import_expected(name TEXT PRIMARY KEY);
"""


def _sha(path, check):
    info = path.stat()
    if path.resolve() != path or not path.is_file():
        raise ValueError("policy_import_aliased_evidence")
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            check()
            value.update(block)
    after = path.stat()
    if (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns) != (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
        after.st_ctime_ns,
    ):
        raise ValueError("policy_import_evidence_changed")
    return value.hexdigest()


def _directory(path):
    info = path.stat()
    return [info.st_dev, info.st_ino, info.st_mtime_ns, info.st_ctime_ns]


def step(
    policy,
    *,
    owner_fd,
    epoch,
    pauses,
    reviewed_writers,
    limit,
    check_deadline=lambda: None,
    fault=lambda _: None,
):
    _owner(owner_fd, policy.owner)
    check_deadline()
    if (
        type(limit) is not int
        or limit <= 0
        or not epoch
        or not reviewed_writers
        or any(item.get("fenced") is not True for item in reviewed_writers)
        or set(pauses) != set(policy.source_ids)
    ):
        raise ValueError("complete_reviewed_writer_inventory_and_finite_page_required")
    authority = PolicyAuthority(policy)
    journal = policy.root / "authority_migration.json"
    staging = authority.path.with_suffix(".import.sqlite3")
    contract = {
        "schema": MIGRATION,
        "epoch": epoch,
        "pauses": pauses,
        "writers": reviewed_writers,
        "owner_identity": _signature(policy.owner),
        "source_ids": sorted(policy.source_ids),
    }
    if authority.head.exists():
        authority.revision()
        return {"complete": True, "phase": "complete"}
    if journal.exists():
        checkpoint = json.loads(journal.read_text())
        if checkpoint["contract"] != contract:
            raise ValueError("policy_import_contract_changed")
    else:
        # This is the sole historical bootstrap audit, outside the tick path.
        policy.inspect()
        index = policy.root / "attempt_index.json"
        original = index.read_bytes()
        expected = json.loads(original)
        if (
            not isinstance(expected, list)
            or len(expected) != len(set(expected))
            or any(not re.fullmatch(r"[a-f0-9]{64}\.json", name) for name in expected)
        ):
            raise ValueError("policy_import_legacy_index_invalid")
        archive = policy.root / "authority_legacy_index.json"
        atomic_write_text(archive, original.decode())
        checkpoint = {
            "contract": contract,
            "phase": "import",
            "legacy_index_sha256": hashlib.sha256(original).hexdigest(),
            "legacy_directory": _directory(policy.root / "attempts"),
            "bootstrap_sha256": _sha(policy.root / "bootstrap.json", check_deadline),
            "marker_sha256": _sha(policy.marker, check_deadline),
        }
        # Journal exists before fencing. Restart uses the retained exact index.
        atomic_write_text(journal, _dump(checkpoint))
    if checkpoint["phase"] == "cutover":
        candidate = authority.path if authority.path.exists() else staging
        if (
            _signature(candidate) != checkpoint["database_identity"]
            or _sha(candidate, check_deadline) != checkpoint["database_sha256"]
        ):
            raise ValueError("policy_import_cutover_database_changed")
        if candidate == staging:
            os.replace(staging, authority.path)
        fault("after_database_rename")
        atomic_write_text(authority.head, _dump(checkpoint["head"]))
        fault("after_authority_head")
        authority.revision()
        return {"complete": True, "phase": "complete"}
    if authority.path.exists():
        raise ValueError("policy_import_unjournalled_native_database")
    archive = policy.root / "authority_legacy_index.json"
    if _sha(archive, check_deadline) != checkpoint["legacy_index_sha256"]:
        raise ValueError("policy_import_original_index_changed")
    # A migrated process and all supplied old writer APIs must refuse admission
    # throughout preparation. Unindexed files remain imported and charged.
    atomic_write_text(
        policy.root / "attempt_index.json", _dump({"schema": SCHEMA, "old_writers_fenced": True})
    )
    if (
        _directory(policy.root / "attempts") != checkpoint["legacy_directory"]
        or _sha(policy.root / "bootstrap.json", check_deadline) != checkpoint["bootstrap_sha256"]
        or _sha(policy.marker, check_deadline) != checkpoint["marker_sha256"]
    ):
        raise ValueError("policy_import_source_boundary_changed_review_required")
    with closing(sqlite3.connect(staging)) as conn:
        conn.row_factory = sqlite3.Row
        conn.create_function("jobagg_policy_write_allowed", 0, lambda: 1)
        conn.executescript(DDL)
        with conn:
            conn.execute("INSERT OR IGNORE INTO import_state VALUES(1,'catalogue',0)")
            conn.execute(
                "INSERT OR IGNORE INTO authority VALUES(1,?,0,?,?,?)",
                (epoch, "bootstrap", "", time.time()),
            )
            for source, pause in pauses.items():
                conn.execute("INSERT OR IGNORE INTO sources VALUES(?,?,0)", (source, pause))
        phase, verified = conn.execute("SELECT phase,verified_ordinal FROM import_state").fetchone()
        if phase == "catalogue":
            # Scandir order matches the historical stable tie order. On restart,
            # retain original ordinal; only genuinely new names get an ordinal.
            imported = 0
            with os.scandir(policy.root / "attempts") as entries:
                for entry in entries:
                    check_deadline()
                    if not entry.name.endswith(".json"):
                        continue
                    if entry.is_symlink() or not re.fullmatch(r"[a-f0-9]{64}\.json", entry.name):
                        raise ValueError("policy_import_unknown_or_aliased_reservation")
                    with conn:
                        imported += conn.execute(
                            "INSERT OR IGNORE INTO import_files(path) VALUES(?)", (entry.path,)
                        ).rowcount
                    if imported >= limit:
                        return {"complete": False, "phase": phase, "processed": imported}
            with conn:
                conn.executemany(
                    "INSERT OR IGNORE INTO import_expected VALUES(?)",
                    ((name,) for name in json.loads(archive.read_text())),
                )
                missing = conn.execute(
                    "SELECT name FROM import_expected WHERE NOT EXISTS(SELECT 1 FROM import_files f WHERE f.path=?||'/'||name) LIMIT 1",
                    (str(policy.root / "attempts"),),
                ).fetchone()
                if missing:
                    raise ValueError("policy_import_missing_indexed_reservation_charges_unknown")
                conn.execute("UPDATE import_state SET phase='payload' WHERE id=1")
            return {"complete": False, "phase": "payload"}
        if phase == "payload":
            rows = conn.execute(
                "SELECT ordinal,path FROM import_files WHERE sha256 IS NULL ORDER BY ordinal LIMIT ?",
                (limit,),
            ).fetchall()
            for row in rows:
                path = Path(row["path"])
                check_deadline()
                if path.stat().st_size > 16 * 1024**2:
                    raise ValueError("policy_import_reservation_oversized")
                sha = _sha(path, check_deadline)
                event = json.loads(path.read_text())
                if _sha(path, check_deadline) != sha:
                    raise ValueError("policy_import_reservation_changed")
                if (
                    path.name != hashlib.sha256(event["attempt_id"].encode()).hexdigest() + ".json"
                    or event["source_id"] not in pauses
                    or not isinstance(event["started_at"], (int, float))
                ):
                    raise ValueError("policy_import_reservation_identity_or_scope_changed")
                with conn:
                    conn.execute(
                        "UPDATE import_files SET sha256=?,event_json=?,started_at=? WHERE ordinal=?",
                        (sha, _dump(event), event["started_at"], row["ordinal"]),
                    )
                    conn.execute("INSERT OR REPLACE INTO evidence VALUES(?,?)", (str(path), sha))
            if not rows:
                with conn:
                    conn.execute("UPDATE import_state SET phase='replay' WHERE id=1")
            return {"complete": False, "phase": phase, "processed": len(rows)}
        if phase == "replay":
            rows = conn.execute(
                "SELECT ordinal,event_json FROM import_files WHERE imported=0 ORDER BY started_at,ordinal LIMIT ?",
                (limit,),
            ).fetchall()
            for row in rows:
                check_deadline()
                with conn:
                    _insert(conn, json.loads(row["event_json"]))
                    conn.execute(
                        "UPDATE import_files SET imported=1 WHERE ordinal=?", (row["ordinal"],)
                    )
            if not rows:
                with conn:
                    conn.execute("UPDATE import_state SET phase='verify' WHERE id=1")
            return {"complete": False, "phase": phase, "processed": len(rows)}
        if phase == "verify":
            rows = conn.execute(
                "SELECT ordinal,path,sha256 FROM import_files WHERE ordinal>? ORDER BY ordinal LIMIT ?",
                (verified, limit),
            ).fetchall()
            for row in rows:
                if _sha(Path(row["path"]), check_deadline) != row["sha256"]:
                    raise ValueError("policy_import_final_evidence_changed")
                with conn:
                    conn.execute(
                        "UPDATE import_state SET verified_ordinal=? WHERE id=1", (row["ordinal"],)
                    )
            if rows:
                return {"complete": False, "phase": phase, "processed": len(rows)}
            if _directory(policy.root / "attempts") != checkpoint["legacy_directory"]:
                raise ValueError("policy_import_final_directory_changed")
            with conn:
                conn.execute(
                    "INSERT OR REPLACE INTO migration_receipt VALUES(1,?)",
                    (
                        _dump(
                            {
                                **checkpoint,
                                "host_states_retained": True,
                                "source_holds_retained": True,
                            }
                        ),
                    ),
                )
                conn.execute("UPDATE import_state SET phase='complete' WHERE id=1")
                for table in (
                    "authority",
                    "sources",
                    "attempts",
                    "evidence",
                    "migration_receipt",
                    "import_state",
                    "import_files",
                    "import_expected",
                ):
                    for op in ("INSERT", "UPDATE", "DELETE"):
                        conn.execute(
                            f"CREATE TRIGGER IF NOT EXISTS policy_guard_{table}_{op} BEFORE {op} ON {table} BEGIN SELECT CASE WHEN jobagg_policy_write_allowed()!=1 THEN RAISE(ABORT,'policy_writer_not_admitted') END; END"
                        )
            schema_hash = _schema_hash(conn)
        elif phase == "complete":
            # Crash between the committed final page and external cutover receipt.
            # Reverify evidence in pages; do not silently bless an orphan DB.
            with conn:
                conn.execute("UPDATE import_state SET phase='verify',verified_ordinal=0 WHERE id=1")
            return {"complete": False, "phase": "verify"}
        else:
            raise ValueError("policy_import_unknown_checkpoint")
    immutable = {}
    for path in (policy.root / "bootstrap.json", policy.marker, archive):
        path.chmod(path.stat().st_mode & ~0o222)
        immutable[str(path)] = _file_identity(path)
    head = {
        "immutable_controls": immutable,
        "schema": SCHEMA,
        "epoch": epoch,
        "revision": 0,
        "transaction_id": "bootstrap",
        "pending": False,
        "schema_sha256": schema_hash,
        "last_wall_at": time.time(),
        "database_identity": _signature(staging),
        "owner_identity": _signature(policy.owner),
        "legacy_boundaries": {"attempts": checkpoint["legacy_directory"]},
    }
    checkpoint.update(
        phase="cutover",
        head=head,
        database_identity=_signature(staging),
        database_sha256=_sha(staging, check_deadline),
    )
    atomic_write_text(journal, _dump(checkpoint))
    fault("before_database_rename")
    return {"complete": False, "phase": "cutover"}


def reconcile_worker(authority, worker, *, after_attempt_id, limit, deadline_at):
    """Read-only paged cross-store reconciliation, retaining policy-only charges.

    A reservation is intentionally durable before the task transaction. Its
    absence from the task DB is conservative charge history, not a quota refund.
    A task attempt without its exact shared reservation requires scoped repair.
    """
    if type(limit) is not int or limit <= 0:
        raise ValueError("finite_policy_reconciliation_page_required")
    if worker.in_transaction:
        raise ValueError("policy_reconciliation_requires_committed_worker_boundary")
    worker.set_progress_handler(lambda: int(time.monotonic() >= deadline_at), 256)
    cursor = worker.cursor()
    cursor.row_factory = sqlite3.Row
    rows = cursor.execute(
        "SELECT attempt_id,task_id,source_id,kind,status,started_at,finished_at FROM remediation_attempts WHERE attempt_id>? ORDER BY attempt_id LIMIT ?",
        (after_attempt_id, limit),
    ).fetchall()
    unresolved = []
    with authority.connect() as conn:
        revision = conn.execute("SELECT revision FROM authority WHERE id=1").fetchone()[0]
        for row in rows:
            if time.monotonic() >= deadline_at:
                raise TimeoutError("policy_reconciliation_deadline")
            policy = conn.execute(
                "SELECT event_json FROM attempts WHERE attempt_id=?", (row["attempt_id"],)
            ).fetchone()
            event = json.loads(policy[0]) if policy else None
            if (
                event is None
                or any(
                    event.get(key) != row[key]
                    for key in ("task_id", "source_id", "kind", "started_at")
                )
                or (row["finished_at"] is not None and event.get("status") != row["status"])
            ):
                unresolved.append(
                    {
                        "attempt_id": row["attempt_id"],
                        "reason": "shared_charge_task_boundary_differs",
                    }
                )
    return {
        "after_attempt_id": rows[-1]["attempt_id"] if rows else after_attempt_id,
        "complete": len(rows) < limit,
        "processed": len(rows),
        "unresolved": unresolved,
        "policy_revision": revision,
        "policy_only_reservations_retained": True,
    }
