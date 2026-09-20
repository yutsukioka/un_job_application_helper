import hashlib
import json
import sqlite3
from contextlib import closing

import pytest

from jobagg.db import JobDatabase
from jobagg.pipelines.consolidation import consolidate_bundle_databases


@pytest.mark.parametrize('conflicting_notes', ['Older reference evidence', None, 'Référence 日本語'])
def test_consolidation_retains_conflicting_grade_definitions(tmp_path, conflicting_notes):
    paths = [tmp_path / name for name in ('a_jobs.sqlite3', 'b_jobs.sqlite3')]
    for path in paths:
        JobDatabase(path).initialize()
        # sqlite3's transaction context does not close the connection. Close
        # fixture writers before taking byte snapshots so a later WAL
        # checkpoint cannot look like a consolidation source mutation.
        with closing(sqlite3.connect(path)) as conn, conn:
            conn.execute("INSERT INTO grade_mappings (mapping_version,organization,raw_grade_code,normalized_raw_grade_code,notes_caveats) VALUES ('historical','Reference organization','X1','X1',?)", ('First definition' if path == paths[0] else conflicting_notes,))
    originals = [path.read_bytes() for path in paths]
    result = consolidate_bundle_databases(output_dir=tmp_path)
    with closing(sqlite3.connect(result.db_path)) as conn:
        conn.row_factory = sqlite3.Row
        assert conn.execute("SELECT notes_caveats FROM grade_mappings WHERE mapping_version='historical'").fetchone()[0] == 'First definition'
        rows = conn.execute('SELECT * FROM grade_mapping_conflicts').fetchall()
        assert len(rows) == 1
        row = rows[0]
        assert row['source_database'] == 'b_jobs.sqlite3'
        payload = json.loads(row['payload_json'])
        assert payload['notes_caveats'] == conflicting_notes
        assert set(payload) == {r[1] for r in conn.execute('PRAGMA table_info(grade_mappings)')}
        assert row['payload_sha256'] == hashlib.sha256(row['payload_json'].encode()).hexdigest()
    assert [path.read_bytes() for path in paths] == originals
    # Rebuilding must retain exactly one source association, not append duplicates.
    result = consolidate_bundle_databases(output_dir=tmp_path)
    with closing(sqlite3.connect(result.db_path)) as conn:
        assert conn.execute('SELECT count(*) FROM grade_mapping_conflicts').fetchone()[0] == 1
