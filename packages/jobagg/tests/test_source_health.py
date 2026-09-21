import json
import time

from jobagg.source_health import read_worker_health
import test_remediation_worker as fixtures

setup = fixtures.setup


def test_queue_health_recovers_without_erasing_failure_history(setup):
    worker, replies, calls, _ = setup
    worker.max_tasks = 1
    worker.tick(execute=True)
    url = next(url for url in replies if not url.endswith("/jobs"))
    good = replies[url]
    replies[url] = TimeoutError("read")
    worker.tick(execute=True)
    health = read_worker_health(worker.db.path)["demo_workday"]
    assert health["health_status"] == "retry_pending" and health["retry_due_at"]
    assert health["last_success_at"] and health["last_attempt_status"] == "pending"
    with worker.db.connect() as conn:
        conn.execute("UPDATE remediation_tasks SET eligible_at=0")
    for path in (worker.shared_policy.root / "hosts").glob("*.json"):
        value = json.loads(path.read_text())
        value.pop("recovery", None)
        value["eligible_at"] = 0
        path.write_text(json.dumps(value))
    replies[url] = good
    worker.tick(execute=True)
    health = read_worker_health(worker.db.path)["demo_workday"]
    assert health["health_status"] == "ok" and health["retry_due_at"] is None
    assert health["coverage_status"] == "incomplete"
    assert (
        read_worker_health(worker.db.path, now=time.time() + 22000)["demo_workday"]["health_status"]
        == "stale"
    )
    with worker.db.connect() as conn:
        assert (
            conn.execute(
                "SELECT count(*) FROM remediation_attempts WHERE status='pending'"
            ).fetchone()[0]
            == 1
        )
        conn.execute("UPDATE remediation_tasks SET status='blocked' WHERE kind='listing'")
    assert read_worker_health(worker.db.path)["demo_workday"]["health_status"] == "degraded"


def test_source_hold_overrides_recent_success(setup):
    worker, _, _, _ = setup
    worker.tick(execute=True)
    (worker.shared_policy.root / "source_holds.json").write_text(
        json.dumps({"demo_workday": {"reason": "access denial"}})
    )
    health = read_worker_health(worker.db.path)["demo_workday"]
    assert health["health_status"] == "held" and health["active_hold"]
