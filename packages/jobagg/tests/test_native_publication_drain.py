"""Real selected projection/publication behind synthetic offline worker exits."""

from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import sqlite3

import pytest


from jobagg import publication_fence as fence, runtime_authority as authority
from jobagg import publish_worker
from jobagg.db import JobDatabase
from test_budgeted_dispatcher import configured, runner  # noqa: F401
from test_live_publication import setup, add_detail, run  # noqa: F401
from test_publication_work import prepare

# ruff: noqa: F811


@pytest.mark.parametrize(
    "budget,fault",
    [
        (False, None),
        (True, None),
        (True, "after_ledger_receipt"),
        (False, "deferred"),
        (True, "deferred"),
        (False, "no_write"),
        (True, "no_write"),
        (True, "no_write_after_receipt"),
    ],
)
def test_three_bounded_generations_preserve_worker_proofs_then_true_empty_skips_projection(
    configured, setup, monkeypatch, budget, fault
):
    config, calls = configured
    import importlib.util
    from jobagg import pending_publication

    spec = importlib.util.spec_from_file_location(
        "native_real_pending", pending_publication.__file__
    )
    actual_pending = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(actual_pending)
    monkeypatch.setattr(pending_publication, "database_binding", actual_pending.database_binding)
    if not budget:
        config.pop("storage_budget")
    else:
        from jobagg.storage_budget import CATEGORY_LIMITS, parse_budget_config

        config["storage_budget"]["roots"].append(
            {
                "name": "fixture-data",
                "path": str(setup["root"]),
                "category": "essential",
                "writers": ["runner", "worker", "publisher"],
            }
        )
        config["native_publication_escrow"] = "per_bounded_batch"
        config["storage_cycle"].update(
            output_dir=str(setup["output"]),
            controller_sql_cap_bytes=131072,
            database_growth_cap_bytes=5_000_000,
            control_cap_bytes=50_000_000,
            publisher_write_limits=dict.fromkeys(CATEGORY_LIMITS, 50_000_000),
        )
        config["publication_policy"].update(
            schema_version=1, transaction_growth_limit_bytes=1_000_000
        )
        config["publication_policy"]["write_limits"] = config["storage_cycle"][
            "publisher_write_limits"
        ]
        budget_config = parse_budget_config(config["storage_budget"])
    config["runtime_control"] = True
    config["phase_budgets"] = dict(
        projection_max_seconds=3,
        projection_min_seconds=0.01,
        publisher_min_seconds=0.01,
        cleanup_report_seconds=0.01,
        measurement_sha256="a" * 64,
    )
    config["publication_admission"] = dict(
        max_candidates=10,
        max_units=1,
        max_sources=1,
        max_targets=2,
        max_bytes=100_000_000,
        max_projection_bytes=5_000_000,
        minimum_seconds=0.01,
        measurement_sha256="a" * 64,
    )
    config["publication_worker_database"] = setup["worker"].path
    config["_registry_path"] = setup["registry"]
    config["_registry_sha256"] = runner.digest(setup["registry"])
    config["publication_argv"][config["publication_argv"].index("--output-dir") + 1] = str(
        setup["output"]
    )
    if budget:
        from jobagg import storage_cycle

        budget_config, _ = storage_cycle._configuration(config)
    owner = config["shared_lock_path"].open("a+")
    for path in (setup["output"] / "test_jobs.sqlite3", setup["output"] / "all_jobs.sqlite3"):
        with JobDatabase(path).connect() as conn:
            fence.prepare_target_schema(conn)
            if not budget:
                fence.activate(
                    conn,
                    owner_path=config["shared_lock_path"],
                    owner_fd=owner.fileno(),
                    epoch="native-test",
                )
    add_detail(setup)
    with fence.writer_scope(owner.fileno(), config["shared_lock_path"], setup["output"]):
        initial = run(setup, execute=True)
    assert initial["status"] == "published"
    if budget:
        from jobagg import semantic_history as history

        targets = [setup["output"] / "test_jobs.sqlite3", setup["output"] / "all_jobs.sqlite3"]
        for path in targets:
            with JobDatabase(path).connect() as conn:
                history.activate_semantic_history(
                    conn,
                    policy_epoch="fixture",
                    max_history_bytes=1_000_000,
                    ledger_path=budget_config.ledger_path,
                    scope_hash=budget_config.scope_hash,
                )
                fence.activate(
                    conn,
                    owner_path=config["shared_lock_path"],
                    owner_fd=owner.fileno(),
                    epoch="native-test",
                )
        exports = json.loads((Path(initial["plan_path"]).parent / "exports.json").read_text())
        initial["publication_boundary"] = fence.seal_boundary(
            targets, exports, output_root=setup["output"]
        )
    add_detail(setup, external_id="002")
    add_detail(setup, external_id="003")
    prepare(setup, owner_path=config["shared_lock_path"], owner_fd=owner.fileno())
    with sqlite3.connect(setup["worker"].path) as conn:
        conn.execute(
            "UPDATE jobagg_dirty_state SET contract_sha256=?",
            (runner.runtime_publication_contract(config)["sha256"],),
        )
        if budget:
            history.activate_semantic_history(
                conn,
                policy_epoch="fixture",
                max_history_bytes=1_000_000,
                ledger_path=budget_config.ledger_path,
                scope_hash=budget_config.scope_hash,
            )
        authority.activate(
            conn,
            epoch="test",
            owner_path=config["shared_lock_path"],
            owner_fd=owner.fileno(),
            bootstrap_receipt={
                "reconciled": True,
                "source_sha256": "b" * 64,
                "publication_boundary": initial["publication_boundary"],
            },
        )
    owner.close()
    config["publication_argv"][config["publication_argv"].index("--output-dir") + 1] = str(
        setup["output"]
    )
    monkeypatch.setattr(runner, "require_native_capabilities", lambda *_: None)
    from jobagg import publication_snapshot

    monkeypatch.setattr(runner, "snapshot_helper", lambda *_: publication_snapshot)
    old_child = runner.subprocess.Popen

    class Child:
        pid = 999999999
        returncode = 0

        def __init__(self, argv, **kwargs):
            self.args = argv
            request_path = Path(argv[argv.index("--request") + 1])
            request = json.loads(request_path.read_text())
            report = Path(argv[argv.index("--report") + 1])
            if "worker_acceptance_sha256" not in request:
                old_child(argv, **kwargs)
                value = json.loads(report.read_text())
                value["concurrency"] = {"accepted_progress": 0}
                report.write_text(json.dumps(value))
            else:
                calls.append(request)
                for key in (
                    "JOBAGG_SHARED_LOCK_FD",
                    "JOBAGG_STORAGE_BUDGET_LEASE",
                    "JOBAGG_STORAGE_CYCLE_LIMITS",
                ):
                    if key in kwargs["env"]:
                        monkeypatch.setenv(key, kwargs["env"][key])
                with redirect_stdout(io.StringIO()):
                    self.returncode = publish_worker.main(
                        [
                            "--worker-database",
                            str(setup["worker"].path),
                            "--registry",
                            str(setup["registry"]),
                            "--output-dir",
                            str(setup["output"]),
                            "--state-dir",
                            str(setup["state"]),
                            "--shared-lock",
                            str(config["shared_lock_path"]),
                            "--request",
                            str(request_path),
                            "--report",
                            str(report),
                            "--max-seconds",
                            "3",
                            "--execute",
                        ]
                    )

        def wait(self, **_):
            return self.returncode

        def poll(self):
            return self.returncode

    monkeypatch.setattr(runner.subprocess, "Popen", Child)
    deferred_fault = fault in {"deferred", "no_write", "no_write_after_receipt"}
    if deferred_fault:
        original_publish = publish_worker.publish_incremental
        deferred = {"done": False}

        def defer_once(*args, **kwargs):
            if not deferred["done"]:
                deferred["done"] = True
                return {
                    "status": "deferred",
                    "generation_not_started": True,
                    "observation_set_sha256": "a" * 64,
                    "budget_exhausted": True,
                    "completeness_certified": False,
                }
            return original_publish(*args, **kwargs)

        monkeypatch.setattr(publish_worker, "publish_incremental", defer_once)
        first = runner.tick(config, ["test_custom_html"])
        assert first["publication_status"] == "deferred", first
        original_snapshot = calls[0]["publication_snapshot"]
        if fault in {"no_write", "no_write_after_receipt"}:
            from jobagg.remediation_worker import shared_owner
            from jobagg import publication_snapshot

            controls = Path(first["run_dir"])
            unknown = {
                **runner.read_json(controls / "outcome.json"),
                "publication_status": "unknown_requires_review_no_replay",
                "publication_exit_code": None,
                "publication_recovery_allowed": True,
            }
            runner.atomic_json(controls / "outcome.json", unknown)
            runner.atomic_json(controls / "state.json", unknown)
            (controls / "publication_result.json").unlink()
            with monkeypatch.context() as clean_env:
                clean_env.delenv("JOBAGG_SHARED_LOCK_FD", raising=False)
                with shared_owner(config["shared_lock_path"]):
                    proof = publication_snapshot.capture_unstarted_publication(
                        runner.read_json(controls / "publication_request.json"),
                        setup["output"],
                        setup["state"],
                        owner_held=True,
                        deadline_at=runner.time.monotonic() + 5,
                    )
            runner.immutable_json(
                controls / "publication_no_write_resolution.json",
                {
                    "run_id": first["run_id"],
                    "previous_outcome_sha256": runner.digest(controls / "outcome.json"),
                    "original_request_sha256": runner.digest(controls / "publication_request.json"),
                    "original_intent_sha256": runner.digest(controls / "publication_intent.json"),
                    "resolved_at": runner.now().isoformat(),
                    "proof": proof,
                },
            )
            if fault == "no_write_after_receipt":
                from jobagg import atomic_files
                from jobagg.storage_budget import StorageBudgetDeferred

                original_write = atomic_files.atomic_write_text
                injected = {"done": False}

                def fail_after_receipt(path, payload, **kwargs):
                    original_write(path, payload, **kwargs)
                    if Path(path).name == "storage_cycle_completed.json" and not injected["done"]:
                        injected["done"] = True
                        raise StorageBudgetDeferred("injected no-write escrow close interruption")

                monkeypatch.setattr(atomic_files, "atomic_write_text", fail_after_receipt)
                interrupted = runner.tick(config, ["test_custom_html"])
                assert interrupted["status"] == "storage_deferred", interrupted
                reconciled = runner.tick(config, ["test_custom_html"])
                assert reconciled.get("storage_cycle_reconciled"), reconciled
        continued = (
            runner.tick(config, ["test_custom_html"])
            if fault != "no_write_after_receipt"
            else {"publication_continuation_prepared": True}
        )
        assert continued.get("publication_continuation_prepared"), continued
    if fault == "after_ledger_receipt":
        from jobagg import atomic_files
        from jobagg.storage_budget import StorageBudgetDeferred

        original_write = atomic_files.atomic_write_text
        injected = {"done": False}

        def interrupted(path, payload, **kwargs):
            original_write(path, payload, **kwargs)
            if Path(path).name == "storage_cycle_completed.json" and not injected["done"]:
                injected["done"] = True
                raise StorageBudgetDeferred("injected after durable native batch ledger")

        monkeypatch.setattr(atomic_files, "atomic_write_text", interrupted)
        first = runner.tick(config, ["test_custom_html"])
        assert first["status"] == "storage_deferred", first
        assert len(calls) == 1
    results = []
    for _ in range(3):
        result = runner.tick(config, ["test_custom_html"])
        assert result.get("publication_status") in {"published", "noop"}, result.get(
            "reasons", result
        )
        results.append(result)
    root = config["state_dir"] / "runs" / results[0]["run_id"]
    proof = {
        name: (root / name).read_bytes()
        for name in ("request.json", "acceptance.json", "worker_result.json")
    }
    assert len(calls) == (4 if deferred_fault else 3)
    if deferred_fault:
        assert calls[1]["publication_snapshot"] == original_snapshot
    assert all("worker_acceptance_sha256" in request for request in calls)
    assert all(record.get("worker_exit_code") is None for record in results)
    assert len({record["run_id"] for record in results}) == 1
    assert results[-1]["pending_backlog_empty"] is True
    if budget:
        from jobagg import storage_cycle

        assert storage_cycle.existing_context(config) is None
    assert (root / "outcome.json").exists()
    assert len(list((root / "publication_batches").glob("*/outcome.json"))) == (
        3 if deferred_fault else 2
    )
    with sqlite3.connect(setup["worker"].path) as conn:
        assert authority.validate(conn)["phase"] == "idle"
    quiet = runner.tick(config, ["test_custom_html"])
    assert quiet["publication_status"] == "proven_empty", quiet
    assert len(calls) == (5 if deferred_fault else 4)
    assert all((root / name).read_bytes() == value for name, value in proof.items())
