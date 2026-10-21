"""Dispatcher admission proofs do not fetch providers or open databases."""

import json
from pathlib import Path

import pytest
from test_storage_budget import fixture_config

from jobagg import storage_cycle
from jobagg.storage_budget import (
    CATEGORY_LIMITS,
    CONTROL_ENVELOPE_BYTES,
    PHYSICAL_RESERVE_BYTES,
    StorageBudgetDeferred,
    StorageBudgetError,
)


def config_fixture(tmp_path):
    budget = fixture_config(tmp_path)
    essential, diagnostics, work = (tmp_path / name for name in ("essential", "diagnostics", "work"))
    package = essential / "package"
    output = essential / "output"
    scratch = work / "scratch"
    for directory in (package, output, scratch, diagnostics / "dispatcher", diagnostics / "attempts"):
        directory.mkdir()
    database = essential / "jobs.sqlite3"
    database.write_bytes(b"bounded worker DB fixture, deliberately not SQLite")
    (output / "all_jobs.sqlite3").write_bytes(b"bounded output fixture")
    (output / "all_jobs.json").write_bytes(b"[]")
    runtime = package / "python"
    runtime.write_bytes(b"runtime identity fixture, never executed")
    config = {
        "_config_path": essential / "config.json", "_config_sha256": "1" * 64,
        "source_manifest_path": essential / "manifest.json", "_registry_path": essential / "registry.json",
        "state_dir": diagnostics / "dispatcher", "attempt_state_dir": diagnostics / "attempts",
        "shared_lock_path": diagnostics / "owner.lock", "publication_worker_database": database,
        "worker_cwd": package, "publication_cwd": package,
        "worker_argv": [str(runtime), "-m", "jobagg.remediation_worker", "--workspace", str(essential)],
        "publication_argv": [str(runtime), "-m", "jobagg.publish_worker", "--output-dir", str(output)],
        "storage_budget": budget.to_dict(),
        "storage_cycle": {"ingress_cap_bytes": 100_000, "accepted_cap_bytes": 100_000,
                          "database_growth_cap_bytes": 128_000, "control_cap_bytes": CONTROL_ENVELOPE_BYTES * 3,
                          "scratch_dir": str(scratch), "support_paths": [str(package), str(scratch)],
                          "publisher_write_limits": dict.fromkeys(CATEGORY_LIMITS, 1_000_000),
                          "maintenance_growth": {**dict.fromkeys(CATEGORY_LIMITS, 0),
                                                 "diagnostics": CONTROL_ENVELOPE_BYTES}},
    }
    for key in ("_config_path", "source_manifest_path", "_registry_path", "shared_lock_path"):
        config[key].write_text("{}")
    return config


def test_disabled_budget_preserves_legacy_no_ledger_path():
    assert storage_cycle.prepare_cycle({}, "legacy") is None
    assert storage_cycle.prepare_maintenance({}, "legacy") is None
    assert storage_cycle.child_environment(None, "worker") == {}


@pytest.mark.parametrize("caps", [None, {}, {"publication": 100},
                                  {**dict.fromkeys(CATEGORY_LIMITS, 1), "publication": float("inf")},
                                  {**dict.fromkeys(CATEGORY_LIMITS, 1), "publication": True}])
def test_publisher_requires_finite_complete_caps_before_any_admission(tmp_path, monkeypatch, caps):
    config = config_fixture(tmp_path)
    if caps is None:
        del config["storage_cycle"]["publisher_write_limits"]
    else:
        config["storage_cycle"]["publisher_write_limits"] = caps
    monkeypatch.setattr(storage_cycle, "inventory", lambda *args, **kwargs: pytest.fail("must reject before inventory"))
    for prepare in (storage_cycle.prepare_cycle, storage_cycle.prepare_maintenance):
        with pytest.raises(StorageBudgetError, match="publisher envelope"):
            prepare(config, "denied")
    assert not Path(config["storage_budget"]["ledger_path"]).exists()


