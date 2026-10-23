"""Actual owner lifetime and stale source restore must precede no-op truth."""

from contextlib import closing
import sqlite3
import time

import pytest

from jobagg import publication_work as work, runtime_authority as authority
from jobagg.remediation_worker import shared_owner


@pytest.fixture
def source(tmp_path):
    path = tmp_path / "source.sqlite3"
    owner_path = tmp_path / "owner.lock"
    with shared_owner(owner_path) as fd, closing(sqlite3.connect(path)) as conn:
        conn.executescript("""CREATE TABLE jobs(job_key TEXT PRIMARY KEY,source_id TEXT,external_id TEXT,description TEXT,closes_at TEXT);
            CREATE TABLE remediation_observations(job_key TEXT PRIMARY KEY,source_id TEXT);
            CREATE TABLE remediation_sources(source_id TEXT PRIMARY KEY,last_list_at REAL,listing_ids TEXT,listing_proof TEXT);""")
        work.prepare(conn, contract_sha256="configuration", epoch="epoch")
        assert work.bootstrap(conn, limit=10, deadline_at=time.monotonic() + 5)
        authority.activate(
            conn,
            epoch="epoch",
            owner_path=owner_path,
            owner_fd=fd,
            bootstrap_receipt={"reconciled": True, "source_sha256": "a" * 64},
        )
        conn.commit()
    return path, owner_path


def test_owner_descriptor_is_required_and_late_commit_cannot_survive_release(source):
    path, owner_path = source
    with shared_owner(owner_path) as fd, closing(sqlite3.connect(path)) as conn:
        authority.begin(conn, "owned", "configuration", owner_fd=fd)
        conn.commit()
    conn = sqlite3.connect(path)
    work.configure(conn)
    with authority.writer(conn, run_id="owned", owner_path=owner_path):
        with pytest.raises(sqlite3.DatabaseError):
            conn.execute("INSERT INTO jobs VALUES('s:1','s','1','unadmitted',NULL)")
    conn.rollback()
    with shared_owner(owner_path) as fd:
        with authority.writer(conn, run_id="owned", owner_path=owner_path, owner_fd=fd):
            conn.execute("INSERT INTO jobs VALUES('s:1','s','1','uncommitted',NULL)")
    with pytest.raises(sqlite3.DatabaseError):
        conn.commit()
    conn.rollback()
    conn.close()
    with sqlite3.connect(path) as reader:
        assert reader.execute("SELECT count(*) FROM jobs").fetchone()[0] == 0
    # The uncommitted external transaction remains explicit uncertainty. A
    # source rollback does not automatically certify that no dispatch occurred.
    with pytest.raises(ValueError, match="uncertain_transaction"):
        authority.binding(path)


def test_in_place_sqlite_restore_cannot_erase_accepted_dirty_versions(source, tmp_path):
    path, owner_path = source
    backup = tmp_path / "prior.sqlite3"
    identity = (path.stat().st_dev, path.stat().st_ino)
    with closing(sqlite3.connect(path)) as live, closing(sqlite3.connect(backup)) as saved:
        live.backup(saved)
    with shared_owner(owner_path) as fd, closing(sqlite3.connect(path)) as conn:
        work.configure(conn)
        authority.begin(conn, "newer", "configuration", owner_fd=fd)
        conn.commit()
        with authority.writer(conn, run_id="newer", owner_path=owner_path, owner_fd=fd):
            conn.execute("INSERT INTO jobs VALUES('s:1','s','1','accepted after backup',NULL)")
        conn.commit()
    assert authority.binding(path)["revision"] > 0
    with closing(sqlite3.connect(backup)) as saved, closing(sqlite3.connect(path)) as live:
        saved.backup(live)
    assert (path.stat().st_dev, path.stat().st_ino) == identity
    with pytest.raises(ValueError, match="restore_or_uncertain"):
        authority.binding(path)


def test_source_control_phase_and_revision_cannot_be_rewritten_by_raw_writer(source):
    path, _ = source
    with sqlite3.connect(path) as conn:
        for statement in (
            "UPDATE jobagg_runtime_state SET phase='idle'",
            "UPDATE jobagg_runtime_state SET revision=0",
        ):
            with pytest.raises(sqlite3.DatabaseError):
                conn.execute(statement)
            conn.rollback()
