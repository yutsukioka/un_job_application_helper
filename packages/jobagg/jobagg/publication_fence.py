"""Budget-independent mutation clocks for publication destinations.

Explicit owner-held preparation installs the complete schema fence. Ordinary
readers need no live owner; every write needs the publisher's live owner scope.
Immutable export identities plus these clocks support bounded no-op checks.
"""
from contextlib import closing, contextmanager
from contextvars import ContextVar
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import stat
import uuid

from jobagg.publication_deadline import expired

TABLE = 'jobagg_publication_revision'
SCHEMA = 'publication-revision-2'
_SCOPE = ContextVar('publication_writer_scope', default=None)


def _quote(name):
    return '"'+name.replace('"','""')+'"'


def _state(conn):
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE name=?", (TABLE,)).fetchone():
        return None
    cursor = conn.execute(f'SELECT * FROM {TABLE} WHERE id=1')
    row = cursor.fetchone()
    if row is None:
        raise ValueError('publication_revision_missing')
    return dict(zip([item[0] for item in cursor.description], row))


def _schema_hash(conn):
    return hashlib.sha256(json.dumps([tuple(row) for row in conn.execute(
        'SELECT name,type,sql FROM sqlite_master ORDER BY name')]).encode()).hexdigest()


def _tables(conn):
    excluded = {row[1] for row in conn.execute('PRAGMA table_list') if row[2] in {'virtual','shadow'}}
    return [row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
            if row[0] not in excluded and row[0] not in {TABLE,'semantic_history_state'} and not row[0].startswith('sqlite_')]


def _validate(conn):
    value = _state(conn)
    if value is None:
        return None
    if value['schema'] != SCHEMA or value['schema_sha256'] != _schema_hash(conn):
        raise ValueError('publication_fence_schema_changed')
    return value


