"""Scale and progress regressions; all HTTP replies are local fixtures."""

from dataclasses import replace
import hashlib
from pathlib import Path
import time

import pytest

from jobagg.pipelines.http_checkpoint import HostIneligible
from jobagg.remediation_scheduling import Task, select_due
from jobagg.remediation_worker import dump
import test_remediation_worker

setup = test_remediation_worker.setup


def test_thousand_pending_details_read_five_thousand_events_only_once(setup, monkeypatch):
    worker, _, _, _ = setup
    worker.initialize()
    now = time.time()
    paths = []
    for index in range(5000):
        name = hashlib.sha256(str(index).encode()).hexdigest() + ".json"
        paths.append(name)
        (worker.shared_policy.root / "attempts" / name).write_text(
            dump(
                {
                    "attempt_id": str(index),
                    "source_id": "demo_workday",
                    "kind": "detail",
                    "started_at": now - 20000 + index,
                    "finished_at": now - 19999 + index,
                }
            )
        )
    (worker.shared_policy.root / "attempt_index.json").write_text(dump(paths))
    with worker.db.connection_scope() as conn:
        for index in range(1200):
            worker.enqueue(conn, "demo_workday", "detail", str(index), {})
    reads = []
    original_read = Path.read_text
    original_due = worker.policy_due
    due_calls = []
    host_calls = []
    original_host = worker.host_state

    def read(path, *args, **kwargs):
        if path.parent == worker.shared_policy.root / "attempts":
            reads.append(path.name)
        return original_read(path, *args, **kwargs)

    def due(*args, **kwargs):
        due_calls.append(args[0].id)
        return original_due(*args, **kwargs)

    def host(*args):
        host_calls.append(args[0])
        return original_host(*args)

    monkeypatch.setattr(Path, "read_text", read)
    monkeypatch.setattr(worker, "policy_due", due)
    monkeypatch.setattr(worker, "host_state", host)
    assert worker.choose()["kind"] == "detail"
    assert len(reads) == len(set(reads)) == 5000
    assert due_calls == ["demo_workday"]
    assert host_calls == ["demo.example"]


def test_kind_fairness_progresses_details_and_documents_during_initial_discovery():
    tasks = [Task(str(n), f"unserved-{n}", f"host-{n}", 1, kind="listing") for n in range(49)]
    tasks.extend(
        [
            Task("detail", "served", "served.example", 2, kind="detail"),
            Task("document", "served", "served.example", 3, kind="document"),
        ]
    )
    kinds = {"listing": 90}
    chosen = []
    for now in (100, 101, 102):
        task = select_due(tasks, now=now, source_last_served={"served": 99}, kind_last_served=kinds)
        chosen.append(task.kind)
        kinds[task.kind] = now
        tasks.remove(task)
    assert chosen == ["detail", "document", "listing"]


def _finish_without_fetch(worker, task, deadline):
    token = worker.claim(task, deadline=deadline)
    with worker.db.connection_scope() as conn:
        worker.finish(conn, task, token, "done", {"fixture_only": True})
    return {"kind": task["kind"]}


def test_detail_cap_excludes_details_and_continues_other_kinds(setup, monkeypatch):
    worker, _, calls, _ = setup
    worker.initialize()
    with worker.db.connection_scope() as conn:
        worker.enqueue(conn, "demo_workday", "detail", "pending", {})
        worker.enqueue(
            conn, "demo_workday", "document", "doc", {"url": "https://demo.example/tor.pdf"}
        )
    worker.max_detail_tasks = 0
    monkeypatch.setattr(
        worker, "perform", lambda task, deadline: _finish_without_fetch(worker, task, deadline)
    )
    report = worker.tick(execute=True)
    assert sorted(row["kind"] for row in report["tick_outcomes"]) == ["document", "listing"]
    with worker.db.connect() as conn:
        assert (
            conn.execute("SELECT status FROM remediation_tasks WHERE kind='detail'").fetchone()[0]
            == "pending"
        )
    assert not calls


def test_kind_service_history_survives_next_tick(setup, monkeypatch):
    worker, _, _, _ = setup
    worker.initialize()
    with worker.db.connection_scope() as conn:
        worker.enqueue(conn, "demo_workday", "detail", "pending", {})
        worker.enqueue(
            conn, "demo_workday", "document", "doc", {"url": "https://demo.example/tor.pdf"}
        )
    worker.max_tasks = 1
    monkeypatch.setattr(
        worker, "perform", lambda task, deadline: _finish_without_fetch(worker, task, deadline)
    )
    kinds = [worker.tick(execute=True)["tick_outcomes"][0]["kind"] for _ in range(3)]
    assert set(kinds) == {"listing", "detail", "document"}


def test_deadline_during_selection_emits_report_without_claim(setup, monkeypatch):
    worker, _, calls, _ = setup
    clock = [time.time()]
    monkeypatch.setattr(time, "time", lambda: clock[0])
    original_choose = worker.choose

    def slow_selection(**kwargs):
        task = original_choose(**kwargs)
        clock[0] = kwargs["deadline"] + 0.001
        return task

    monkeypatch.setattr(worker, "choose", slow_selection)
    report = worker.tick(execute=True)
    assert report["tick_stop_reason"] == "work_deadline"
    assert report["tick_outcomes"] == []
    assert report["report_reserve_seconds"] == 15
    assert (worker.workspace / "ticks" / f"{worker.tick_id}.json").exists()
    with worker.db.connect() as conn:
        assert conn.execute("SELECT count(*) FROM remediation_attempts").fetchone()[0] == 0
    assert not calls


