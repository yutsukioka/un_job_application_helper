"""Opt-in exact semantic history in the caller's SQLite transaction.

No helper commits, consults an archive, or authorizes removal. Coverage is useful
only with the independent terminal/closure/epoch and storage admission checks.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

SCHEMA = "jobagg-semantic-history-1"
MAX_OBJECT_BYTES = 16 * 1024 * 1024
MAX_GRAPH_NODES = 100_000
MAX_HISTORY_BYTES = 40_000_000_000
TABLES = (
    "semantic_objects", "semantic_subjects", "semantic_versions",
    "semantic_spans", "semantic_coverage",
)


class SemanticHistoryError(ValueError):
    pass


class SemanticCapacityError(SemanticHistoryError):
    """A visible storage deferral, never a provider failure or deletion request."""


def _json(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(",", ":"), allow_nan=False)


def _hash(value):
    return hashlib.sha256(value).hexdigest()


def _now():
    return datetime.now(UTC).isoformat()


def _exists(conn, name):
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


def ensure_schema(conn):
    existing = _state(conn)
    if existing and existing["policy_epoch"]:
        # Activation installed and validated the complete schema. Runtime
        # callers must not issue DDL, even harmless CREATE IF NOT EXISTS.
        if existing["schema"] != SCHEMA:
            raise SemanticHistoryError("unsupported_semantic_schema")
        return
    # executescript would implicitly commit the caller's publication transaction.
    statements = (
        "CREATE TABLE IF NOT EXISTS semantic_history_state (id INTEGER PRIMARY KEY CHECK(id=1), schema TEXT NOT NULL, policy_epoch TEXT, max_bytes INTEGER NOT NULL, ledger_path TEXT, scope_hash TEXT, revision INTEGER NOT NULL DEFAULT 0, fence_json TEXT NOT NULL DEFAULT '[]')",
        "CREATE TABLE IF NOT EXISTS semantic_objects (object_id TEXT PRIMARY KEY, payload BLOB NOT NULL, size_bytes INTEGER NOT NULL)",
        "CREATE TABLE IF NOT EXISTS semantic_subjects (subject_id TEXT PRIMARY KEY, identity_json TEXT NOT NULL)",
        "CREATE TABLE IF NOT EXISTS semantic_versions (version_id TEXT PRIMARY KEY, subject_id TEXT NOT NULL REFERENCES semantic_subjects(subject_id), entity_kind TEXT NOT NULL, entity_key TEXT NOT NULL, object_id TEXT NOT NULL REFERENCES semantic_objects(object_id), blob_sha256 TEXT)",
        "CREATE TABLE IF NOT EXISTS semantic_spans (stream_id TEXT NOT NULL, subject_id TEXT NOT NULL, entity_key TEXT NOT NULL, episode INTEGER NOT NULL, version_id TEXT NOT NULL REFERENCES semantic_versions(version_id), first_at TEXT, last_at TEXT, observations INTEGER NOT NULL, last_operation TEXT, PRIMARY KEY(stream_id,subject_id,entity_key,episode))",
        "CREATE TABLE IF NOT EXISTS semantic_coverage (coverage_id TEXT PRIMARY KEY, payload_json TEXT NOT NULL)",
        "CREATE INDEX IF NOT EXISTS semantic_versions_subject ON semantic_versions(subject_id,entity_kind)",
    )
    for statement in statements:
        conn.execute(statement)
    if not conn.execute("SELECT 1 FROM semantic_history_state WHERE id=1").fetchone():
        conn.execute("INSERT INTO semantic_history_state(id,schema,max_bytes) VALUES(1,?,?)", (SCHEMA, MAX_HISTORY_BYTES))
    state = _state(conn)
    if state["schema"] != SCHEMA:
        raise SemanticHistoryError("unsupported_semantic_schema")


def _state(conn):
    if not _exists(conn, "semantic_history_state"):
        return None
    row = conn.execute("SELECT * FROM semantic_history_state WHERE id=1").fetchone()
    if row is None:
        raise SemanticHistoryError("semantic_state_missing")
    columns = [x[1] for x in conn.execute("PRAGMA table_info(semantic_history_state)")]
    return dict(zip(columns, row))


def semantic_history_enabled(conn):
    state = _state(conn)
    return bool(state and state["policy_epoch"])


def _database_path(conn):
    row = next((r for r in conn.execute("PRAGMA database_list") if r[1] == "main"), None)
    if not row or not row[2]:
        raise SemanticHistoryError("semantic_authority_requires_file_database")
    return str(Path(row[2]).absolute())


def _mutation_tables(conn):
    # Only genuine virtual/shadow tables are excluded; a future ordinary table
    # whose name merely starts with jobs_fts must still invalidate the fence.
    excluded = {r[1] for r in conn.execute("PRAGMA table_list") if r[2] in {"virtual", "shadow"}}
    return sorted(r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
                  if not r[0].startswith(("sqlite_", "jobagg_dirty_")) and r[0] not in {"semantic_history_state", "jobagg_runtime_state", "jobagg_publication_revision"}
                  and r[0] not in excluded)


def register_denied_writer(conn):
    conn.create_function("jobagg_semantic_write_allowed", 0, lambda: 0)


def _identifier(value):
    return '"' + value.replace('"', '""') + '"'


def _replacement_conflicts(conn, table, origin="prior"):
    """Match every row SQLite REPLACE could delete before DELETE triggers run.

    Ordinary UNIQUE indexes obey their declared collations and do not conflict
    on NULL. Expression/partial indexes need their actual expressions/predicate;
    refusing that schema is safer than certifying an incomplete writer fence.
    """
    terms = []
    for index in conn.execute(f"PRAGMA index_list({_identifier(table)})"):
        if not index[2]:
            continue
        columns = [r for r in conn.execute(f"PRAGMA index_xinfo({_identifier(index[1])})") if r[5]]
        if index[4] or not columns or any(r[1] < 0 or r[2] is None for r in columns):
            raise SemanticHistoryError("semantic_unsupported_unique_conflict_index:" + table)
        terms.append("(" + " AND ".join(
            f"{origin}.{_identifier(r[2])} COLLATE {_identifier(r[4])}=NEW.{_identifier(r[2])}"
            for r in columns) + ")")
    table_info = next(r for r in conn.execute("PRAGMA table_list") if r[0] == "main" and r[1] == table)
    if not table_info[4]:  # INTEGER PRIMARY KEY and explicit rowid conflicts.
        columns = {r[1].lower() for r in conn.execute(f"PRAGMA table_info({_identifier(table)})")}
        alias = next((name for name in ("rowid", "_rowid_", "oid") if name not in columns), None)
        if alias is None:
            raise SemanticHistoryError("semantic_hidden_rowid_conflict_unavailable:" + table)
        terms.append(f"{origin}.{alias}=NEW.{alias}")
    if not terms:
        raise SemanticHistoryError("semantic_unique_conflict_key_missing:" + table)
    return "(" + " OR ".join(terms) + ")"


def _row_json(columns, origin):
    # Chunk to stay below SQLite's function argument limit.
    chunks = []
    for start in range(0, len(columns), 40):
        pairs = ["'" + c.replace("'", "''") + "'," + origin + "." + _identifier(c)
                 for c in columns[start:start + 40]]
        chunks.append("json_object(" + ",".join(pairs) + ")")
    value = chunks[0]
    for chunk in chunks[1:]:
        # json_patch deletes null-valued keys. Flat concatenation retains them.
        value = "(substr(" + value + ",1,length(" + value + ")-1)||','||substr(" + chunk + ",2))"
    return value


def _trigger_definitions(conn, tables):
    definitions = {}
    def add(name, sql):
        definitions[name] = "CREATE TRIGGER " + name + " " + sql
    for table in tables:
        quoted = '"' + table.replace('"', '""') + '"'
        for event in ("INSERT", "UPDATE", "DELETE"):
            tag = hashlib.sha256((table + event).encode()).hexdigest()[:24]
            add("semantic_guard_" + tag, f"BEFORE {event} ON {quoted} BEGIN SELECT CASE WHEN jobagg_semantic_write_allowed() != 1 THEN RAISE(ABORT,'semantic_writer_not_admitted') END; SELECT CASE WHEN (SELECT revision FROM semantic_history_state WHERE id=1) >= 9223372036854775806 THEN RAISE(ABORT,'semantic_revision_exhausted') END; END")
            add("semantic_revision_" + tag, f"AFTER {event} ON {quoted} BEGIN UPDATE semantic_history_state SET revision=revision+1 WHERE id=1; END")
    for event in ("INSERT", "UPDATE", "DELETE"):
        add("semantic_state_guard_" + event.lower(), f"BEFORE {event} ON semantic_history_state BEGIN SELECT CASE WHEN jobagg_semantic_write_allowed() != 1 THEN RAISE(ABORT,'semantic_writer_not_admitted') END; END")
    add("semantic_state_monotonic", "BEFORE UPDATE ON semantic_history_state WHEN NEW.revision != OLD.revision+1 OR NEW.id IS NOT OLD.id OR NEW.schema IS NOT OLD.schema OR NEW.policy_epoch IS NOT OLD.policy_epoch OR NEW.max_bytes IS NOT OLD.max_bytes OR NEW.ledger_path IS NOT OLD.ledger_path OR NEW.scope_hash IS NOT OLD.scope_hash OR NEW.fence_json IS NOT OLD.fence_json BEGIN SELECT RAISE(ABORT,'semantic_state_immutable_binding'); END")
    for event in ("INSERT", "DELETE"):
        add("semantic_state_immutable_" + event.lower(), f"BEFORE {event} ON semantic_history_state BEGIN SELECT RAISE(ABORT,'semantic_state_immutable_binding'); END")
    if "attachment_blobs" in tables:
        conflicts = _replacement_conflicts(conn, "attachment_blobs")
        referenced = "EXISTS(SELECT 1 FROM semantic_versions WHERE blob_sha256=OLD.content_sha256)"
        if "job_attachments" in tables:
            referenced += " OR EXISTS(SELECT 1 FROM job_attachments WHERE content_sha256=OLD.content_sha256)"
        insert_referenced = referenced.replace("OLD.", "prior.")
        for event in ("INSERT", "UPDATE"):
            add("semantic_referenced_blob_conflict_" + event.lower(), f"BEFORE {event} ON attachment_blobs WHEN EXISTS(SELECT 1 FROM attachment_blobs AS prior WHERE {conflicts} AND ((prior.content_sha256=NEW.content_sha256 AND (prior.size_bytes IS NOT NEW.size_bytes OR prior.content IS NOT NEW.content)) OR (({insert_referenced}) AND prior.content_sha256 IS NOT NEW.content_sha256))) BEGIN SELECT RAISE(ABORT,'semantic_retained_blob_immutable'); END")
        for event in ("UPDATE", "DELETE"):
            add("semantic_referenced_blob_" + event.lower(), f"BEFORE {event} ON attachment_blobs WHEN {referenced} BEGIN SELECT RAISE(ABORT,'semantic_retained_blob_immutable'); END")
    for table in ("semantic_objects", "semantic_subjects", "semantic_versions", "semantic_coverage"):
        conflicts = _replacement_conflicts(conn, table)
        add("semantic_immutable_" + table + "_insert", f"BEFORE INSERT ON {table} WHEN EXISTS(SELECT 1 FROM {table} AS prior WHERE {conflicts}) BEGIN SELECT RAISE(ABORT,'semantic_immutable_authority'); END")
        for event in ("UPDATE", "DELETE"):
            add("semantic_immutable_" + table + "_" + event.lower(),
                f"BEFORE {event} ON {table} BEGIN SELECT RAISE(ABORT,'semantic_immutable_authority'); END")
    for table, function in (("jobs", "jobagg_semantic_capture_job"), ("job_attachments", "jobagg_semantic_capture_document")):
        if table not in tables:
            continue
        columns = [r[1] for r in conn.execute(f'PRAGMA table_info("{table}")')]
        conflicts = _replacement_conflicts(conn, table)
        # REPLACE's implicit DELETE skips DELETE triggers unless the connection
        # enables recursive_triggers. Capture the legacy row before that point;
        # ordinary UPSERT/IGNORE may visit it too, but identical versions coalesce.
        for event in ("INSERT", "UPDATE"):
            add("semantic_capture_" + table + "_conflict_" + event.lower(),
                f' BEFORE {event} ON "{table}" BEGIN SELECT {function}({_row_json(columns, "prior")}) FROM "{table}" AS prior WHERE {conflicts}; END')
        for timing, event, origin in (("BEFORE", "UPDATE", "OLD"), ("BEFORE", "DELETE", "OLD"), ("AFTER", "UPDATE", "NEW"), ("AFTER", "INSERT", "NEW")):
            value = _row_json(columns, origin)
            add("semantic_capture_" + table + "_" + timing.lower() + "_" + event.lower(),
                f' {timing} {event} ON "{table}" BEGIN SELECT {function}({value}); END')
    return definitions


def activate_semantic_history(conn, *, policy_epoch, max_history_bytes, ledger_path, scope_hash):
    """Explicit activation; caller must already own admission/maintenance guards."""
    if not isinstance(policy_epoch, str) or not policy_epoch.strip():
        raise SemanticHistoryError("semantic_policy_epoch_required")
    if type(max_history_bytes) is not int or not 0 < max_history_bytes <= MAX_HISTORY_BYTES:
        raise SemanticHistoryError("semantic_history_allowance_invalid")
    if not ledger_path or not scope_hash:
        raise SemanticHistoryError("semantic_ledger_binding_required")
    state = _state(conn)
    bindings = (policy_epoch, str(Path(ledger_path).absolute()), scope_hash)
    if state and state["policy_epoch"]:
        if bindings != (state["policy_epoch"], state["ledger_path"], state["scope_hash"]) or max_history_bytes != state["max_bytes"]:
            raise SemanticHistoryError("semantic_activation_binding_changed")
        _check_fence(conn, state)
        return
    ensure_schema(conn)
    tables = _mutation_tables(conn)
    definitions = _trigger_definitions(conn, tables)
    conn.execute("UPDATE semantic_history_state SET policy_epoch=?,max_bytes=?,ledger_path=?,scope_hash=?,fence_json=? WHERE id=1", (*bindings[:1], max_history_bytes, *bindings[1:], _json(tables)))
    for statement in definitions.values():
        conn.execute(statement)
    register_denied_writer(conn)


def _check_fence(conn, state):
    if state is None or state["schema"] != SCHEMA or not state["policy_epoch"]:
        raise SemanticHistoryError("semantic_history_not_activated")
    tables = _mutation_tables(conn)
    if json.loads(state["fence_json"]) != tables:
        raise SemanticHistoryError("semantic_unfenced_table")
    for name, expected in _trigger_definitions(conn, tables).items():
        actual = conn.execute("SELECT sql FROM sqlite_master WHERE type='trigger' AND name=?", (name,)).fetchone()
        if not actual or " ".join(actual[0].split()) != " ".join(expected.split()):
            raise SemanticHistoryError("semantic_fence_missing_or_changed")


@contextmanager
def admitted_semantic_write(conn, *, policy_epoch, run_id, lease, authorizer=None):
    """Grant writes only while the actual durable ledger authorizes this writer."""
    from jobagg.storage_budget import BudgetLedger
    state = _state(conn)
    _check_fence(conn, state)
    if state["policy_epoch"] != policy_epoch:
        raise SemanticHistoryError("semantic_policy_epoch_mismatch")
    ledger = BudgetLedger.attach(lease)
    path = _database_path(conn)

    def validate():
        ledger.validate_writer(expected_cycle_id=run_id, expected_scope_hash=state["scope_hash"],
                               expected_ledger_path=state["ledger_path"],
                               allowed_phases=("worker_admitted", "publication_admitted"), required_path=path)
        return 1

    validate()
    failures = []
    transaction = {"dirty": False}
    def authorize(action, first, second, database, trigger):
        if action in {sqlite3.SQLITE_CREATE_INDEX, sqlite3.SQLITE_CREATE_TABLE, sqlite3.SQLITE_CREATE_TRIGGER,
                      sqlite3.SQLITE_CREATE_VIEW, sqlite3.SQLITE_CREATE_TEMP_INDEX, sqlite3.SQLITE_CREATE_TEMP_TABLE,
                      sqlite3.SQLITE_CREATE_TEMP_TRIGGER, sqlite3.SQLITE_CREATE_TEMP_VIEW,
                      sqlite3.SQLITE_DROP_INDEX, sqlite3.SQLITE_DROP_TABLE, sqlite3.SQLITE_DROP_TRIGGER,
                      sqlite3.SQLITE_DROP_VIEW, sqlite3.SQLITE_DROP_TEMP_INDEX, sqlite3.SQLITE_DROP_TEMP_TABLE,
                      sqlite3.SQLITE_DROP_TEMP_TRIGGER, sqlite3.SQLITE_DROP_TEMP_VIEW,
                      sqlite3.SQLITE_ALTER_TABLE, sqlite3.SQLITE_CREATE_VTABLE, sqlite3.SQLITE_DROP_VTABLE,
                      sqlite3.SQLITE_ATTACH, sqlite3.SQLITE_DETACH}:
            return sqlite3.SQLITE_DENY
        if action == sqlite3.SQLITE_PRAGMA and second is not None and first.lower() not in {"table_info", "table_xinfo", "index_info", "index_xinfo", "index_list", "foreign_key_list"}:
            return sqlite3.SQLITE_DENY
        if authorizer is not None and authorizer(action, first, second, database, trigger) != sqlite3.SQLITE_OK:
            return sqlite3.SQLITE_DENY
        # Keep this hook after scope exit: callers commonly commit their outer
        # transaction immediately afterwards. Recheck the actual durable phase
        # at COMMIT, not just at the last INSERT/UPDATE/DELETE.
        if (action == sqlite3.SQLITE_TRANSACTION and first == "COMMIT") or (action == sqlite3.SQLITE_SAVEPOINT and first == "RELEASE"):
            try:
                validate()
                transaction["dirty"] = False
            except Exception:  # noqa: BLE001 - any failed capability check must deny the commit
                return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK
    conn.set_authorizer(authorize)
    def write_allowed():
        validate()
        if not transaction["dirty"]:
            # SQLite authorizers run at prepare time. Expire cached COMMIT /
            # RELEASE statements when the next transaction first mutates, so
            # its boundary necessarily prepares and revalidates the lease.
            conn.set_authorizer(authorize)
            transaction["dirty"] = True
        return 1
    conn.create_function("jobagg_semantic_write_allowed", 0, write_allowed)
    def capture(function, payload):
        try:
            validate()
            return function(conn, json.loads(payload))
        except Exception as exc:
            failures.append(exc)
            raise
    def capture_job(payload):
        return capture(preserve_job, payload)
    def capture_document(payload):
        return capture(preserve_document_association, payload)
    conn.create_function("jobagg_semantic_capture_job", 1, capture_job)
    conn.create_function("jobagg_semantic_capture_document", 1, capture_document)
    try:
        yield conn
    except sqlite3.Error as exc:
        if failures:
            raise failures[-1] from exc
        raise
    finally:
        register_denied_writer(conn)
        conn.create_function("jobagg_semantic_capture_job", 1, None)
        conn.create_function("jobagg_semantic_capture_document", 1, None)


def _safe_database(path, allowed_roots):
    p = Path(path)
    if not p.is_absolute() or ".." in p.parts:
        raise SemanticHistoryError("semantic_database_path_not_canonical")
    if not allowed_roots or not any(p == Path(r).resolve() or Path(r).resolve() in p.parents for r in allowed_roots):
        raise SemanticHistoryError("semantic_database_outside_allowed_roots")
    for part in (p, *p.parents):
        if part.is_symlink():
            raise SemanticHistoryError("semantic_database_symlink")
    if not p.is_file():
        raise SemanticHistoryError("semantic_database_missing")
    return p


def semantic_database_binding(path):
    p = _safe_database(path, [Path(path).absolute().parent])
    conn = sqlite3.connect(p.as_uri() + "?mode=ro", uri=True)
    try:
        conn.execute("PRAGMA query_only=ON")
        state = _state(conn)
        if not state or not state["policy_epoch"]:
            return {"schema": SCHEMA, "writer_fenced": False, "policy_epoch": None, "revision": None}
        _check_fence(conn, state)
        return {"schema": SCHEMA, "writer_fenced": True, "policy_epoch": state["policy_epoch"], "revision": state["revision"],
                "ledger_path": state["ledger_path"], "scope_hash": state["scope_hash"],
                "table_fence_hash": _hash(state["fence_json"].encode())}
    finally:
        conn.close()


def _capacity(conn, extra):
    state = _state(conn)
    limit = state["max_bytes"] if state else MAX_HISTORY_BYTES
    # Logical admission backstop; physical SQLite/WAL envelopes are admitted
    # separately by the finite storage ledger and caller transaction guard.
    used = conn.execute("SELECT COALESCE(sum(size_bytes+256),0) FROM semantic_objects").fetchone()[0]
    used += sum(conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0] * 1024 for table in TABLES if table != "semantic_objects")
    used += conn.execute("SELECT COALESCE(sum(length(CAST(payload_json AS BLOB))),0) FROM semantic_coverage").fetchone()[0]
    used += conn.execute("SELECT COALESCE(sum(length(CAST(identity_json AS BLOB))),0) FROM semantic_subjects").fetchone()[0]
    if _exists(conn, "attachment_blobs"):
        used += conn.execute("SELECT COALESCE(sum(size_bytes),0) FROM attachment_blobs WHERE content_sha256 IN (SELECT blob_sha256 FROM semantic_versions)").fetchone()[0]
    if used + extra > limit:
        raise SemanticCapacityError("semantic_history_capacity_deferred")


def _put_object(conn, value):
    """Typed tree nodes share long strings and nested unchanged payloads exactly."""
    if isinstance(value, dict):
        node = {"type": "map", "items": [[k, _put_object(conn, v)] for k, v in sorted(value.items())]}
    elif isinstance(value, (list, tuple)):
        node = {"type": "list", "items": [_put_object(conn, v) for v in value]}
    else:
        if value is not None and not isinstance(value, (str, bool, int, float)):
            raise SemanticHistoryError("unsupported_semantic_value")
        node = {"type": "value", "value": value}
    payload = _json(node).encode()
    if len(payload) > MAX_OBJECT_BYTES:
        raise SemanticCapacityError("semantic_object_capacity_deferred")
    key = _hash(SCHEMA.encode() + b"\0" + payload)
    old = conn.execute("SELECT payload,size_bytes FROM semantic_objects WHERE object_id=?", (key,)).fetchone()
    if old:
        if bytes(old[0]) != payload or old[1] != len(payload):
            raise SemanticHistoryError("semantic_object_collision_or_corruption")
    else:
        _capacity(conn, len(payload) + 256)
        conn.execute("INSERT INTO semantic_objects VALUES(?,?,?)", (key, payload, len(payload)))
    return key


def _read_object(conn, key, *, seen=None, deadline_at=None, budget=None):
    seen = set() if seen is None else seen
    budget = [MAX_GRAPH_NODES] if budget is None else budget
    budget[0] -= 1
    if budget[0] < 0:
        raise SemanticHistoryError("semantic_object_graph_limit")
    if deadline_at is not None and time.monotonic() >= deadline_at:
        raise TimeoutError("semantic coverage deadline exhausted")
    if key in seen or len(seen) >= MAX_GRAPH_NODES:
        raise SemanticHistoryError("semantic_object_graph_cycle_or_limit")
    seen.add(key)
    row = conn.execute("SELECT payload,size_bytes FROM semantic_objects WHERE object_id=?", (key,)).fetchone()
    if not row or len(row[0]) != row[1] or _hash(SCHEMA.encode() + b"\0" + bytes(row[0])) != key:
        raise SemanticHistoryError("semantic_object_missing_or_corrupt")
    node = json.loads(row[0])
    if node.get("type") == "map":
        pairs = node["items"]
        if len({x[0] for x in pairs}) != len(pairs):
            raise SemanticHistoryError("semantic_duplicate_key")
        value = {k: _read_object(conn, v, seen=seen, deadline_at=deadline_at, budget=budget) for k, v in pairs}
    elif node.get("type") == "list":
        value = [_read_object(conn, v, seen=seen, deadline_at=deadline_at, budget=budget) for v in node["items"]]
    elif node.get("type") == "value":
        value = node["value"]
    else:
        raise SemanticHistoryError("semantic_object_kind_invalid")
    seen.remove(key)
    return value


# Only named occurrence fields in known proof envelopes are coalesced. Unknown
# fields (including arbitrary fields called "timestamp") remain exact content.
_OCCURRENCE = {"observed_at", "checked_at", "retrieved_at", "generation_id", "publication_key"}
_KNOWN_PROOFS = {"_deterministic_fetch_observation", "_deterministic_publication", "_jobagg_listing_verification", "_jobagg_main_text_verification"}


def _proof(value):
    if not isinstance(value, dict):
        return value
    current = {k: v for k, v in value.items() if k not in _OCCURRENCE and k != "previous_verification"}
    previous = _proof(value.get("previous_verification"))
    # Repeated identical assertions do not grow a recursive proof chain.
    if isinstance(previous, dict):
        previous_fact = {k: v for k, v in previous.items() if k != "previous_verification"}
        if current == previous_fact:
            return previous
    if previous is not None:
        current["previous_verification"] = previous
    return current


def _job_payload(row):
    row = dict(row)
    raw = row.pop("raw", None)
    encoded = row.pop("raw_json", None)
    if raw is None:
        try:
            raw = json.loads(encoded or "{}")
        except (TypeError, ValueError) as exc:
            raise SemanticHistoryError("semantic_raw_json_invalid") from exc
    if not isinstance(raw, dict):
        raise SemanticHistoryError("semantic_raw_not_object")
    # Preserve all unclassified columns/raw values. Poll bookkeeping alone is not
    # a semantic version, and remains in the mutable row/current proof.
    for key in ("last_seen_at", "normalized_hash", "missing_run_count", "source_latest_observed_at"):
        row.pop(key, None)
    row["raw"] = {k: _proof(v) if k in _KNOWN_PROOFS else v for k, v in raw.items()}
    return row


def _subject(conn, row):
    identity = {"source_id": row.get("source_id"), "external_id": row.get("external_id"), "origin_job_key": row.get("job_key")}
    if not identity["source_id"] or not identity["origin_job_key"]:
        raise SemanticHistoryError("semantic_subject_identity_missing")
    encoded = _json(identity)
    subject = _hash(encoded.encode())
    old = conn.execute("SELECT identity_json FROM semantic_subjects WHERE subject_id=?", (subject,)).fetchone()
    if old and old[0] != encoded:
        raise SemanticHistoryError("semantic_subject_conflict")
    if not old:
        _capacity(conn, 1024 + len(encoded.encode()))
        conn.execute("INSERT INTO semantic_subjects VALUES(?,?)", (subject, encoded))
    return subject


def _version(conn, row, kind, entity_key, payload, *, observed_at=None, operation_key=None, blob=None):
    subject = _subject(conn, row)
    object_id = _put_object(conn, payload)
    key = _hash(_json([SCHEMA, subject, kind, entity_key, object_id, blob]).encode())
    expected = (subject, kind, entity_key, object_id, blob)
    old = conn.execute("SELECT subject_id,entity_kind,entity_key,object_id,blob_sha256 FROM semantic_versions WHERE version_id=?", (key,)).fetchone()
    if old and tuple(old) != expected:
        raise SemanticHistoryError("semantic_version_conflict")
    if not old:
        blob_bytes = 0
        if blob and not conn.execute("SELECT 1 FROM semantic_versions WHERE blob_sha256=? LIMIT 1", (blob,)).fetchone():
            blob_bytes = conn.execute("SELECT size_bytes FROM attachment_blobs WHERE content_sha256=?", (blob,)).fetchone()[0]
        _capacity(conn, 1024 + blob_bytes)
        conn.execute("INSERT INTO semantic_versions VALUES(?,?,?,?,?,?)", (key, *expected))
    database = next(r[2] for r in conn.execute("PRAGMA database_list") if r[1] == "main")
    stream = _hash((database or "memory").encode())
    span = conn.execute("SELECT episode,version_id,last_at,observations,last_operation FROM semantic_spans WHERE stream_id=? AND subject_id=? AND entity_key=? ORDER BY episode DESC LIMIT 1", (stream, subject, entity_key)).fetchone()
    observed_at = observed_at or row.get("last_seen_at") or row.get("first_seen_at")
    if span and span[1] == key:
        if (operation_key and span[4] == operation_key) or (not operation_key and span[2] == observed_at):
            return key
        if span[3] >= 9223372036854775806:
            raise SemanticCapacityError("semantic_observation_counter_exhausted")
        conn.execute("UPDATE semantic_spans SET last_at=?,observations=observations+1,last_operation=? WHERE stream_id=? AND subject_id=? AND entity_key=? AND episode=?", (observed_at, operation_key, stream, subject, entity_key, span[0]))
    else:
        _capacity(conn, 1024)
        conn.execute("INSERT INTO semantic_spans VALUES(?,?,?,?,?,?,?,?,?)", (stream, subject, entity_key, 1 if not span else span[0] + 1, key, observed_at, observed_at, 1, operation_key))
    return key


def preserve_job(conn, row, *, observed_at=None, operation_key=None):
    ensure_schema(conn)
    row = dict(row)
    return _version(conn, row, "job", "job", _job_payload(row), observed_at=observed_at, operation_key=operation_key)


def _verify_blob(conn, digest, *, deadline_at=None):
    row = conn.execute("SELECT length(content),size_bytes FROM attachment_blobs WHERE content_sha256=?", (digest,)).fetchone()
    if not row or row[0] != row[1]:
        raise SemanticHistoryError("semantic_document_blob_missing_or_corrupt")
    hasher = hashlib.sha256()
    for offset in range(0, row[0], 1024 * 1024):
        if deadline_at is not None and time.monotonic() >= deadline_at:
            raise TimeoutError("semantic coverage deadline exhausted")
        chunk = conn.execute("SELECT substr(content,?,?) FROM attachment_blobs WHERE content_sha256=?", (offset + 1, 1024 * 1024, digest)).fetchone()
        hasher.update(bytes(chunk[0]))
    if hasher.hexdigest() != digest:
        raise SemanticHistoryError("semantic_document_blob_missing_or_corrupt")


def _document_payload(association):
    doc = dict(association)
    if "metadata_json" in doc:
        doc["metadata"] = json.loads(doc.pop("metadata_json") or "{}")
    for key in ("retrieved_at", "observed_at"):
        doc.pop(key, None)
    if isinstance(doc.get("metadata"), dict):
        doc["metadata"] = {k: v for k, v in doc["metadata"].items() if k not in {"retrieved_at", "observed_at", "prepared_binary_path"}}
    return doc


def preserve_document_association(conn, association, *, job_row=None, observed_at=None, operation_key=None):
    ensure_schema(conn)
    doc = _document_payload(association)
    if job_row is None:
        names = [x[1] for x in conn.execute("PRAGMA table_info(jobs)")]
        current = conn.execute("SELECT * FROM jobs WHERE job_key=?", (doc.get("job_key"),)).fetchone()
        if current is None:
            raise SemanticHistoryError("semantic_document_subject_missing")
        job_row = dict(zip(names, current))
    digest = doc.get("content_sha256")
    if digest:
        _verify_blob(conn, digest)
    identity = doc.get("attachment_id") or _hash(_json([doc.get("url"), digest]).encode())
    return _version(conn, dict(job_row), "document_association", "document:" + identity, doc,
                    observed_at=observed_at, operation_key=operation_key, blob=digest)


def _source_subject(source_id):
    if not isinstance(source_id, str) or not source_id:
        raise SemanticHistoryError("semantic_listing_source_required")
    return {"source_id": source_id, "external_id": None, "job_key": "source:" + source_id}


def _listing_payload(frame):
    # The exact membership/proof facts survive; occurrence and rollback paths
    # belong to the bounded generation receipt and mutable latest frame.
    return {k: v for k, v in frame.items() if k not in
            {"destinations", "observed_at", "generation_id", "publication_key"}}


def preserve_listing_frame(conn, frame, *, operation_key=None):
    ensure_schema(conn)
    return _version(conn, _source_subject(frame.get("source_id")), "listing_frame", "listing_frame",
                    _listing_payload(frame), observed_at=frame.get("observed_at"), operation_key=operation_key)


def _beforeimage_records(beforeimage, *, source_id=None):
    """Yield exact useful payloads and identities; reject unclassified shapes."""
    if not isinstance(beforeimage, dict):
        raise SemanticHistoryError("semantic_beforeimage_invalid")
    if "job" in beforeimage:
        if set(beforeimage) - {"job", "related"}:
            raise SemanticHistoryError("semantic_beforeimage_unknown_fields")
        row = beforeimage["job"]
        if row is None:
            if any(beforeimage.get("related", {}).values()):
                raise SemanticHistoryError("semantic_related_without_subject")
            return
        yield row, "job", "job", _job_payload(row), None
        for table, records in beforeimage.get("related", {}).items():
            if not isinstance(records, list):
                raise SemanticHistoryError("semantic_related_records_invalid")
            for record in records:
                if table == "job_attachments":
                    doc = _document_payload(record)
                    digest = doc.get("content_sha256")
                    identity = doc.get("attachment_id") or _hash(_json([doc.get("url"), digest]).encode())
                    yield row, "document_association", "document:" + identity, doc, digest
                else:
                    yield row, "related_record", "related:" + table + ":" + _hash(_json(record).encode()), {"table": table, "row": record}, None
        return
    allowed = {"live_listing_frames", "live_listing_inventory", "job_presence_rows"}
    if not beforeimage or set(beforeimage) - allowed:
        raise SemanticHistoryError("semantic_beforeimage_unknown_shape")
    for table, records in beforeimage.items():
        if not isinstance(records, list):
            raise SemanticHistoryError("semantic_listing_records_invalid")
        for record in records:
            subject = _source_subject(record.get("source_id") or source_id)
            yield subject, "related_record", "related:" + table + ":" + _hash(_json(record).encode()), {"table": table, "row": record}, None


def preserve_beforeimage(conn, beforeimage, *, source_id=None, observed_at=None, operation_key=None):
    ensure_schema(conn)
    versions = []
    for row, kind, entity, payload, blob in _beforeimage_records(beforeimage, source_id=source_id):
        if blob:
            _verify_blob(conn, blob)
        versions.append(_version(conn, row, kind, entity, payload, blob=blob,
                                 observed_at=observed_at, operation_key=operation_key))
    return sorted(set(versions))


def preserve_publication_plan(conn, plan, *, destination_path, operation_key=None):
    versions = set()
    for change in [*plan.get("changes", []), *plan.get("listing_frames", [])]:
        for destination in change.get("destinations", []):
            if str(Path(destination["path"]).absolute()) != str(Path(destination_path).absolute()):
                continue
            if "worker_row" in change:
                versions.add(preserve_job(conn, change["worker_row"], operation_key=operation_key))
            else:
                versions.add(preserve_listing_frame(conn, change, operation_key=operation_key))
            before = json.loads(Path(destination["beforeimage_path"]).read_text())
            versions.update(preserve_beforeimage(conn, before, source_id=change.get("source_id"), operation_key=operation_key))
    return sorted(versions)


def _require_payloads(expected, selected):
    actual = {(_json(v["subject"]), v["entity_kind"], v["entity_key"], _json(v["payload"]), v["blob_sha256"]) for v in selected}
    for row, kind, entity, payload, blob in expected:
        identity = {"source_id": row.get("source_id"), "external_id": row.get("external_id"), "origin_job_key": row.get("job_key")}
        if (_json(identity), kind, entity, _json(payload), blob) not in actual:
            raise SemanticHistoryError("semantic_artifact_payload_uncovered")


def _require_document(document, job, selected):
    if not isinstance(document, dict) or not document.get("url") or not document.get("content_sha256"):
        raise SemanticHistoryError("semantic_plan_document_invalid")
    for version in selected:
        if version["entity_kind"] != "document_association":
            continue
        subject, actual = version["subject"], version["payload"]
        if (subject["source_id"], subject["external_id"]) != (job.get("source_id"), job.get("external_id")):
            continue
        actual_key = subject["origin_job_key"]
        identifier = _hash((actual_key + "\n" + document["url"] + "\n" + document["content_sha256"]).encode())
        metadata = {k: v for k, v in document.items() if k != "prepared_binary_path"}
        metadata.update(attachment_id=identifier, job_key=actual_key,
                        binary_ref={"table": "attachment_blobs", "key": document["content_sha256"], "column": "content"},
                        status="captured_unverified", whole_job_complete=False, fidelity_complete=False)
        expected = _document_payload({"attachment_id":identifier, "job_key":actual_key,
            "source_id":job["source_id"], "url":document["url"], "final_url":document.get("final_url"),
            "label":document.get("label"), "category":"unresolved", "required_for_complete_text":1,
            "status":"captured_unverified", "content_sha256":document["content_sha256"],
            "extracted_text":document.get("extracted_text", ""), "metadata":metadata})
        if version["blob_sha256"] == document["content_sha256"] and all(actual.get(k) == v for k, v in expected.items()):
            return
    raise SemanticHistoryError("semantic_plan_document_uncovered")


def _artifact_payload_binding(artifact, data, selected, *, database, subject_kind, subject_id):
    if artifact["kind"] == "blob":
        if not any(v["blob_sha256"] == artifact["sha256"] for v in selected):
            raise SemanticHistoryError("semantic_blob_association_uncovered")
        return
    document = json.loads(data)
    if artifact["kind"] == "job_beforeimage":
        sources = {v["subject"]["source_id"] for v in selected}
        # Listing beforeimages omit source_id in presence rows. The publisher
        # supplies it, or it is unambiguous among this artifact's versions.
        source_id = artifact.get("source_id") or (next(iter(sources)) if len(sources) == 1 else None)
        _require_payloads(_beforeimage_records(document, source_id=source_id), selected)
        return
    if subject_kind != "generation" or document.get("generation_id") != subject_id:
        raise SemanticHistoryError("semantic_plan_generation_mismatch")
    matched = False
    for entry in [*document.get("changes", []), *document.get("listing_frames", [])]:
        for destination in entry.get("destinations", []):
            if str(Path(destination["path"]).absolute()) != database:
                continue
            matched = True
            if "worker_row" in entry:
                row = entry["worker_row"]
                _require_payloads([(row, "job", "job", _job_payload(row), None)], selected)
                if destination.get("before"):
                    prior = destination["before"]
                    _require_payloads([(prior, "job", "job", _job_payload(prior), None)], selected)
                for document in entry.get("documents", []):
                    _require_document(document, row, selected)
            else:
                row = _source_subject(entry.get("source_id"))
                _require_payloads([(row, "listing_frame", "listing_frame", _listing_payload(entry), None)], selected)
    if not matched:
        raise SemanticHistoryError("semantic_plan_destination_uncovered")


def read_semantic_version(conn, version_id, *, deadline_at=None):
    row = conn.execute("SELECT subject_id,entity_kind,entity_key,object_id,blob_sha256 FROM semantic_versions WHERE version_id=?", (version_id,)).fetchone()
    if not row:
        raise SemanticHistoryError("semantic_version_missing")
    if _hash(_json([SCHEMA, *tuple(row)]).encode()) != version_id:
        raise SemanticHistoryError("semantic_version_corrupt")
    subject = conn.execute("SELECT identity_json FROM semantic_subjects WHERE subject_id=?", (row[0],)).fetchone()
    if not subject or _hash(subject[0].encode()) != row[0]:
        raise SemanticHistoryError("semantic_subject_corrupt")
    if row[4]:
        _verify_blob(conn, row[4], deadline_at=deadline_at)
    return {"version_id": version_id, "subject": json.loads(subject[0]), "entity_kind": row[1],
            "entity_key": row[2], "payload": _read_object(conn, row[3], deadline_at=deadline_at), "blob_sha256": row[4]}


def record_semantic_coverage(conn, *, subject_kind, subject_id, policy_epoch, version_ids, artifacts, operation_keys=()):
    state = _state(conn)
    _check_fence(conn, state)
    if state["policy_epoch"] != policy_epoch or not version_ids:
        raise SemanticHistoryError("semantic_coverage_epoch_or_versions_invalid")
    versions = sorted(set(version_ids))
    decoded = {version: read_semantic_version(conn, version) for version in versions}
    selected = []
    for artifact in artifacts:
        artifact = dict(artifact)
        path = Path(artifact["path"])
        if path.is_symlink() or not path.is_file():
            raise SemanticHistoryError("semantic_artifact_not_regular")
        if artifact.get("kind") != "blob" and path.stat().st_size > MAX_OBJECT_BYTES:
            raise SemanticCapacityError("semantic_artifact_validation_capacity_deferred")
        data = None
        hasher = hashlib.sha256()
        size = 0
        if artifact.get("kind") == "blob":
            with path.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    hasher.update(chunk)
                    size += len(chunk)
        else:
            data = path.read_bytes()
            hasher.update(data)
            size = len(data)
        if artifact.get("sha256") != hasher.hexdigest() or artifact.get("size_bytes") != size:
            raise SemanticHistoryError("semantic_artifact_binding_mismatch")
        if artifact.get("kind") not in {"publication_plan", "job_beforeimage", "blob"}:
            raise SemanticHistoryError("semantic_artifact_kind_uncovered")
        if not artifact.get("version_ids") or not set(artifact["version_ids"]) <= set(versions):
            raise SemanticHistoryError("semantic_artifact_versions_unbound")
        if artifact["kind"] == "blob":
            _verify_blob(conn, artifact["sha256"])
        _artifact_payload_binding(artifact, data, [decoded[v] for v in artifact["version_ids"]],
                                  database=_database_path(conn), subject_kind=subject_kind, subject_id=subject_id)
        selected.append(artifact)
    payload = {"schema": SCHEMA, "subject_kind": subject_kind, "subject_id": subject_id,
               "policy_epoch": policy_epoch, "database": _database_path(conn), "version_ids": versions,
               "artifacts": sorted(selected, key=lambda x: x["path"]), "operation_keys": sorted(set(operation_keys))}
    encoded = _json(payload)
    if len(encoded.encode()) > MAX_OBJECT_BYTES:
        raise SemanticCapacityError("semantic_coverage_capacity_deferred")
    key = _hash(encoded.encode())
    old = conn.execute("SELECT payload_json FROM semantic_coverage WHERE coverage_id=?", (key,)).fetchone()
    if old and old[0] != encoded:
        raise SemanticHistoryError("semantic_coverage_conflict")
    if not old:
        _capacity(conn, len(encoded.encode()) + 1024)
        conn.execute("INSERT INTO semantic_coverage VALUES(?,?)", (key, encoded))
    return {"schema": SCHEMA, "database": payload["database"], "coverage_id": key}


def validate_semantic_coverage(coverage_ref, *, subject_kind, subject_id, policy_epoch, allowed_roots, deadline_at=None):
    if not isinstance(coverage_ref, dict) or coverage_ref.get("schema") != SCHEMA:
        raise SemanticHistoryError("semantic_coverage_ref_invalid")
    refs = coverage_ref.get("destinations", [coverage_ref])
    if not isinstance(refs, list) or not refs:
        raise SemanticHistoryError("semantic_coverage_destinations_empty")
    payloads = []
    for ref in refs:
        if not isinstance(ref, dict) or ref.get("schema") != SCHEMA:
            raise SemanticHistoryError("semantic_coverage_destination_invalid")
        path = _safe_database(ref["database"], allowed_roots)
        conn = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
        try:
            conn.execute("PRAGMA query_only=ON")
            state = _state(conn)
            _check_fence(conn, state)
            if state["policy_epoch"] != policy_epoch:
                raise SemanticHistoryError("semantic_coverage_epoch_mismatch")
            row = conn.execute("SELECT payload_json FROM semantic_coverage WHERE coverage_id=?", (ref["coverage_id"],)).fetchone()
            if not row or _hash(row[0].encode()) != ref["coverage_id"]:
                raise SemanticHistoryError("semantic_coverage_missing_or_corrupt")
            value = json.loads(row[0])
            if (value.get("subject_kind"), value.get("subject_id"), value.get("policy_epoch"), value.get("database")) != (subject_kind, subject_id, policy_epoch, str(path)):
                raise SemanticHistoryError("semantic_coverage_identity_mismatch")
            for version in value["version_ids"]:
                read_semantic_version(conn, version, deadline_at=deadline_at)
            payloads.append(value)
        finally:
            conn.close()
    return {"schema": SCHEMA, "subject_kind": subject_kind, "subject_id": subject_id, "policy_epoch": policy_epoch,
            "destinations": payloads, "version_ids": sorted({v for p in payloads for v in p["version_ids"]}),
            "artifacts": [a for p in payloads for a in p["artifacts"]]}


def copy_semantic_history(source, target):
    """Union immutable retained authority, including consolidated-only subjects."""
    if not _exists(source, "semantic_versions"):
        return {}
    ensure_schema(target)
    counts = {}
    for row in source.execute("SELECT DISTINCT blob_sha256 FROM semantic_versions WHERE blob_sha256 IS NOT NULL"):
        digest = row[0]
        _verify_blob(source, digest)
        blob = source.execute("SELECT content_sha256,media_type,size_bytes,content FROM attachment_blobs WHERE content_sha256=?", (digest,)).fetchone()
        old = target.execute("SELECT size_bytes,content FROM attachment_blobs WHERE content_sha256=?", (digest,)).fetchone()
        if old and (old[0] != blob[2] or bytes(old[1]) != bytes(blob[3])):
            raise SemanticHistoryError("semantic_copy_blob_conflict")
        if not old:
            target.execute("INSERT INTO attachment_blobs VALUES(?,?,?,?)", tuple(blob))
    for table in TABLES:
        columns = [r[1] for r in source.execute(f"PRAGMA table_info({table})")]
        if columns != [r[1] for r in target.execute(f"PRAGMA table_info({table})")]:
            raise SemanticHistoryError("semantic_copy_schema_mismatch")
        keys = [r[1] for r in source.execute(f"PRAGMA table_info({table})") if r[5]]
        count = 0
        for row in source.execute(f"SELECT * FROM {table}"):
            values = dict(zip(columns, row))
            if table == "semantic_objects" and (len(values["payload"]) != values["size_bytes"] or _hash(SCHEMA.encode() + b"\0" + bytes(values["payload"])) != values["object_id"]):
                raise SemanticHistoryError("semantic_copy_object_corrupt")
            if table == "semantic_subjects" and _hash(values["identity_json"].encode()) != values["subject_id"]:
                raise SemanticHistoryError("semantic_copy_subject_corrupt")
            if table == "semantic_coverage" and _hash(values["payload_json"].encode()) != values["coverage_id"]:
                raise SemanticHistoryError("semantic_copy_coverage_corrupt")
            where = " AND ".join(k + "=?" for k in keys)
            old = target.execute(f"SELECT * FROM {table} WHERE {where}", tuple(values[k] for k in keys)).fetchone()
            if old and tuple(old) != tuple(row):
                if table == "semantic_spans":
                    # Independent destinations may observe the same exact
                    # version different numbers of times. Do not add counts.
                    if old[4] != row[4]:
                        raise SemanticHistoryError("semantic_copy_span_conflict")
                    target.execute("UPDATE semantic_spans SET first_at=?,last_at=?,observations=?,last_operation=? WHERE " + where,
                                   (min(x for x in (old[5], row[5]) if x is not None) if old[5] or row[5] else None,
                                    max(x for x in (old[6], row[6]) if x is not None) if old[6] or row[6] else None,
                                    max(old[7], row[7]), None, *(values[k] for k in keys)))
                else:
                    raise SemanticHistoryError("semantic_copy_immutable_conflict")
            elif not old:
                target.execute(f"INSERT INTO {table} VALUES({','.join('?' for _ in columns)})", tuple(row))
                count += 1
        counts[table] = count
    for row in source.execute("SELECT version_id FROM semantic_versions"):
        read_semantic_version(target, row[0])
    _capacity(target, 0)
    return counts
