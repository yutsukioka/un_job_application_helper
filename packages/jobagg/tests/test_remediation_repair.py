"""Queue recovery uses only temporary worker fixtures; no source requests."""

import json
from pathlib import Path
import time

import pytest

from jobagg import remediation_repair as repair
from jobagg.remediation_worker import dump, frame_listing, record
import test_remediation_worker

setup = test_remediation_worker.setup


def mutated_pending(worker):
    worker.tick(execute=True)
    with worker.db.connect() as conn:
        conn.execute("UPDATE remediation_sources SET next_list_at=0")
    worker.max_tasks = 1
    worker.tick(execute=True)
    with worker.db.connect() as conn:
        task = dict(conn.execute("SELECT * FROM remediation_tasks WHERE kind='detail'").fetchone())
        payload = json.loads(task["payload"])
        listing = frame_listing(payload, task["source_id"], task["external_id"])
        current = conn.execute(
            "SELECT * FROM jobs WHERE job_key=?", (listing.identity_key(),)
        ).fetchone()
        payload["listing"] = repair.projected_legacy_payload(listing, current)
        conn.execute(
            "UPDATE remediation_tasks SET payload=? WHERE task_id=?",
            (dump(payload), task["task_id"]),
        )
    return task["task_id"]


def snapshots(worker):
    with worker.db.connect() as conn:
        values = {
            name: [dict(x) for x in conn.execute("SELECT * FROM " + name)]
            for name in (
                "jobs",
                "remediation_attempts",
                "remediation_observations",
                "remediation_documents",
            )
        }
    values["policy"] = {
        str(p): p.read_bytes() for p in worker.shared_policy.root.rglob("*") if p.is_file()
    }
    return values


def test_exact_reproduced_mutation_restores_queue_without_history_or_jobs_changes(setup):
    worker, _, calls, _ = setup
    key = mutated_pending(worker)
    before = snapshots(worker)
    before_calls = list(calls)
    plan = repair.prepare(worker.workspace, worker.shared_lock)
    assert len(plan["candidates"]) == 1
    assert plan["candidates"][0]["category"] == "reproduced_database_listing_merge"
    assert plan["database_writes"] == 0 and snapshots(worker) == before
    result = repair.apply(plan)
    assert result["tasks_requeued"] == 1 and snapshots(worker) == before and calls == before_calls
    with worker.db.connect() as conn:
        task = dict(
            conn.execute("SELECT * FROM remediation_tasks WHERE task_id=?", (key,)).fetchone()
        )
        assert conn.execute("SELECT count(*) FROM remediation_queue_repairs").fetchone()[0] == 1
    frame_listing(json.loads(task["payload"]), task["source_id"], task["external_id"])
    for field in ("attempts", "claim", "last_error", "receipt", "discovered_at"):
        assert task[field] == plan["candidates"][0]["task_before"][field]
    assert repair.apply(plan)["status"] == "already_applied"
    assert snapshots(worker) == before


def test_zero_http_blocked_mutation_is_repairable_without_new_reservation(setup):
    worker, _, calls, _ = setup
    mutated_pending(worker)
    with worker.db.connect() as conn:
        conn.execute("UPDATE remediation_tasks SET eligible_at=0 WHERE kind='detail'")
    worker.tick(execute=True)
    before = snapshots(worker)
    plan = repair.prepare(worker.workspace, worker.shared_lock)
    assert len(plan["candidates"]) == 1
    assert plan["candidates"][0]["task_before"]["status"] == "blocked"
    repair.apply(plan)
    assert snapshots(worker) == before
    assert len(calls) == 3


