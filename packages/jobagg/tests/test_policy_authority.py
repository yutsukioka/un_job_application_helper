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
    with pytest.raises((ValueError, OSError)):
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


def test_checkpointed_migration_resumes_each_page_and_cutover_without_empty_charges(native):
    from jobagg.policy_migration import step

    policy, owner = native
    for i in range(7):
        policy.reserve(str(i), "example", "detail", time.time() - 20 + i, policy.root, str(i))
    kwargs = dict(
        owner_fd=owner.fileno(),
        epoch="test",
        pauses=dict.fromkeys(policy.source_ids, 120),
        reviewed_writers=[{"name": "all fixture writers", "fenced": True}],
        limit=2,
    )

    def crash(where):
        if where == "after_database_rename":
            raise RuntimeError("power loss")

    progress = step(policy, **kwargs)
    assert not progress["complete"]
    with pytest.raises(ValueError, match="migration_incomplete"):
        policy.reserve("must-not-dispatch", "example", "detail", time.time(), policy.root, "x")
    for _ in range(40):
        progress = step(policy, **kwargs)
        if progress["phase"] == "cutover":
            break
    else:
        pytest.fail("migration did not reach finite cutover")
    with pytest.raises(RuntimeError, match="power loss"):
        step(policy, **kwargs, fault=crash)
    assert not (policy.root / "authority.json").exists()
    assert step(policy, **kwargs)["complete"]
    authority = PolicyAuthority(policy)
    with authority.connect() as conn:
        assert conn.execute("SELECT count(*) FROM attempts").fetchone()[0] == 7
        assert conn.execute("SELECT sum(n) FROM sources").fetchone()[0] == 7
    with pytest.raises((ValueError, OSError)):
        policy.event_snapshot()  # lagged JSON is never admission authority


def test_overdue_audit_has_explicit_bounded_catchup_without_charge_reset(native):
    policy, owner = native
    for i in range(3):
        policy.reserve(str(i), "example", "detail", time.time() - 10 + i, policy.root, str(i))
    authority = activate(policy, owner)
    authority.owner_fd = owner.fileno()
    future = time.time() + 100
    with pytest.raises(ValueError, match="audit_overdue"):
        authority.audit(limit=1, now=future, maximum_lag_seconds=1)
    assert authority.revision() == 0  # an admission hold did not leave pending writes
    for _ in range(3):
        authority.audit(limit=1, now=future, maximum_lag_seconds=1, reviewed_catchup=True)
    with authority.connect() as conn:
        assert conn.execute("SELECT count(*) FROM attempts").fetchone()[0] == 3
        assert conn.execute("SELECT audit_completed_at FROM authority").fetchone()[0] == future


def test_task_reconciliation_retains_orphan_charge_and_rejects_missing_charge(native):
    from jobagg.policy_migration import reconcile_worker

    policy, owner = native
    policy.reserve("charged", "example", "detail", 10, policy.root, "task")
    authority = activate(policy, owner)
    worker = sqlite3.connect(":memory:")
    worker.execute(
        "CREATE TABLE remediation_attempts(attempt_id TEXT PRIMARY KEY,task_id TEXT,source_id TEXT,kind TEXT,status TEXT,started_at REAL,finished_at REAL)"
    )
    worker.execute(
        "INSERT INTO remediation_attempts VALUES('unfunded','task','example','detail','reserved',10,NULL)"
    )
    worker.commit()
    result = reconcile_worker(
        authority, worker, after_attempt_id="", limit=2, deadline_at=time.monotonic() + 5
    )
    assert result["unresolved"][0]["attempt_id"] == "unfunded"
    assert result["policy_only_reservations_retained"]
    with authority.connect() as conn:
        assert conn.execute("SELECT count(*) FROM attempts").fetchone()[0] == 1
    worker.close()


def test_policy_commit_rechecks_actual_owner_lifetime_and_preserves_uncertainty(native):
    import fcntl

    policy, owner = native
    authority = activate(policy, owner)
    authority.owner_fd = owner.fileno()
    with pytest.raises(sqlite3.DatabaseError, match="not authorized"):
        with authority.connect(write=True) as conn:
            conn.execute(
                "INSERT INTO attempts VALUES('uncertain','example','listing',NULL,10,10,0,'{}')"
            )
            fcntl.flock(owner, fcntl.LOCK_UN)
    with pytest.raises(ValueError, match="pending_repair"):
        authority.revision()


def test_directory_fence_stops_actual_legacy_reserve_before_provider_and_preserves_unknown_charge(
    native,
):
    import hashlib

    policy, owner = native
    policy.reserve("known", "example", "detail", time.time(), policy.root, "known-task")
    authority = activate(policy, owner)
    assert (policy.root / "attempt_index.json").is_dir()
    provider = []
    # Exact old ordering: reserve bytes, then json.load/spread/rewrite index,
    # then dispatch. An object index alone can be spread as keys by that code.
    with pytest.raises(IsADirectoryError):
        event = policy.root / "attempts" / (hashlib.sha256(b"old-writer").hexdigest() + ".json")
        event.write_text(
            json.dumps(
                {
                    "attempt_id": "old-writer",
                    "source_id": "example",
                    "kind": "detail",
                    "started_at": time.time(),
                }
            )
        )
        index = json.loads((policy.root / "attempt_index.json").read_text())
        (policy.root / "attempt_index.json").write_text(json.dumps(sorted({*index, event.name})))
        provider.append("must not dispatch")
    assert not provider and event.exists()
    with pytest.raises(ValueError, match="boundary_changed"):
        authority.revision()
    with sqlite3.connect(authority.path) as conn:
        assert (
            conn.execute("SELECT count(*) FROM attempts").fetchone()[0] == 1
        )  # known charges retained; new unknown file requires reconciliation


def test_bootstrap_proofs_join_bounded_audit_and_source_hold_changes_require_review(
    native, monkeypatch
):
    import hashlib

    policy, owner = native
    original = policy.root / "bootstrap.json"
    value = json.loads(original.read_text())
    proof = policy.root.parent / "reviewed-original.json"
    proof.write_text("reviewed history")
    value["evidence"] = [
        {"path": str(proof), "sha256": hashlib.sha256(proof.read_bytes()).hexdigest()}
    ]
    original.write_text(json.dumps(value))
    marker = json.loads(policy.marker.read_text())
    marker["bootstrap_sha256"] = hashlib.sha256(original.read_bytes()).hexdigest()
    policy.marker.write_text(json.dumps(marker))
    monkeypatch.setattr(
        policy, "inspect", lambda: pytest.fail("unbounded initial historical audit")
    )
    authority = activate(policy, owner)
    authority.owner_fd = owner.fileno()
    with authority.connect() as conn:
        assert (
            conn.execute("SELECT sha256 FROM evidence WHERE path=?", (str(proof),)).fetchone()[0]
            == value["evidence"][0]["sha256"]
        )
    proof.write_text("changed historical proof")
    with pytest.raises(ValueError, match="evidence_missing_or_changed"):
        authority.audit(limit=1, now=time.time(), maximum_lag_seconds=100)


def test_native_source_hold_file_cannot_silently_release_reviewed_gates(native):
    policy, owner = native
    authority = activate(policy, owner)
    path = policy.root / "source_holds.json"
    path.chmod(0o600)
    path.write_text("{}")  # same content still changes the trusted boundary
    with pytest.raises(ValueError, match="bootstrap_control_changed"):
        authority.revision()
