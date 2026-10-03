import hashlib
import json
import time

import pytest

from jobagg.source_health import read_worker_health, worker_source_health as source_health, listing_recovery_pending
from jobagg.pipelines.http_checkpoint import HostIneligible
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


def host_file(worker):
    return worker.shared_policy.root / "hosts" / (
        "host-" + hashlib.sha256(b"demo.example").hexdigest()[:24] + ".json")


def initialize(worker, now):
    worker.initialize()
    worker.seed_listings()
    with worker.db.connection_scope() as conn:
        conn.execute("UPDATE remediation_sources SET last_list_at=?,next_list_at=?", (now - 14400, now + 10800))
        conn.execute("UPDATE remediation_tasks SET eligible_at=?", (now - 10,))


def health(worker, now, **kwargs):
    source = worker.by_id["demo_workday"]
    with worker.db.connect() as conn:
        state = conn.execute("SELECT * FROM remediation_sources WHERE source_id=?", (source.id,)).fetchone()
        return source_health(worker, conn, source, state, now=now, **kwargs)


def test_stale_listing_reports_nested_probe_budget_not_top_level_floor(setup):
    worker, _, calls, _ = setup
    now = time.time()
    initialize(worker, now)
    # EU's actual failure mode: both cooldown timestamps have elapsed, but
    # three reserved recovery probes still consume the rolling 24-hour budget.
    first = now - 80000
    host_file(worker).write_text(json.dumps({
        "stopped": False, "eligible_at": now - 100, "reason": "Transport interrupted",
        "failure_category": "transient_transport",
        "recovery": {"schema_version": 1, "phase": "cooldown",
                     "failure_kind": "transient_transport", "failures": 4,
                     "eligible_at": now - 100,
                     "probe_attempts": [first, now - 70000, now - 60000]},
    }))
    before = host_file(worker).read_bytes()
    result = health(worker, now)
    assert result["listing_stale"] and result["listing_age_seconds"] == 14400
    assert result["listing_interval_seconds"] == 10800
    assert result["listing"]["next_permitted_attempt_at"] == first + 86400
    assert result["listing"]["reasons"] == ["host_cooldown", "host_probe_budget"]
    assert result["hosts"][0]["recovery"]["recent_probe_count"] == 3
    assert result["next_pending_attempt_at"] == first + 86400
    assert result["eligible_pending_tasks"] == 0
    assert host_file(worker).read_bytes() == before and calls == []


def test_stopped_host_has_no_automatic_next_attempt_even_with_old_floor(setup):
    worker, _, _, _ = setup
    now = time.time()
    initialize(worker, now)
    host_file(worker).write_text(json.dumps({"stopped": True, "eligible_at": 1,
        "reason": "HTTP 403", "failure_category": "access_denied"}))
    result = health(worker, now)
    assert result["listing_stale"]
    assert result["listing"]["reasons"] == ["host_review_hold"]
    assert result["listing"]["next_permitted_attempt_at"] is None
    assert result["hosts"][0]["next_permitted_attempt_at"] is None
    assert result["task_eligibility"]["listing"]["held_pending"] == 1
    assert result["next_pending_attempt_at"] is None


def test_blocked_listing_survives_future_seed_and_has_no_predicted_retry(setup):
    worker, _, _, _ = setup
    now = time.time()
    initialize(worker, now)
    with worker.db.connection_scope() as conn:
        conn.execute("UPDATE remediation_tasks SET status='blocked',last_error='Inventory mismatch'")
        conn.execute("UPDATE remediation_sources SET next_list_at=0")
    result = health(worker, now)
    assert result["listing"]["task_status"] == "blocked"
    assert result["listing"]["last_error"] == "Inventory mismatch"
    assert result["listing"]["reasons"] == ["listing_blocked"]
    assert result["listing"]["next_permitted_attempt_at"] is None
    assert result["task_eligibility"]["listing"]["states"] == {"blocked": 1}