@pytest.mark.parametrize("tamper", ["task", "frame", "plan", "source_hold", "host_hold"])
def test_apply_rejects_changed_inputs_and_keeps_history(setup, tamper):
    worker, _, _, _ = setup
    key = mutated_pending(worker)
    plan = repair.prepare(worker.workspace, worker.shared_lock)
    if tamper == "task":
        with worker.db.connect() as conn:
            conn.execute(
                "UPDATE remediation_tasks SET last_error='Changed after preview' WHERE task_id=?",
                (key,),
            )
    elif tamper == "frame":
        Path(plan["candidates"][0]["evidence"][0]["path"]).write_text("{}")
    elif tamper == "plan":
        plan["candidates"][0]["new_payload"] = "{}"
    elif tamper == "source_hold":
        (worker.shared_policy.root / "source_holds.json").write_text(
            dump({"demo_workday": {"reason": "review"}})
        )
    else:
        host = next((worker.shared_policy.root / "hosts").glob("host-*.json"))
        value = json.loads(host.read_text())
        value["stopped"] = True
        host.write_text(dump(value))
    before = snapshots(worker)
    with pytest.raises(ValueError):
        repair.apply(plan)
    assert snapshots(worker) == before


def test_arbitrary_raw_mutation_is_not_blessed_as_legacy_merge(setup):
    worker, _, _, _ = setup
    key = mutated_pending(worker)
    with worker.db.connect() as conn:
        task = dict(
            conn.execute("SELECT * FROM remediation_tasks WHERE task_id=?", (key,)).fetchone()
        )
        payload = json.loads(task["payload"])
        payload["listing"]["raw"]["invented"] = "foreign"
        conn.execute("UPDATE remediation_tasks SET payload=? WHERE task_id=?", (dump(payload), key))
    assert repair.prepare(worker.workspace, worker.shared_lock)["candidates"] == []


def test_false_metadata_readback_plan_preserves_failed_capture_and_attempt(setup, monkeypatch):
    worker, _, _, _ = setup
    worker.max_tasks = 1
    worker.tick(execute=True)
    prior = record(worker.db.get_job("demo_workday:R1"))
    prior.department = "Observed unit"
    worker.db.upsert_job(prior)
    original = worker.db.get_job

    def bad_readback(key):
        result = original(key)
        result["department"] = "Different"
        return result

    monkeypatch.setattr(worker.db, "get_job", bad_readback)
    worker.tick(execute=True)
    monkeypatch.setattr(worker.db, "get_job", original)
    before = snapshots(worker)
    plan = repair.prepare(worker.workspace, worker.shared_lock)
    assert len(plan["candidates"]) == 1
    assert plan["candidates"][0]["category"] == "false_absent_metadata_readback"
    assert repair.apply(plan)["tasks_requeued"] == 1
    assert snapshots(worker) == before


def test_legacy_single_transient_failure_can_requeue_but_host_floor_persists(setup):
    worker, replies, _, _ = setup
    url = next(url for url in replies if not url.endswith("/jobs"))
    replies[url] = TimeoutError("Legacy fixture timeout")
    worker.tick(execute=True)
    with worker.db.connect() as conn:
        conn.execute("UPDATE remediation_tasks SET status='blocked' WHERE kind='detail'")
    before = snapshots(worker)
    plan = repair.prepare(worker.workspace, worker.shared_lock)
    assert len(plan["candidates"]) == 1
    assert plan["candidates"][0]["category"] == "guarded_transient_transport"
    assert plan["candidates"][0]["eligible_at_floor"] > time.time()
    repair.apply(plan)
    assert snapshots(worker) == before


@pytest.mark.parametrize("tamper", [False, True])
def test_only_bound_historical_listing_marker_may_differ(setup, tamper):
    worker, _, _, _ = setup
    worker.tick(execute=True)
    old_marker = worker.db.get_job("demo_workday:R1")["raw"]["_jobagg_listing_verification"]
    with worker.db.connect() as conn:
        conn.execute("UPDATE remediation_sources SET next_list_at=0")
    worker.max_tasks = 1
    worker.tick(execute=True)
    with worker.db.connect() as conn:
        task = dict(conn.execute("SELECT * FROM remediation_tasks WHERE kind='detail'").fetchone())
        payload = json.loads(task["payload"])
        listing = frame_listing(payload, task["source_id"], task["external_id"])
        current = conn.execute(
            "SELECT * FROM jobs WHERE job_key=?", (listing.identity_key(),)
        ).fetchone()
        payload["listing"] = repair.projected_legacy_payload(listing, current)
        if tamper:
            old_marker["observed_at"] = "2000-01-01T00:00:00+00:00"
        payload["listing"]["raw"]["_jobagg_listing_verification"] = old_marker
        conn.execute(
            "UPDATE remediation_tasks SET payload=? WHERE task_id=?",
            (dump(payload), task["task_id"]),
        )
    plan = repair.prepare(worker.workspace, worker.shared_lock)
    if tamper:
        assert plan["candidates"] == []
        assert any("own original frame" in item["reason"] for item in plan["held"])
    else:
        assert len(plan["candidates"]) == 1
        assert (
            plan["candidates"][0]["category"]
            == "reproduced_merge_with_bound_historical_listing_marker"
        )
        before = snapshots(worker)
        repair.apply(plan)
        assert snapshots(worker) == before