@pytest.mark.parametrize("key", ["worker_argv", "publication_argv"])
@pytest.mark.parametrize("variant", ["script", "unknown_module", "command", "extra_module", "extra_command",
                                    "compact_module", "compact_command", "interpreter_option", "separator", "duplicate_flag"])
def test_unknown_or_ambiguous_writer_is_denied_before_admission(tmp_path, monkeypatch, key, variant):
    config = config_fixture(tmp_path)
    executable, _, module, *arguments = config[key]
    commands = {
        "script": [executable, "arbitrary.py", *arguments],
        "unknown_module": [executable, "-m", "jobagg.pipelines.live_publication", *arguments],
        "command": [executable, "-c", "pass", "-m", module, *arguments],
        "extra_module": [executable, "-m", module, "-m", "unknown", *arguments],
        "extra_command": [executable, "-m", module, "-c", "pass", *arguments],
        "compact_module": [executable, "-m", module, "-munknown", *arguments],
        "compact_command": [executable, "-m", module, "-cpass", *arguments],
        "interpreter_option": [executable, "-X", "unknown", "-m", module, *arguments],
        "separator": [executable, "--", "-m", module, *arguments],
        "duplicate_flag": [executable, "-B", "-B", "-m", module, *arguments],
    }
    config[key] = commands[variant]
    monkeypatch.setattr(storage_cycle, "inventory", lambda *args, **kwargs: pytest.fail("must reject before inventory"))
    for prepare in (storage_cycle.prepare_cycle, storage_cycle.prepare_maintenance):
        with pytest.raises(StorageBudgetError, match=key):
            prepare(config, "denied")
    assert not Path(config["storage_budget"]["ledger_path"]).exists()


def test_supported_entrypoints_allow_only_explicit_safe_interpreter_flags(tmp_path):
    config = config_fixture(tmp_path)
    for key in ("worker_argv", "publication_argv"):
        config[key][1:1] = ["-B", "-u"]
    assert storage_cycle.prepare_cycle(config, "bounded")["admission"].ready


def test_known_too_small_projection_cap_is_denied_before_worker_admission(tmp_path, monkeypatch):
    config = config_fixture(tmp_path)
    config["storage_cycle"]["database_growth_cap_bytes"] = 1
    monkeypatch.setattr(storage_cycle, "inventory", lambda *a, **k: pytest.fail("unsupported cap must fail before admission"))
    with pytest.raises(StorageBudgetError, match="projection cap"):
        storage_cycle.prepare_cycle(config, "denied")
    assert not Path(config["storage_budget"]["ledger_path"]).exists()


def test_full_cycle_and_child_wire_are_bound(tmp_path):
    config = config_fixture(tmp_path)
    context = storage_cycle.prepare_cycle(config, "cycle")
    assert context["admission"].ready
    assert context["basis"]["worker_db_bytes"] >= config["publication_worker_database"].stat().st_size
    env = storage_cycle.child_environment(context, "worker")
    wire = json.loads(env[storage_cycle.LEASE_ENV])
    assert wire["cycle_id"] == "cycle"
    assert env["TMPDIR"] == config["storage_cycle"]["scratch_dir"]
    assert env["PYTHONDONTWRITEBYTECODE"] == "1"
    assert json.loads(env[storage_cycle.LIMITS_ENV])["ingress_cap_bytes"] == 100_000


