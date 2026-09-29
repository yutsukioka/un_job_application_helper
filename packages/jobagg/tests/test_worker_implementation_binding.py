"""All package code changes require a reviewed worker binding update."""

import json

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
        path.write_text("# original implementation\n")
    monkeypatch.setattr(worker_module, "__file__", str(root / "remediation_worker.py"))
    return root


@pytest.fixture
def bound_worker(synthetic_package, request):
    return request.getfixturevalue("worker_setup")


@pytest.mark.parametrize(
    "name",
    [
        "remediation_worker.py",
        "adapters/example.py",
        "pipelines/worker_policy.py",
        "storage_retention.py",
        "storage_cold_archive.py",
        "new_module.py",
        "pipelines/storage_retention.py",
    ],
)
def test_every_package_python_file_is_bound(synthetic_package, name):
    path = synthetic_package / name
    old = path.read_bytes() if path.exists() else None
    before = worker_module.implementation_hash()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("# changed implementation\n")
    changed = worker_module.implementation_hash()
    assert changed != before
    path.write_text("# another revision\n")
    assert worker_module.implementation_hash() not in {before, changed}
    path.unlink()
    if old is None:
        assert worker_module.implementation_hash() == before
    else:
        assert worker_module.implementation_hash() != before
        path.write_bytes(old)
        assert worker_module.implementation_hash() == before


def file_hashes(root):
    return {
        str(path.relative_to(root)): worker_module.sha(path)
        for path in root.rglob("*")
        if path.is_file()
    }


@pytest.mark.parametrize("name", ["adapters/example.py", "storage_retention.py"])
def test_unreviewed_startup_change_refuses_before_network_or_writes(
    bound_worker, synthetic_package, name
):
    worker, _, calls, _ = bound_worker
    worker.initialize()
    workspace_before = file_hashes(worker.workspace)
    policy_before = file_hashes(worker.shared_policy.root)
    (synthetic_package / name).write_text("# changed after binding\n")
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


@pytest.mark.parametrize("name", ["adapters/example.py", "storage_retention.py"])
def test_any_mid_tick_code_change_refuses_acceptance(
    bound_worker, synthetic_package, monkeypatch, name
):
    worker, _, calls, _ = bound_worker
    perform = worker.perform

    def perform_then_change_code(task, deadline):
        outcome = perform(task, deadline)
        (synthetic_package / name).write_text("# modified during fetch\n")
        return outcome

    monkeypatch.setattr(worker, "perform", perform_then_change_code)
    with pytest.raises(ValueError, match="Input code/config changed during tick"):
        worker.tick(execute=True)
    assert calls, "The implementation must change after actual fixture fetching"
    assert not (worker.workspace / "ticks" / f"{worker.tick_id}.json").exists()


def test_reviewed_marker_update_accepts_exact_new_fingerprint(
    bound_worker, synthetic_package
):
    worker, _, calls, _ = bound_worker
    worker.initialize()
    marker = worker.workspace / "worker_workspace.json"
    old = json.loads(marker.read_text())
    (synthetic_package / "storage_retention.py").write_text("# approved maintenance code\n")
    revised = worker_module.Worker(
        registry=worker.registry,
        robots=worker.robots_path,
        workspace=worker.workspace,
        shared_lock=worker.shared_lock,
        client_factory=worker.client_factory,
    )
    with pytest.raises(ValueError, match="code/config drift"):
        revised.preview()
    assert old["implementation_sha256"] != revised.binding["implementation_sha256"]
    assert {k: v for k, v in revised.binding.items() if k != "implementation_sha256"} == {
        k: v for k, v in old.items() if k != "implementation_sha256"
    }
    with worker_module.shared_owner(worker.shared_lock):
        worker_module.write_json(marker, revised.binding)
        assert revised.preview()["binding"] == revised.binding
    assert calls == []
