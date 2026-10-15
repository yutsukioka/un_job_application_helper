"""Synthetic dispatcher incidents; subprocess boundaries never contact providers."""

import hashlib
import importlib
import importlib.util
import json
from dataclasses import replace
from pathlib import Path

import pytest
from test_storage_cycle import config_fixture

from jobagg import dispatcher_storage, pending_publication, storage_cycle
from jobagg.storage_budget import PHYSICAL_RESERVE_BYTES

ROOT = Path(__file__).resolve().parents[3]
SPEC = importlib.util.spec_from_file_location("budgeted_dispatcher", ROOT / "reports/jobagg-remediation-plan-2026-09-10/proposed_runner/runner.py")
runner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runner)


@pytest.fixture
def configured(tmp_path, monkeypatch):
    config = config_fixture(tmp_path)
    config.update(timeout_seconds=5, total_timeout_seconds=15, publication_timeout_seconds=5,
                  terminate_grace_seconds=0.1, listing_max_age_seconds=3600,
                  detail_max_age_seconds=86400, sealed_publication_snapshots=True,
                  publication_snapshot_mode="projection", concurrency=None,
                  storage_retention={"enabled": False},
                  publication_policy={"policy_epoch": "fixture", "max_history_bytes": 1_000_000,
                                      "export_storage": "record_chunks_v1"})
    config["storage_cycle"]["control_cap_bytes"] = 10_000_000
    config["storage_cycle"]["database_growth_cap_bytes"] = 1_000_000
    config["worker_argv"] += ["--request", "{request_path}", "--report", "{report_path}"]
    config["publication_argv"] += ["--request", "{publication_request_path}", "--report", "{publication_report_path}"]
    config["_registry_path"].write_text('{}')
    config["_registry_sha256"] = hashlib.sha256(b'{}').hexdigest()
    config["source_manifest_path"].write_text(json.dumps({"sources": [{"source_id": "fixture", "enabled": True}]}))
    config["_manifest_sha256"] = runner.digest(config["source_manifest_path"])
    config["_config_path"].write_text(json.dumps(config, default=str))
    config["_config_sha256"] = runner.digest(config["_config_path"])
    monkeypatch.setattr(runner, "package_helper", lambda _c, name: importlib.import_module("jobagg." + name))
    monkeypatch.setattr(runner, "former_processes_absent", lambda _r: {
        "checked_at": runner.now().isoformat(), "ps_sha256": "1" * 64,
        "former_pids_groups_and_commands_absent": True})
    monkeypatch.setattr(runner, "group_alive", lambda _pid: False)
    monkeypatch.setattr(runner, "terminate_group", lambda *_: {"term_sent": False, "kill_sent": False})
    monkeypatch.setattr(runner, "validate_report", lambda *_: ("incomplete", ["synthetic bounded batch"]))
    monkeypatch.setattr(pending_publication, "database_binding", lambda _p: {
        "files": [], "semantic": {"writer_fenced": True, "revision": 1}})
    monkeypatch.setattr(dispatcher_storage, "wait_logged", lambda process, *_: process.wait())
    calls = []

    class Child:
        pid = 999999999
        returncode = 0
        stdout = stderr = None

        def __init__(self, argv, **_kwargs):
            self.args = argv
            request = json.loads(Path(argv[argv.index("--request") + 1]).read_text())
            report = Path(argv[argv.index("--report") + 1])
            calls.append(request)
            if "worker_acceptance_sha256" in request:
                receipt = {**request, "status": "published", "generated_at": runner.now().isoformat(),
                           "observation_set_sha256": "2" * 64, "whole_job_completeness_certified": False}
            else:
                receipt = {"run_id": request["run_id"], "database": str(config["publication_worker_database"])}
            report.write_text(json.dumps(receipt))

        def wait(self, **_kwargs):
            return 0

        def poll(self):
            return 0

    monkeypatch.setattr(runner.subprocess, "Popen", Child)
    return config, calls


def test_incident_peak_is_denied_before_worker_or_run_directory(configured, monkeypatch):
    config, calls = configured
    device = config["publication_worker_database"].stat().st_dev
    # Incident's reserve margin cannot admit its 744,759,296-byte projection.
    monkeypatch.setattr(storage_cycle, "_free_bytes", lambda _: {device: PHYSICAL_RESERVE_BYTES + 467_034_112})
    monkeypatch.setattr(storage_cycle, "_basis", lambda *_a, **_k: {
        "worker_db_bytes": 744_759_296, "output_db_bytes": 0, "export_bytes": 0})
    result = runner.tick(config, ["fixture"])
    assert result["status"] == "storage_deferred"
    assert not calls
    assert not (config["state_dir"] / "runs").exists()
    assert not Path(config["storage_budget"]["ledger_path"]).exists()


