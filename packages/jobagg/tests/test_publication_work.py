from contextlib import nullcontext
import hashlib
import json
from pathlib import Path
import sqlite3
import time


from jobagg import accepted_evidence, publication_work as work
from jobagg.publication_projection import build_projection
from test_live_publication import setup, add_detail, run  # noqa: F401

# ruff: noqa: F811


def prepare(fixture, *, owner_path=None, owner_fd=None, import_exits=True):
    from jobagg import acceptance_catalog
    from jobagg.remediation_worker import shared_owner

    owner_path = owner_path or fixture["root"] / "migration-owner.lock"
    with (
        shared_owner(owner_path) if owner_fd is None else nullcontext(owner_fd) as fd,
        fixture["worker"].connect() as conn,
    ):
        conn.execute(
            "CREATE TABLE IF NOT EXISTS remediation_sources(source_id TEXT PRIMARY KEY,last_list_at REAL,listing_ids TEXT,listing_proof TEXT)"
        )
        accepted_evidence.prepare(conn)
        if import_exits:
            run_dir = fixture["root"] / "verified-old-worker"
            run_dir.mkdir(exist_ok=True)
            (run_dir / "source_manifest.json").write_text(
                json.dumps({"expected_source_ids": ["test_custom_html"]})
            )
            manifest_sha = hashlib.sha256(
                (run_dir / "source_manifest.json").read_bytes()
            ).hexdigest()
            request = {
                "run_id": run_dir.name,
                "source_manifest_sha256": manifest_sha,
                "expected_source_ids": ["test_custom_html"],
            }
            attempts = [
                dict(row)
                for row in conn.execute("SELECT * FROM remediation_attempts WHERE status='done'")
            ]
            report = {
                "run_id": run_dir.name,
                "status": "incomplete",
                "source_manifest_sha256": manifest_sha,
                "binding": {"implementation_sha256": "f" * 64, "shared_lock": str(owner_path)},
                "concurrency": {
                    "task_intervals": [
                        {
                            **{
                                key: row[key]
                                for key in ("attempt_id", "task_id", "source_id", "kind")
                            },
                            "outcome": {
                                {
                                    "detail": "detail_stored",
                                    "listing": "listing_observed",
                                    "document": "document_captured",
                                }[row["kind"]]: "fixture"
                            },
                        }
                        for row in attempts
                    ]
                },
            }
            (run_dir / "request.json").write_text(json.dumps(request))
            (run_dir / "acceptance.json").write_text(json.dumps(report))
            (run_dir / "worker_result.json").write_text(
                json.dumps(
                    {
                        "run_id": run_dir.name,
                        "worker_exit_code": 0,
                        "report_sha256": hashlib.sha256(
                            (run_dir / "acceptance.json").read_bytes()
                        ).hexdigest(),
                        "request_sha256": hashlib.sha256(
                            (run_dir / "request.json").read_bytes()
                        ).hexdigest(),
                    }
                )
            )
            acceptance_catalog.import_run(
                conn,
                run_dir,
                owner_fd=fd,
                owner_path=owner_path,
                reviewed_implementations={"f" * 64},
                deadline_at=time.monotonic() + 10,
            )
        accepted_evidence.backfill(conn, limit=100, deadline_at=time.monotonic() + 10)
        while not accepted_evidence.backfill_events(
            conn, limit=100, deadline_at=time.monotonic() + 10
        )["complete"]:
            pass
        work.prepare(conn, contract_sha256="contract", epoch="epoch")
        while not work.bootstrap(conn, limit=10, deadline_at=time.monotonic() + 10):
            pass


def select(fixture, conn, **overrides):
    policy = dict(
        max_candidates=10,
        max_units=2,
        max_sources=1,
        max_targets=2,
        max_bytes=100_000_000,
        minimum_seconds=0.01,
        measurement_sha256="a" * 64,
    )
    policy.update(overrides)
    return work.select(
        conn,
        policy=policy,
        contract_sha256="contract",
        output_root=fixture["output"],
        source_paths={"test_custom_html": fixture["output"] / "test_jobs.sqlite3"},
        deadline_at=time.monotonic() + 10,
    )


def test_selected_projection_reads_only_admitted_dependencies_and_preserves_output(setup, tmp_path):
    add_detail(setup)
    prepare(setup)
    with setup["worker"].connect() as conn:
        selection = select(setup, conn)
        assert len(selection["units"]) == 1
    origin = sqlite3.connect(setup["worker"].path)
    destination = sqlite3.connect(tmp_path / "selected.sqlite3")
    try:
        receipt = build_projection(
            origin,
            destination,
            selection=selection,
            deadline_at=time.monotonic() + 10,
            max_bytes=2_000_000,
        )
        assert receipt["manifest"]["version"] == "publication-projection-5"
        assert (
            destination.execute("SELECT count(*) FROM remediation_observations").fetchone()[0] == 1
        )
        assert destination.execute("SELECT count(*) FROM remediation_attempts").fetchone()[0] == 1
        assert (
            destination.execute("SELECT count(*) FROM jobagg_accepted_evidence").fetchone()[0] == 1
        )
    finally:
        origin.close()
        destination.close()
    from jobagg.pipelines.live_publication import plan_publication

    broad = plan_publication(setup["worker"].path, setup["registry"], setup["output"])
    selected = plan_publication(tmp_path / "selected.sqlite3", setup["registry"], setup["output"])
    assert selected["changes"] == broad["changes"]
    assert selected["rejected"] == broad["rejected"]


