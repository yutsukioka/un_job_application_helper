import hashlib
import json
import time

import pytest

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


def stop_host(worker, host):
    name = "host-" + hashlib.sha256(host.encode()).hexdigest()[:24] + ".json"
    hosts = worker.shared_policy.root / "hosts"
    hosts.mkdir(exist_ok=True)
    (hosts / name).write_text(json.dumps({"stopped": True}))


@pytest.mark.parametrize("host_setting", ["cxs_base_url", "listing_url"])
@pytest.mark.parametrize("queue", ["empty", "listing", "detail", "completed"])
def test_effective_source_host_hold_covers_empty_listing_and_public_detail_urls(
    setup, host_setting, queue
):
    worker, _, calls, _ = setup
    source = worker.by_id["demo_workday"]
    source.extra.pop("cxs_base_url")
    source.extra[host_setting] = "https://api.example/jobs"
    worker.initialize()
    if queue in {"listing", "completed"}:
        worker.seed_listings()
    with worker.db.connect() as conn:
        if queue == "detail":
            worker.enqueue(conn, source.id, "detail", "R1", {
                "listing": {"source_url": "https://demo.example/R1"},
            })
        elif queue == "completed":
            conn.execute("UPDATE remediation_tasks SET status='done'")
        persisted_host = conn.execute(
            "SELECT host FROM remediation_sources WHERE source_id=?", (source.id,)
        ).fetchone()[0]
    assert persisted_host == worker.task_host({"source_id": source.id, "payload": "{}"}) == "api.example"
    stop_host(worker, "api.example")
    health = read_worker_health(worker.db.path)[source.id]
    assert health["health_status"] == "held" and health["active_hold"]
    assert not calls


@pytest.mark.parametrize("status", ["pending", "blocked"])
def test_document_host_hold_uses_top_level_url(setup, status):
    worker, _, _, _ = setup
    worker.initialize()
    with worker.db.connect() as conn:
        worker.enqueue(conn, "demo_workday", "document", "attachment", {
            "url": "https://documents.example/attachment.pdf",
        })
        conn.execute("UPDATE remediation_tasks SET status=?", (status,))
    stop_host(worker, "documents.example")
    health = read_worker_health(worker.db.path)["demo_workday"]
    assert health["health_status"] == "held" and health["active_hold"]


@pytest.mark.parametrize("schema", ["missing_column", "null_host"])
def test_legacy_worker_health_is_read_only_without_host_metadata(setup, schema):
    worker, _, _, _ = setup
    worker.initialize()
    with worker.db.connect() as conn:
        if schema == "missing_column":
            conn.execute("ALTER TABLE remediation_sources DROP COLUMN host")
        else:
            conn.execute("UPDATE remediation_sources SET host=NULL")
    stop_host(worker, "demo.example")
    before = worker.db.path.read_bytes()
    health = read_worker_health(worker.db.path)["demo_workday"]
    assert health["health_status"] == "not_checked" and not health["active_hold"]
    assert worker.db.path.read_bytes() == before
    with worker.db.connect() as conn:
        worker.enqueue(conn, "demo_workday", "detail", "R1", {
            "listing": {"apply_url": "https://demo.example/R1"},
        })
    before = worker.db.path.read_bytes()
    health = read_worker_health(worker.db.path)["demo_workday"]
    assert health["health_status"] == "held" and health["active_hold"]
    assert worker.db.path.read_bytes() == before


def test_initialize_backfills_effective_host_in_legacy_source_table(setup):
    worker, _, _, _ = setup
    worker.by_id["demo_workday"].extra["cxs_base_url"] = "https://api.example/jobs"
    worker.initialize()
    with worker.db.connect() as conn:
        conn.execute("ALTER TABLE remediation_sources DROP COLUMN host")
    worker.initialize()
    with worker.db.connect() as conn:
        assert conn.execute("SELECT host FROM remediation_sources").fetchone()[0] == "api.example"
