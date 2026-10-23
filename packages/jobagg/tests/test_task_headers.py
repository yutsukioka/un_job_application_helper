import time

import pytest

from jobagg import task_headers
from jobagg.remediation_worker import source_request_host
from test_remediation_worker import setup  # noqa: F401

# ruff: noqa: F811


def test_header_frontiers_preserve_selection_and_avoid_payload_hydration(setup):
    worker, _, _, _ = setup
    worker.initialize()
    with worker.db.connect() as conn:
        for i in range(300):
            worker.enqueue(
                conn,
                "demo_workday",
                "detail",
                str(i),
                {"large_unused": "x" * 10000},
                due=time.time() + 100,
            )
        wanted = worker.enqueue(conn, "demo_workday", "detail", "ready", {}, due=time.time() - 1)
        original = worker.choose()
        task_headers.prepare(conn, {s.id: source_request_host(s) for s in worker.by_id.values()})
    assert original["task_id"] == wanted
    trace = []
    with worker.db.connect() as conn:
        conn.set_trace_callback(trace.append)
        rows = task_headers.candidates(conn, worker, time.time())
    assert [row["task_id"] for row in rows] == [wanted]
    assert all("payload" not in row for row in rows)
    assert worker.choose()["task_id"] == wanted
    assert not any("remediation_attempts" in sql for sql in trace)


def test_header_triggers_follow_task_and_attempt_transactions(setup):
    worker, _, _, _ = setup
    worker.initialize()
    with worker.db.connect() as conn:
        task_headers.prepare(conn, {s.id: source_request_host(s) for s in worker.by_id.values()})
        key = worker.enqueue(
            conn, "demo_workday", "document", "one", {"url": "https://documents.example/a"}
        )
        row = conn.execute("SELECT * FROM jobagg_task_headers WHERE task_id=?", (key,)).fetchone()
        assert row["host"] == "documents.example"
        conn.execute(
            "INSERT INTO remediation_attempts VALUES('attempt',?,'demo_workday','document',12,NULL,'reserved',NULL)",
            (key,),
        )
        assert (
            conn.execute(
                "SELECT last_attempt_at FROM jobagg_task_headers WHERE task_id=?", (key,)
            ).fetchone()[0]
            == 12
        )
        conn.execute("UPDATE remediation_tasks SET status='blocked' WHERE task_id=?", (key,))
        assert (
            conn.execute(
                "SELECT status FROM jobagg_task_headers WHERE task_id=?", (key,)
            ).fetchone()[0]
            == "blocked"
        )
        with pytest.raises(ValueError, match="contract_changed"):
            task_headers.prepare(conn, {"demo_workday": "other.example"})


def test_report_groups_pending_times_without_materializing_every_due_timestamp(setup):
    from jobagg.source_health import worker_source_health

    worker, _, _, _ = setup
    worker.initialize()
    source = next(iter(worker.by_id.values()))
    at = __import__("time").time()
    with worker.db.connect() as conn:
        from jobagg.task_headers import prepare
        from jobagg.remediation_worker import source_request_host

        prepare(conn, {item.id: source_request_host(item) for item in worker.by_id.values()})
        for index in range(1024):
            worker.enqueue(
                conn,
                source.id,
                "detail",
                str(index),
                {},
                due=at - 100 - index if index < 512 else at + 100 + index,
            )
        listing = dict(
            conn.execute(
                "SELECT * FROM remediation_sources WHERE source_id=?", (source.id,)
            ).fetchone()
        )
        emitted = [0]

        class CountedRows:
            def __init__(self, cursor):
                self.cursor = cursor

            def __iter__(self):
                for row in self.cursor:
                    emitted[0] += 1
                    yield row

        class Reader:
            def execute(self, query, args=()):
                cursor = conn.execute(query, args)
                return CountedRows(cursor) if "FROM jobagg_task_headers" in query else cursor

        result = worker_source_health(worker, Reader(), source, listing, now=at, events=[])
        assert result["task_eligibility"]["detail"]["eligible_pending"] == 512
        assert result["task_eligibility"]["detail"]["deferred_pending"] == 512
        assert emitted[0] <= 2
