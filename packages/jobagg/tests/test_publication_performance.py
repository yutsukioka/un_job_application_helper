"""Regression coverage for indexed, bounded publication preparation."""

import fcntl
import hashlib
import json
from pathlib import Path
import sqlite3
import time
from types import SimpleNamespace

import pytest

from jobagg.accepted_detail_lineage import (
    DETAIL_ATTEMPTS_QUERY,
    ensure_detail_lineage_indexes,
    matching_detail_attempts,
)
from jobagg.publication_projection import build_projection
from jobagg.publication_snapshot import create_publication_snapshot
from test_live_publication import add_detail, setup as _live_setup
from test_remediation_worker import setup as _remediation_setup


@pytest.fixture
def live_setup(tmp_path):
    return _live_setup.__wrapped__(tmp_path)


@pytest.fixture
def remediation_setup(tmp_path, monkeypatch):
    return _remediation_setup.__wrapped__(tmp_path, monkeypatch)


INDEX_NAMES = {
    "idx_remediation_tasks_detail_identity",
    "idx_remediation_attempts_detail_lineage",
}

def _table_rows(conn, name):
    return [tuple(row) for row in conn.execute('SELECT * FROM "' + name + '" ORDER BY 1')]


def test_existing_worker_migrates_indexes_without_resetting_history_or_fetching(remediation_setup):
    worker, _, calls, _ = remediation_setup
    worker.tick(execute=True)
    tables = (
        "remediation_sources",
        "remediation_tasks",
        "remediation_attempts",
        "remediation_observations",
        "remediation_documents",
    )
    marker = (worker.workspace / "worker_workspace.json").read_bytes()
    network_calls = list(calls)
    with worker.db.connect() as conn:
        for name in INDEX_NAMES:
            conn.execute('DROP INDEX IF EXISTS "' + name + '"')
        before = {table: _table_rows(conn, table) for table in tables}
        assert conn.execute("SELECT count(*) FROM remediation_attempts").fetchone()[0] > 0
    worker.initialize()
    worker.initialize()  # A later scheduled startup performs the same migration safely.
    with worker.db.connect() as conn:
        assert {table: _table_rows(conn, table) for table in tables} == before
        indexes = {
            row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='index'")
        }
        assert INDEX_NAMES <= indexes
        task_plan = list(conn.execute("EXPLAIN QUERY PLAN " + DETAIL_ATTEMPTS_QUERY, ("demo_workday", "R1")))
        operations = [str(row[3]) for row in task_plan]
        assert any(
            "SEARCH t" in operation and "idx_remediation_tasks_detail_identity" in operation
            for operation in operations
        )
        assert any(
            "SEARCH a" in operation and "idx_remediation_attempts_detail_lineage" in operation
            for operation in operations
        )
        assert not any("SCAN a" in operation for operation in operations)
    assert (worker.workspace / "worker_workspace.json").read_bytes() == marker
    assert calls == network_calls