def test_transient_failure_cannot_bless_unrelated_payload_mutation(setup):
    worker, replies, _, _ = setup
    url = next(url for url in replies if not url.endswith("/jobs"))
    replies[url] = TimeoutError("Legacy timeout")
    worker.tick(execute=True)
    with worker.db.connect() as conn:
        task = dict(conn.execute("SELECT * FROM remediation_tasks WHERE kind='detail'").fetchone())
        payload = json.loads(task["payload"])
        payload["listing"]["raw"]["unrelated"] = "tampered"
        conn.execute(
            "UPDATE remediation_tasks SET status='blocked',payload=? WHERE task_id=?",
            (dump(payload), task["task_id"]),
        )
    assert repair.prepare(worker.workspace, worker.shared_lock)["candidates"] == []


def test_legacy_listing_budget_repair_requires_typed_captures_and_preserves_holds(setup, monkeypatch):
    worker, _, _, _ = setup
    worker.initialize()
    source, token = 'worldbank_csod', 'old-budget-attempt'
    target = worker.workspace / 'captures' / token / 'http'
    target.mkdir(parents=True)
    metadata = {'phase': {'kind': 'listing', 'job_id': None}, 'state': 'failed',
                'error_type': 'HostIneligible', 'error': 'HostIneligible: Bounded worker deadline reached',
                'url': 'https://us.api.csod.com/rec-job-search/external/jobs',
                'started_at': '2026-09-17T10:20:41Z', 'finished_at': '2026-09-17T10:20:43Z'}
    path = target / '00001.json'; path.write_text(json.dumps(metadata))
    receipt = json.dumps({'capture_directory': str(target.parent), 'error': 'old CSOD wrapper'})
    with worker.db.connection_scope() as conn:
        key = worker.enqueue(conn, source, 'listing', '', {})
        conn.execute("UPDATE remediation_tasks SET status='blocked',claim=?,receipt=? WHERE task_id=?", (token, receipt, key))
        conn.execute('INSERT INTO remediation_attempts VALUES(?,?,?,?,?,?,?,?)',
                     (token, key, source, 'listing', 1, 2, 'blocked', receipt))
        task = dict(conn.execute('SELECT * FROM remediation_tasks WHERE task_id=?', (key,)).fetchone())
        monkeypatch.setattr(repair, 'policy_state', lambda *args: (123, []))
        candidate = repair.classify_budget_listing(conn, worker.workspace, worker.shared_lock, task)
        assert candidate['category'] == 'captured_legacy_listing_budget_deferral'
        assert candidate['new_payload'] == task['payload'] and candidate['eligible_at_floor'] == max(task['eligible_at'], 123)
        assert candidate['evidence'] == [repair.reference(path)]
        metadata.update(error_type='HTTPError', status_code=403)
        path.write_text(json.dumps(metadata))
        assert repair.classify_budget_listing(conn, worker.workspace, worker.shared_lock, task) is None
        metadata.update(error_type='HostIneligible', status_code=None)
        path.write_text(json.dumps(metadata))
        def held(*args):
            raise ValueError('Host policy remains stopped')
        monkeypatch.setattr(repair, 'policy_state', held)
        with pytest.raises(ValueError, match='Host policy remains stopped'):
            repair.classify_budget_listing(conn, worker.workspace, worker.shared_lock, task)
        assert conn.execute('SELECT status FROM remediation_tasks WHERE task_id=?', (key,)).fetchone()[0] == 'blocked'
