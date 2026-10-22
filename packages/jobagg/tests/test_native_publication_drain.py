"""Real selected projection/publication behind synthetic offline worker exits."""

from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import sqlite3


from jobagg import publication_fence as fence, runtime_authority as authority
from jobagg import publish_worker
from jobagg.db import JobDatabase
from test_budgeted_dispatcher import configured, runner  # noqa: F401
from test_live_publication import setup, add_detail, run  # noqa: F401
from test_publication_work import prepare

# ruff: noqa: F811


def test_three_bounded_generations_preserve_worker_proofs_then_true_empty_skips_projection(
    configured, setup, monkeypatch
):
    config, calls = configured
    config.pop("storage_budget")
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
    owner = config["shared_lock_path"].open("a+")
    for path in (setup["output"] / "test_jobs.sqlite3", setup["output"] / "all_jobs.sqlite3"):
        with JobDatabase(path).connect() as conn:
            fence.prepare_target_schema(conn)
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
    add_detail(setup, external_id="002")
    add_detail(setup, external_id="003")
    prepare(setup)
    with sqlite3.connect(setup["worker"].path) as conn:
        conn.execute("UPDATE jobagg_dirty_state SET contract_sha256=?", (config["_config_sha256"],))
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
                monkeypatch.setenv("JOBAGG_SHARED_LOCK_FD", kwargs["env"]["JOBAGG_SHARED_LOCK_FD"])
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
    assert len(calls) == 4
    assert len({record["run_id"] for record in results}) == 1
    assert results[-1]["pending_backlog_empty"] is True
    assert (root / "outcome.json").exists()
    assert len(list((root / "publication_batches").glob("*/outcome.json"))) == 2
    with sqlite3.connect(setup["worker"].path) as conn:
        assert authority.validate(conn)["phase"] == "idle"
    quiet = runner.tick(config, ["test_custom_html"])
    assert quiet["publication_status"] == "proven_empty", quiet
    assert len(calls) == 5
    assert all((root / name).read_bytes() == value for name, value in proof.items())