def test_indexes_preserve_all_exact_historical_matches_and_projection(live_setup, tmp_path):
    record, proof = add_detail(live_setup)
    with live_setup["worker"].connect() as conn:
        original = dict(conn.execute("SELECT * FROM remediation_attempts").fetchone())
        conn.execute("UPDATE remediation_tasks SET status='pending',receipt='{}'")
        inserts = []
        for attempt_id, finished_at, status, evidence, source_id, kind in (
            (
                "accepted-older",
                original["finished_at"] - 100,
                "done",
                original["evidence"],
                record.source_id,
                "detail",
            ),
            (
                "failed-newer",
                original["finished_at"] + 100,
                "failed",
                original["evidence"],
                record.source_id,
                "detail",
            ),
            ("unfinished", None, "done", original["evidence"], record.source_id, "detail"),
            (
                "bad-hash",
                original["finished_at"] + 200,
                "done",
                json.dumps({**json.loads(original["evidence"]), "detail_sha256": "0" * 64}),
                record.source_id,
                "detail",
            ),
            (
                "wrong-source",
                original["finished_at"] + 300,
                "done",
                original["evidence"],
                "other",
                "detail",
            ),
            (
                "wrong-kind",
                original["finished_at"] + 400,
                "done",
                original["evidence"],
                record.source_id,
                "listing",
            ),
        ):
            inserts.append(
                (
                    attempt_id,
                    original["task_id"],
                    source_id,
                    kind,
                    original["started_at"],
                    finished_at,
                    status,
                    evidence,
                )
            )
        bad_path = tmp_path / "different-description.json"
        artifact = json.loads(Path(json.loads(original["evidence"])["detail_path"]).read_text())
        artifact["job"]["description"] = "A different accepted text"
        bad_path.write_text(json.dumps(artifact))
        inserts.append(
            (
                "wrong-description",
                original["task_id"],
                record.source_id,
                "detail",
                original["started_at"],
                original["finished_at"] + 500,
                "done",
                json.dumps(
                    {
                        "detail_path": str(bad_path),
                        "detail_sha256": hashlib.sha256(bad_path.read_bytes()).hexdigest(),
                    }
                ),
            )
        )
        conn.executemany("INSERT INTO remediation_attempts VALUES(?,?,?,?,?,?,?,?)", inserts)
        row = conn.execute(
            "SELECT * FROM jobs WHERE job_key=?", (record.identity_key(),)
        ).fetchone()
        before = matching_detail_attempts(conn, row, proof)
        ensure_detail_lineage_indexes(conn)
        ensure_detail_lineage_indexes(conn)
        after = matching_detail_attempts(conn, row, proof)
        assert after == before
        assert [binding["attempt"]["attempt_id"] for binding in after] == [
            "accepted-001",
            "accepted-older",
        ]
    with (
        sqlite3.connect(live_setup["worker"].path) as origin,
        sqlite3.connect(":memory:") as destination,
    ):
        receipt = build_projection(origin, destination)
        assert destination.execute(
            "SELECT attempt_id FROM remediation_attempts ORDER BY attempt_id"
        ).fetchall() == [("accepted-001",), ("accepted-older",)]
        assert (
            destination.execute(
                "SELECT status FROM remediation_tasks WHERE kind='detail'"
            ).fetchone()[0]
            == "pending"
        )
        assert receipt["manifest"]["tables"]["remediation_attempts"]["rows"] == 2
        assert INDEX_NAMES <= {
            row[0]
            for row in destination.execute("SELECT name FROM sqlite_master WHERE type='index'")
        }


class _InterruptAtProgress(sqlite3.Connection):
    """Expire the test clock from a real SQLite engine progress callback."""

    def set_progress_handler(self, callback, count):
        if callback is None:
            return super().set_progress_handler(None, count)

        def engine_progress():
            self.clock["callbacks"] += 1
            self.clock["now"] = self.clock["deadline"] + 1
            return callback()

        return super().set_progress_handler(engine_progress, count)


def _clock(monkeypatch, *modules):
    deadline = time.monotonic() + 60
    clock = {"deadline": deadline, "now": deadline - 60, "callbacks": 0}
    for module in modules:
        monkeypatch.setattr(module, "time", SimpleNamespace(monotonic=lambda: clock["now"]))
    return clock


def _large_origin(clock):
    conn = sqlite3.connect(":memory:", factory=_InterruptAtProgress)
    conn.clock = clock
    conn.executescript(
        "CREATE TABLE jobs(job_key TEXT PRIMARY KEY); CREATE TABLE remediation_observations(job_key TEXT PRIMARY KEY);"
    )
    conn.executemany("INSERT INTO jobs VALUES(?)", [("legacy-" + str(i),) for i in range(3000)])
    conn.commit()
    return conn


def test_mid_query_deadline_is_timeout_and_both_connections_release_handlers(monkeypatch):
    from jobagg import publication_projection as projection

    clock = _clock(monkeypatch, projection)
    origin = _large_origin(clock)
    destination = sqlite3.connect(":memory:")
    try:
        with pytest.raises(TimeoutError, match="deadline"):
            build_projection(origin, destination, deadline_at=clock["deadline"])
        assert clock["callbacks"] > 0  # Actual SQL was interrupted after beginning execution.
        assert not origin.in_transaction and not destination.in_transaction
        callbacks_before = clock["callbacks"]
        # With the clock still expired, subsequent caller SQL must not inherit a handler.
        for conn in (origin, destination):
            assert (
                conn.execute(
                    "WITH RECURSIVE n(x) AS (VALUES(1) UNION ALL SELECT x+1 FROM n WHERE x<3000) SELECT sum(x) FROM n"
                ).fetchone()[0]
                == 4501500
            )
        assert clock["callbacks"] == callbacks_before
        assert (
            destination.execute("SELECT count(*) FROM sqlite_master WHERE type='table'").fetchone()[
                0
            ]
            == 0
        )
    finally:
        origin.close()
        destination.close()