@pytest.mark.parametrize("changed", ["unknown_runtime", "missing_registry", "scratch_category", "infinite_ingress", "unbounded_inventory", "missing_runtime", "missing_file", "response_over_cap"])
def test_incomplete_runtime_or_caps_prevent_cycle_creation(tmp_path, changed):
    config = config_fixture(tmp_path)
    if changed == "unknown_runtime":
        config["worker_argv"][0] = str(tmp_path / "unregistered-python")
    elif changed == "missing_registry":
        del config["_registry_path"]
    elif changed == "scratch_category":
        config["storage_cycle"]["scratch_dir"] = str(tmp_path / "essential")
    elif changed == "infinite_ingress":
        config["storage_cycle"]["ingress_cap_bytes"] = float("inf")
    elif changed == "unbounded_inventory":
        config["storage_cycle"]["inventory_seconds"] = 121
    elif changed == "missing_runtime":
        Path(config["worker_argv"][0]).unlink()
    elif changed == "missing_file":
        config["source_manifest_path"].unlink()
    else:
        config["storage_cycle"]["response_cap_bytes"] = 50 * 1024**2 + 1
    with pytest.raises(StorageBudgetError):
        storage_cycle.prepare_cycle(config, "denied")
    assert not Path(config["storage_budget"]["ledger_path"]).exists()


def test_response_cap_is_finite_subdivision_of_ingress(tmp_path):
    config = config_fixture(tmp_path)
    normalized = storage_cycle.validate_config(config["storage_budget"], config["storage_cycle"])
    assert normalized["storage_cycle"]["response_cap_bytes"] == 100_000
    config["storage_cycle"]["response_cap_bytes"] = 50_000
    assert storage_cycle.prepare_cycle(config, "bounded")["limits"]["response_cap_bytes"] == 50_000


def test_controller_log_is_bound_to_existing_diagnostics_file(tmp_path):
    config = config_fixture(tmp_path)
    log = tmp_path / "diagnostics" / "cron.log"
    log.write_bytes(b"old log")
    config["storage_cycle"]["controller_log_path"] = str(log)
    context = storage_cycle.prepare_cycle(config, "with-log")
    assert log in context["required_paths"]
    config["storage_cycle"]["controller_log_path"] = str(tmp_path / "essential" / "log")
    with pytest.raises(StorageBudgetError, match="diagnostics"):
        storage_cycle.validate_config(config["storage_budget"], config["storage_cycle"])


def test_postworker_space_loss_defers_projection_without_discarding_accepted_work(tmp_path, monkeypatch):
    config = config_fixture(tmp_path)
    context = storage_cycle.prepare_cycle(config, "accepted")
    context["ledger"].transition("worker_admitted", "accepted_pending")
    pending_before = Path(config["storage_budget"]["ledger_path"]).read_bytes()
    device = (tmp_path / "essential").stat().st_dev
    monkeypatch.setattr(storage_cycle, "_free_bytes", lambda _: {device: PHYSICAL_RESERVE_BYTES + 1})
    result = storage_cycle.recheck_cycle(config, context, "accepted_pending")
    assert not result.ready
    assert any(reason.startswith("physical_reserve_insufficient") for reason in result.reasons)
    assert Path(config["storage_budget"]["ledger_path"]).read_bytes() == pending_before
    assert context["ledger"].snapshot()["phase"] == "accepted_pending"


def test_recheck_does_not_double_count_finished_operations(tmp_path):
    config = config_fixture(tmp_path)
    context = storage_cycle.prepare_cycle(config, "cycle")
    initial_essential = context["admission"].inventory.charged_bytes["essential"]
    worker = context["ledger"].for_writer("worker")
    target = tmp_path / "essential" / "capture"
    grant = worker.reserve("essential", 100, path=target)
    grant.consume(100)
    target.write_bytes(b"x" * 100)
    grant.settle()
    context["ledger"].transition("worker_admitted", "accepted_pending")
    result = storage_cycle.recheck_cycle(config, context, "accepted_pending")
    assert result.ready
    assert result.projected_growth["essential"] == context["envelope"]["essential"] - 100
    assert result.inventory.charged_bytes["essential"] >= initial_essential + 100


@pytest.mark.parametrize("key", ["accepted_cap_bytes", "database_growth_cap_bytes", "publisher_write_limits"])
def test_caps_cannot_change_midcycle(tmp_path, key):
    config = config_fixture(tmp_path)
    context = storage_cycle.prepare_cycle(config, "cycle")
    if key == "publisher_write_limits":
        config["storage_cycle"][key] = dict.fromkeys(CATEGORY_LIMITS, 1)
    else:
        config["storage_cycle"][key] += 1
    with pytest.raises(StorageBudgetError, match="scope"):
        storage_cycle.recheck_cycle(config, context, "worker_admitted")


