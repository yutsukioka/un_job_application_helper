"""Cold first useful native worker cycle, without historical JSON reconstruction."""

from datetime import datetime, UTC
import sqlite3
import time

import pytest

from jobagg import accepted_evidence, publication_work, runtime_authority, task_headers
from jobagg.policy_authority import migrate
from jobagg.remediation_worker import shared_owner, source_request_host, fetch_schedule_policy
from test_remediation_worker import setup  # noqa: F401

# ruff: noqa: F811


def test_cold_first_useful_cycle_charges_sql_and_accepts_pointer_atomically(setup, monkeypatch):
    worker, _, calls, _ = setup
    worker.initialize()
    with shared_owner(worker.shared_lock) as owner:
        migrate(
            worker.shared_policy,
            owner_fd=owner,
            epoch="native",
            pauses={
                source.id: float(
                    fetch_schedule_policy(source).get("detail_batch_pause_seconds")
                    or fetch_schedule_policy(source).get("oracle_detail_batch_pause_seconds")
                    or 0
                )
                for source in worker.by_id.values()
            },
            reviewed_writers=[{"name": "offline fixture inventory", "fenced": True}],
        )
        with worker.db.connect() as conn:
            accepted_evidence.prepare(conn)
            task_headers.prepare(
                conn, {source.id: source_request_host(source) for source in worker.by_id.values()}
            )
            publication_work.prepare(conn, contract_sha256="configuration", epoch="native")
            assert publication_work.bootstrap(conn, limit=10, deadline_at=time.monotonic() + 10)
            runtime_authority.activate(
                conn,
                epoch="native",
                owner_path=worker.shared_lock,
                owner_fd=owner,
                bootstrap_receipt={"reconciled": True, "source_sha256": "a" * 64},
            )
            runtime_authority.begin(conn, "first-useful", "configuration")
    monkeypatch.setattr(
        worker.shared_policy, "event_snapshot", lambda **_: pytest.fail("cold historical census")
    )
    report = worker.tick(
        execute=True,
        request={
            "run_id": "first-useful",
            "deadline_at": datetime.fromtimestamp(time.time() + 30, UTC).isoformat(),
            "source_manifest_sha256": worker.binding["registry_sha256"],
            "policy_audit": {"max_objects": 2, "maximum_lag_seconds": 3600},
        },
    )
    assert len(calls) == 2
    assert report["concurrency"]["accepted_progress"] >= 1
    with worker.db.connect() as conn:
        references = conn.execute("SELECT * FROM jobagg_accepted_evidence").fetchall()
        assert len(references) == 1
        pointer = references[0]
        assert pointer["artifact_id"] == "sha256:" + pointer["artifact_sha256"]
        assert (
            conn.execute(
                "SELECT status FROM remediation_attempts WHERE attempt_id=?",
                (pointer["attempt_id"],),
            ).fetchone()[0]
            == "done"
        )
        assert (
            publication_work.vector(conn, contract_sha256="configuration", now=time.time())["empty"]
            is False
        )
    authority = worker.shared_policy.native_authority()
    with sqlite3.connect(authority.path) as conn:
        assert conn.execute("SELECT count(*) FROM attempts").fetchone()[0] == 2
        assert conn.execute("SELECT count(*) FROM attempts WHERE kind='detail'").fetchone()[0] == 1
