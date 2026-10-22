"""M0 incident characterizations, not M1 correctness/performance acceptance.

All roots are temporary; subprocesses and provider traffic are fixture doubles.
Keep these reproductions when M1 changes the expected failure assertions.
"""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from jobagg import pending_publication
from test_budgeted_dispatcher import configured, runner  # noqa: F401

# Imported pytest fixtures intentionally appear as test parameters.
# ruff: noqa: F811


@pytest.mark.parametrize("budget", ["absent", False, True])
@pytest.mark.parametrize("window", ["worker_result_to_marker", "marker_to_intent"])
def test_pre_intent_interruption_baseline(configured, monkeypatch, budget, window):
    config, calls = configured
    if budget == "absent":
        config.pop("storage_budget")
    else:
        config["storage_budget"]["enabled"] = budget
    immutable = runner.immutable_json
    projections = []

    def interrupt_result(path, value):
        immutable(path, value)
        if path.name == "worker_result.json" and window == "worker_result_to_marker":
            raise RuntimeError("M0 interruption after durable worker result")

    def interrupt_projection(*args, **kwargs):
        projections.append(args)
        raise TimeoutError("M0 interrupted pre-intent projection")

    monkeypatch.setattr(runner, "immutable_json", interrupt_result)
    monkeypatch.setattr(
        runner,
        "snapshot_helper",
        lambda _: SimpleNamespace(create_publication_snapshot=interrupt_projection),
    )
    result = runner.tick(config, ["synthetic-m0"])
    expected_marker = budget is True and window == "marker_to_intent"
    assert result["status"] == (
        "accepted_work_pending" if expected_marker else "process_failure"
    ), result
    assert len(calls) == 1  # Validated worker only; no publisher.
    run = Path(result["run_dir"])
    proof = json.loads((run / "worker_result.json").read_text())
    assert proof["worker_exit_code"] == 0
    assert proof["report_sha256"] == runner.digest(run / "acceptance.json")
    assert not (run / "publication_intent.json").exists()
    assert not (run / "publication_snapshot.sqlite3").exists()
    marker = pending_publication.inspect(config["state_dir"])
    # Current limitation: only the budget-enabled marker-to-intent window is fenced.
    expected_marker = budget is True and window == "marker_to_intent"
    assert (marker is not None) == expected_marker
    assert len(projections) == (window == "marker_to_intent")
    if marker:
        assert marker["run_id"] == run.name
        assert marker["acceptance_sha256"] == runner.digest(run / "acceptance.json")
    if window == "marker_to_intent":
        retained = {
            name: (run / name).read_bytes()
            for name in ("request.json", "acceptance.json", "worker_result.json")
        }
        again = runner.tick(config, ["synthetic-m0-restart"])
        assert len(calls) == (1 if expected_marker else 2)
        assert (again["run_id"] == run.name) == expected_marker
        assert all((run / name).read_bytes() == data for name, data in retained.items())


@pytest.mark.parametrize("older_pending", [False, True])
def test_zero_task_batch_still_enters_projection(configured, monkeypatch, older_pending):
    config, calls = configured
    config.pop("storage_budget")
    child = runner.subprocess.Popen
    projections = []
    if older_pending:
        old = config["state_dir"] / "runs" / "older-accepted"
        old.mkdir(parents=True)
        (old / "acceptance.json").write_text('{"accepted_jobs":["older-job"]}')
        (old / "worker_result.json").write_text('{"worker_exit_code":0}')
        preserved = {p: p.read_bytes() for p in old.iterdir()}

    def empty_child(argv, **kwargs):
        process = child(argv, **kwargs)
        report = Path(argv[argv.index("--report") + 1])
        value = json.loads(report.read_text())
        value.update(attempted_tasks=0, accepted_tasks=0)
        report.write_text(json.dumps(value))
        return process

    def projection(*args, **kwargs):
        projections.append(args)
        raise TimeoutError("M0 zero-task projection deadline")

    monkeypatch.setattr(runner.subprocess, "Popen", empty_child)
    monkeypatch.setattr(
        runner, "snapshot_helper", lambda _: SimpleNamespace(create_publication_snapshot=projection)
    )
    result = runner.tick(config, ["synthetic-m0-zero"])
    assert result["status"] == "process_failure", result
    assert len(calls) == len(projections) == 1
    run = Path(result["run_dir"])
    assert json.loads((run / "acceptance.json").read_text())["accepted_tasks"] == 0
    assert not (run / "publication_intent.json").exists()
    if older_pending:
        assert all(p.read_bytes() == data for p, data in preserved.items())
    # Neither variant proves authoritative backlog emptiness. M1 must reconcile it.


