"""Finite accounting and durable admission use only small isolated fixtures."""

import json
import os
import subprocess
import sys
import time
from dataclasses import replace

import pytest

from jobagg.storage_budget import (
    CATEGORY_LIMITS,
    CONTROL_ENVELOPE_BYTES,
    PHYSICAL_RESERVE_BYTES,
    TOTAL_LIMIT,
    BudgetLedger,
    StorageBudgetDeferred,
    StorageBudgetError,
    assess,
    estimate_cycle_growth,
    inventory,
    parse_budget_config,
)


def fixture_config(tmp_path, **overrides):
    roots = []
    for category in CATEGORY_LIMITS:
        path = tmp_path / category
        path.mkdir(parents=True)
        roots.append({"name": category, "path": str(path), "category": category,
                      "writers": ["runner", "worker", "publisher"]})
    value = {"enabled": True, "roots": roots,
             "writer_roles": {"runner": "controller", "worker": "worker", "publisher": "publisher"},
             "ledger_path": str(tmp_path / "diagnostics" / "budget.json"), **overrides}
    return parse_budget_config(value)


def growth(**overrides):
    return {**{key: 1000 for key in CATEGORY_LIMITS},
            "diagnostics": CONTROL_ENVELOPE_BYTES + 1000, **overrides}


def admission(config, **overrides):
    observed = inventory(config)
    assert observed.complete, observed.errors
    return assess(config, observed, projected_growth=growth(),
                  physical_free_bytes={device: TOTAL_LIMIT * 2 for device in observed.device_charged_bytes},
                  **overrides)


def ledger_fixture(tmp_path):
    config = fixture_config(tmp_path)
    ledger = BudgetLedger.create(admission(config), cycle_id="fixture-cycle")
    return config, ledger


def test_proven_settlement_atomically_retains_operation_and_consumed_charge(tmp_path):
    config, ledger = ledger_fixture(tmp_path)
    path = tmp_path / "work" / "projection"
    grant = ledger.reserve("work", 100, path=path, operation_id="projection")
    grant.consume(100)
    before = ledger.snapshot()
    with pytest.raises(StorageBudgetError, match="proof"):
        grant.settle(100, proof_sha256="not-a-digest")
    assert ledger.snapshot() == before
    grant.settle(100, proof_sha256="a" * 64)
    reopened = BudgetLedger.attach(ledger.wire()).snapshot()
    assert not reopened["operations"] and reopened["settled"]["work"] == 100
    assert reopened["completions"]["projection"] == {
        "category": "work", "maximum": 100, "consumed": 100, "path": str(path),
        "writer_id": "runner", "proof_sha256": "a" * 64}
    with pytest.raises(StorageBudgetError, match="already reserved"):
        ledger.reserve("work", 100, path=path, operation_id="projection")
    document = json.loads(config.ledger_path.read_bytes())
    document["settled"]["work"] = 99
    config.ledger_path.write_text(json.dumps(document))
    with pytest.raises(StorageBudgetError):
        BudgetLedger.attach(ledger.wire())


def test_fixed_decimal_total_and_exact_categories(tmp_path):
    assert sum(CATEGORY_LIMITS.values()) == TOTAL_LIMIT == 100_000_000_000
    assert CATEGORY_LIMITS == {"essential": 40_000_000_000, "publication": 15_000_000_000,
                               "evidence": 10_000_000_000, "diagnostics": 5_000_000_000,
                               "work": 30_000_000_000}
    config = fixture_config(tmp_path)
    value = config.to_dict()
    assert parse_budget_config(value) == config
    for changed in ({"total_limit_bytes": 100 * 1024**3},
                    {"category_limits": {**CATEGORY_LIMITS, "evidence": 11_000_000_000}},
                    {"physical_reserve_bytes": PHYSICAL_RESERVE_BYTES - 1}):
        with pytest.raises(StorageBudgetError):
            parse_budget_config({**value, **changed})


def test_inventory_deduplicates_hardlinks_but_not_same_content(tmp_path):
    config = fixture_config(tmp_path)
    before = inventory(config)
    first = tmp_path / "essential" / "first"
    first.write_bytes(b"same content")
    os.link(first, first.with_name("hardlink"))
    first.with_name("copy").write_bytes(first.read_bytes())
    measured = inventory(config)
    assert measured.complete
    assert measured.logical_bytes - before.logical_bytes == len(b"same content") * 2
    assert measured.charged_bytes["essential"] >= first.stat().st_blocks * 512 * 2


def test_sparse_files_are_charged_at_least_logical_length(tmp_path):
    config = fixture_config(tmp_path)
    sparse = tmp_path / "essential" / "sparse"
    with sparse.open("wb") as stream:
        stream.truncate(2_000_000)
    assert inventory(config).charged_bytes["essential"] >= 2_000_000