def test_postacceptance_deferral_keeps_exact_batch_without_intent(configured, monkeypatch):
    config, calls = configured
    original = storage_cycle.recheck_cycle

    def deny_projection(config, context, phase):
        decision = original(config, context, phase)
        return replace(decision, ready=False, reasons=("physical_reserve_insufficient:fixture",)) if phase == "accepted_pending" else decision

    monkeypatch.setattr(storage_cycle, "recheck_cycle", deny_projection)
    result = runner.tick(config, ["fixture"])
    assert result["status"] == "accepted_work_pending"
    assert len(calls) == 1
    marker = pending_publication.inspect(config["state_dir"])
    run = config["state_dir"] / "runs" / marker["run_id"]
    assert (run / "worker_result.json").exists()
    assert not (run / "publication_intent.json").exists()
    original_acceptance = (run / "acceptance.json").read_bytes()
    original_request = (run / "request.json").read_bytes()
    # A later invocation under the same low-space condition must not refetch.
    again = runner.tick(config, ["fixture"])
    assert again["status"] == "accepted_work_pending"
    assert len(calls) == 1
    assert (run / "acceptance.json").read_bytes() == original_acceptance
    assert (run / "request.json").read_bytes() == original_request


def test_controller_write_rejects_unregistered_path_before_creation(configured, tmp_path):
    config, _ = configured
    context = storage_cycle.prepare_cycle(config, "control-cycle")
    outside = tmp_path / "outside.json"
    with dispatcher_storage.controller_scope(context["ledger"]), pytest.raises(ValueError):
        dispatcher_storage.write_json(context["ledger"], outside, {"cannot": "escape"})
    assert not outside.exists()


def test_controller_temp_and_payload_are_charged_before_write(configured):
    config, _ = configured
    context = storage_cycle.prepare_cycle(config, "control-cycle")
    path = config["state_dir"] / "bounded.json"
    before = context["ledger"].snapshot()["settled"]["diagnostics"]
    with dispatcher_storage.controller_scope(context["ledger"]):
        dispatcher_storage.write_json(context["ledger"], path, {"bounded": "payload"})
    after = context["ledger"].snapshot()["settled"]["diagnostics"]
    assert after - before >= path.stat().st_size + 16384
    assert not context["ledger"].snapshot()["operations"]


def test_space_recovery_resumes_original_acceptance_without_worker(configured, monkeypatch):
    from types import SimpleNamespace
    config, calls = configured
    original = storage_cycle.recheck_cycle

    def deny_projection(config, context, phase):
        decision = original(config, context, phase)
        return replace(decision, ready=False, reasons=("fixture_space_loss",)) if phase == "accepted_pending" else decision

    monkeypatch.setattr(storage_cycle, "recheck_cycle", deny_projection)
    first = runner.tick(config, ["fixture"])
    assert first["status"] == "accepted_work_pending"
    run = Path(first["run_dir"])
    acceptance = (run / "acceptance.json").read_bytes()
    deferral = (run / "outcome.json").read_bytes()

    def snapshot(source, target, acceptance_sha, *_args, **_kwargs):
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"sealed bounded synthetic projection")
        return {"path": str(target), "sha256": runner.digest(target),
                "source_database": str(source), "worker_acceptance_sha256": acceptance_sha}

    monkeypatch.setattr(storage_cycle, "recheck_cycle", original)
    monkeypatch.setattr(runner, "snapshot_helper", lambda _c: SimpleNamespace(create_publication_snapshot=snapshot))
    resumed = runner.tick(config, ["fixture"])
    assert resumed["publication_status"] == "published", resumed
    assert resumed["run_id"] == first["run_id"]
    assert resumed["accepted_work_resumed"] is True
    assert len(calls) == 2  # One worker, then one publisher after the space hold.
    assert "worker_acceptance_sha256" in calls[1]
    assert (run / "acceptance.json").read_bytes() == acceptance
    assert not pending_publication.marker_path(config["state_dir"]).exists()
    assert any(p.read_bytes() == deferral for p in (run / "accepted_work_deferrals").rglob("*.json"))
    assert storage_cycle.existing_context(config) is None