def test_eight_timeout_cycles_mutating_wal_reset_identical_condition_guard(configured):
    config, _ = configured
    obs = runner.OBSERVABILITY
    config["attempt_state_dir"].mkdir(parents=True, exist_ok=True)
    wal = Path(str(config["publication_worker_database"]) + "-wal")
    shm = Path(str(config["publication_worker_database"]) + "-shm")
    fingerprints = []
    chronology = []
    for cycle in range(8):
        wal.write_bytes(b"bounded synthetic WAL metadata" * (cycle + 1))
        shm.write_bytes(b"synthetic SHM" * (cycle + 1))
        fingerprint = obs.condition_fingerprint(config, Path(runner.__file__))
        assert obs.failure_hold(config, fingerprint) is None
        obs.record_failure(
            config,
            fingerprint,
            {
                "status": "process_failure",
                "reasons": ["Worker timed out"],
                "unresolved_publication": False,
            },
        )
        prior = json.loads((config["attempt_state_dir"] / "failure_condition.json").read_text())
        fingerprints.append(fingerprint)
        chronology.append(prior["unchanged_failures"])
    assert len(set(fingerprints)) == 8
    assert chronology == [1] * 8  # Known M1 recurrence defect; six-run subset also resets.
    # Negative control: the same condition reaches the existing three-failure hold.
    for _ in range(2):
        obs.record_failure(
            config,
            fingerprints[-1],
            {
                "status": "process_failure",
                "reasons": ["Worker timed out"],
                "unresolved_publication": False,
            },
        )
    assert obs.failure_hold(config, fingerprints[-1])["unchanged_failures"] == 3


def test_joint_worker_publisher_schema_bounded_sql_and_exact_outputs(
    tmp_path, monkeypatch, record_property
):
    import sqlite3
    import time

    from jobagg.accepted_detail_lineage import DETAIL_ATTEMPTS_QUERY, matching_detail_attempts
    from test_live_publication import setup as live_setup, add_detail, add_document
    from test_publication_projection import plan, snapshot
    from test_remediation_worker import setup as worker_setup

    worker_root = tmp_path / "worker-schema"
    worker_root.mkdir()
    worker, _, calls, _ = worker_setup.__wrapped__(worker_root, monkeypatch)
    worker.tick(execute=True)
    network_calls = list(calls)
    with worker.db.connect() as conn:
        ddl = [
            row[0]
            for row in conn.execute(
                "SELECT sql FROM sqlite_master WHERE type='index' AND sql IS NOT NULL "
                "AND tbl_name IN ('remediation_tasks','remediation_attempts')"
            )
        ]
    publisher_root = tmp_path / "publisher"
    publisher_root.mkdir()
    setup = live_setup.__wrapped__(publisher_root)
    record, proof = add_detail(setup)
    add_document(setup, record, proof)
    measurements = []
    with setup["worker"].connect() as conn:
        for sql in ddl:
            conn.execute(sql)
        assert conn.execute(
            "SELECT 1 FROM sqlite_master WHERE name='idx_remediation_attempts_kind_started'"
        ).fetchone()
        conn.execute("UPDATE remediation_tasks SET status='pending' WHERE kind='detail'")
        conn.execute(
            "INSERT INTO remediation_attempts VALUES('newer-failure','detail-001','test_custom_html','detail',9999999999,9999999999,'blocked','{}')"
        )
        for size in (128, 2048):
            conn.execute("DELETE FROM remediation_attempts WHERE attempt_id LIKE 'noise-%'")
            conn.executemany(
                "INSERT INTO remediation_attempts VALUES(?,?,?,?,?,?,?,?)",
                [
                    (f"noise-{i}", f"orphan-{i}", "unrelated", "detail", i, i + 1, "done", "{}")
                    for i in range(size)
                ],
            )
            steps = [0]

            def progress():
                steps[0] += 1
                return 0

            query_plan = [
                list(row)
                for row in conn.execute(
                    "EXPLAIN QUERY PLAN " + DETAIL_ATTEMPTS_QUERY,
                    (record.source_id, record.external_id),
                )
            ]
            conn.set_progress_handler(progress, 1)
            started = time.monotonic()
            rows = list(conn.execute(DETAIL_ATTEMPTS_QUERY, (record.source_id, record.external_id)))
            sql_seconds = time.monotonic() - started
            conn.set_progress_handler(None, 0)
            assert [row["attempt_id"] for row in rows] == ["accepted-001"]
            row = conn.execute(
                "SELECT * FROM jobs WHERE job_key=?", (record.identity_key(),)
            ).fetchone()
            started = time.monotonic()
            matches = matching_detail_attempts(conn, row, proof)
            validation_seconds = time.monotonic() - started
            assert [m["attempt"]["attempt_id"] for m in matches] == ["accepted-001"]
            measurements.append(
                dict(
                    history_rows=size,
                    sql_vm_steps=steps[0],
                    sql_seconds=sql_seconds,
                    lineage_validation_seconds=validation_seconds,
                    query_plan=query_plan,
                )
            )
    # Characterization only: report work growth, don't enshrine a slow plan as a requirement.
    assert all(m["sql_vm_steps"] > 0 for m in measurements)
    expected = plan(setup, setup["worker"].path)
    path, receipt = snapshot(setup)
    assert plan(setup, path) == expected
    assert len(expected["changes"]) == 1 and not expected["rejected"]
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT attempt_id FROM remediation_attempts").fetchall() == [
            ("accepted-001",)
        ]
    # The same joint schema must preserve exact rejection when accepted bytes drift.
    (setup["root"] / "capture/001/detail.json").write_text("{}")
    rejected = plan(setup, setup["worker"].path)
    assert not rejected["changes"]
    assert rejected["rejected"][0]["reason"] == "completed_detail_artifact_not_bound"
    rejected_path, _ = snapshot(setup, name="rejected-projection.sqlite3")
    assert plan(setup, rejected_path) == rejected
    assert calls == network_calls
    assert sum(p.stat().st_size for p in tmp_path.rglob("*") if p.is_file()) < 32_000_000
    record_property("m0_joint_schema", json.dumps(measurements))
    record_property("m0_projection_timings", json.dumps(receipt["projection"]["timings"]))


