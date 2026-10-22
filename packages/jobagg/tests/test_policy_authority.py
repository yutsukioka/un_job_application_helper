import json
import sqlite3
import time

import pytest

from jobagg.policy_authority import PolicyAuthority, migrate
from test_worker_policy import bootstrap


@pytest.fixture
def native(tmp_path):
    policy, path, value = bootstrap(tmp_path)
    value["scope_source_ids"] = ["example", "unicef_pageup"]
    path.write_text(json.dumps(value))
    policy.source_ids = set(value["scope_source_ids"])
    policy.initialize()
    owner = policy.owner.open("a+")
    yield policy, owner
    owner.close()


def activate(policy, owner, pause=120):
    return migrate(
        policy,
        owner_fd=owner.fileno(),
        epoch="test",
        pauses={source: pause for source in policy.source_ids},
        reviewed_writers=[
            {"name": "fixture worker/manual/browser/recovery inventory", "fenced": True}
        ],
    )


def test_cold_indexed_gate_matches_overfull_legacy_window_without_history_census(
    native, monkeypatch
):
    policy, owner = native
    clock = time.time()
    for i in range(17):
        policy.reserve(str(i), "unicef_pageup", "detail", clock - 170 + i, policy.root, str(i))
    expected = policy.event_snapshot()["unicef_pageup"]
    authority = activate(policy, owner)
    cold = PolicyAuthority(policy)
    monkeypatch.setattr(policy, "event_snapshot", lambda **_: pytest.fail("cold history census"))
    kwargs = dict(minimum=15, jitter=7, batch=4, pause=120)
    finishes = [row["started_at"] for row in expected]
    predicted = max(finishes[-1] + 22, finishes[-1] + 120, sorted(finishes)[-10] + 3600)
    assert cold.due("unicef_pageup", clock, **kwargs) == predicted
    assert authority.revision() == 0


def test_reservation_is_durable_before_task_commit_and_late_finish_preserves_batch(
    native, monkeypatch
):
    policy, owner = native
    authority = activate(policy, owner, pause=10)
    authority.owner_fd = owner.fileno()
    clock = time.time()
    monkeypatch.setattr("jobagg.policy_authority.time.time", lambda: clock + 25)
    for i, start in enumerate((clock, clock + 20, clock + 21)):
        authority.reserve(
            dict(
                attempt_id=str(i),
                source_id="example",
                kind="detail",
                started_at=start,
                finished_at=None,
                status="reserved",
            )
        )
    assert authority.due("example", clock, minimum=0, jitter=0, batch=3, pause=10) == clock + 21
    authority.finish("0", "failed")
    assert authority.due("example", clock, minimum=0, jitter=0, batch=3, pause=10) == clock + 31
    with sqlite3.connect(authority.path) as conn:
        assert conn.execute("SELECT count(*) FROM attempts").fetchone()[0] == 3
    with pytest.raises(ValueError, match="owner"):
        PolicyAuthority(policy).reserve(
            dict(attempt_id="4", source_id="example", kind="detail", started_at=clock + 30)
        )


def test_import_includes_unindexed_and_missing_finish_charges(native):
    policy, owner = native
    policy.reserve("crash", "example", "detail", time.time() - 10, policy.root, "task")
    (policy.root / "attempt_index.json").write_text("[]")
    authority = activate(policy, owner)
    assert (
        authority.due("example", time.time(), minimum=30, jitter=0, batch=0, pause=120)
        > time.time()
    )
    with pytest.raises(ValueError, match="Malformed"):
        policy.event_snapshot()


def test_audit_detects_selected_damage_without_refunding_charge(native):
    policy, owner = native
    policy.reserve("one", "example", "detail", time.time(), policy.root, "task")
    authority = activate(policy, owner)
    authority.owner_fd = owner.fileno()
    evidence = next((policy.root / "attempts").glob("*.json"))
    evidence.write_text("changed in place")
    with pytest.raises(ValueError, match="evidence_missing_or_changed"):
        authority.audit(limit=1, now=time.time(), maximum_lag_seconds=100)
    with sqlite3.connect(authority.path) as conn:
        assert conn.execute("SELECT count(*) FROM attempts").fetchone()[0] == 1
    with pytest.raises(ValueError, match="repair_required"):
        authority.revision()


def test_restore_or_lagged_head_cannot_authorize_empty_window(native):
    policy, owner = native
    authority = activate(policy, owner)
    authority.owner_fd = owner.fileno()
    old = authority.head.read_bytes()
    authority.reserve(
        dict(
            attempt_id="one",
            source_id="example",
            kind="detail",
            started_at=time.time(),
            finished_at=None,
        )
    )
    authority.head.write_bytes(old)
    with pytest.raises(ValueError, match="restore_mismatch"):
        authority.due("example", time.time(), minimum=0, jitter=0, batch=0, pause=120)