def test_interrupted_final_ledger_close_reconciles_without_replay(configured, monkeypatch):
    from types import SimpleNamespace

    from jobagg.storage_budget import BudgetLedger, StorageBudgetDeferred

    config, calls = configured

    def snapshot(source, target, acceptance_sha, *_args, **_kwargs):
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"sealed completed projection")
        return {"path": str(target), "sha256": runner.digest(target),
                "source_database": str(source), "worker_acceptance_sha256": acceptance_sha}

    monkeypatch.setattr(runner, "snapshot_helper", lambda _c: SimpleNamespace(create_publication_snapshot=snapshot))
    transition = BudgetLedger.transition

    def interrupted_close(self, old, new):
        if (old, new) == ("publication_admitted", "completed"):
            raise StorageBudgetDeferred("injected interruption before ledger close")
        return transition(self, old, new)

    monkeypatch.setattr(BudgetLedger, "transition", interrupted_close)
    first = runner.tick(config, ["fixture"])
    assert first["status"] == "storage_deferred"
    assert len(calls) == 2
    context = storage_cycle.existing_context(config)
    assert context["ledger"].snapshot()["phase"] == "publication_admitted"
    monkeypatch.setattr(BudgetLedger, "transition", transition)
    result = runner.tick(config, ["fixture"])
    assert result["storage_cycle_reconciled"] is True
    assert result["publication_status"] == "published"
    assert result["worker_reexecuted"] is False
    assert result["publisher_reexecuted"] is False
    assert len(calls) == 2
    assert storage_cycle.existing_context(config) is None


def test_log_budget_deferral_holds_unknown_worker_without_replay(configured, monkeypatch):
    from jobagg.storage_budget import StorageBudgetDeferred

    config, calls = configured
    wait_logged = dispatcher_storage.wait_logged

    def exhausted_log(*_args):
        raise StorageBudgetDeferred("operation_envelope_exhausted:worker_log")

    monkeypatch.setattr(dispatcher_storage, "wait_logged", exhausted_log)
    first = runner.tick(config, ["fixture"])
    assert first["status"] == "storage_deferred", first
    assert first["storage_deferral"] is True
    assert first["review_required"] is True
    assert first["worker_outcome_unknown"] is True
    assert first["publication_status"] == "not_started"
    assert len(calls) == 1
    run = Path(first["run_dir"])
    acceptance = (run / "acceptance.json").read_bytes()
    assert not (run / "worker_result.json").exists()
    assert pending_publication.inspect(config["state_dir"]) is None

    # Even an acceptance file and subsequently available storage cannot prove
    # that this child exited successfully before logging was interrupted.
    monkeypatch.setattr(dispatcher_storage, "wait_logged", wait_logged)
    for _ in range(2):
        held = runner.tick(config, ["fixture"])
        assert held["status"] == "storage_deferred", held
        assert held["review_required"] is True
        assert held["worker_outcome_unknown"] is True
        assert held["worker_dispatched"] is False
        assert held["publisher_dispatched"] is False
    assert len(calls) == 1
    assert (run / "acceptance.json").read_bytes() == acceptance
    assert not (run / "publication_intent.json").exists()


def test_log_and_terminal_journal_exhaustion_does_not_claim_no_dispatch(configured, monkeypatch):
    from jobagg.storage_budget import StorageBudgetDeferred

    config, calls = configured
    wait_logged, immutable_json = dispatcher_storage.wait_logged, runner.immutable_json

    def exhausted_log(*_args):
        raise StorageBudgetDeferred("operation_envelope_exhausted:worker_log")

    def exhausted_terminal(path, value):
        if path.name == "outcome.json":
            raise StorageBudgetDeferred("operation_envelope_exhausted:terminal_journal")
        return immutable_json(path, value)

    monkeypatch.setattr(dispatcher_storage, "wait_logged", exhausted_log)
    monkeypatch.setattr(runner, "immutable_json", exhausted_terminal)
    first = runner.tick(config, ["fixture"])
    assert first["status"] == "storage_deferred", first
    assert first["dispatch_attempted"] is True
    assert first["review_required"] is True
    assert first["worker_outcome_unknown"] is True
    assert "worker_dispatched" not in first
    assert len(calls) == 1
    monkeypatch.setattr(dispatcher_storage, "wait_logged", wait_logged)
    monkeypatch.setattr(runner, "immutable_json", immutable_json)
    held = runner.tick(config, ["fixture"])
    assert held["review_required"] is True
    assert held["worker_outcome_unknown"] is True
    assert len(calls) == 1


def test_publisher_log_deferral_retains_intent_without_automatic_replay(configured, monkeypatch):
    from types import SimpleNamespace

    from jobagg.storage_budget import StorageBudgetDeferred

    config, calls = configured
    wait_logged = dispatcher_storage.wait_logged

    def publisher_log_full(process, *args):
        if len(calls) == 2:
            raise StorageBudgetDeferred("operation_envelope_exhausted:publisher_log")
        return wait_logged(process, *args)

    def snapshot(source, target, acceptance_sha, *_args, **_kwargs):
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"sealed publisher logging projection")
        return {"path": str(target), "sha256": runner.digest(target),
                "source_database": str(source), "worker_acceptance_sha256": acceptance_sha}

    monkeypatch.setattr(dispatcher_storage, "wait_logged", publisher_log_full)
    monkeypatch.setattr(runner, "snapshot_helper", lambda _c: SimpleNamespace(create_publication_snapshot=snapshot))
    first = runner.tick(config, ["fixture"])
    assert first["status"] == "storage_deferred", first
    assert first["publication_status"] == "unknown_requires_review_no_replay"
    assert first["publication_recovery_allowed"] is True
    assert len(calls) == 2
    run = Path(first["run_dir"])
    intent = (run / "publication_intent.json").read_bytes()
    monkeypatch.setattr(dispatcher_storage, "wait_logged", wait_logged)
    held = runner.tick(config, ["fixture"])
    assert held["status"] == "publication_held", held
    assert len(calls) == 2
    assert (run / "publication_intent.json").read_bytes() == intent