def test_source_hold_excludes_pending_listing_and_preserves_reason(setup):
    worker, _, _, _ = setup
    now = time.time()
    initialize(worker, now)
    hold = {"reason": "Review requires complete listing evidence"}
    result = health(worker, now, source_hold=hold)
    assert result["source_policy_hold"] == hold
    assert result["listing"]["reasons"] == ["source_policy_hold"]
    assert result["next_pending_attempt_at"] is None


def test_done_listing_reports_next_seed_instead_of_immediate_dispatch(setup):
    worker, _, _, _ = setup
    now = time.time()
    initialize(worker, now)
    with worker.db.connection_scope() as conn:
        conn.execute("UPDATE remediation_tasks SET status='done'")
        conn.execute("UPDATE remediation_sources SET last_list_at=?", (now - 600,))
    result = health(worker, now)
    assert result["listing_stale"] is False
    assert result["listing_inventory_complete"] is False
    assert result["listing"]["next_permitted_attempt_at"] == now + 10800
    assert result["listing"]["reasons"] == ["listing_schedule"]
    assert result["next_pending_attempt_at"] is None
    with worker.db.connection_scope() as conn:
        conn.execute('UPDATE remediation_sources SET listing_proof=?', ('{"complete":true}',))
    assert health(worker, now)["listing_inventory_complete"] is True


def test_source_pacing_uses_supplied_snapshot_once_and_task_cooldown_wins(setup, monkeypatch):
    worker, _, _, _ = setup
    now = time.time()
    worker.max_tasks = 1
    worker.tick(execute=True)
    with worker.db.connection_scope() as conn:
        conn.execute("UPDATE remediation_tasks SET eligible_at=? WHERE kind='detail'", (now + 900,))
    snapshot = [{"started_at": now - 30, "finished_at": now - 10}]
    seen = []
    def policy(source, kind, stamp, *, attempts=None):
        seen.append((kind, attempts))
        return stamp + 600 if kind == "detail" else stamp
    monkeypatch.setattr(worker, "policy_due", policy)
    monkeypatch.setattr(worker.shared_policy, "event_snapshot", lambda **_: (_ for _ in ()).throw(
        AssertionError("health must not repeatedly reread global event history")))
    result = health(worker, now, events=snapshot)
    assert result["task_eligibility"]["detail"]["deferred_pending"] == 1
    assert result["next_pending_attempt_at"] == now + 900
    assert result["pending_exclusion_reasons"] == {"task_cooldown": 1, "source_pacing_or_quota": 1}
    assert ("detail", snapshot) in seen


def test_reviewed_recovery_requires_new_complete_listing_at_selection_and_claim(setup):
    worker, _, calls, _ = setup
    now = time.time()
    initialize(worker, now)
    with worker.db.connection_scope() as conn:
        worker.enqueue(conn, "demo_workday", "detail", "pending", {})
        selected = dict(conn.execute("SELECT * FROM remediation_tasks WHERE kind='detail'").fetchone())
    host_file(worker).write_text(json.dumps({"stopped": False,
        "reviewed_source_recovery": {"requires_listing_after": now - 1}}))
    assert worker.choose(excluded_kinds=("listing",)) is None
    with pytest.raises(HostIneligible, match="Fresh complete listing"):
        worker.claim(selected)
    result = health(worker, now)
    assert result["task_eligibility"]["detail"]["held_pending"] == 1
    assert result["pending_exclusion_reasons"] == {"awaiting_recovery_listing": 1}
    with worker.db.connection_scope() as conn:
        conn.execute("UPDATE remediation_sources SET last_list_at=?,listing_proof=?", (now, '{"complete":false}'))
    assert worker.choose(excluded_kinds=("listing",)) is None
    with worker.db.connection_scope() as conn:
        conn.execute("UPDATE remediation_sources SET listing_proof=?", ('{"complete":true}',))
    assert worker.choose(excluded_kinds=("listing",))["task_id"] == selected["task_id"]
    assert health(worker, time.time())["task_eligibility"]["detail"]["eligible_pending"] == 1
    worker.claim(selected)
    assert calls == []


def test_missing_listing_state_is_allowed_normally_but_held_during_reviewed_recovery():
    assert not listing_recovery_pending(None, {})
    assert listing_recovery_pending(None, {"reviewed_source_recovery": {"requires_listing_after": 1}})