def test_atomic_dirty_versions_cas_contract_and_held_work_never_false_noop(setup):
    add_detail(setup)
    prepare(setup)
    with setup["worker"].connect() as conn:
        selection = select(setup, conn)
        key = selection["units"][0]["job_key"]
        conn.execute("UPDATE jobs SET location=? WHERE job_key=?", ("newer", key))
        work.acknowledge(conn, selection, result_sha256="b" * 64)
        assert work.vector(conn, contract_sha256="contract", now=time.time())["empty"] is False
        fresh = select(setup, conn)
        work.acknowledge(conn, fresh, result_sha256="c" * 64)
        assert work.vector(conn, contract_sha256="contract", now=time.time())["empty"] is True
        assert (
            work.vector(conn, contract_sha256="changed-parser", now=time.time())["empty"] is False
        )
        conn.execute("UPDATE jobagg_dirty_state SET next_transition_at=1")
        assert work.vector(conn, contract_sha256="contract", now=time.time())[
            "revalidation_reasons"
        ] == ["time_transition_due"]


def test_oversized_unit_remains_explicit_hold_and_cannot_clear_backlog(setup):
    add_detail(setup)
    prepare(setup)
    with setup["worker"].connect() as conn:
        selection = select(setup, conn, max_bytes=1)
        assert not selection["units"]
        assert selection["held"][0]["reason"] == "oversized_unit_requires_reviewed_measured_path"
        work.acknowledge(conn, selection, result_sha256="b" * 64)
        assert work.vector(conn, contract_sha256="contract", now=time.time())["held"]


def test_time_and_delete_invalidation_never_disappear_behind_old_acknowledgement(setup):
    add_detail(setup)
    prepare(setup)
    with setup["worker"].connect() as conn:
        selection = select(setup, conn)
        key = selection["units"][0]["job_key"]
        work.acknowledge(conn, selection, result_sha256="a" * 64)
        conn.execute(
            "UPDATE jobs SET closes_at='2000-01-01T00:00:00+00:00' WHERE job_key=?", (key,)
        )
        metadata = select(setup, conn)
        work.acknowledge(conn, metadata, result_sha256="b" * 64)
        assert work.vector(conn, contract_sha256="contract", now=time.time())["empty"] is False
        assert work.advance_time(conn, now=time.time(), limit=1) == 1
        timed = select(setup, conn)
        assert timed["units"][0]["operation"] == "time_transition_revalidation"
        work.acknowledge(conn, metadata, result_sha256="c" * 64)
        assert work.vector(conn, contract_sha256="contract", now=time.time())["pending"]
        conn.execute("DELETE FROM remediation_observations WHERE job_key=?", (key,))
        missing = select(setup, conn)
        assert any(
            item["reason"] == "unknown_or_disabled_scope_requires_review"
            for item in missing["held"]
        )


def test_done_rows_without_exit_proof_remain_rejected(setup, tmp_path):
    add_detail(setup)
    prepare(setup, import_exits=False)
    with setup["worker"].connect() as conn:
        assert conn.execute("SELECT count(*) FROM jobagg_accepted_evidence").fetchone()[0] == 0
        assert (
            conn.execute("SELECT disposition FROM jobagg_evidence_backfill").fetchone()[0]
            == "missing_successful_exit_proof"
        )
        selection = select(setup, conn)
        destination = sqlite3.connect(tmp_path / "killed.sqlite3")
        build_projection(
            conn,
            destination,
            selection=selection,
            deadline_at=time.monotonic() + 10,
            max_bytes=2_000_000,
        )
        destination.close()
    from jobagg.pipelines.live_publication import plan_publication

    plan = plan_publication(tmp_path / "killed.sqlite3", setup["registry"], setup["output"])
    assert plan["changes"] == []
    assert plan["rejected"]


