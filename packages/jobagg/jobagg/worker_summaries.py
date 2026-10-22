"""Transactional task-count projection; explicit schema preparation precedes fencing.

Routine readers visit a bounded set of source/kind/status counters. The history
query remains an explicit compatibility path for unmigrated read-only stores.
"""


def ensure(conn):
    exists = conn.execute("SELECT 1 FROM sqlite_master WHERE name='remediation_task_counts'").fetchone()
    if exists:
        return
    conn.execute('CREATE TABLE remediation_task_counts(source_id TEXT,kind TEXT,status TEXT,n INTEGER NOT NULL CHECK(n>=0),PRIMARY KEY(source_id,kind,status))')
    conn.execute('INSERT INTO remediation_task_counts SELECT source_id,kind,status,count(*) FROM remediation_tasks GROUP BY source_id,kind,status')
    # SQLite REPLACE can suppress DELETE triggers. Reject colliding insertion;
    # the supported enqueue path updates existing immutable task identities.
    conn.execute("CREATE TRIGGER remediation_task_counts_identity_guard BEFORE INSERT ON remediation_tasks "
                 "WHEN EXISTS(SELECT 1 FROM remediation_tasks WHERE task_id=new.task_id) "
                 "BEGIN SELECT RAISE(ABORT,'task_identity_requires_update'); END")
    for operation in ('INSERT', 'DELETE', 'UPDATE'):
        statements = []
        if operation in {'DELETE', 'UPDATE'}:
            statements.append('UPDATE remediation_task_counts SET n=n-1 WHERE source_id=old.source_id AND kind=old.kind AND status=old.status;')
        if operation in {'INSERT', 'UPDATE'}:
            statements.append('INSERT INTO remediation_task_counts VALUES(new.source_id,new.kind,new.status,1) ON CONFLICT(source_id,kind,status) DO UPDATE SET n=n+1;')
        conn.execute(f'CREATE TRIGGER remediation_task_counts_{operation.lower()} AFTER {operation} ON remediation_tasks BEGIN ' + ''.join(statements) + ' END')


def counts(conn, source_id=None):
    params = () if source_id is None else (source_id,)
    predicate = '' if source_id is None else ' WHERE source_id=?'
    if conn.execute("SELECT 1 FROM sqlite_master WHERE name='remediation_task_counts'").fetchone():
        return conn.execute('SELECT source_id,kind,status,n FROM remediation_task_counts' + predicate +
                            (' AND' if predicate else ' WHERE') + ' n>0', params).fetchall()
    return conn.execute('SELECT source_id,kind,status,count(*) AS n FROM remediation_tasks' + predicate +
                        ' GROUP BY source_id,kind,status', params).fetchall()
