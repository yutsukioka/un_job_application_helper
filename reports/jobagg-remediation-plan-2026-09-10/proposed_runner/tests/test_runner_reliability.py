import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

import pytest
import test_runner as base

RUNNER = base.RUNNER

HERE = Path(__file__).parent


@pytest.fixture
def fixture():
    value = base.RunnerTests()
    value.setUp()
    try:
        yield value
    finally:
        value.tearDown()


def phase_config(fixture, mode="complete", publication=None, **kw):
    root = fixture.root
    settings = {
        "worker_argv": [
            sys.executable,
            str(HERE / "phase_worker.py"),
            "--request",
            "{request_path}",
            "--report",
            "{report_path}",
            "--database",
            str(root / "worker.sqlite"),
            "--mode",
            mode,
        ]
    }
    if publication:
        settings.update(
            publication_argv=[
                sys.executable,
                str(HERE / "fake_publisher.py"),
                "--request",
                "{publication_request_path}",
                "--report",
                "{publication_report_path}",
                "--max-seconds",
                "{publication_max_seconds}",
                "--mode",
                publication,
            ],
            publication_cwd=str(HERE.parents[3] / "packages/jobagg"),
            publication_worker_database=str(root / "worker.sqlite"),
            publication_timeout_seconds=1,
        )
    settings.update(kw)
    fixture.configure(**settings)


def invoke_recovery(fixture):
    p = subprocess.run(
        [
            sys.executable,
            str(RUNNER),
            "--config",
            str(fixture.config_path),
            "--execute",
            "--recover-publication",
        ],
        text=True,
        capture_output=True,
        timeout=10,
    )
    return p.returncode, json.loads(p.stdout)


def stale(fixture, **extra):
    root = fixture.root.resolve() / "state"
    d = root / "runs/former-run"
    d.mkdir(parents=True)
    r = {
        "schema_version": 1,
        "run_id": "former-run",
        "status": "running",
        "run_dir": str(d),
        "request_path": str(d / "request.json"),
        "report_path": str(d / "acceptance.json"),
        "started_at": "2000-01-01T00:00:00+00:00",
        **extra,
    }
    (d / "request.json").write_text(json.dumps({"run_id": "former-run"}))
    (d / "state.json").write_text(json.dumps(r))
    (root / "state.json").write_text(json.dumps(r))
    return d, r


def test_graceful_deadline_acceptance_is_incomplete_not_process_failure(fixture):
    phase_config(fixture, "graceful", timeout_seconds=0.25, terminate_grace_seconds=0.5)
    code, r = fixture.invoke()
    assert (code, r["status"]) == (2, "incomplete")
    assert r["worker_exit_code"] == 0 and r["worker_timeout"]["term_sent"]
    assert not r["worker_timeout"]["kill_sent"]
    assert json.loads((Path(r["run_dir"]) / "worker_result.json").read_text())[
        "graceful_timeout"
    ]


def test_written_acceptance_does_not_override_killed_worker(fixture):
    phase_config(fixture, "acceptance_then_sleep", timeout_seconds=0.2)
    code, r = fixture.invoke()
    assert (code, r["status"]) == (3, "process_failure")
    assert Path(r["report_path"]).exists() and r["worker_exit_code"] != 0
    assert not (Path(r["run_dir"]) / "worker_result.json").exists()


def test_stale_state_archived_only_with_owner_and_absent_process(fixture):
    d, old = stale(fixture)
    original = (d / "state.json").read_bytes()
    code, r = fixture.invoke()
    assert code == 0
    assert r["prior_reconciliations"][0]["run_id"] == "former-run"
    assert next(d.glob("state.before-recovery-*")).read_bytes() == original
    assert json.loads((d / "state.json").read_text())["status"] == "process_failure"
    assert json.loads((d / "recovery.json").read_text())["owner"][
        "exclusive_owner_held"
    ]


def test_old_age_never_overrides_existing_process(fixture):
    d, old = stale(fixture, wrapper_pid=os.getpid())
    code, r = fixture.invoke()
    assert code == 3
    assert json.loads((d / "state.json").read_text()) == old
    assert not (d / "outcome.json").exists()


def test_stale_acceptance_is_retained_without_assuming_exit_success(fixture):
    d, _ = stale(fixture)
    (d / "acceptance.json").write_text('{"status":"complete"}')
    fixture.invoke()
    final = json.loads((d / "state.json").read_text())
    assert final["status"] == "process_failure" and final["preserved_acceptance_sha256"]


