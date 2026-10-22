"""Cold first useful native worker cycle, without historical JSON reconstruction."""

from datetime import datetime, UTC
import sqlite3
import time

import pytest

from jobagg import accepted_evidence, publication_work, runtime_authority, task_headers
from jobagg.policy_authority import migrate
from jobagg.remediation_worker import shared_owner, source_request_host, fetch_schedule_policy
from test_remediation_worker import setup  # noqa: F401
from test_budgeted_dispatcher import configured  # noqa: F401

# ruff: noqa: F811


def _first_useful(setup, monkeypatch):
    worker, _, calls, _ = setup
    worker.initialize()
    with shared_owner(worker.shared_lock) as owner:
        migrate(
            worker.shared_policy,
            owner_fd=owner,
            epoch="native",
            pauses={
                source.id: float(
                    fetch_schedule_policy(source).get("detail_batch_pause_seconds")
                    or fetch_schedule_policy(source).get("oracle_detail_batch_pause_seconds")
                    or 0
                )
                for source in worker.by_id.values()
            },
            reviewed_writers=[{"name": "offline fixture inventory", "fenced": True}],
        )
        with worker.db.connect() as conn:
            accepted_evidence.prepare(conn)
            task_headers.prepare(
                conn, {source.id: source_request_host(source) for source in worker.by_id.values()}
            )
            publication_work.prepare(conn, contract_sha256="configuration", epoch="native")
            assert publication_work.bootstrap(conn, limit=10, deadline_at=time.monotonic() + 10)
            runtime_authority.activate(
                conn,
                epoch="native",
                owner_path=worker.shared_lock,
                owner_fd=owner,
                bootstrap_receipt={"reconciled": True, "source_sha256": "a" * 64},
            )
            runtime_authority.begin(conn, "first-useful", "configuration", owner_fd=owner)
    monkeypatch.setattr(
        worker.shared_policy, "event_snapshot", lambda **_: pytest.fail("cold historical census")
    )
    report = worker.tick(
        execute=True,
        request={
            "run_id": "first-useful",
            "deadline_at": datetime.fromtimestamp(time.time() + 30, UTC).isoformat(),
            "source_manifest_sha256": worker.binding["registry_sha256"],
            "policy_audit": {"max_objects": 2, "maximum_lag_seconds": 3600},
        },
    )
    assert len(calls) == 2
    assert report["concurrency"]["accepted_progress"] >= 1
    with worker.db.connect() as conn:
        references = conn.execute("SELECT * FROM jobagg_accepted_evidence").fetchall()
        assert len(references) == 1
        pointer = references[0]
        assert pointer["artifact_id"] == "sha256:" + pointer["artifact_sha256"]
        assert (
            conn.execute(
                "SELECT status FROM remediation_attempts WHERE attempt_id=?",
                (pointer["attempt_id"],),
            ).fetchone()[0]
            == "done"
        )
        assert (
            publication_work.vector(conn, contract_sha256="configuration", now=time.time())["empty"]
            is False
        )
    authority = worker.shared_policy.native_authority()
    with sqlite3.connect(authority.path) as conn:
        assert conn.execute("SELECT count(*) FROM attempts").fetchone()[0] == 2
        assert conn.execute("SELECT count(*) FROM attempts WHERE kind='detail'").fetchone()[0] == 1

    return report


def test_cold_first_useful_cycle_charges_sql_and_accepts_pointer_atomically(setup, monkeypatch):
    _first_useful(setup, monkeypatch)


