"""Actual owner lifetime and stale source restore must precede no-op truth."""

from contextlib import closing
import sqlite3
import time

import pytest

from jobagg import publication_work as work, runtime_authority as authority
from jobagg.remediation_worker import shared_owner


@pytest.fixture(params=[False, True], ids=["default-head", "external-head"])
def source(tmp_path, request):
    external = request.param
    (tmp_path / "worker").mkdir()
    path = tmp_path / "worker" / "source.sqlite3"
    owner_path = tmp_path / "owner.lock"
    head_root = tmp_path / "external-heads"
    head_root.mkdir(mode=0o700)
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
            restore_head_root=head_root if external else None,
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


@pytest.mark.parametrize("location", ["database", "ancestor", "alias", "writable"])
def test_external_head_rejects_unsafe_roots_before_schema_changes(tmp_path, location):
    from jobagg import runtime_source_head as head

    worker = tmp_path / "worker"
    worker.mkdir()
    external = tmp_path / "heads"
    external.mkdir(mode=0o700)
    alias = tmp_path / "alias"
    alias.symlink_to(external, target_is_directory=True)
    if location == "writable":
        external.chmod(0o777)
    root = {"database": worker, "ancestor": tmp_path, "alias": alias, "writable": external}[
        location
    ]
    with (
        shared_owner(tmp_path / "owner.lock") as fd,
        closing(sqlite3.connect(worker / "source.sqlite3")) as conn,
    ):
        conn.executescript(
            "CREATE TABLE jobs(job_key TEXT PRIMARY KEY,source_id TEXT,closes_at TEXT); CREATE TABLE remediation_observations(job_key TEXT,source_id TEXT); CREATE TABLE remediation_sources(source_id TEXT,last_list_at REAL,listing_ids TEXT,listing_proof TEXT);"
        )
        work.prepare(conn, contract_sha256="configuration", epoch="epoch")
        conn.commit()
        with pytest.raises(ValueError, match="head_root"):
            authority.activate(
                conn,
                epoch="epoch",
                owner_path=tmp_path / "owner.lock",
                owner_fd=fd,
                bootstrap_receipt={"reconciled": True, "source_sha256": "a" * 64},
                restore_head_root=root,
            )
        assert authority.state(conn) is None
        assert not external.joinpath("unexpected").exists()
        with pytest.raises(ValueError, match="head_root"):
            head.path_for(conn, tmp_path / "owner.lock", head_root=root)


def test_external_head_root_and_handoff_are_pinned(source, tmp_path):
    import json
    from pathlib import Path

    path, owner_path = source
    with shared_owner(owner_path) as fd, closing(sqlite3.connect(path)) as conn:
        value = authority.validate(conn)
        bootstrap = json.loads(value["bootstrap_json"])
        if not bootstrap.get("restore_head_root"):
            return  # The default binding is covered by the other parametrization.
        other = tmp_path / "other-heads"
        other.mkdir(mode=0o700)
        with pytest.raises(ValueError, match="handoff_requires_review"):
            authority.activate(
                conn,
                epoch="epoch",
                owner_path=owner_path,
                owner_fd=fd,
                bootstrap_receipt={"reconciled": True, "source_sha256": "a" * 64},
                restore_head_root=other,
            )
        root = Path(bootstrap["restore_head_root"])
        root.rename(tmp_path / "old-heads")
        root.mkdir(mode=0o700)
        with pytest.raises(ValueError, match="head_root_identity_changed"):
            authority.validate(conn)


@pytest.mark.parametrize("symlink", [False, True])
def test_initial_head_never_overwrites_existing_operator_file(tmp_path, symlink):
    from jobagg.runtime_source_head import path_for

    worker = tmp_path / "worker"
    worker.mkdir()
    root = tmp_path / "heads"
    root.mkdir(mode=0o700)
    owner_path = tmp_path / "owner.lock"
    with (
        shared_owner(owner_path) as fd,
        closing(sqlite3.connect(worker / "source.sqlite3")) as conn,
    ):
        conn.executescript(
            "CREATE TABLE jobs(job_key TEXT PRIMARY KEY,source_id TEXT,closes_at TEXT); CREATE TABLE remediation_observations(job_key TEXT,source_id TEXT); CREATE TABLE remediation_sources(source_id TEXT,last_list_at REAL,listing_ids TEXT,listing_proof TEXT);"
        )
        work.prepare(conn, contract_sha256="configuration", epoch="epoch")
        conn.commit()
        head = path_for(conn, owner_path, head_root=root)
        if symlink:
            head.symlink_to(tmp_path / "missing")
        else:
            head.write_text("another operator")
        with pytest.raises(ValueError, match="head_exists"):
            authority.activate(
                conn,
                epoch="epoch",
                owner_path=owner_path,
                owner_fd=fd,
                bootstrap_receipt={"reconciled": True, "source_sha256": "a" * 64},
                restore_head_root=root,
            )
        assert authority.state(conn) is None
        assert head.is_symlink() if symlink else head.read_text() == "another operator"