def test_claim_rechecks_deadline_after_fresh_policy_read_and_does_not_reserve(setup, monkeypatch):
    worker, _, calls, _ = setup
    worker.initialize()
    with worker.db.connection_scope() as conn:
        worker.enqueue(conn, "demo_workday", "detail", "pending", {})
    task = worker.choose()
    now = time.time()
    clock = [now]
    monkeypatch.setattr(time, "time", lambda: clock[0])
    original_due = worker.policy_due

    def slow_policy(*args, **kwargs):
        value = original_due(*args, **kwargs)
        clock[0] = now + 2
        return value

    monkeypatch.setattr(worker, "policy_due", slow_policy)
    with pytest.raises(HostIneligible, match="budget exhausted"):
        worker.claim(task, deadline=now + 1)
    assert not list((worker.shared_policy.root / "attempts").glob("*.json"))
    with worker.db.connect() as conn:
        assert conn.execute("SELECT status FROM remediation_tasks").fetchone()[0] == "pending"
    assert not calls


def test_selected_task_cannot_bypass_new_cooldown_or_quota(setup):
    worker, _, calls, _ = setup
    worker.initialize()
    worker.seed_listings()
    task = worker.choose()
    host = "host-" + hashlib.sha256(b"demo.example").hexdigest()[:24] + ".json"
    state = worker.shared_policy.root / "hosts" / host
    state.write_text(dump({"eligible_at": time.time() + 100, "stopped": False}))
    with pytest.raises(HostIneligible, match="eligibility changed"):
        worker.claim(task)
    state.write_text("{}")
    source = replace(worker.by_id["demo_workday"], id="unicef_pageup")
    worker.by_id[source.id] = source
    with worker.db.connection_scope() as conn:
        worker.enqueue(conn, source.id, "detail", "pending", {})
    selected = worker.choose(excluded_kinds={"listing"})
    for n in range(10):
        worker.shared_policy.reserve(
            str(n), source.id, "detail", time.time(), worker.workspace, "x"
        )
    with pytest.raises(HostIneligible, match="pacing/quota"):
        worker.claim(selected)
    assert len(worker.shared_policy.events(source.id)) == 10
    assert not calls


def test_snapshot_counts_unindexed_crash_reservation_and_rejects_missing_indexed_event(setup):
    worker, _, _, _ = setup
    worker.initialize()
    worker.shared_policy.reserve(
        "crash", "demo_workday", "detail", time.time(), worker.workspace, "job"
    )
    index = worker.shared_policy.root / "attempt_index.json"
    indexed = index.read_text()
    index.write_text("[]")
    assert len(worker.shared_policy.event_snapshot()["demo_workday"]) == 1
    index.write_text(indexed)
    next((worker.shared_policy.root / "attempts").glob("*.json")).unlink()
    with pytest.raises(ValueError, match="history removed"):
        worker.shared_policy.event_snapshot()


def test_snapshot_budget_check_interrupts_long_scan_without_mutation(setup):
    worker, _, _, _ = setup
    worker.initialize()
    for n in range(130):
        worker.shared_policy.reserve(
            str(n), "demo_workday", "detail", time.time(), worker.workspace, "x"
        )
    checks = []

    def stop():
        checks.append(True)
        if len(checks) == 3:
            raise HostIneligible("budget", category="budget")

    with pytest.raises(HostIneligible, match="budget"):
        worker.shared_policy.event_snapshot(check_deadline=stop)
    assert len(list((worker.shared_policy.root / "attempts").glob("*.json"))) == 130


def test_retry_is_served_after_untouched_sibling_in_same_source_share():
    tasks = [
        Task("old-retry", "source", "host", 1, kind="detail", last_attempt_at=90),
        Task("untouched", "source", "host", 2, kind="detail"),
    ]
    assert select_due(tasks, now=100).task_id == "untouched"


def test_pending_retry_floor_survives_listing_refresh(setup):
    worker, _, _, _ = setup
    worker.initialize()
    with worker.db.connection_scope() as conn:
        key = worker.enqueue(conn, "demo_workday", "detail", "retry", {})
        conn.execute(
            "UPDATE remediation_tasks SET eligible_at=?,last_error='temporary timeout' WHERE task_id=?",
            (time.time() + 600, key),
        )
        before = dict(
            conn.execute("SELECT * FROM remediation_tasks WHERE task_id=?", (key,)).fetchone()
        )
        worker.enqueue(
            conn,
            "demo_workday",
            "detail",
            "retry",
            {"new": "listing"},
            due=time.time(),
            refresh=True,
        )
        after = dict(
            conn.execute("SELECT * FROM remediation_tasks WHERE task_id=?", (key,)).fetchone()
        )
    assert after["eligible_at"] == before["eligible_at"]
    assert after["attempts"] == before["attempts"]