def test_overlay_policy_cache_preserves_unindexed_charges_and_detects_changes(
    tmp_path, monkeypatch
):
    from test_worker_policy import bootstrap

    policy, _, _ = bootstrap(tmp_path)
    policy.initialize()
    policy.reserve("one", "example", "detail", 1, tmp_path, "task")
    reads = []
    read_text = Path.read_text

    def read(path, *args, **kwargs):
        if path.parent == policy.root / "attempts":
            reads.append(path)
        return read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", read)
    events = policy.event_snapshot()
    assert len(reads) == 1
    events["example"][0]["started_at"] = 999  # Returned values cannot poison cache.
    assert policy.event_snapshot()["example"][0]["started_at"] == 1
    assert len(reads) == 1
    original = reads[0]
    value = json.loads(read_text(original))
    value["started_at"] = 2
    original.write_text(json.dumps(value))
    assert policy.event_snapshot()["example"][0]["started_at"] == 2
    assert len(reads) == 2
    unindexed = original.with_name("unindexed.json")
    unindexed.write_text(json.dumps({**value, "attempt_id": "unindexed"}))
    assert len(policy.event_snapshot()["example"]) == 2
    original.unlink()
    with pytest.raises(ValueError, match="history removed"):
        policy.event_snapshot()


def test_overlay_fts_initialization_rebuilds_once_without_content_census(tmp_path):
    from jobagg.db import JobDatabase
    from test_live_publication import setup as live_setup

    setup = live_setup.__wrapped__(tmp_path)
    db = JobDatabase(setup["output"] / "all_jobs.sqlite3")
    with db.connect() as conn:
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='jobs_fts'").fetchone():
            pytest.skip("SQLite build has no FTS5")
        conn.execute("DROP TABLE jobs_fts")
        statements = []
        conn.set_trace_callback(statements.append)
        db._ensure_fts(conn)
        assert any("VALUES('rebuild')" in sql for sql in statements)
        statements.clear()
        db._ensure_fts(conn)
        assert not any(
            "VALUES('rebuild')" in sql or "COUNT(*)" in sql.upper() for sql in statements
        )
        assert conn.execute(
            "SELECT rowid FROM jobs_fts WHERE jobs_fts MATCH 'Programme'"
        ).fetchall()
