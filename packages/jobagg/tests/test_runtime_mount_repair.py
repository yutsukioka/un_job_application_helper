"""Device renewal keeps all unrelated identities and SQL state protected."""

from contextlib import closing
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import plistlib
import sqlite3
import time
from types import SimpleNamespace

import pytest


@pytest.fixture
def repair():
    path = Path(__file__).resolve().parents[1] / "scripts/repair_runtime_mount.py"
    spec = importlib.util.spec_from_file_location("tested_runtime_mount_repair", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def identity_case(tmp_path):
    path = tmp_path / "retained.json"
    path.write_text("retained evidence")
    before = dict(path=str(path), device=11, inode=22, size=17, mtime_ns=33, ctime_ns=44)
    return path, before, {**before, "device": 55}, tmp_path


def test_device_only_renewal_preserves_every_other_field(repair, identity_case):
    path, before, after, mount = identity_case
    assert repair.renew(path, before, after, mount, 11, 55) == after
    assert before["device"] == 11


@pytest.mark.parametrize("field", ["inode", "size", "mtime_ns", "ctime_ns", "path"])
def test_non_device_identity_change_is_rejected(repair, identity_case, field):
    path, before, after, mount = identity_case
    after[field] = str(path) + ".other" if field == "path" else after[field] + 1
    with pytest.raises(ValueError, match="Non-device identity changed"):
        repair.renew(path, before, after, mount, 11, 55)


@pytest.mark.parametrize("old,new", [(12, 55), (11, 56)])
def test_unreviewed_device_transition_is_rejected(repair, identity_case, old, new):
    path, before, after, mount = identity_case
    with pytest.raises(ValueError, match="Unexpected device transition"):
        repair.renew(path, before, after, mount, old, new)


def test_added_identity_field_is_rejected(repair, identity_case):
    path, before, after, mount = identity_case
    with pytest.raises(ValueError, match="Identity shape changed"):
        repair.renew(path, before, {**after, "unexpected": True}, mount, 11, 55)


def test_aliased_or_outside_path_is_rejected(repair, identity_case):
    path, before, after, mount = identity_case
    alias = mount / "alias"
    alias.symlink_to(path)
    for candidate, boundary in ((alias, mount), (path, mount / "other")):
        with pytest.raises(ValueError, match="Aliased or out-of-volume path"):
            repair.renew(candidate, before, after, boundary, 11, 55)


@pytest.fixture
def volume_case(repair, tmp_path, monkeypatch):
    mount = tmp_path / "mount"
    mount.mkdir()
    sentinel = mount / "sentinel.json"
    sentinel.write_text(json.dumps({"volume_uuid": "retained-volume-uuid"}))
    config = {"storage_guard": {"mount_root": str(mount), "sentinel_path": str(sentinel),
                                "sentinel_sha256": repair.sha(sentinel)}}
    observed = {"VolumeUUID": "retained-volume-uuid", "MountPoint": str(mount)}
    calls = []

    def query(args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(stdout=plistlib.dumps(observed))

    monkeypatch.setattr(repair.subprocess, "run", query)
    return config, observed, sentinel, calls


def test_volume_uuid_comes_from_pinned_sentinel(repair, volume_case):
    config, observed, _, calls = volume_case
    assert repair.pinned_volume(config) == (Path(observed["MountPoint"]), observed["VolumeUUID"])
    assert calls == [(["/usr/sbin/diskutil", "info", "-plist", observed["MountPoint"]],
                      {"check": True, "capture_output": True, "timeout": 10})]


@pytest.mark.parametrize("field", ["VolumeUUID", "MountPoint"])
def test_changed_uuid_or_mount_point_is_rejected(repair, volume_case, field):
    config, observed, _, _ = volume_case
    observed[field] = "unreviewed-volume"
    with pytest.raises(ValueError, match="Persistent volume differs"):
        repair.pinned_volume(config)


def test_changed_sentinel_is_rejected_before_volume_query(repair, volume_case):
    config, _, sentinel, calls = volume_case
    sentinel.write_text(json.dumps({"volume_uuid": "different-volume-uuid"}))
    with pytest.raises(ValueError, match="Storage sentinel changed"):
        repair.pinned_volume(config)
    assert not calls


@pytest.fixture
def sql_case():
    # No production path or production owner is used by these SQL guard tests.
    conn = sqlite3.connect(":memory:", cached_statements=0)
    conn.executescript("""
        CREATE TABLE jobagg_runtime_state(
            id INTEGER PRIMARY KEY, bootstrap_json TEXT, control_json TEXT,
            revision INTEGER, source_transaction_id TEXT
        );
        INSERT INTO jobagg_runtime_state VALUES(1, 'before', 'cursor', 29821, 'retained-token');
        CREATE TABLE jobs(id INTEGER PRIMARY KEY, title TEXT);
        INSERT INTO jobs VALUES(1, 'retained description');
        CREATE TRIGGER runtime_state_control_guard
        BEFORE UPDATE OF bootstrap_json, control_json ON jobagg_runtime_state
        BEGIN SELECT CASE WHEN jobagg_runtime_control_allowed()!=1
            THEN RAISE(ABORT, 'runtime_control_not_admitted') END; END;
    """)
    conn.create_function("jobagg_runtime_control_allowed", 0, lambda: 1)
    try:
        yield conn
    finally:
        conn.set_authorizer(None)
        conn.rollback()
        conn.close()


def state(conn):
    return conn.execute("SELECT * FROM jobagg_runtime_state").fetchall(), conn.execute("SELECT * FROM jobs").fetchall()


def install(repair, conn, guard=lambda: None):
    conn.set_authorizer(repair.restricted({"jobagg_runtime_state": {"bootstrap_json", "control_json"}}, guard))


def test_exact_metadata_update_commits_without_domain_or_clock_change(repair, sql_case):
    conn = sql_case
    calls = []
    install(repair, conn, lambda: calls.append("commit"))
    with conn:
        conn.execute("UPDATE jobagg_runtime_state SET bootstrap_json=?, control_json=? WHERE id=1", ("renewed", "new cursor"))
    assert state(conn) == ([(1, "renewed", "new cursor", 29821, "retained-token")], [(1, "retained description")])
    assert calls == ["commit"]


@pytest.mark.parametrize("statement", [
    "UPDATE jobs SET title='lost' WHERE id=1",
    "UPDATE jobagg_runtime_state SET revision=0 WHERE id=1",
    "UPDATE jobagg_runtime_state SET source_transaction_id='new' WHERE id=1",
    "INSERT INTO jobs VALUES(2, 'new')",
    "DELETE FROM jobs WHERE id=1",
    "DELETE FROM jobagg_runtime_state WHERE id=1",
])
def test_unrelated_writes_are_denied_without_changes(repair, sql_case, statement):
    conn = sql_case
    before = state(conn)
    install(repair, conn)
    with pytest.raises(sqlite3.DatabaseError, match="not authorized"):
        conn.execute(statement)
    conn.rollback()
    assert state(conn) == before


@pytest.mark.parametrize("statement", [
    "CREATE TABLE unauthorized(value TEXT)",
    "DROP TABLE jobs",
    "ALTER TABLE jobs ADD COLUMN unauthorized TEXT",
    "ATTACH DATABASE ':memory:' AS unauthorized",
    "PRAGMA journal_mode=OFF",
    "PRAGMA user_version=999",
    "PRAGMA writable_schema=ON",
])
def test_schema_attachment_and_pragma_mutations_are_denied(repair, sql_case, statement):
    conn = sql_case
    before = state(conn)
    install(repair, conn)
    with pytest.raises(sqlite3.DatabaseError, match="not authorized"):
        conn.execute(statement)
    conn.rollback()
    assert state(conn) == before


@pytest.mark.parametrize("indirect", [
    "UPDATE jobs SET title='lost' WHERE id=1;",
    "UPDATE jobagg_runtime_state SET revision=0 WHERE id=1;",
    "UPDATE jobagg_runtime_state SET source_transaction_id='new' WHERE id=1;",
    "UPDATE jobagg_runtime_state SET control_json='indirect' WHERE id=1;",
])
def test_trigger_cannot_write_domain_clock_token_or_even_allowed_column(repair, sql_case, indirect):
    conn = sql_case
    conn.execute("CREATE TRIGGER hidden_write AFTER UPDATE OF bootstrap_json ON jobagg_runtime_state BEGIN " + indirect + " END")
    before = state(conn)
    install(repair, conn)
    with pytest.raises(sqlite3.DatabaseError, match="not authorized"):
        conn.execute("UPDATE jobagg_runtime_state SET bootstrap_json='renewed' WHERE id=1")
    conn.rollback()
    assert state(conn) == before


def test_allowed_column_in_attached_database_is_denied(repair, sql_case):
    conn = sql_case
    conn.execute("ATTACH DATABASE ':memory:' AS other")
    conn.execute("CREATE TABLE other.jobagg_runtime_state(id INTEGER, bootstrap_json TEXT)")
    conn.execute("INSERT INTO other.jobagg_runtime_state VALUES(1, 'retained')")
    conn.commit()
    install(repair, conn)
    with pytest.raises(sqlite3.DatabaseError, match="not authorized"):
        conn.execute("UPDATE other.jobagg_runtime_state SET bootstrap_json='changed'")
    assert conn.execute("SELECT * FROM other.jobagg_runtime_state").fetchall() == [(1, "retained")]


def test_lost_owner_guard_refuses_commit_and_retains_original_rows(repair, sql_case):
    conn = sql_case
    owner_held = True

    def guard():
        if not owner_held:
            raise ValueError("owner lost")

    install(repair, conn, guard)
    # First commit proves a previously successful check cannot authorize a later one.
    conn.execute("UPDATE jobagg_runtime_state SET bootstrap_json='first renewal' WHERE id=1")
    conn.commit()
    before = state(conn)
    conn.execute("UPDATE jobagg_runtime_state SET bootstrap_json='uncommitted' WHERE id=1")
    owner_held = False
    with pytest.raises(sqlite3.DatabaseError, match="not authorized"):
        conn.commit()
    assert conn.in_transaction
    conn.rollback()
    assert state(conn) == before


def test_real_native_authority_renewal_and_validation_under_restricted_authorizer(repair, tmp_path):
    from jobagg import publication_work, runtime_authority, runtime_source_head
    from jobagg.remediation_worker import shared_owner

    database = tmp_path / "worker.sqlite3"
    owner_path = tmp_path / "actual-owner.lock"
    with shared_owner(owner_path) as fd, closing(sqlite3.connect(database, cached_statements=0)) as conn:
        conn.executescript("""
            CREATE TABLE jobs(job_key TEXT PRIMARY KEY,source_id TEXT,external_id TEXT,
                description TEXT,closes_at TEXT);
            INSERT INTO jobs VALUES('source:1','source','1','retained full description',NULL);
            CREATE TABLE remediation_observations(job_key TEXT PRIMARY KEY,source_id TEXT);
            CREATE TABLE remediation_sources(source_id TEXT PRIMARY KEY,last_list_at REAL,
                listing_ids TEXT,listing_proof TEXT);
        """)
        publication_work.prepare(conn, contract_sha256="configuration", epoch="retained-epoch")
        assert publication_work.bootstrap(conn, limit=10, deadline_at=time.monotonic() + 5)
        runtime_authority.activate(
            conn, epoch="retained-epoch", owner_path=owner_path, owner_fd=fd,
            bootstrap_receipt={"reconciled": True, "source_sha256": "a" * 64},
        )
        conn.commit()
        current = runtime_authority.validate(conn)
        bootstrap = json.loads(current["bootstrap_json"])
        head_path = Path(bootstrap["restore_head_path"])
        original_head_bytes = head_path.read_bytes()
        original_head = json.loads(original_head_bytes)
        original_jobs = conn.execute("SELECT * FROM jobs").fetchall()

        # Simulate only old boot device identifiers in this temporary database.
        old_device = database.stat().st_dev + 100
        old_bootstrap = deepcopy(bootstrap)
        old_bootstrap["owner_identity"]["device"] = old_device
        old_head = deepcopy(original_head)
        old_head["database"]["device"] = old_device
        old_head["owner_identity"]["device"] = old_device
        conn.create_function("jobagg_runtime_control_allowed", 0, lambda: 1)
        conn.execute("UPDATE jobagg_runtime_state SET bootstrap_json=? WHERE id=1", (json.dumps(old_bootstrap, sort_keys=True),))
        conn.commit()
        head_path.write_text(json.dumps(old_head, sort_keys=True))
        with pytest.raises(runtime_authority.RuntimeRepairRequired, match="runtime_owner_identity_changed"):
            runtime_authority.validate(conn)

        def guard():
            runtime_source_head.owner(fd, owner_path)

        conn.create_function("jobagg_runtime_control_allowed", 0, lambda: guard() or 1)
        install(repair, conn, guard)
        with conn:
            conn.execute("UPDATE jobagg_runtime_state SET bootstrap_json=?,control_json=? WHERE id=1",
                         (current["bootstrap_json"], current["control_json"]))
            head_path.write_bytes(original_head_bytes)
            # Includes the genuine schema, trigger, table-list, DB and source-head checks.
            assert runtime_authority.validate(conn) == current
        assert runtime_authority.validate(conn) == current
        assert json.loads(head_path.read_text()) == original_head
        assert conn.execute("SELECT * FROM jobs").fetchall() == original_jobs
        conn.set_authorizer(None)


def test_real_destination_identity_renewal_and_validation_under_restricted_authorizer(repair, tmp_path):
    from jobagg import publication_fence
    from jobagg.remediation_worker import shared_owner
    from jobagg.runtime_source_head import owner

    database = tmp_path / "destination.sqlite3"
    owner_path = tmp_path / "actual-owner.lock"
    with shared_owner(owner_path) as fd, closing(sqlite3.connect(database, cached_statements=0)) as conn:
        conn.executescript("CREATE TABLE jobs(id INTEGER PRIMARY KEY,title TEXT); INSERT INTO jobs VALUES(1,'retained full description');")
        publication_fence.activate(conn, owner_path=owner_path, owner_fd=fd, epoch="retained-epoch")
        conn.commit()
        current = publication_fence._validate(conn)
        original_jobs = conn.execute("SELECT * FROM jobs").fetchall()
        old_device = current["owner_device"] + 100
        conn.create_function("jobagg_publication_control_allowed", 0, lambda: 1)
        conn.execute("UPDATE jobagg_publication_revision SET owner_device=? WHERE id=1", (old_device,))
        conn.commit()

        def guard():
            owner(fd, owner_path)

        conn.create_function("jobagg_publication_control_allowed", 0, lambda: guard() or 1)
        conn.set_authorizer(repair.restricted({publication_fence.TABLE: {"owner_device"}}, guard))
        with conn:
            conn.execute("UPDATE jobagg_publication_revision SET owner_device=? WHERE id=1 AND owner_device=?",
                         (current["owner_device"], old_device))
            assert publication_fence._validate(conn) == current
        assert publication_fence._validate(conn) == current
        assert conn.execute("SELECT * FROM jobs").fetchall() == original_jobs
        conn.set_authorizer(None)