@pytest.mark.parametrize("projection", [False, True])
def test_mid_sql_snapshot_deadline_removes_only_unpublished_temp(
    live_setup, monkeypatch, projection
):
    from jobagg import publication_projection as projection_module
    from jobagg import publication_snapshot as snapshot_module

    clock = _clock(monkeypatch, projection_module, snapshot_module)
    worker = live_setup["worker"]
    record, _ = add_detail(live_setup)
    with worker.connect() as conn:
        row = dict(
            conn.execute("SELECT * FROM jobs WHERE job_key=?", (record.identity_key(),)).fetchone()
        )
        columns = list(row)
        placeholders = ",".join("?" for _ in columns)
        inserts = []
        for index in range(1500):
            values = dict(row)
            values["job_key"] = "legacy-" + str(index)
            inserts.append(tuple(values[column] for column in columns))
        conn.executemany(
            "INSERT INTO jobs(" + ",".join(columns) + ") VALUES(" + placeholders + ")", inserts
        )
    real_connect = sqlite3.connect

    def connect(*args, **kwargs):
        kwargs["factory"] = _InterruptAtProgress
        connection = real_connect(*args, **kwargs)
        connection.clock = clock
        return connection

    monkeypatch.setattr(sqlite3, "connect", connect)
    root = live_setup["root"]
    lock = root / "owner.lock"
    target = root / "never-sealed.sqlite3"
    preserved = root / ".publication-backup-unrelated.sqlite3"
    preserved.write_bytes(b"another owner's retained bytes")
    with lock.open("a+") as owner:
        fcntl.flock(owner, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(TimeoutError, match="deadline"):
            create_publication_snapshot(
                worker.path.resolve(),
                target.resolve(),
                "a" * 64,
                owner.fileno(),
                lock.resolve(),
                projection=projection,
                deadline_at=clock["deadline"],
            )
    assert clock["callbacks"] > 0
    assert not target.exists()
    assert preserved.read_bytes() == b"another owner's retained bytes"
    assert list(root.glob(".publication-backup-*")) == [preserved]
    with real_connect(worker.path) as conn:
        assert conn.execute("SELECT count(*) FROM jobs").fetchone()[0] == 1501


def test_unrelated_sqlite_interrupt_is_not_relabelled_as_deadline(monkeypatch):
    from jobagg import publication_projection as projection

    clock = _clock(monkeypatch, projection)
    original_error = sqlite3.OperationalError("interrupted")
    original_error.sqlite_errorcode = sqlite3.SQLITE_INTERRUPT

    class FaultConnection(sqlite3.Connection):
        def execute(self, sql, *args, **kwargs):
            if sql.startswith("SELECT count(*) FROM jobs j LEFT JOIN"):
                raise original_error
            return super().execute(sql, *args, **kwargs)

    origin = sqlite3.connect(":memory:", factory=FaultConnection)
    destination = sqlite3.connect(":memory:")
    try:
        origin.executescript(
            "CREATE TABLE jobs(job_key TEXT); CREATE TABLE remediation_observations(job_key TEXT);"
        )
        with pytest.raises(sqlite3.OperationalError) as caught:
            build_projection(origin, destination, deadline_at=clock["deadline"])
        assert caught.value is original_error
        assert not origin.in_transaction and not destination.in_transaction
        assert origin.execute("SELECT count(*) FROM jobs").fetchone()[0] == 0
    finally:
        origin.close()
        destination.close()


def test_deadline_during_artifact_hash_propagates_instead_of_missing_lineage(
    live_setup, monkeypatch
):
    from jobagg import accepted_detail_lineage as lineage

    record, proof = add_detail(live_setup)
    clock = _clock(monkeypatch, lineage)
    with live_setup["worker"].connect() as conn:
        attempt = conn.execute("SELECT evidence FROM remediation_attempts").fetchone()
        detail_path = Path(json.loads(attempt[0])["detail_path"])
        row = conn.execute(
            "SELECT * FROM jobs WHERE job_key=?", (record.identity_key(),)
        ).fetchone()
        real_open = Path.open

        class HashReader:
            def __init__(self, stream):
                self.stream = stream

            def __enter__(self):
                self.stream.__enter__()
                return self

            def __exit__(self, *args):
                return self.stream.__exit__(*args)

            def read(self, *args):
                block = self.stream.read(*args)
                if block:
                    clock["now"] = clock["deadline"] + 1
                return block

        def open_path(path, mode="r", *args, **kwargs):
            stream = real_open(path, mode, *args, **kwargs)
            return HashReader(stream) if path == detail_path and "b" in mode else stream

        monkeypatch.setattr(Path, "open", open_path)
        with pytest.raises(TimeoutError, match="deadline"):
            matching_detail_attempts(conn, row, proof, deadline_at=clock["deadline"])
        assert clock["now"] > clock["deadline"]


def test_projection_timing_diagnostics_do_not_change_deterministic_binding(live_setup):
    add_detail(live_setup)
    with sqlite3.connect(live_setup["worker"].path) as origin:
        ensure_detail_lineage_indexes(origin)
    receipts = []
    for _ in range(2):
        with (
            sqlite3.connect(live_setup["worker"].path) as origin,
            sqlite3.connect(":memory:") as destination,
        ):
            receipts.append(build_projection(origin, destination))
    assert receipts[0]["manifest"] == receipts[1]["manifest"]
    assert receipts[0]["sha256"] == receipts[1]["sha256"]
    for receipt in receipts:
        assert "timings" not in receipt["manifest"]
        assert receipt["timings"]["accepted_detail_lineage_seconds"] >= 0
        assert receipt["timings"]["total_seconds"] >= 0
        assert receipt["timings"]["tables"]


@pytest.mark.parametrize("bounded", [False, True])
@pytest.mark.parametrize("failure_phase", ["open", "read"])
def test_artifact_io_timeout_remains_nonmatching_evidence(
    live_setup, monkeypatch, bounded, failure_phase
):
    from jobagg import accepted_detail_lineage as lineage

    record, proof = add_detail(live_setup)
    clock = _clock(monkeypatch, lineage)
    with live_setup["worker"].connect() as conn:
        attempt = conn.execute("SELECT evidence FROM remediation_attempts").fetchone()
        detail_path = Path(json.loads(attempt[0])["detail_path"])
        row = conn.execute(
            "SELECT * FROM jobs WHERE job_key=?", (record.identity_key(),)
        ).fetchone()
        real_open = Path.open

        class FailingReader:
            def __init__(self, stream):
                self.stream = stream

            def __enter__(self):
                self.stream.__enter__()
                return self

            def __exit__(self, *args):
                return self.stream.__exit__(*args)

            def read(self, *args):
                raise TimeoutError("Artifact filesystem read timed out")

        def open_path(path, mode="r", *args, **kwargs):
            if path == detail_path and "b" in mode:
                if failure_phase == "open":
                    raise TimeoutError("Artifact filesystem open timed out")
                return FailingReader(real_open(path, mode, *args, **kwargs))
            return real_open(path, mode, *args, **kwargs)

        monkeypatch.setattr(Path, "open", open_path)
        assert (
            matching_detail_attempts(
                conn, row, proof, deadline_at=clock["deadline"] if bounded else None
            )
            == []
        )
        assert clock["now"] < clock["deadline"]


@pytest.mark.parametrize("active", ["origin", "destination", "both"])
def test_projection_rejects_active_caller_transactions_without_rolling_them_back(active):
    origin = sqlite3.connect(":memory:")
    destination = sqlite3.connect(":memory:")
    try:
        for name, connection in (("origin", origin), ("destination", destination)):
            connection.execute("CREATE TABLE caller_work(value TEXT)")
            if active in (name, "both"):
                connection.execute("INSERT INTO caller_work VALUES('uncommitted caller row')")
                assert connection.in_transaction
        states = [
            (connection.in_transaction, connection.execute("SELECT * FROM caller_work").fetchall())
            for connection in (origin, destination)
        ]
        with pytest.raises(ValueError, match="transaction"):
            build_projection(origin, destination)
        for connection, (was_active, rows) in zip((origin, destination), states):
            assert connection.in_transaction is was_active
            assert connection.execute("SELECT * FROM caller_work").fetchall() == rows
    finally:
        origin.close()
        destination.close()