def test_imported_exit_controls_are_rechecked_and_failed_exit_cannot_backfill(setup):
    import pytest
    from jobagg import acceptance_catalog
    from jobagg.remediation_worker import shared_owner

    add_detail(setup)
    prepare(setup)
    controls = setup["root"] / "verified-old-worker"
    result = json.loads((controls / "worker_result.json").read_text())
    result["worker_exit_code"] = -9
    (controls / "worker_result.json").write_text(json.dumps(result))
    owner = setup["root"] / "migration-owner.lock"
    with shared_owner(owner) as fd, setup["worker"].connect() as conn:
        attempt = dict(conn.execute("SELECT * FROM remediation_attempts").fetchone())
        with pytest.raises(ValueError, match="successful_bound_report"):
            acceptance_catalog.verified_attempt(
                conn, attempt, deadline_at=time.monotonic() + 10, cache={}
            )
        with pytest.raises(ValueError, match="successful_bound_report"):
            acceptance_catalog.import_run(
                conn,
                controls,
                owner_fd=fd,
                owner_path=owner,
                reviewed_implementations={"f" * 64},
                deadline_at=time.monotonic() + 10,
            )


def test_freshness_transition_and_checkpointed_contract_revalidation_never_noop(setup):
    add_detail(setup)
    with setup["worker"].connect() as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS remediation_sources(source_id TEXT PRIMARY KEY,last_list_at REAL,listing_ids TEXT,listing_proof TEXT)"
        )
        accepted_evidence.prepare(conn)
        freshness = {"detail_max_age_seconds": 60, "listing_max_age_seconds": 120}
        work.prepare(conn, contract_sha256="old", epoch="test", freshness=freshness)
        while not work.bootstrap(conn, limit=5, deadline_at=time.monotonic() + 5):
            pass
        work.validate_freshness(conn, freshness)
        due = conn.execute(
            "SELECT due_at FROM jobagg_dirty_transitions WHERE object_key LIKE 'freshness:%'"
        ).fetchone()[0]
        assert work.vector(conn, contract_sha256="old", now=due + 1)["empty"] is False
        assert work.advance_time(conn, now=due + 1, limit=1) == 1
        first = work.revalidate_contract(
            conn,
            contract_sha256="new",
            receipt_sha256="a" * 64,
            limit=1,
            deadline_at=time.monotonic() + 5,
        )
        assert not first["complete"]
        assert (
            "contract_epoch_changed"
            in work.vector(conn, contract_sha256="new", now=time.time())["revalidation_reasons"]
        )
        second = work.revalidate_contract(
            conn,
            contract_sha256="new",
            receipt_sha256="a" * 64,
            limit=1,
            deadline_at=time.monotonic() + 5,
        )
        assert second["complete"]
        assert work.vector(conn, contract_sha256="new", now=time.time())["pending"]


def test_aged_cohort_target_union_and_existing_export_cost_are_bounded(setup):
    from jobagg.db import JobDatabase

    add_detail(setup)
    prepare(setup)
    second = setup["output"] / "second_jobs.sqlite3"
    JobDatabase(second).initialize()
    paths = {"test_custom_html": setup["output"] / "test_jobs.sqlite3", "second": second}
    policy = {
        "max_candidates": 10,
        "max_units": 2,
        "max_sources": 2,
        "max_targets": 3,
        "max_bytes": 1_000_000_000,
        "minimum_seconds": 0.01,
        "measurement_sha256": "a" * 64,
    }
    with setup["worker"].connect() as conn:
        work.acknowledge(conn, select(setup, conn), result_sha256="a" * 64)
        conn.execute(
            "INSERT OR REPLACE INTO jobagg_dirty_cohorts VALUES('test_custom_html',1000000)"
        )
        work._mark(
            conn, "health:test_custom_html", "test_custom_html", None, "health", "health_update"
        )
        work._mark(conn, "health:second", "second", None, "health", "health_update")
        low = work.select(
            conn,
            policy={**policy, "max_units": 1},
            contract_sha256="contract",
            output_root=setup["output"],
            source_paths=paths,
            deadline_at=time.monotonic() + 10,
        )
        assert low["source_ids"] == [
            "second"
        ]  # actual service history protects the low-volume cohort
        both = work.select(
            conn,
            policy=policy,
            contract_sha256="contract",
            output_root=setup["output"],
            source_paths=paths,
            deadline_at=time.monotonic() + 10,
        )
        assert len(both["target_paths"]) == 3
        family = sum(
            Path(path + suffix).stat().st_size
            for path in both["target_paths"]
            for suffix in ("", "-wal", "-shm")
            if Path(path + suffix).exists()
        )
        assert both["estimated_bytes"] == family * 8  # consolidated cost appears once
        export = setup["output"] / "all_jobs_history.json"
        export.write_bytes(b"x" * 4096)
        increased = work.select(
            conn,
            policy=policy,
            contract_sha256="contract",
            output_root=setup["output"],
            source_paths=paths,
            deadline_at=time.monotonic() + 10,
        )
        assert increased["estimated_bytes"] == both["estimated_bytes"] + 8192
        too_narrow = work.select(
            conn,
            policy={**policy, "max_targets": 1},
            contract_sha256="contract",
            output_root=setup["output"],
            source_paths=paths,
            deadline_at=time.monotonic() + 10,
        )
        assert not too_narrow["units"] and all(
            item["reason"] == "destination_fanout_exceeds_reviewed_bound"
            for item in too_narrow["held"]
        )