@pytest.mark.parametrize("problem", ["external_symlink", "missing_root", "entry_limit", "deadline", "cross_category_hardlink"])
def test_incomplete_inventory_fails_closed(tmp_path, problem):
    config = fixture_config(tmp_path)
    kwargs = {}
    if problem == "external_symlink":
        outside = tmp_path / "outside"
        outside.write_text("unique")
        (tmp_path / "essential" / "escape").symlink_to(outside)
    elif problem == "missing_root":
        (tmp_path / "evidence").rmdir()
    elif problem == "entry_limit":
        config = replace(config, max_entries=1)
    elif problem == "deadline":
        kwargs["deadline"] = time.monotonic() - 1
    else:
        first = tmp_path / "essential" / "file"
        first.write_text("unique")
        os.link(first, tmp_path / "publication" / "alias")
    observed = inventory(config, **kwargs)
    assert not observed.complete
    result = assess(config, observed, projected_growth=growth(), physical_free_bytes={})
    assert not result.ready and "inventory_incomplete" in result.reasons


def test_internal_symlink_and_nested_category_are_charged_once(tmp_path):
    base = tmp_path / "active"
    diag = base / "diagnostics"
    diag.mkdir(parents=True)
    (base / "payload").write_text("x")
    (base / "alias").symlink_to(base / "payload")
    config = parse_budget_config({"enabled": True, "writer_roles": {"runner": "controller"},
        "roots": [{"name": "active", "path": str(base), "category": "essential", "writers": ["runner"]},
                  {"name": "diag", "path": str(diag), "category": "diagnostics", "writers": ["runner"]}],
        "ledger_path": str(diag / "ledger")})
    result = inventory(config)
    assert result.complete
    assert result.entries == 4
    assert result.charged_bytes["diagnostics"] == diag.stat().st_blocks * 512


def test_sqlite_family_rules_keep_new_exports_in_publication_and_match_reservations(tmp_path):
    original = fixture_config(tmp_path)
    raw = original.to_dict()
    raw["category_rules"] = [
        {"root": "publication", "pattern": pattern, "category": "essential"}
        for pattern in ("*.sqlite3", "*.sqlite3-wal", "*.sqlite3-shm", "*.sqlite3-journal")]
    raw["category_rules"].extend({"root": "publication", "pattern": pattern, "category": "work"}
                                 for pattern in (".*_jobs.sqlite3.tmp", ".*_jobs.sqlite3.tmp-*"))
    config = parse_budget_config(raw)
    output = tmp_path / "publication"
    before = inventory(config)
    categories = {"all_jobs.sqlite3": "essential", "all_jobs.sqlite3-wal": "essential",
                  "all_jobs.sqlite3-shm": "essential", "all_jobs.sqlite3-journal": "essential",
                  "new_jobs.json": "publication", "new_jobs.csv": "publication",
                  "suspicious.sqlite3.json": "publication", ".all_jobs.sqlite3.tmp": "work",
                  ".all_jobs.sqlite3.tmp-wal": "work", ".all_jobs.sqlite3.tmp-shm": "work"}
    expected = dict.fromkeys(CATEGORY_LIMITS, 0)
    for name, category in categories.items():
        path = output / name
        # The same rule must classify future write targets and existing files.
        assert config.root_for(path).category == category
        path.write_bytes(b"payload")
        expected[category] += max(path.stat().st_size, path.stat().st_blocks * 512)
    measured = inventory(config)
    assert measured.complete
    for category in CATEGORY_LIMITS:
        assert measured.charged_bytes[category] >= before.charged_bytes[category] + expected[category]
    admitted = assess(config, measured, projected_growth=growth(essential=100_000, publication=100_000, work=100_000),
                       physical_free_bytes={device: TOTAL_LIMIT for device in measured.device_charged_bytes})
    ledger = BudgetLedger.create(admitted, cycle_id="mixed-layout")
    for name, category in categories.items():
        assert ledger.category_for(output / name) == category
        grant = ledger.reserve(category, 1, path=output / name)
        grant.settle()


def test_conflicting_rules_or_recursive_patterns_fail_closed(tmp_path):
    config = fixture_config(tmp_path)
    raw = config.to_dict()
    raw["category_rules"] = [{"root": "publication", "pattern": "*.sqlite3", "category": "essential"},
                             {"root": "publication", "pattern": "all*", "category": "work"}]
    config = parse_budget_config(raw)
    with pytest.raises(StorageBudgetError, match="ambiguous"):
        config.root_for(tmp_path / "publication" / "all.sqlite3")
    (tmp_path / "publication" / "all.sqlite3").write_bytes(b"held")
    assert not inventory(config).complete
    raw["category_rules"][0]["pattern"] = "**/*.sqlite3"
    with pytest.raises(StorageBudgetError, match="immediate-child"):
        parse_budget_config(raw)


