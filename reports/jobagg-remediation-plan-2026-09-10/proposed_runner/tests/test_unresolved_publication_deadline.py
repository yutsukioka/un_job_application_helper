"""Synthetic controls prove one closure per scan and fail-closed deadlines."""

import hashlib
import importlib.util
import io
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_runner import RUNNER


@pytest.fixture
def runner():
    spec = importlib.util.spec_from_file_location("deadline_runner", RUNNER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))
    return hashlib.sha256(path.read_bytes()).hexdigest()


def make_run(root, name, *, retired=False, status="incomplete"):
    run = root / name
    write(run / "outcome.json", {"run_id": name, "publication_status": status})
    if retired:
        write(run / "terminal-history-retirement.json", {})
    return run


def no_write_resolution(root):
    run = make_run(root, "run", status="unknown_requires_review_no_replay")
    outcome_hash = hashlib.sha256((run / "outcome.json").read_bytes()).hexdigest()
    request_hash = write(run / "publication_request.json", {
        "generated_at": "2026-09-17T05:30:00+00:00"})
    intent_hash = write(run / "publication_intent.json", {"run_id": "run"})
    gate = {"state": "complete", "completed_at": "2026-09-17T05:11:00+00:00"}
    plan = run / "plan.json"
    plan_hash = write(plan, {"payload": "synthetic"})
    result = run / "result.json"
    result_hash = write(result, gate)
    write(run / "publication_no_write_resolution.json", {
        "run_id": "run", "previous_outcome_sha256": outcome_hash,
        "original_request_sha256": request_hash, "original_intent_sha256": intent_hash,
        "proof": {
            "kind": "verified_unstarted_publication", "generation_not_started": True,
            "prior_gate": gate,
            "prior_plan": {"path": str(plan), "sha256": plan_hash},
            "prior_result": {"path": str(result), "sha256": result_hash},
        },
    })
    return plan


def test_retired_receipts_share_one_lazy_closure_and_deadline(runner, tmp_path, monkeypatch):
    root = tmp_path / "jobagg" / "deterministic-dispatcher-proposal" / "runs"
    retired = [make_run(root, name, retired=True) for name in ("one", "two", "three")]
    unresolved = make_run(root, "unresolved")
    calls = []
    closure = {"runs": [], "generations": []}
    config = {"selected": "fixture"}
    deadline = runner.time.monotonic() + 30

    def build(actual_root, **kwargs):
        calls.append(("closure", actual_root, kwargs))
        return closure

    def classify(subject, **kwargs):
        assert kwargs["closure"] is closure
        assert kwargs["root"] == root.parent.parent
        assert kwargs["deadline_at"] == deadline
        calls.append(("classify", subject))
        return {"disposition": "retired_terminal"}

    def helper(actual_config, name):
        assert actual_config is config and name == "terminal_history"
        calls.append(("helper",))
        return SimpleNamespace(operational_closure=build, classify_history=classify)

    monkeypatch.setattr(runner, "package_helper", helper)
    assert runner.unresolved_publications(root, config, deadline_at=deadline) == [unresolved / "outcome.json"]
    assert sum(call[0] == "helper" for call in calls) == 1
    assert [call for call in calls if call[0] == "closure"] == [
        ("closure", root.parent.parent, {"deadline_at": deadline})]
    assert {call[1] for call in calls if call[0] == "classify"} == set(retired)


@pytest.mark.parametrize("configured", [False, True])
def test_ordinary_scan_does_not_load_history_helper(runner, tmp_path, monkeypatch, configured):
    pending = make_run(tmp_path, "pending")
    make_run(tmp_path, "published", status="published")
    monkeypatch.setattr(runner, "package_helper", lambda *_: pytest.fail("unneeded closure"))
    args = ({"fixture": True},) if configured else ()
    assert runner.unresolved_publications(tmp_path, *args) == [pending / "outcome.json"]


def test_retired_history_without_config_still_fails_closed(runner, tmp_path):
    make_run(tmp_path, "retired", retired=True)
    with pytest.raises(runner.InvalidReport, match="canonical classifier"):
        runner.unresolved_publications(tmp_path)


def test_expired_empty_scan_raises_instead_of_returning_clear(runner, tmp_path):
    with pytest.raises(TimeoutError, match="deadline"):
        runner.unresolved_publications(tmp_path, deadline_at=runner.time.monotonic() - 1)


def test_closure_timeout_is_not_treated_as_retired(runner, tmp_path, monkeypatch):
    make_run(tmp_path, "retired", retired=True)

    def closure(*_args, **kwargs):
        assert kwargs["deadline_at"] == 100
        raise TimeoutError("closure exhausted")

    monkeypatch.setattr(runner.time, "monotonic", lambda: 0)
    monkeypatch.setattr(runner, "package_helper", lambda *_: SimpleNamespace(
        operational_closure=closure,
        classify_history=lambda *_args, **_kwargs: pytest.fail("classification after timeout")))
    with pytest.raises(TimeoutError, match="closure exhausted"):
        runner.unresolved_publications(tmp_path, {}, deadline_at=100)


def test_deadline_rechecked_after_receipt_classification(runner, tmp_path, monkeypatch):
    make_run(tmp_path, "retired", retired=True)
    clock = [0]

    def classify(*_args, **kwargs):
        assert kwargs["deadline_at"] == 100
        clock[0] = 100
        return {"disposition": "retired_terminal"}

    monkeypatch.setattr(runner.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(runner, "package_helper", lambda *_: SimpleNamespace(
        operational_closure=lambda *_args, **_kwargs: {"runs": [], "generations": []},
        classify_history=classify))
    with pytest.raises(TimeoutError, match="deadline"):
        runner.unresolved_publications(tmp_path, {}, deadline_at=100)


def test_control_read_timeout_never_returns_partial_scan(runner, tmp_path, monkeypatch):
    make_run(tmp_path, "pending")
    original_read = runner.read_json
    clock = [0]

    def read(path):
        result = original_read(path)
        clock[0] = 100
        return result

    monkeypatch.setattr(runner.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(runner, "read_json", read)
    with pytest.raises(TimeoutError, match="deadline"):
        runner.unresolved_publications(tmp_path, deadline_at=100)


def test_no_write_resolution_preserved_with_deadline(runner, tmp_path):
    no_write_resolution(tmp_path)
    assert runner.unresolved_publications(
        tmp_path, deadline_at=runner.time.monotonic() + 30) == []


def test_prior_plan_hash_checks_deadline_between_chunks(runner, tmp_path, monkeypatch):
    plan = no_write_resolution(tmp_path)
    original_open = Path.open
    clock = [0]
    reads = []

    class InterruptedPlan(io.BytesIO):
        def read(self, count=-1):
            reads.append(count)
            result = super().read(count)
            clock[0] = 100
            return result

    def opening(path, *args, **kwargs):
        if path == plan and args == ("rb",):
            return InterruptedPlan(b"x" * (2 * 1024 * 1024))
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(runner.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(Path, "open", opening)
    with pytest.raises(TimeoutError, match="deadline"):
        runner.unresolved_publications(tmp_path, deadline_at=100)
    assert reads == [1024 * 1024]