def _owner(owner_fd, path):
    import fcntl
    path = Path(path).absolute()
    if path.resolve() != path:
        raise ValueError('publication_owner_alias')
    actual, expected = os.fstat(owner_fd), path.stat()
    if (actual.st_dev, actual.st_ino) != (expected.st_dev, expected.st_ino):
        raise ValueError('publication_owner_descriptor_differs')
    fcntl.flock(owner_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    return actual


def activate(conn, *, owner_path, owner_fd, epoch):
    """Explicit migration only, after complete target schema/recovery preparation."""
    owner = _owner(owner_fd, owner_path)
    existing = _validate(conn)
    if existing:
        if existing['epoch'] != epoch or existing['owner_path'] != str(Path(owner_path).absolute()):
            raise ValueError('publication_fence_epoch_changed')
        return
    conn.execute(f'CREATE TABLE {TABLE}(id INTEGER PRIMARY KEY CHECK(id=1),schema TEXT,epoch TEXT,store_id TEXT,owner_path TEXT,owner_device INTEGER,owner_inode INTEGER,revision INTEGER,schema_sha256 TEXT)')
    conn.execute(f'INSERT INTO {TABLE} VALUES(1,?,?,?,?,?,?,0,?)',
                 (SCHEMA, epoch, uuid.uuid4().hex, str(Path(owner_path).absolute()), owner.st_dev, owner.st_ino, 'preparing'))
    for table in _tables(conn):
        for operation in ('INSERT','UPDATE','DELETE'):
            name = 'publication_fence_'+hashlib.sha256((table+operation).encode()).hexdigest()
            conn.execute(f'CREATE TRIGGER {_quote(name)} BEFORE {operation} ON {_quote(table)} BEGIN '
                         "SELECT CASE WHEN jobagg_publication_write_allowed()!=1 THEN RAISE(ABORT,'publication_writer_not_admitted') END; "
                         f'UPDATE {TABLE} SET revision=revision+1 WHERE id=1; END')
    conn.create_function('jobagg_publication_control_allowed', 0, lambda: 1)
    for operation in ('INSERT', 'DELETE'):
        conn.execute(f"CREATE TRIGGER publication_control_{operation.lower()} BEFORE {operation} ON {TABLE} BEGIN "
                     "SELECT CASE WHEN jobagg_publication_control_allowed()!=1 THEN RAISE(ABORT,'publication_control_not_admitted') END; END")
    conn.execute(f"CREATE TRIGGER publication_control_identity BEFORE UPDATE OF schema,epoch,store_id,owner_path,owner_device,owner_inode,schema_sha256 ON {TABLE} BEGIN "
                 "SELECT CASE WHEN jobagg_publication_control_allowed()!=1 THEN RAISE(ABORT,'publication_control_not_admitted') END; END")
    conn.execute(f"CREATE TRIGGER publication_control_revision BEFORE UPDATE OF revision ON {TABLE} BEGIN "
                 "SELECT CASE WHEN jobagg_publication_write_allowed()!=1 THEN RAISE(ABORT,'publication_writer_not_admitted') END; END")
    conn.execute(f'UPDATE {TABLE} SET schema_sha256=? WHERE id=1', (_schema_hash(conn),))
    conn.create_function('jobagg_publication_control_allowed', 0, lambda: 0)
    conn.create_function('jobagg_publication_write_allowed', 0, lambda: 0)


@contextmanager
def writer_scope(owner_fd, owner_path, output_root):
    owner = _owner(owner_fd, owner_path)
    root = Path(output_root).absolute()
    if root.resolve() != root:
        raise ValueError('publication_output_alias')
    scope = {'fd': owner_fd, 'owner_path': str(Path(owner_path).absolute()),
             'device': owner.st_dev, 'inode': owner.st_ino, 'root': root}
    token = _SCOPE.set(scope)
    try:
        yield
    finally:
        _SCOPE.reset(token)


def transaction_authorizer(conn):
    """Compose this guard with the semantic/storage authorizer, never replace it."""
    value = _validate(conn)
    if value is None:
        return None
    scope = _SCOPE.get()
    triggers = {
        'publication_fence_' + hashlib.sha256((table + operation).encode()).hexdigest()
        for table in _tables(conn) for operation in ('INSERT', 'UPDATE', 'DELETE')
    }

    def authorize(action, first, second, database, trigger):
        # Only the domain triggers may advance the clock. Ordinary owner-held
        # maintenance still cannot reset it and conceal a comparator change.
        if first == TABLE and action in {sqlite3.SQLITE_INSERT, sqlite3.SQLITE_DELETE}:
            return sqlite3.SQLITE_DENY
        if first == TABLE and action == sqlite3.SQLITE_UPDATE and (
            second != 'revision' or trigger not in triggers
        ):
            return sqlite3.SQLITE_DENY
        if ((action == sqlite3.SQLITE_TRANSACTION and first == 'COMMIT') or
                (action == sqlite3.SQLITE_SAVEPOINT and first == 'RELEASE')):
            try:
                _verify_live_scope(scope, value)
            except (ValueError, OSError, TypeError):
                return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK
    return authorize


def _verify_live_scope(scope, value):
    from jobagg.runtime_source_head import owner
    if scope is None or _SCOPE.get() is not scope:
        raise ValueError('publication_writer_scope_expired')
    owner(scope['fd'], scope['owner_path'])
    if (value['owner_path'] != scope['owner_path'] or
            (value['owner_device'], value['owner_inode']) != (scope['device'], scope['inode'])):
        raise ValueError('publication_writer_scope_differs')


def configure(conn, path, *, read_only=False, authorizer=None):
    conn.create_function('jobagg_publication_write_allowed', 0, lambda: 0)
    conn.create_function('jobagg_publication_control_allowed', 0, lambda: 0)
    if read_only or _state(conn) is None:
        return
    value = _validate(conn)
    scope = _SCOPE.get()
    if scope is None:
        return  # Reads remain available; guarded DML is denied.
    path = Path(path).absolute()
    if path.resolve() != path or path.parent != scope['root']:
        raise ValueError('publication_writer_scope_differs')
    _verify_live_scope(scope, value)
    authorize = authorizer or transaction_authorizer(conn)

    def allowed():
        try:
            _verify_live_scope(scope, value)
        except (ValueError, OSError, TypeError):
            return 0
        # Invalidate a cached COMMIT on each mutation. If a semantic grant is
        # active, its composed callback retains both lease and owner checks.
        conn.set_authorizer(authorize)
        return 1
    conn.create_function('jobagg_publication_write_allowed', 0, allowed)
    conn.set_authorizer(authorize)


def binding(path):
    path = Path(path).absolute()
    if path.resolve() != path or not path.is_file():
        raise ValueError('publication_database_alias_or_missing')
    info = path.stat()
    with closing(sqlite3.connect(path.as_uri()+'?mode=ro', uri=True, timeout=1)) as conn:
        value = _validate(conn)
    if value is None:
        return None
    return {'path': str(path), 'device': info.st_dev, 'inode': info.st_ino,
            'schema': value['schema'], 'epoch': value['epoch'], 'store_id': value['store_id'],
            'revision': value['revision'], 'schema_sha256': value['schema_sha256']}


def _file_identity(path):
    path = Path(path).absolute()
    if path.resolve() != path:
        raise ValueError('publication_export_alias')
    info = path.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o222:
        raise ValueError('publication_export_not_sealed')
    return {'path': str(path), 'device': info.st_dev, 'inode': info.st_ino,
            'size': info.st_size, 'mtime_ns': info.st_mtime_ns, 'ctime_ns': info.st_ctime_ns,
            'mode': info.st_mode, 'links': info.st_nlink}


def publication_scope(registry_path, output_root):
    from jobagg.pipelines.sync_source import load_sources
    from jobagg.pipelines.bundles import source_output_slug
    from jobagg.pipelines.bundles import source_output_paths
    root = Path(output_root).absolute()
    paths = sorted({root / (source_output_slug(source)+'_jobs.sqlite3')
                    for source in load_sources(registry_path) if source.enabled} | {root/'all_jobs.sqlite3'})
    exports = {}
    for path in paths:
        names = source_output_paths(root, path.name.removesuffix('_jobs.sqlite3'))
        exports[str(path)] = [str(names[key]) for key in ('current_json','current_csv','history_json','history_csv')]
    return {'output_root': str(root), 'databases': [str(path) for path in paths], 'exports': exports}


def seal_boundary(databases, export_manifest, *, output_root, previous=None):
    """Called only after verified generation/export readback under the owner."""
    root = Path(output_root).absolute()
    stores = [binding(path) for path in databases]
    if not stores or any(value is None for value in stores):
        return None  # Legacy destinations have no authoritative mutation clock.
    from jobagg.pipelines.bundles import source_output_paths
    previous_stores = {item['path']: item for item in (previous or {}).get('databases', [])}
    previous_exports = {item['path']: item for item in (previous or {}).get('exports', [])}
    merged = dict(export_manifest)
    for store in stores:
        paths = source_output_paths(root, Path(store['path']).name.removesuffix('_jobs.sqlite3'))
        required = [str(paths[key]) for key in ('current_json','current_csv','history_json','history_csv')]
        for path in required:
            if path not in merged:
                prior = previous_exports.get(path)
                if previous_stores.get(store['path']) != store or prior is None:
                    return None
                if _file_identity(path) != {key: value for key,value in prior.items() if key != 'sha256'}:
                    return None
                merged[path] = {'sha256': prior['sha256']}
    exports = []
    for path, receipt in sorted(merged.items()):
        if expired(None):
            raise TimeoutError('publication boundary deadline')
        target = Path(path)
        if target.parent != root or not isinstance(receipt.get('sha256'), str):
            raise ValueError('publication_export_scope_differs')
        # The export writer verified these exact bytes. Seal their inode before
        # recording a cheap identity; any later chmod/write/replace invalidates it.
        if target.stat().st_mode & 0o222:
            target.chmod(target.stat().st_mode & ~0o222)
        exports.append({**_file_identity(target), 'sha256': receipt['sha256']})
    if not exports:
        return None
    result = {'schema': 'publication-boundary-1', 'output_root': str(root),
              'databases': stores, 'exports': exports}
    result['sha256'] = hashlib.sha256(json.dumps(result, sort_keys=True).encode()).hexdigest()
    return result


def verify_boundary(value, *, expected_scope=None):
    if not isinstance(value, dict) or value.get('schema') != 'publication-boundary-1':
        return False
    if expected_scope is not None:
        required_exports = {path for paths in expected_scope['exports'].values() for path in paths}
        if (value.get('output_root') != expected_scope['output_root'] or
                {item['path'] for item in value.get('databases', [])} != set(expected_scope['databases']) or
                {item['path'] for item in value.get('exports', [])} != required_exports):
            return False
    unsigned = {key: item for key, item in value.items() if key != 'sha256'}
    if hashlib.sha256(json.dumps(unsigned, sort_keys=True).encode()).hexdigest() != value.get('sha256'):
        return False
    try:
        for expected in value['databases']:
            if expired(None) or binding(expected['path']) != expected:
                return False
        for expected in value['exports']:
            if expired(None) or _file_identity(expected['path']) != {key: item for key,item in expected.items() if key != 'sha256'}:
                return False
        return bool(value['databases'] and value['exports'])
    except (OSError, ValueError, sqlite3.Error):
        return False


def prepare_target_schema(conn):
    """Explicit preparation on an already initialized JobDatabase, under owner."""
    from jobagg.pipelines.live_inventory import apply_frames
    conn.execute("CREATE TABLE IF NOT EXISTS jobagg_publication_receipts(publication_key TEXT PRIMARY KEY,generation_id TEXT NOT NULL,job_key TEXT NOT NULL,committed_at TEXT NOT NULL)")
    apply_frames(conn, [], 'schema-preparation')


def source_paths(registry_path, output_root):
    from jobagg.pipelines.sync_source import load_sources
    from jobagg.pipelines.bundles import source_output_slug
    return {source.id: str(Path(output_root).absolute() / (source_output_slug(source)+'_jobs.sqlite3'))
            for source in load_sources(registry_path) if source.enabled}