def test_offline_backup_is_explicit_disjoint_and_reported(tmp_path):
    config = fixture_config(tmp_path, offline_backup_paths=[str(tmp_path / "offline")])
    result = admission(config)
    assert result.ready
    assert result.to_dict()["offline_backup_paths"] == [str(tmp_path / "offline")]
    with pytest.raises(StorageBudgetError, match="disjoint"):
        parse_budget_config({**config.to_dict(), "offline_backup_paths": [str(tmp_path / "essential" / "backup")]})


def test_unknown_paths_writers_stale_and_false_hard_attestation_deny(tmp_path):
    config = fixture_config(tmp_path)
    result = admission(config, required_paths=[tmp_path / "unregistered"], writer_ids=["unknown"])
    assert {"unregistered_required_path", "unregistered_writer"} <= set(result.reasons)
    hard = replace(config, mode="hard")
    observed = inventory(hard)
    result = assess(hard, observed, projected_growth=growth(),
                    physical_free_bytes={dev: TOTAL_LIMIT for dev in observed.device_charged_bytes},
                    enforcement={"verified": True, "quota": TOTAL_LIMIT})
    assert not result.ready and "hard_enforcement_backend_unavailable" in result.reasons
    stale = replace(observed, observed_monotonic=time.monotonic() - 100)
    assert "inventory_stale" in assess(hard, stale, projected_growth=growth(), physical_free_bytes={}).reasons


def test_category_limit_cannot_borrow_other_category_space(tmp_path):
    config = fixture_config(tmp_path)
    observed = inventory(config)
    observed = replace(observed, charged_bytes={**observed.charged_bytes, "publication": 15_000_000_000})
    result = assess(config, observed, projected_growth=growth(),
                    physical_free_bytes={dev: TOTAL_LIMIT for dev in observed.device_charged_bytes})
    assert not result.ready
    assert "category_exhausted:publication" in result.reasons
    assert "total_budget_exhausted" not in result.reasons


def test_device_plan_cannot_shift_storage_off_actual_writer_device(tmp_path):
    config = fixture_config(tmp_path)
    observed = inventory(config)
    device = next(iter(observed.device_charged_bytes))
    other = device + 1
    observed = replace(observed, device_charged_bytes={device: 1, other: 1})
    result = assess(config, observed, projected_growth=growth(),
                    physical_free_bytes={device: PHYSICAL_RESERVE_BYTES, other: TOTAL_LIMIT},
                    device_growth={device: 0, other: sum(growth().values())})
    assert not result.ready
    assert "device_envelope_understates_growth" in result.reasons


def test_467mb_margin_rejects_745mb_projection_before_admission(tmp_path):
    config = fixture_config(tmp_path)
    observed = inventory(config)
    result = assess(config, observed,
                    projected_growth=growth(essential=0, publication=0, evidence=0, work=744_759_296),
                    physical_free_bytes={dev: PHYSICAL_RESERVE_BYTES + 467_000_000 for dev in observed.device_charged_bytes})
    assert not result.ready
    assert any(reason.startswith("physical_reserve_insufficient") for reason in result.reasons)
    with pytest.raises(StorageBudgetDeferred):
        BudgetLedger.create(result, cycle_id="never-started")
    assert not config.ledger_path.exists()


def test_estimate_escrows_completion_not_previous_tick_size():
    result = estimate_cycle_growth(worker_db_bytes=1000, output_db_bytes=2000, export_bytes=3000,
        ingress_cap_bytes=100, accepted_cap_bytes=50, database_growth_cap_bytes=500)
    assert result["essential"] == 650
    assert result["work"] == 8100
    assert result["publication"] == 5600
    assert result["diagnostics"] >= CONTROL_ENVELOPE_BYTES
    with pytest.raises(StorageBudgetError):
        estimate_cycle_growth(worker_db_bytes=-1, output_db_bytes=0, export_bytes=0,
            ingress_cap_bytes=0, accepted_cap_bytes=0, database_growth_cap_bytes=0)


def test_durable_interruption_retains_full_grant_and_charged_bytes(tmp_path):
    config, parent = ledger_fixture(tmp_path)
    worker = parent.for_writer("worker")
    grant = worker.reserve("essential", 900, path=tmp_path / "essential" / "body", operation_id="fetch")
    grant.consume(500)
    reopened = BudgetLedger.attach(worker.wire())
    assert reopened.remaining_envelope()["essential"] == 100
    with pytest.raises(StorageBudgetDeferred, match="unfinished"):
        BudgetLedger.create(admission(config), cycle_id="new-cycle")
    with pytest.raises(StorageBudgetDeferred, match="exhausted"):
        reopened.reserve("essential", 101, path=tmp_path / "essential" / "new")
    with pytest.raises(StorageBudgetError, match="retain"):
        reopened.resume("fetch").settle(0)
    reopened.resume("fetch").settle(500)
    assert reopened.remaining_envelope()["essential"] == 500