def test_maintenance_has_independent_small_escrow_before_whole_cycle(tmp_path):
    config = config_fixture(tmp_path)
    maintenance = storage_cycle.prepare_maintenance(config, "maintenance")
    assert maintenance["maintenance"] is True
    assert maintenance["envelope"]["work"] == 0
    maintenance["ledger"].transition("worker_admitted", "completed")
    cycle = storage_cycle.prepare_cycle(config, "next")
    assert cycle["ledger"].cycle_id == "next"


def test_unrelated_maintenance_reuses_pending_escrow_without_replacing_it(tmp_path):
    config = config_fixture(tmp_path)
    cycle = storage_cycle.prepare_cycle(config, "pending")
    cycle["ledger"].transition("worker_admitted", "accepted_pending")
    maintenance = storage_cycle.prepare_maintenance(config, "maintenance", pending_context=cycle)
    assert maintenance["maintenance_reuses_pending"] is True
    assert maintenance["ledger"].cycle_id == "pending"
    assert maintenance["ledger"].snapshot()["phase"] == "accepted_pending"


def test_explicit_publisher_caps_replace_only_publisher_estimate(tmp_path):
    config = config_fixture(tmp_path)
    configured = {"essential": 100, "publication": 200, "evidence": 0, "diagnostics": 300, "work": 400}
    config["storage_cycle"]["publisher_write_limits"] = configured
    context = storage_cycle.prepare_cycle(config, "bounded-publisher")
    assert context["envelope"]["publication"] == 200
    assert context["envelope"]["essential"] == 328_100 + config["storage_cycle"]["control_cap_bytes"]
    assert context["envelope"]["work"] >= config["publication_worker_database"].stat().st_size + 300_400


def test_hard_mode_does_not_create_or_claim_operational_cycle(tmp_path):
    config = config_fixture(tmp_path)
    config["storage_budget"]["mode"] = "hard"
    with pytest.raises(StorageBudgetDeferred, match="hard_enforcement_backend_unavailable"):
        storage_cycle.prepare_cycle(config, "hard")
    assert not Path(config["storage_budget"]["ledger_path"]).exists()


def test_existing_context_resumes_pending_grants_and_never_starts_a_new_cycle(tmp_path):
    config = config_fixture(tmp_path)
    assert storage_cycle.existing_context(config) is None
    original = storage_cycle.prepare_cycle(config, "original")
    operation = original["ledger"].for_writer("worker").reserve(
        "essential", 100, path=tmp_path / "essential" / "interrupted", operation_id="interrupted")
    operation.consume(25)
    restored = storage_cycle.existing_context(config)
    assert restored["ledger"].cycle_id == "original"
    assert restored["ledger"].snapshot()["operations"]["interrupted"]["consumed"] == 25
    operation.settle()
    original["ledger"].transition("worker_admitted", "accepted_pending")
    restored = storage_cycle.attach_context(config, original["ledger"].wire())
    assert restored["ledger"].snapshot()["phase"] == "accepted_pending"
    assert restored["projection_cap_bytes"] == original["projection_cap_bytes"]
    original["ledger"].transition("accepted_pending", "publication_admitted")
    original["ledger"].transition("publication_admitted", "completed")
    assert storage_cycle.existing_context(config) is None


def test_existing_context_rejects_changed_configuration_and_child_capability(tmp_path):
    config = config_fixture(tmp_path)
    original = storage_cycle.prepare_cycle(config, "original")
    with pytest.raises(StorageBudgetError, match="controller"):
        storage_cycle.attach_context(config, original["ledger"].for_writer("worker").wire())
    config["_config_sha256"] = "2" * 64
    with pytest.raises(StorageBudgetError, match="scope"):
        storage_cycle.existing_context(config)