def test_interrupted_native_worker_promotes_only_committed_evidence_and_retains_failed_exit(
    setup, configured, monkeypatch
):
    import json
    from test_budgeted_dispatcher import runner
    from jobagg import pending_publication

    # Run the same real offline collector/verifier/acceptance fixture. Simulate a
    # process loss after acceptance commit, before any valid report/exit proof.
    _first_useful(setup, monkeypatch)
    worker, _, calls, _ = setup
    config, _ = configured
    config.pop("storage_budget")
    config.update(
        runtime_control=True,
        publication_worker_database=worker.db.path,
        shared_lock_path=worker.shared_lock,
        atomic_recovery_max_attempts=2,
    )
    config["_config_sha256"] = "configuration"
    config["_manifest_sha256"] = worker.binding["registry_sha256"]
    config["source_manifest_path"] = worker.registry
    config["_registry_path"] = worker.registry
    config["_registry_sha256"] = worker.binding["registry_sha256"]
    monkeypatch.setattr(runner, "require_native_capabilities", lambda *_: None)
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "atomic_native_pending", pending_publication.__file__
    )
    actual = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(actual)
    monkeypatch.setattr(pending_publication, "database_binding", actual.database_binding)
    root = config["state_dir"] / "runs" / "first-useful"
    root.mkdir(parents=True)
    request = {
        "run_id": root.name,
        "source_manifest_sha256": config["_manifest_sha256"],
        "expected_source_ids": list(worker.by_id),
        "started_at": "2026-10-22T00:00:00+00:00",
    }
    (root / "source_manifest.json").write_bytes(worker.registry.read_bytes())
    original = {
        "request.json": request,
        "acceptance.json": {"status": "interrupted"},
        "worker_result.json": {"run_id": root.name, "worker_exit_code": -9},
        "state.json": {
            "run_id": root.name,
            "run_dir": str(root),
            "status": "process_failure",
            "process_groups": [],
        },
        "outcome.json": {
            "run_id": root.name,
            "run_dir": str(root),
            "status": "process_failure",
            "worker_exit_code": -9,
        },
    }
    for name, value in original.items():
        (root / name).write_text(json.dumps(value))
    immutable = {name: (root / name).read_bytes() for name in original}
    with shared_owner(worker.shared_lock) as fd:
        config["_runtime_controller_fd"] = fd
        with runner.runtime_connection(config) as conn:
            control = runtime_authority.publication_control(conn)
            control.update(publication_control_dir=str(root), publication_sequence=0)
            conn.execute("UPDATE jobagg_runtime_state SET control_json=?", (json.dumps(control),))
        resumed = runner.runtime_resume(config, fd)
    assert len(calls) == 2
    assert resumed["proof_files"] == {
        "acceptance": "recovered_acceptance.json",
        "worker_result": "recovered_worker_result.json",
    }
    certificate = json.loads((root / "recovered_worker_result.json").read_text())
    assert certificate["worker_exit_code"] is None
    assert certificate["schema"] == "native-accepted-queue-proof-1"
    assert resumed["publication_control_dir"] == str(root / "publication_batches" / "000000")
    assert all((root / name).read_bytes() == value for name, value in immutable.items())


def test_native_listing_reference_survives_failed_refresh_and_exact_reconciliation(
    setup, monkeypatch, tmp_path
):
    import json
    import hashlib
    from pathlib import Path
    from jobagg import publication_fence as fence
    from jobagg.db import JobDatabase
    from jobagg.pipelines.live_publication import publish_incremental
    from jobagg.runtime_reconciliation import reconcile_run

    report = _first_useful(setup, monkeypatch)
    worker, _, calls, _ = setup
    output = tmp_path / "published"
    output.mkdir()
    paths = fence.source_paths(worker.registry, output)
    targets = sorted({Path(value) for value in paths.values()} | {output / "all_jobs.sqlite3"})
    with shared_owner(worker.shared_lock) as fd:
        with worker.db.connect() as conn:
            with runtime_authority.writer(
                conn, run_id="first-useful", owner_path=worker.shared_lock, owner_fd=fd
            ):
                conn.execute("UPDATE remediation_tasks SET status='failed' WHERE kind='listing'")
        for path in targets:
            db = JobDatabase(path)
            db.initialize()
            with db.connect() as conn:
                fence.prepare_target_schema(conn)
                fence.activate(conn, owner_fd=fd, owner_path=worker.shared_lock, epoch="test")
        with fence.writer_scope(fd, worker.shared_lock, output):
            result = publish_incremental(
                worker.db.path,
                worker.registry,
                output,
                tmp_path / "publication-state",
                execute=True,
                deadline_at=time.monotonic() + 10,
            )
    assert result["status"] == "published", result
    assert result["published_listing_frames"] == 1
    controls = tmp_path / "first-useful"
    controls.mkdir()
    (controls / "request.json").write_text(json.dumps({"run_id": controls.name}))
    (controls / "acceptance.json").write_text(json.dumps(report, default=str))
    (controls / "worker_result.json").write_text(
        json.dumps(
            {
                "run_id": controls.name,
                "worker_exit_code": 0,
                "report_sha256": hashlib.sha256(
                    (controls / "acceptance.json").read_bytes()
                ).hexdigest(),
            }
        )
    )
    reconciled = reconcile_run(
        controls,
        worker.db.path,
        targets,
        deadline_at=time.monotonic() + 10,
        publication_boundary=result["publication_boundary"],
        expected_publication_scope=fence.publication_scope(worker.registry, output),
    )
    assert reconciled["activation_reconciled"], reconciled
    assert any(
        item.get("kind") == "listing" and item["exact_readback"]
        for item in reconciled["accepted_details"]
    )
