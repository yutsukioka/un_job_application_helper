"""Budget-independent worker revision fence and compact active-run authority.

Activation is an explicit, owner-held migration on an initialized worker DB.
Unknown legacy history is never inferred empty. No compression/retention coupling.
"""
from contextlib import contextmanager
import os
import hashlib
import json
from pathlib import Path
import sqlite3

SCHEMA = 'jobagg-runtime-authority-2'


class RuntimeRepairRequired(ValueError):
    pass


def _quote(name):
    return '"' + name.replace('"', '""') + '"'


def _tables(conn):
    excluded = {row[1] for row in conn.execute('PRAGMA table_list') if row[2] in {'virtual', 'shadow'}}
    return sorted(row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
                  if row[0] not in excluded and row[0] not in {'semantic_history_state', 'jobagg_runtime_state'} and not row[0].startswith(('sqlite_', 'jobagg_dirty_')))


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


def _schema_hash(conn):
    rows = list(conn.execute("SELECT name,type,sql FROM sqlite_master ORDER BY name"))
    return hashlib.sha256(json.dumps([tuple(row) for row in rows], sort_keys=True).encode()).hexdigest()


def validate(conn):
    value = state(conn)
    if value is None or value['schema'] != SCHEMA:
        raise RuntimeRepairRequired('runtime_authority_missing_or_unknown')
    if (not isinstance(value['epoch'], str) or not value['epoch']
            or value['phase'] not in {'idle', 'worker', 'accepted', 'publisher'}
            or type(value['revision']) is not int or value['revision'] < 0
            or type(value['start_revision']) is not int or not 0 <= value['start_revision'] <= value['revision']):
        raise RuntimeRepairRequired('runtime_authority_malformed')
    if value['phase'] != 'idle' and (not isinstance(value['run_id'], str) or Path(value['run_id']).name != value['run_id']):
        raise RuntimeRepairRequired('runtime_active_identity_invalid')
    owner = Path(value['owner_path']).stat()
    bootstrap = json.loads(value['bootstrap_json'])
    expected_owner = bootstrap.get('owner_identity')
    if bootstrap.get('schema_sha256') != _schema_hash(conn):
        raise RuntimeRepairRequired('runtime_schema_binding_changed')
    if expected_owner != {'device': owner.st_dev, 'inode': owner.st_ino}:
        raise RuntimeRepairRequired('runtime_owner_identity_changed')
    tables = _tables(conn)
    if tables != json.loads(value['tables_json']):
        raise RuntimeRepairRequired('runtime_unfenced_schema')
    actual = dict(conn.execute("SELECT name,sql FROM sqlite_master WHERE type='trigger'"))
    for name, sql in _triggers(tables).items():
        if name not in actual or ' '.join(actual[name].split()) != ' '.join(sql.split()):
            raise RuntimeRepairRequired('runtime_fence_changed')
    return value


