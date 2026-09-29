"""Maintenance code must not invalidate the fetching implementation binding."""

import pytest

from jobagg import remediation_worker as worker_module
from test_remediation_worker import setup as worker_setup  # noqa: F401


@pytest.fixture
def synthetic_package(tmp_path, monkeypatch):
    root = tmp_path / "synthetic_jobagg"
    for name in (
        "remediation_worker.py",
        "adapters/example.py",
        "pipelines/worker_policy.py",
    ):
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# original fetching implementation\n")
    monkeypatch.setattr(worker_module, "__file__", str(root / "remediation_worker.py"))
    return root


@pytest.fixture
def bound_worker(synthetic_package, request):
    # Build the worker after switching to the synthetic implementation tree.
    return request.getfixturevalue("worker_setup")


@pytest.mark.parametrize("name", ["storage_retention.py", "storage_cold_archive.py"])
def test_exact_maintenance_module_addition_edit_and_removal_preserve_binding(
    synthetic_package, name
):
    before = worker_module.implementation_hash()
    path = synthetic_package / name
    path.write_text("# first maintenance implementation\n")
    assert worker_module.implementation_hash() == before
    path.write_text("# revised maintenance implementation\n")
    assert worker_module.implementation_hash() == before
    path.unlink()
    assert worker_module.implementation_hash() == before


@pytest.mark.parametrize(
    "name",
    [
        "remediation_worker.py",
        "adapters/example.py",
        "pipelines/worker_policy.py",
        "new_worker_module.py",
        "pipelines/storage_retention.py",
        "adapters/storage_cold_archive.py",
    ],
)
def test_fetching_existing_new_and_nested_modules_remain_bound(synthetic_package, name):
    path = synthetic_package / name
    original = path.read_bytes() if path.exists() else None
    before = worker_module.implementation_hash()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("# changed fetching implementation\n")
    changed = worker_module.implementation_hash()
    assert changed != before
    path.write_text("# another fetching implementation\n")
    assert worker_module.implementation_hash() not in {before, changed}
    path.unlink()
    if original is None:
        assert worker_module.implementation_hash() == before
    else:
        assert worker_module.implementation_hash() != before
        path.write_bytes(original)
        assert worker_module.implementation_hash() == before


def file_hashes(root):
    return {
        str(path.relative_to(root)): worker_module.sha(path)
        for path in root.rglob("*")
        if path.is_file()
    }


def test_startup_refuses_changed_fetching_code_before_network_or_writes(
    bound_worker, synthetic_package
):
    worker, _, calls, _ = bound_worker
    worker.initialize()
    workspace_before = file_hashes(worker.workspace)
    policy_before = file_hashes(worker.shared_policy.root)
    (synthetic_package / "adapters/example.py").write_text("# unreviewed adapter change\n")
    changed_worker = worker_module.Worker(
        registry=worker.registry,
        robots=worker.robots_path,
        workspace=worker.workspace,
        shared_lock=worker.shared_lock,
        client_factory=worker.client_factory,
    )

    with pytest.raises(ValueError, match="code/config drift"):
        changed_worker.tick(execute=True)

    assert calls == []
    assert file_hashes(worker.workspace) == workspace_before
    assert file_hashes(worker.shared_policy.root) == policy_before


@pytest.mark.parametrize(
    ("name", "should_accept"),
    [
        ("adapters/example.py", False),
        ("storage_retention.py", True),
        ("storage_cold_archive.py", True),
    ],
)
def test_mid_tick_only_maintenance_changes_can_complete_acceptance(
    bound_worker, synthetic_package, monkeypatch, name, should_accept
):
    worker, _, calls, _ = bound_worker
    perform = worker.perform

    def perform_then_change_code(task, deadline):
        outcome = perform(task, deadline)
        (synthetic_package / name).write_text("# modified during the fetch tick\n")
        return outcome

    monkeypatch.setattr(worker, "perform", perform_then_change_code)
    if should_accept:
        report = worker.tick(execute=True)
        assert report["binding"] == worker.binding
        assert (worker.workspace / "ticks" / f"{worker.tick_id}.json").is_file()
    else:
        with pytest.raises(ValueError, match="Input code/config changed during tick"):
            worker.tick(execute=True)
        assert not (worker.workspace / "ticks" / f"{worker.tick_id}.json").exists()
    assert calls, "The implementation change must happen after actual fixture fetching"