def test_busy_owner_does_not_reconcile_stale_state(fixture):
    import fcntl

    d, old = stale(fixture)
    with (fixture.root / "shared.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        code, r = fixture.invoke()
        assert code == 75
    assert json.loads((d / "state.json").read_text()) == old


def test_terminal_journal_recovers_after_summary_write_interrupted(fixture):
    hook = fixture.root / "crash_final_summary.py"
    hook.write_text(f"""import importlib.util,sys
spec=importlib.util.spec_from_file_location('runner',{str(RUNNER)!r});m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
original=m.atomic_json
def write(path,value):
    if path.name=='state.json' and value.get('status') in ('complete','incomplete','process_failure'):
        raise OSError('synthetic summary write interrupted')
    return original(path,value)
m.atomic_json=write
sys.exit(m.main(['--config',{str(fixture.config_path)!r},'--execute']))
""")
    first = subprocess.run(
        [sys.executable, str(hook)], capture_output=True, text=True, timeout=10
    )
    assert first.returncode == 3
    oldrun = next((fixture.root / "state/runs").iterdir())
    assert json.loads((oldrun / "state.json").read_text())["status"] == "running"
    assert json.loads((oldrun / "outcome.json").read_text())["status"] == "complete"
    code, result = fixture.invoke()
    assert code == 0
    assert json.loads((oldrun / "state.json").read_text())["status"] == "complete"
    assert (
        result["prior_reconciliations"][0]["reason"]
        == "durable_terminal_outcome_reconciled"
    )


def test_maintenance_is_visible_in_preview_and_blocks_under_owner(fixture):
    marker = fixture.root / "maintenance"
    marker.write_text("operator pause")
    fixture.configure(maintenance_file=str(marker))
    code, preview = fixture.invoke(False)
    assert code == 0 and preview["maintenance_active"]
    assert not (fixture.root / "state").exists()
    code, r = fixture.invoke()
    assert (code, r["status"]) == (75, "maintenance_paused")
    assert not (fixture.root / "state/state.json").exists()


@pytest.mark.parametrize("worker_status", ["complete", "incomplete"])
def test_publication_after_valid_worker_retains_separate_completeness(
    fixture, worker_status
):
    phase_config(fixture, worker_status, publication="published")
    code, r = fixture.invoke()
    assert code == (0 if worker_status == "complete" else 2)
    assert (
        r["worker_status"] == worker_status and r["publication_status"] == "published"
    )
    request = json.loads((Path(r["run_dir"]) / "publication_request.json").read_text())
    assert (
        request["worker_status"] == worker_status and request["worker_database_files"]
    )
    assert request["deadline_epoch"] > time.time() - 3
    assert len((fixture.root / "publication_commits.txt").read_text().splitlines()) == 1


@pytest.mark.parametrize("mode", ["wrong_binding", "wrong_scope", "certify"])
def test_bad_publication_receipt_is_not_success_and_not_automatically_replayed(
    fixture, mode
):
    phase_config(fixture, publication=mode)
    code, r = fixture.invoke()
    assert code == 3 and r["publication_status"] == "unknown_requires_review_no_replay"
    before = (fixture.root / "worker_starts.txt").read_bytes()
    code, _ = fixture.invoke()
    assert code == 3
    assert (fixture.root / "worker_starts.txt").read_bytes() == before
    assert len((fixture.root / "publication_commits.txt").read_text().splitlines()) == 1


def test_publication_timeout_bounded_and_preserves_valid_worker(fixture):
    phase_config(
        fixture,
        publication="timeout",
        timeout_seconds=0.5,
        publication_timeout_seconds=0.2,
        total_timeout_seconds=1,
    )
    start = time.monotonic()
    code, r = fixture.invoke()
    assert code == 3 and r["worker_status"] == "complete"
    assert time.monotonic() - start < 2
    assert r["publication_timeout"]["term_sent"]


def test_explicit_idempotent_publication_recovery_never_refetches_or_duplicates_commit(
    fixture,
):
    phase_config(fixture, "incomplete", publication="commit_then_fail")
    code, first = fixture.invoke()
    assert code == 3
    before = (fixture.root / "worker_starts.txt").read_bytes()
    code, result = invoke_recovery(fixture)
    assert (code, result["status"]) == (2, "incomplete")
    assert result["worker_reexecuted"] is False
    assert (fixture.root / "worker_starts.txt").read_bytes() == before
    assert len((fixture.root / "publication_commits.txt").read_text().splitlines()) == 1
    old = Path(first["run_dir"])
    assert (old / "publication_resolution.json").exists()
    assert (
        json.loads((old / "outcome.json").read_text())["publication_status"]
        == "unknown_requires_review_no_replay"
    )
    code, _ = invoke_recovery(fixture)
    assert code == 3
    assert len((fixture.root / "publication_commits.txt").read_text().splitlines()) == 1


def test_recovery_refuses_changed_config_or_worker_snapshot(fixture):
    phase_config(fixture, publication="commit_then_fail")
    fixture.invoke()
    (fixture.root / "worker.sqlite").write_text("changed")
    code, r = invoke_recovery(fixture)
    assert code == 3 and "binding changed" in r["reasons"][0]
    assert len((fixture.root / "worker_starts.txt").read_text().splitlines()) == 1


def test_signal_cleanup_writes_terminal_outcome(fixture):
    phase_config(fixture, "acceptance_then_sleep")
    process = subprocess.Popen(
        [
            sys.executable,
            str(RUNNER),
            "--config",
            str(fixture.config_path),
            "--execute",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        deadline = time.monotonic() + 3
        while not list((fixture.root / "state/runs").glob("*/acceptance.json")):
            assert time.monotonic() < deadline
            time.sleep(0.02)
        process.send_signal(signal.SIGTERM)
        stdout, stderr = process.communicate(timeout=3)
        assert process.returncode == 3
        state = json.loads(stdout)
        assert state["status"] == "process_failure"
        assert (Path(state["run_dir"]) / "outcome.json").exists()
        fixture.configure()
        assert fixture.invoke()[0] == 0
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()


def test_recovery_refuses_changed_command_configuration(fixture):
    phase_config(fixture, publication="commit_then_fail")
    fixture.invoke()
    config = json.loads(fixture.config_path.read_text())
    config["publication_timeout_seconds"] = 2
    fixture.config_path.write_text(json.dumps(config))
    code, result = invoke_recovery(fixture)
    assert code == 3 and "configuration differs" in result["reasons"][0]
    assert len((fixture.root / "worker_starts.txt").read_text().splitlines()) == 1


def test_resolution_cannot_borrow_another_run_identity(fixture):
    import hashlib

    phase_config(fixture, publication="commit_then_fail")
    _, first = fixture.invoke()
    assert invoke_recovery(fixture)[0] == 0
    root = Path(first["run_dir"])
    resolution_path = root / "publication_resolution.json"
    resolution = json.loads(resolution_path.read_text())
    for name in ("request", "result"):
        p = root / resolution[name]["path"]
        value = json.loads(p.read_text())
        value["run_id"] = "borrowed-other-run"
        p.write_text(json.dumps(value))
        resolution[name]["sha256"] = hashlib.sha256(p.read_bytes()).hexdigest()
    resolution_path.write_text(json.dumps(resolution))
    before = (fixture.root / "worker_starts.txt").read_bytes()
    code, result = fixture.invoke()
    assert code == 3 and "different intent" in result["reasons"][0]
    assert (fixture.root / "worker_starts.txt").read_bytes() == before


def test_durable_publisher_receipt_resolves_lost_final_outcome_without_replay(fixture):
    phase_config(fixture, publication="published")
    code, first = fixture.invoke()
    assert code == 0
    d = Path(first["run_dir"])
    running = {
        k: v
        for k, v in first.items()
        if k not in ("wrapper_pid", "worker_pid", "publication_pid", "finished_at")
    }
    running.update(status="running", process_groups=[])
    (d / "state.json").write_text(json.dumps(running))
    (d / "outcome.json").unlink()
    code, second = fixture.invoke()
    assert code == 0
    recovered = json.loads((d / "outcome.json").read_text())
    assert recovered["recovered_publication_receipt_without_replay"]
    commits = (fixture.root / "publication_commits.txt").read_text().splitlines()
    assert commits == [first["run_id"], second["run_id"]]


@pytest.mark.parametrize("mode", ["commit_then_crash", "defer_once"])
def test_next_tick_recovers_same_publication_before_any_new_worker(fixture, mode):
    phase_config(fixture, "incomplete", publication=mode)
    code, first = fixture.invoke()
    assert code == (3 if mode == "commit_then_crash" else 2)
    if mode == "commit_then_crash":
        assert first["publication_exit_code"] == -9
    before = (fixture.root / "worker_starts.txt").read_bytes()
    code, recovered = fixture.invoke()
    assert code == 2 and recovered["publication_status"] == "published"
    assert recovered["worker_reexecuted"] is False
    assert recovered["run_id"] == first["run_id"]
    assert (fixture.root / "worker_starts.txt").read_bytes() == before
    assert (fixture.root / "publication_commits.txt").read_text().splitlines() == [
        first["run_id"]
    ]
    current = json.loads((fixture.root / "state/state.json").read_text())
    assert current["publication_status"] == "published"
    assert current["status"] == "incomplete"


def test_clean_publication_deferral_does_not_block_next_fetch(fixture):
    phase_config(fixture, publication="deferred")
    code, first = fixture.invoke()
    assert code == 2 and first["publication_status"] == "deferred"
    assert first["publication_recovery_allowed"] is False
    code, second = fixture.invoke()
    assert code == 2 and second["publication_status"] == "deferred"
    assert first["run_id"] != second["run_id"]
    assert not (fixture.root / "publication_commits.txt").exists()


def test_false_clean_deferral_is_rejected(fixture):
    phase_config(fixture, publication="false_deferral")
    code, result = fixture.invoke()
    assert result["status"] == "process_failure"
    assert any("no generation started" in reason for reason in result["reasons"])
