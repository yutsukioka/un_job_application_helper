"""Budget-independent worker revision fence and compact active-run authority.

Activation is an explicit, owner-held migration on an initialized worker DB.
Unknown legacy history is never inferred empty. No compression/retention coupling.
"""
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import sqlite3

SCHEMA = 'jobagg-runtime-authority-1'


class RuntimeRepairRequired(ValueError):
    pass


def _quote(name):
    return '"' + name.replace('"', '""') + '"'


def _tables(conn):
    excluded = {row[1] for row in conn.execute('PRAGMA table_list') if row[2] in {'virtual', 'shadow'}}
    return sorted(row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
                  if row[0] not in excluded and row[0] not in {'semantic_history_state', 'jobagg_runtime_state'} and not row[0].startswith('sqlite_'))


def _triggers(tables):
    result = {}
    for table in tables:
        for operation in ('INSERT', 'UPDATE', 'DELETE'):
            name = 'jobagg_runtime_' + hashlib.sha256((table + operation).encode()).hexdigest()
            result[name] = (f'CREATE TRIGGER {_quote(name)} BEFORE {operation} ON {_quote(table)} BEGIN '
                            "SELECT CASE WHEN jobagg_runtime_write_allowed() != 1 THEN RAISE(ABORT,'runtime_writer_not_admitted') END; "
                            'UPDATE jobagg_runtime_state SET revision=revision+1 WHERE id=1; END')
    return result


def state(conn):
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='jobagg_runtime_state'").fetchone():
        return None
    cursor = conn.execute('SELECT * FROM jobagg_runtime_state WHERE id=1')
    row = cursor.fetchone()
    if row is None:
        raise RuntimeRepairRequired('runtime_state_missing')
    return dict(zip([field[0] for field in cursor.description], row))


def validate(conn):
    value = state(conn)
    if value is None or value['schema'] != SCHEMA:
        raise RuntimeRepairRequired('runtime_authority_missing_or_unknown')
    tables = _tables(conn)
    if tables != json.loads(value['tables_json']):
        raise RuntimeRepairRequired('runtime_unfenced_schema')
    for name, sql in _triggers(tables).items():
        actual = conn.execute("SELECT sql FROM sqlite_master WHERE name=?", (name,)).fetchone()
        if not actual or ' '.join(actual[0].split()) != ' '.join(sql.split()):
            raise RuntimeRepairRequired('runtime_fence_changed')
    return value


def activate(conn, *, epoch, owner_path, bootstrap_receipt):
    """Caller proves shared owner, explicit complete historical reconciliation.

    Receipt must cover all accepted/published identities and unresolved controls;
    this function refuses a nonempty pending set rather than dropping it.
    """
    if not epoch or not bootstrap_receipt.get('reconciled') or not bootstrap_receipt.get('source_sha256'):
        raise RuntimeRepairRequired('runtime_checkpointed_bootstrap_required')
    if bootstrap_receipt.get('unresolved_runs') or bootstrap_receipt.get('unpublished_identities'):
        raise RuntimeRepairRequired('runtime_bootstrap_has_unresolved_work')
    if state(conn):
        existing = validate(conn)
        if existing['epoch'] != epoch or existing['owner_path'] != str(Path(owner_path).absolute()):
            raise RuntimeRepairRequired('runtime_activation_binding_changed')
        return existing
    conn.execute('CREATE TABLE jobagg_runtime_state(id INTEGER PRIMARY KEY CHECK(id=1),schema TEXT,epoch TEXT,owner_path TEXT,revision INTEGER,tables_json TEXT,bootstrap_json TEXT,run_id TEXT,phase TEXT,config_sha256 TEXT,start_revision INTEGER)')
    tables = _tables(conn)
    conn.execute('INSERT INTO jobagg_runtime_state VALUES(1,?,?,?,?,?,?,NULL,\'idle\',NULL,0)',
                 (SCHEMA, epoch, str(Path(owner_path).absolute()), 0, json.dumps(tables), json.dumps(bootstrap_receipt, sort_keys=True)))
    for sql in _triggers(tables).values():
        conn.execute(sql)
    return validate(conn)


def binding(path):
    with sqlite3.connect(Path(path).absolute().as_uri() + '?mode=ro', uri=True) as conn:
        if state(conn) is None:
            return None
        value = validate(conn)
        return {'schema': SCHEMA, 'writer_fenced': True, 'policy_epoch': value['epoch'],
                'revision': value['revision'], 'owner_path': value['owner_path'],
                'run_id': value['run_id'], 'phase': value['phase'],
                'bootstrap_sha256': hashlib.sha256(value['bootstrap_json'].encode()).hexdigest()}


def begin(conn, run_id, config_sha256):
    value = validate(conn)
    if value['phase'] != 'idle':
        raise RuntimeRepairRequired('runtime_active_work_requires_continuation')
    conn.execute("UPDATE jobagg_runtime_state SET run_id=?,phase='worker',config_sha256=?,start_revision=revision WHERE id=1", (run_id, config_sha256))


def transition(conn, run_id, old, new):
    validate(conn)
    if (old, new) not in {('worker', 'accepted'), ('accepted', 'publisher'), ('publisher', 'idle')}:
        raise RuntimeRepairRequired('runtime_invalid_transition')
    changed = conn.execute('UPDATE jobagg_runtime_state SET phase=? WHERE id=1 AND run_id=? AND phase=?', (new, run_id, old)).rowcount
    if changed != 1:
        raise RuntimeRepairRequired('runtime_stale_run_or_phase')


@contextmanager
def writer(conn, *, run_id, owner_path):
    value = state(conn)
    if value is None:
        yield  # Existing generations retain their original contract until explicit migration.
        return
    value = validate(conn)
    if value['owner_path'] != str(Path(owner_path).absolute()) or value['run_id'] != run_id or value['phase'] != 'worker':
        conn.create_function('jobagg_runtime_write_allowed', 0, lambda: 0)
        yield  # Read-only use is valid; every mutation remains denied by triggers.
        return
    # Caller holds the actual inherited shared owner for this whole context.
    def allowed():
        current = state(conn)
        return int(current['run_id'] == run_id and current['phase'] == 'worker' and current['epoch'] == value['epoch'])
    conn.create_function('jobagg_runtime_write_allowed', 0, allowed)
    try:
        yield
    finally:
        conn.create_function('jobagg_runtime_write_allowed', 0, lambda: 0)


def proven_empty(conn, run_id, *, accepted_progress):
    value = validate(conn)
    return (value['run_id'] == run_id and value['phase'] == 'worker'
            and type(accepted_progress) is int and accepted_progress == 0
            and value['revision'] == value['start_revision'])


def close_empty(conn, run_id, *, accepted_progress):
    if not proven_empty(conn, run_id, accepted_progress=accepted_progress):
        raise RuntimeRepairRequired('runtime_backlog_not_proven_empty')
    conn.execute("UPDATE jobagg_runtime_state SET phase='idle' WHERE id=1 AND run_id=?", (run_id,))
