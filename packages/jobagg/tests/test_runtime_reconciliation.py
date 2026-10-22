import hashlib
import json
from pathlib import Path
import time

import pytest

from jobagg import (
    runtime_reconciliation as reconciliation,
    runtime_control_catalog as catalog,
    publication_fence as fence,
)
from jobagg.remediation_worker import shared_owner
from test_live_publication import setup, add_detail, add_document, add_frame, run, body  # noqa: F401
from test_publication_work import prepare, select

# ruff: noqa: F811


def test_catalogue_closes_only_after_exact_readback_and_current_export_boundary(setup):
    owner = setup["root"] / "owner.lock"
    targets = [setup["output"] / "test_jobs.sqlite3", setup["output"] / "all_jobs.sqlite3"]
    with shared_owner(owner) as fd:
        for path in targets:
            from jobagg.db import JobDatabase

            with JobDatabase(path).connect() as conn:
                fence.prepare_target_schema(conn)
                fence.activate(conn, owner_fd=fd, owner_path=owner, epoch="test")
        add_detail(setup)
        prepare(setup, owner_path=owner, owner_fd=fd)
        root = setup["root"] / "verified-old-worker"
        controls = {
            name: hashlib.sha256((root / name).read_bytes()).hexdigest()
            for name in ("request.json", "acceptance.json", "worker_result.json")
        }
        manifest = {
            "schema": catalog.SCHEMA,
            "epoch": "test",
            "shared_owner": str(owner),
            "complete_control_root_inventory": True,
            "writers": [{"name": "reviewed synthetic writers", "fenced": True}],
            "runs": [{"run_id": root.name, "path": str(root), "controls": controls}],
        }
        with setup["worker"].connect() as conn:
            catalog.prepare(conn, manifest=manifest, owner_fd=fd, owner_path=owner)
            assert catalog.step(
                conn,
                owner_fd=fd,
                owner_path=owner,
                limit=1,
                deadline_at=time.monotonic() + 10,
                reviewed_implementations={"f" * 64},
            )["complete"]
            with pytest.raises(ValueError, match="exact_identity_and_export"):
                catalog.close_reconciled(
                    conn,
                    root.name,
                    worker_database=setup["worker"].path,
                    destinations=targets,
                    publication_boundary=None,
                    expected_publication_scope=None,
                    owner_fd=fd,
                    owner_path=owner,
                    deadline_at=time.monotonic() + 10,
                )
        with fence.writer_scope(fd, owner, setup["output"]):
            result = run(setup, execute=True)
        assert result["status"] == "published"
        scope = fence.publication_scope(setup["registry"], setup["output"])
        with setup["worker"].connect() as conn:
            proof = catalog.close_reconciled(
                conn,
                root.name,
                worker_database=setup["worker"].path,
                destinations=targets,
                publication_boundary=result["publication_boundary"],
                expected_publication_scope=scope,
                owner_fd=fd,
                owner_path=owner,
                deadline_at=time.monotonic() + 10,
            )
            assert proof["activation_reconciled"]
            assert catalog.oldest_unresolved(conn) is None


def test_older_accepted_occurrence_requires_reviewed_exact_generation_not_latest_row(setup):
    record, _ = add_detail(setup)
    first = run(setup, execute=True)
    root = setup["root"] / "old-run"
    root.mkdir()
    with setup["worker"].connect() as conn:
        conn.execute("UPDATE remediation_attempts SET attempt_id='original-occurrence'")
    request = {"run_id": root.name}
    report = {
        "run_id": root.name,
        "concurrency": {
            "task_intervals": [
                {
                    "attempt_id": "original-occurrence",
                    "task_id": "detail-001",
                    "source_id": record.source_id,
                    "kind": "detail",
                    "outcome": {"detail_stored": record.identity_key()},
                }
            ]
        },
    }
    (root / "request.json").write_text(json.dumps(request))
    (root / "acceptance.json").write_text(json.dumps(report))
    (root / "worker_result.json").write_text(
        json.dumps(
            {
                "run_id": root.name,
                "worker_exit_code": 0,
                "report_sha256": hashlib.sha256(
                    (root / "acceptance.json").read_bytes()
                ).hexdigest(),
            }
        )
    )
    add_detail(
        {**setup, "root": setup["root"] / "new-capture"},
        description=body("Later"),
        observed="2025-09-15T12:00:00+00:00",
    )
    assert run(setup, execute=True)["status"] == "published"
    targets = [setup["output"] / "test_jobs.sqlite3", setup["output"] / "all_jobs.sqlite3"]
    assert not reconciliation.reconcile_run(
        root, setup["worker"].path, targets, deadline_at=time.monotonic() + 10
    )["reconciled"]
    plan = Path(first["plan_path"])
    result = plan.parent / "result.json"
    reference = {
        "plan_path": str(plan),
        "plan_sha256": hashlib.sha256(plan.read_bytes()).hexdigest(),
        "result_path": str(result),
        "result_sha256": hashlib.sha256(result.read_bytes()).hexdigest(),
    }
    proof = reconciliation.reconcile_run(
        root,
        setup["worker"].path,
        targets,
        deadline_at=time.monotonic() + 10,
        generation_references=[reference],
        reviewed_generation_sha256={reference["result_sha256"]},
    )
    assert (
        proof["reconciled"] and not proof["activation_reconciled"]
    )  # current exports still need their own cursor


def test_selected_document_acceptance_survives_failed_refresh_and_reconciles_exact_bytes(
    setup, tmp_path
):
    from jobagg.publication_projection import build_projection
    import sqlite3

    record, proof = add_detail(setup)
    add_document(setup, record, proof)
    with setup["worker"].connect() as conn:
        task = conn.execute("SELECT * FROM remediation_tasks WHERE kind='document'").fetchone()
        conn.execute(
            "INSERT INTO remediation_attempts VALUES(?,?,?,?,?,?,?,?)",
            (
                "accepted-document",
                task["task_id"],
                task["source_id"],
                "document",
                1,
                2,
                "done",
                task["receipt"],
            ),
        )
    prepare(setup)
    with setup["worker"].connect() as conn:
        conn.execute("UPDATE remediation_tasks SET status='failed' WHERE kind='document'")
        selection = select(setup, conn)
    origin = sqlite3.connect(setup["worker"].path)
    destination = sqlite3.connect(tmp_path / "selected.sqlite3")
    build_projection(
        origin,
        destination,
        selection=selection,
        deadline_at=time.monotonic() + 10,
        max_bytes=2_000_000,
    )
    origin.close()
    destination.close()
    from jobagg.pipelines.live_publication import publish_incremental

    result = publish_incremental(
        tmp_path / "selected.sqlite3",
        setup["registry"],
        setup["output"],
        setup["state"],
        execute=True,
    )
    assert result["status"] == "published", result
    controls = setup["root"] / "verified-old-worker"
    exact = reconciliation.reconcile_run(
        controls,
        setup["worker"].path,
        [setup["output"] / "test_jobs.sqlite3", setup["output"] / "all_jobs.sqlite3"],
        deadline_at=time.monotonic() + 10,
    )
    assert exact["reconciled"], exact
    assert any(
        item.get("kind") == "document" and item["exact_readback"]
        for item in exact["accepted_details"]
    )
