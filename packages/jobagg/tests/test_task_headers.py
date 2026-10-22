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
