"""Directory allocations stay charged across capture grants and failed setup."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import time

import pytest

from jobagg.pipelines.http_checkpoint import DurableCapture
from jobagg.remediation_worker import WorkerStorage
from jobagg.storage_budget import StorageBudgetDeferred
from test_remediation_worker import setup
from test_worker_storage_ingress import enable_storage


@pytest.fixture
def budgeted(setup, monkeypatch):
    worker, replies, calls, tmp, request, ledger = enable_storage(setup, monkeypatch)
    budget = WorkerStorage(worker, ledger.for_writer("worker").wire(), request["storage_limits"])
    return worker, budget, calls, request, ledger


def capture(worker, budget, target, grant):
    source = worker.by_id["demo_workday"]
    return DurableCapture(
        worker.client_factory(source, worker.policy), worker.policy, target, {},
        lock_root=worker.shared_policy.root / "hosts", storage=budget,
        first_storage_grant=grant,
    )


def test_capture_constructor_charges_all_missing_parents_before_creation(budgeted, monkeypatch):
    worker, budget, calls, _, ledger = budgeted
    target = worker.workspace / "captures" / "attempt"
    grant = budget.request_grant()
    observed = []
    original = Path.mkdir

    def checked_mkdir(path, *args, **kwargs):
        if path == target.parent or target == path or target in path.parents:
            observed.append((path, grant.consumed))
            assert grant.consumed == len(observed) * 4096
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", checked_mkdir)
    capture(worker, budget, target, grant)
    assert [path for path, _ in observed] == [target.parent, target, target / "http"]
    assert grant.consumed == 3 * 4096
    grant.close()
    assert budget.used["ingress"] == 3 * 4096
    assert ledger.snapshot()["settled"]["essential"] == 3 * 4096
    assert not ledger.snapshot()["operations"] and not calls


def test_repeated_capture_grants_charge_only_new_directories(budgeted):
    worker, budget, calls, _, ledger = budgeted
    first = budget.request_grant()
    capture(worker, budget, worker.workspace / "captures" / "first", first)
    first.close()
    second = budget.request_grant()
    capture(worker, budget, worker.workspace / "captures" / "first", second)
    assert second.consumed == 0
    capture(worker, budget, worker.workspace / "captures" / "second", second)
    assert second.consumed == 2 * 4096
    second.close()
    assert budget.used["ingress"] == ledger.snapshot()["settled"]["essential"] == 5 * 4096
    assert not ledger.snapshot()["operations"] and not calls


def test_settled_directories_exhaust_later_request_admission(budgeted):
    worker, budget, calls, _, ledger = budgeted
    probe = budget.request_grant()
    maximum = probe.maximum
    probe.close()
    occupied = ledger.reserve(
        "essential", ledger.remaining_envelope()["essential"] - maximum - 3 * 4096,
        path=worker.workspace,
    )
    try:
        for name in ("first", "second"):
            grant = budget.request_grant()
            capture(worker, budget, worker.workspace / "captures" / name, grant)
            grant.close()
        assert budget.used["ingress"] == 5 * 4096
        with pytest.raises(StorageBudgetDeferred, match="operation_envelope_exhausted"):
            budget.request_grant()
        assert not (worker.workspace / "captures" / "third").exists()
    finally:
        occupied.settle(0)
    assert ledger.snapshot()["settled"]["essential"] == 5 * 4096
    assert not ledger.snapshot()["operations"] and not calls


@pytest.mark.parametrize("limit,created", [(4095, 0), (4096, 1), (8192, 2), (12288, 3)])
def test_directory_cap_denies_before_unpaid_parent_or_child(budgeted, limit, created):
    worker, budget, calls, _, ledger = budgeted
    target = worker.workspace / "captures" / "attempt" / "http"
    grant = budget.reserve("ingress", limit, target)
    if created < 3:
        with pytest.raises(StorageBudgetDeferred, match="write_limit_exceeded"):
            budget.ensure_directory(target, grant=grant)
    else:
        budget.ensure_directory(target, grant=grant)
    paths = [target.parent.parent, target.parent, target]
    assert [path.exists() for path in paths] == [index < created for index in range(3)]
    assert grant.consumed == created * 4096 <= limit
    grant.close()
    assert ledger.snapshot()["settled"]["essential"] == created * 4096
    assert not ledger.snapshot()["operations"] and not calls


def test_existing_directories_need_no_remaining_grant(budgeted):
    worker, budget, calls, _, ledger = budgeted
    grant = budget.reserve("ingress", 1, worker.workspace)
    grant.consume(1)
    budget.ensure_directory(worker.workspace, grant=grant)
    with pytest.raises(FileExistsError):
        budget.ensure_directory(worker.workspace, grant=grant, exist_ok=False)
    assert grant.consumed == 1
    grant.close()
    assert ledger.snapshot()["settled"]["essential"] == 1 and not calls


def test_concurrent_captures_charge_shared_parent_only_once(budgeted):
    worker, budget, calls, _, ledger = budgeted
    grants = [budget.request_grant(), budget.request_grant()]
    targets = [worker.workspace / "captures" / str(index) / "http" for index in range(2)]
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda pair: budget.ensure_directory(pair[0], grant=pair[1]), zip(targets, grants)))
    assert sum(grant.consumed for grant in grants) == 5 * 4096
    for grant in grants:
        grant.close()
    assert ledger.snapshot()["settled"]["essential"] == 5 * 4096
    assert not ledger.snapshot()["operations"] and not calls


def test_host_directory_uses_preclaimed_control_grant(budgeted):
    worker, budget, calls, _, ledger = budgeted
    worker.seed_listings()
    task = worker.choose()
    budget.preclaim(task)
    control = budget.completion[task["task_id"]]["first_host"]
    with budget.task_scope(task["task_id"]):
        budget.ensure_directory(worker.shared_policy.root / "hosts" / "nested" / "leaf")
    assert control.consumed == 2 * 4096
    assert budget.initial[task["task_id"]].consumed == 0
    budget.release_task(task["task_id"])
    assert budget.used["control"] == 2 * 4096
    assert not ledger.snapshot()["operations"] and not calls


@pytest.mark.parametrize("failure", ["capture", "adapter"])
def test_context_failure_releases_grants_but_keeps_directory_charges(budgeted, monkeypatch, failure):
    worker, budget, calls, _, ledger = budgeted
    worker.seed_listings()
    task = worker.choose()
    worker.storage = budget
    budget.preclaim(task)
    grant = budget.initial[task["task_id"]]
    target = worker.workspace / "captures" / "failed-setup"
    source = worker.by_id[task["source_id"]]
    if failure == "capture":
        original = worker.client_factory

        def invalid_tls(*args):
            client = original(*args)
            client.tls_verify = False
            return client

        worker.client_factory = invalid_tls
        error = "verified TLS"
    else:
        def fail_adapter(*args, **kwargs):
            raise ValueError("fixture adapter setup failed")

        monkeypatch.setattr("jobagg.remediation_worker.get_adapter_class", fail_adapter)
        error = "fixture adapter"
    with budget.task_scope(task["task_id"]):
        with pytest.raises(ValueError, match=error):
            worker.context(source, target, task, time.time() + 120)
    assert grant.consumed == 3 * 4096
    budget.release_task(task["task_id"])
    assert grant.closed and budget.used["ingress"] == 3 * 4096
    assert not budget.initial and not budget.completion and not ledger.snapshot()["operations"]
    assert not calls


def test_worker_target_creation_charges_the_preclaimed_ingress(budgeted, monkeypatch):
    worker, budget, calls, request, ledger = budgeted
    original = Path.mkdir
    checked = []

    def checked_mkdir(path, *args, **kwargs):
        capture_root = worker.workspace / "captures"
        if not path.exists() and (path == capture_root or capture_root in path.parents):
            operations = ledger.snapshot()["operations"].values()
            consumed = sum(item["consumed"] for item in operations if item["path"] == str(capture_root))
            checked.append((path, consumed))
            assert consumed >= len(checked) * 4096
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", checked_mkdir)
    result = worker.tick(execute=True, request=request)
    assert result["tick_outcomes"][0]["independent_enumeration"] is True
    assert len(checked) >= 3 and calls
    assert not ledger.snapshot()["operations"]


def test_nonbudget_capture_keeps_legacy_directory_behavior(setup):
    worker, replies, calls, _ = setup
    target = worker.workspace / "nested" / "attempt"
    capture(worker, None, target, None)
    assert (target / "http").is_dir() and (worker.shared_policy.root / "hosts").is_dir()
    capture(worker, None, target, None)
    assert not calls
