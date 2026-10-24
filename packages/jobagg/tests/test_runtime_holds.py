"""Scoped review gates exclude held work before selection and durable charge."""

import sqlite3
import time

import pytest
from jobagg import runtime_holds as holds, runtime_authority as authority, task_headers
from jobagg.remediation_worker import shared_owner, source_request_host, HostIneligible
from test_remediation_worker import setup  # noqa: F401
from test_legacy_wrapper_reconciliation import setup_catalog

# ruff: noqa: F811


def test_held_frontier_does_not_starve_independent_task_and_claim_rechecks_before_charge(
    setup, tmp_path
):
    worker, _, _, _ = setup
    worker.initialize()
    owner = worker.shared_lock
    with shared_owner(owner) as fd, worker.db.connect() as conn:
        held = worker.enqueue(conn, "demo_workday", "detail", "held", {}, due=time.time() - 2)
        wanted = worker.enqueue(
            conn, "demo_workday", "detail", "independent", {}, due=time.time() - 1
        )
        task_headers.prepare(conn, {s.id: source_request_host(s) for s in worker.by_id.values()})
        evidence = tmp_path / "controls"
        evidence.mkdir()
        setup_catalog(evidence, conn, fd, owner)
        from jobagg.runtime_control_catalog import receipt

        authority.activate(
            conn,
            epoch="fixture",
            owner_fd=fd,
            owner_path=owner,
            bootstrap_receipt={
                "reconciled": True,
                "source_sha256": "a" * 64,
                "control_catalogue_sha256": receipt(conn)["sha256"],
            },
        )
        conn.commit()
        selected = dict(
            conn.execute("SELECT * FROM remediation_tasks WHERE task_id=?", (held,)).fetchone()
        )
        before = authority.validate(conn)
        with authority.controller(conn, run_id=None, owner_fd=fd):
            value = holds.put(
                conn,
                scope="task",
                identity=held,
                reason="missing original exit",
                evidence_sha256="a" * 64,
                next_condition="owner-reviewed exact evidence",
                owner_fd=fd,
            )
            assert (
                holds.put(
                    conn,
                    scope="task",
                    identity=held,
                    reason="missing original exit",
                    evidence_sha256="a" * 64,
                    next_condition="owner-reviewed exact evidence",
                    owner_fd=fd,
                )
                == value
            )
            conn.commit()
        assert authority.validate(conn) == before
        assert worker.choose()["task_id"] == wanted
        called = []
        worker.shared_policy.reserve = lambda *a, **k: called.append(a)
        with pytest.raises(HostIneligible, match="Scoped integrity hold"):
            worker.claim(selected)
        assert not called
        assert (
            conn.execute(
                "SELECT attempts,status FROM remediation_tasks WHERE task_id=?", (held,)
            ).fetchone()["status"]
            == "pending"
        )
        assert not value["automatic_retry"] and value["probe_ceiling"] == 0
        with authority.controller(conn, run_id=None, owner_fd=fd):
            holds.put(
                conn,
                scope="unit",
                identity="job:unrelated",
                reason="unrelated integrity review",
                evidence_sha256="b" * 64,
                next_condition="separate review",
                owner_fd=fd,
            )
            with pytest.raises(ValueError, match="original_review_changed"):
                holds.release(
                    conn,
                    scope="task",
                    identity=held,
                    expected_evidence_sha256="b" * 64,
                    review_sha256="c" * 64,
                    correction="reviewed exact evidence",
                    owner_fd=fd,
                )
            released = holds.release(
                conn,
                scope="task",
                identity=held,
                expected_evidence_sha256="a" * 64,
                review_sha256="c" * 64,
                correction="reviewed exact evidence",
                owner_fd=fd,
            )
            conn.commit()
        assert released["previous_review"] == value
        assert holds.find(conn, task=held) is None
        assert worker.choose()["task_id"] == held
        assert holds.find(conn, unit="job:unrelated")["evidence_sha256"] == "b" * 64
        with authority.controller(conn, run_id=None, owner_fd=fd):
            holds.put(
                conn,
                scope="unit",
                identity="job:unrelated",
                reason="different document proof gap",
                evidence_sha256="d" * 64,
                next_condition="independent document review",
                owner_fd=fd,
            )
            holds.release(
                conn,
                scope="unit",
                identity="job:unrelated",
                expected_evidence_sha256="b" * 64,
                review_sha256="e" * 64,
                correction="first gap resolved",
                owner_fd=fd,
            )
            conn.commit()
        assert holds.find(conn, unit="job:unrelated")["reason"] == "different document proof gap"
        assert (
            conn.execute(
                "SELECT count(*) FROM jobagg_legacy_review_holds WHERE run_id LIKE 'runtime-hold-release/%'"
            ).fetchone()[0]
            == 2
        )
        with pytest.raises(sqlite3.DatabaseError):
            conn.execute("DELETE FROM jobagg_legacy_review_holds")
