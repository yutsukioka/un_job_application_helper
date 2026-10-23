import json

import pytest

from jobagg import verification_budget as budget
from test_remediation_worker import setup  # noqa: F401

# ruff: noqa: F811


def test_chunked_decompression_stops_on_suspend_and_restores_deadline_context(monkeypatch):
    clock = [0.0]
    reads = []
    monkeypatch.setattr(budget.time, "monotonic", lambda: clock[0])

    class Stream:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def read(self, n):
            reads.append(n)
            clock[0] = 11
            return b"x" * n

    monkeypatch.setattr(budget.gzip, "open", lambda *_: Stream())
    with pytest.raises(budget.VerificationBudgetExceeded):
        with budget.scope(deadline_at=10):
            budget.read("/synthetic/no-open", compressed=True)
    assert reads == [1024 * 1024]
    # No deadline from the expired nested scope leaks into another operation.
    budget.check()


@pytest.mark.parametrize("storage", [False, True])
def test_inner_census_expiry_keeps_charge_capture_and_pending_task_without_provider_failure(
    setup, monkeypatch, storage
):
    worker, _, calls, _ = setup
    request = None
    if storage:
        from test_worker_storage_ingress import enable_storage

        worker, _, calls, _, request, _ = enable_storage(setup, monkeypatch)
    worker.max_tasks = 1

    def expired(*_, **__):
        raise budget.VerificationBudgetExceeded(
            "injected after capture before independent census completes"
        )

    monkeypatch.setattr("jobagg.pipelines.inventory_checks.verification_read", expired)
    report = worker.tick(execute=True, request=request)
    assert report["tick_stop_reason"] == "work_deadline"
    assert report["concurrency"]["accepted_progress"] == 0
    assert report["tick_outcomes"][0]["provider_failure"] is False
    assert calls
    with worker.db.connect() as conn:
        assert conn.execute("SELECT status FROM remediation_tasks").fetchone()[0] == "pending"
        assert (
            conn.execute("SELECT status FROM remediation_attempts").fetchone()[0]
            == "verification_deferred"
        )
        assert conn.execute("SELECT count(*) FROM remediation_observations").fetchone()[0] == 0
    attempts = list((worker.shared_policy.root / "attempts").glob("*.json"))
    assert len(attempts) == 1
    assert json.loads(attempts[0].read_text())["status"] == "verification_deferred"
    assert list((worker.workspace / "captures").glob("*/http/*.body.gz"))