@pytest.fixture
def interrupted_pending_seal(configured, monkeypatch):
    from jobagg.storage_budget import StorageBudgetDeferred

    config, calls = configured
    seal = pending_publication.seal

    def interrupted_seal(*_args, **_kwargs):
        raise StorageBudgetDeferred("operation_envelope_exhausted:pending_seal")

    monkeypatch.setattr(pending_publication, "seal", interrupted_seal)
    first = runner.tick(config, ["fixture"])
    assert first["status"] == "accepted_work_pending", first
    assert first["storage_deferral"] is True
    assert len(calls) == 1
    assert pending_publication.inspect(config["state_dir"]) is None
    run = Path(first["run_dir"])
    assert (run / "worker_result.json").exists()
    assert not (run / "publication_intent.json").exists()
    monkeypatch.setattr(pending_publication, "seal", seal)
    return config, calls, first, run


def test_terminal_worker_proof_recovers_pending_seal_without_refetch(interrupted_pending_seal, monkeypatch):
    from types import SimpleNamespace

    config, calls, first, run = interrupted_pending_seal
    original = {name: (run / name).read_bytes()
                for name in ("request.json", "acceptance.json", "worker_result.json")}
    absence_checks = []
    absent = runner.former_processes_absent

    def prove_absent(record):
        assert len(calls) == 1
        absence_checks.append(record["run_id"])
        return absent(record)

    def snapshot(source, target, acceptance_sha, *_args, **_kwargs):
        assert absence_checks
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"sealed recovered worker projection")
        return {"path": str(target), "sha256": runner.digest(target),
                "source_database": str(source), "worker_acceptance_sha256": acceptance_sha}

    monkeypatch.setattr(runner, "former_processes_absent", prove_absent)
    monkeypatch.setattr(runner, "snapshot_helper", lambda _c: SimpleNamespace(create_publication_snapshot=snapshot))
    resumed = runner.tick(config, ["fixture"])
    assert resumed["publication_status"] == "published", resumed
    assert resumed["run_id"] == first["run_id"]
    assert resumed["accepted_work_resumed"] is True
    assert absence_checks == [first["run_id"], first["run_id"]]
    assert len(calls) == 2
    assert "worker_acceptance_sha256" in calls[1]
    for name, payload in original.items():
        assert (run / name).read_bytes() == payload
    assert storage_cycle.existing_context(config) is None


@pytest.mark.parametrize("invalid", ["acceptance", "request", "configuration", "worker_exit",
                                      "database", "missing_state", "missing_process", "live_process"])
def test_terminal_worker_recovery_holds_inexact_or_live_evidence(interrupted_pending_seal, monkeypatch, invalid):
    config, calls, _first, run = interrupted_pending_seal
    if invalid in {"acceptance", "request"}:
        path = run / (invalid + ".json")
        path.write_text(path.read_text() + "\n")
    elif invalid in {"configuration", "worker_exit"}:
        path = run / "worker_result.json"
        proof = json.loads(path.read_text())
        proof["config_sha256" if invalid == "configuration" else "worker_exit_code"] = (
            "a" * 64 if invalid == "configuration" else 1)
        path.write_text(json.dumps(proof))
    elif invalid == "database":
        monkeypatch.setattr(pending_publication, "database_binding", lambda _p: {
            "files": [], "semantic": {"writer_fenced": True, "revision": 2}})
    elif invalid == "missing_state":
        (run / "state.json").unlink()
    elif invalid == "missing_process":
        path = run / "state.json"
        state = json.loads(path.read_text())
        state.pop("worker_pid")
        path.write_text(json.dumps(state))
    else:
        def still_running(_record):
            raise runner.InvalidReport("former worker PID is still present")

        monkeypatch.setattr(runner, "former_processes_absent", still_running)

    held = runner.tick(config, ["fixture"])
    assert held["status"] == "storage_deferred", held
    assert held["review_required"] is True
    assert held["worker_dispatched"] is False
    assert held["publisher_dispatched"] is False
    assert len(calls) == 1
    assert pending_publication.inspect(config["state_dir"]) is None
    assert not (run / "publication_intent.json").exists()