def activate(conn, *, epoch, owner_path, owner_fd, bootstrap_receipt):
    """Caller proves shared owner, explicit complete historical reconciliation.

    Receipt must cover all accepted/published identities and unresolved controls;
    this function refuses a nonempty pending set rather than dropping it.
    """
    import fcntl
    owner_path = Path(owner_path).absolute()
    if owner_path.resolve() != owner_path:
        raise RuntimeRepairRequired('runtime_owner_aliased')
    actual, expected = os.fstat(owner_fd), owner_path.stat()
    if (actual.st_dev, actual.st_ino) != (expected.st_dev, expected.st_ino):
        raise RuntimeRepairRequired('runtime_owner_descriptor_differs')
    fcntl.flock(owner_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    bootstrap_receipt = {**bootstrap_receipt, 'owner_identity': {'device': actual.st_dev, 'inode': actual.st_ino}}
    if not epoch or bootstrap_receipt.get('reconciled') is not True or not bootstrap_receipt.get('source_sha256'):
        raise RuntimeRepairRequired('runtime_checkpointed_bootstrap_required')
    if bootstrap_receipt.get('unresolved_runs') or bootstrap_receipt.get('unpublished_identities'):
        raise RuntimeRepairRequired('runtime_bootstrap_has_unresolved_work')
    if state(conn):
        existing = validate(conn)
        if existing['epoch'] != epoch or existing['owner_path'] != str(Path(owner_path).absolute()):
            raise RuntimeRepairRequired('runtime_activation_binding_changed')
        return existing
    conn.execute("CREATE TABLE jobagg_runtime_state(id INTEGER PRIMARY KEY CHECK(id=1),schema TEXT,epoch TEXT,owner_path TEXT,revision INTEGER,tables_json TEXT,bootstrap_json TEXT,run_id TEXT,phase TEXT,config_sha256 TEXT,start_revision INTEGER,control_json TEXT NOT NULL DEFAULT '{}')")
    tables = _tables(conn)
    conn.execute('INSERT INTO jobagg_runtime_state VALUES(1,?,?,?,?,?,?,NULL,\'idle\',NULL,0,\'{}\')',
                 (SCHEMA, epoch, str(Path(owner_path).absolute()), 0, json.dumps(tables), json.dumps(bootstrap_receipt, sort_keys=True)))
    for sql in _triggers(tables).values():
        conn.execute(sql)
    for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'jobagg_dirty_%'").fetchall():
        for operation in ('INSERT', 'UPDATE', 'DELETE'):
            name = 'runtime_control_' + hashlib.sha256((row[0] + operation).encode()).hexdigest()
            conn.execute(f'CREATE TRIGGER {_quote(name)} BEFORE {operation} ON {_quote(row[0])} BEGIN '
                "SELECT CASE WHEN jobagg_runtime_write_allowed()!=1 THEN RAISE(ABORT,'runtime_control_not_admitted') END; END")
    if bootstrap_receipt.get('publication_boundary') is not None:
        from jobagg.publication_fence import verify_boundary
        if not verify_boundary(bootstrap_receipt['publication_boundary']):
            raise RuntimeRepairRequired('runtime_publication_bootstrap_boundary_invalid')
        conn.execute('UPDATE jobagg_runtime_state SET control_json=? WHERE id=1',
                     (json.dumps({'publication_cursor': bootstrap_receipt['publication_boundary']}, sort_keys=True),))
    bootstrap_receipt['schema_sha256'] = _schema_hash(conn)
    conn.execute('UPDATE jobagg_runtime_state SET bootstrap_json=? WHERE id=1', (json.dumps(bootstrap_receipt, sort_keys=True),))
    return validate(conn)


def binding(path):
    from contextlib import closing
    with closing(sqlite3.connect(Path(path).absolute().as_uri() + '?mode=ro', uri=True)) as conn:
        if state(conn) is None:
            return None
        value = validate(conn)
        return {'schema': SCHEMA, 'writer_fenced': True, 'policy_epoch': value['epoch'],
                'revision': value['revision'], 'owner_path': value['owner_path'],
                'run_id': value['run_id'], 'phase': value['phase'],
                'bootstrap_sha256': hashlib.sha256(value['bootstrap_json'].encode()).hexdigest()}


def begin(conn, run_id, config_sha256, *, control_dir=None, publication_scope=None):
    value = validate(conn)
    if value['phase'] != 'idle':
        raise RuntimeRepairRequired('runtime_active_work_requires_continuation')
    control = json.loads(value['control_json'])
    control.update(publication_sequence=0, publication_control_dir=str(control_dir) if control_dir else None,
                   publication_scope=publication_scope)
    conn.execute("UPDATE jobagg_runtime_state SET run_id=?,phase='worker',config_sha256=?,start_revision=revision,control_json=? WHERE id=1",
                 (run_id, config_sha256, json.dumps(control, sort_keys=True)))


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


def proven_empty(conn, run_id, *, accepted_progress, publication_empty_check=None):
    value = validate(conn)
    if not (value['run_id'] == run_id and value['phase'] == 'worker'
            and type(accepted_progress) is int and accepted_progress == 0
            and value['revision'] == value['start_revision']):
        return False
    if publication_empty_check is not None:
        return publication_empty_check(value) is True
    from jobagg.publication_fence import verify_boundary
    from jobagg.publication_work import exists as work_exists, vector
    control = json.loads(value['control_json'])
    if work_exists(conn) and not vector(conn, contract_sha256=value['config_sha256'], now=__import__('time').time())['empty']:
        return False
    return (control.get('publication_scope') is not None and
            verify_boundary(control.get('publication_cursor'), expected_scope=control['publication_scope']))


def close_empty(conn, run_id, *, accepted_progress, publication_empty_check=None):
    if not proven_empty(conn, run_id, accepted_progress=accepted_progress, publication_empty_check=publication_empty_check):
        raise RuntimeRepairRequired('runtime_backlog_not_proven_empty')
    conn.execute("UPDATE jobagg_runtime_state SET phase='idle' WHERE id=1 AND run_id=?", (run_id,))


def finish_publication(conn, run_id):
    """Finalize the exact proven generation, including intent→phase crash gaps."""
    value = validate(conn)
    if value['run_id'] != run_id:
        raise RuntimeRepairRequired('runtime_completion_belongs_to_another_run')
    if value['phase'] == 'worker':
        transition(conn, run_id, 'worker', 'accepted')
        value['phase'] = 'accepted'
    if value['phase'] == 'accepted':
        transition(conn, run_id, 'accepted', 'publisher')
        value['phase'] = 'publisher'
    if value['phase'] == 'publisher':
        transition(conn, run_id, 'publisher', 'idle')


def publication_control(conn):
    return json.loads(validate(conn)['control_json'])


def remember_publication_boundary(conn, run_id, boundary):
    value = validate(conn)
    if value['run_id'] != run_id:
        raise RuntimeRepairRequired('runtime_publication_boundary_run_differs')
    from jobagg.publication_fence import verify_boundary
    control = json.loads(value['control_json'])
    if not verify_boundary(boundary, expected_scope=control.get('publication_scope')):
        raise RuntimeRepairRequired('runtime_publication_boundary_not_verified')
    control['publication_cursor'] = boundary
    conn.execute('UPDATE jobagg_runtime_state SET control_json=? WHERE id=1', (json.dumps(control, sort_keys=True),))


@contextmanager
def controller(conn, *, run_id, owner_fd):
    from jobagg.publication_fence import _owner
    value = validate(conn)
    if value['run_id'] != run_id:
        raise RuntimeRepairRequired('runtime_controller_run_differs')
    _owner(owner_fd, value['owner_path'])
    conn.create_function('jobagg_runtime_write_allowed', 0, lambda: 1)
    try:
        yield
    finally:
        conn.create_function('jobagg_runtime_write_allowed', 0, lambda: 0)