def test_overflow_is_deferred_before_write_and_preserves_consumed_prefix(tmp_path):
    _, parent = ledger_fixture(tmp_path)
    worker = parent.for_writer("worker")
    grant = worker.reserve("essential", 10, path=tmp_path / "essential" / "partial")
    grant.consume(10)
    with pytest.raises(StorageBudgetDeferred, match="ingress_or_write_limit"):
        grant.consume(1)
    grant.settle()
    assert worker.snapshot()["settled"]["essential"] == 10


def test_phase_fence_blocks_worker_after_accepted_and_requires_bound_capability(tmp_path):
    config, parent = ledger_fixture(tmp_path)
    wire = parent.for_writer("worker").wire()
    worker = BudgetLedger.attach(wire)
    required = {"expected_cycle_id": "fixture-cycle", "expected_scope_hash": config.scope_hash,
        "expected_ledger_path": config.ledger_path, "allowed_phases": ("worker_admitted", "publication_admitted"),
        "required_path": tmp_path / "essential" / "jobs.sqlite3"}
    assert worker.validate_writer(**required)["phase"] == "worker_admitted"
    with pytest.raises(StorageBudgetError):
        BudgetLedger.attach({**wire, "token": "0" * 32})
    parent.transition("worker_admitted", "accepted_pending")
    with pytest.raises(StorageBudgetDeferred, match="phase"):
        worker.validate_writer(**required)
    publisher = parent.for_writer("publisher")
    with pytest.raises(StorageBudgetDeferred, match="phase"):
        publisher.validate_writer(**required)
    parent.transition("accepted_pending", "publication_admitted")
    assert publisher.validate_writer(**required)["phase"] == "publication_admitted"
    parent.transition("publication_admitted", "completed")
    with pytest.raises(StorageBudgetDeferred):
        publisher.validate_writer(**required)
    replacement = BudgetLedger.create(admission(config), cycle_id="next")
    assert replacement.cycle_id == "next"


@pytest.mark.parametrize("corruption", ["empty", "envelope", "root", "symlink"])
def test_corrupt_ledger_never_reinitializes_or_authorizes(tmp_path, corruption):
    config, parent = ledger_fixture(tmp_path)
    path = config.ledger_path
    if corruption == "symlink":
        other = tmp_path / "diagnostics" / "other"
        path.rename(other)
        path.symlink_to(other)
    else:
        value = json.loads(path.read_text())
        if corruption == "empty":
            value = {}
        elif corruption == "envelope":
            value["envelope"]["essential"] += 1
        else:
            value["root_identities"][str(tmp_path / "essential")][1] += 1
        path.write_text(json.dumps(value))
    with pytest.raises((StorageBudgetError, ValueError)):
        parent.snapshot()
    with pytest.raises((StorageBudgetError, ValueError)):
        BudgetLedger.create(admission(config), cycle_id="replacement")


def test_subprocesses_cannot_double_allocate_same_envelope(tmp_path):
    _, parent = ledger_fixture(tmp_path)
    wire = json.dumps(parent.for_writer("worker").wire())
    script = """import json,sys
from jobagg.storage_budget import BudgetLedger,StorageBudgetDeferred
ledger=BudgetLedger.attach(json.loads(sys.argv[1]))
try:
    grant=ledger.reserve('essential',700,path=sys.argv[2])
    grant.consume(700)
    grant.settle()
    print('admitted')
except StorageBudgetDeferred:
    print('deferred')
"""
    children = [subprocess.Popen([sys.executable, "-B", "-c", script, wire, str(tmp_path / "essential" / f"child-{i}")],
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for i in range(2)]
    outcomes = [child.communicate(timeout=20) for child in children]
    assert all(child.returncode == 0 for child in children), outcomes
    assert sorted(out.strip() for out, _ in outcomes) == ["admitted", "deferred"]
    assert parent.snapshot()["settled"]["essential"] == 700


def test_outstanding_operations_fence_phase_transition(tmp_path):
    _, parent = ledger_fixture(tmp_path)
    grant = parent.for_writer("worker").reserve("essential", 1, path=tmp_path / "essential" / "pending")
    with pytest.raises(StorageBudgetDeferred, match="unsettled"):
        parent.transition("worker_admitted", "accepted_pending")
    grant.settle()
    parent.transition("worker_admitted", "accepted_pending")
