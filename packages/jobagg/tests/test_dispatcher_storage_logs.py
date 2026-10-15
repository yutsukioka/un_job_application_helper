"""Bounded controller output uses fixture processes and registered paths only."""

import json
import os
import subprocess
import sys
from contextlib import contextmanager
from types import SimpleNamespace

from test_budgeted_dispatcher import configured, runner  # noqa: F401 - fixture import
from test_storage_cycle import config_fixture

from jobagg import dispatcher_storage, storage_cycle


def test_pipe_logs_are_precharged_and_both_streams_drain(tmp_path):
    config = config_fixture(tmp_path)
    context = storage_cycle.prepare_cycle(config, "pipe-log-fixture")
    paths = [config["state_dir"] / "stdout.log", config["state_dir"] / "stderr.log"]
    process = subprocess.Popen(
        [sys.executable, "-c", "import os; os.write(1,b'out\\n'); os.write(2,b'err\\n')"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    before = context["ledger"].snapshot()["settled"]["diagnostics"]
    dispatcher_storage.wait_logged(process, paths, context["ledger"], timeout=5)
    assert process.returncode == 0
    assert [path.read_bytes() for path in paths] == [b"out\n", b"err\n"]
    after = context["ledger"].snapshot()["settled"]["diagnostics"]
    assert after - before >= 4 * 16384
    assert not context["ledger"].snapshot()["operations"]


def test_regular_controller_output_is_charged_before_cycle_closes(configured, monkeypatch):  # noqa: F811
    config, calls = configured
    log = config["state_dir"] / "controller.log"
    log.touch()
    config["storage_cycle"]["controller_log_path"] = str(log)
    config["_controller_stdout_regular"] = True
    charged = dispatcher_storage.charged_write
    writes = []

    @contextmanager
    def checked_charge(ledger, path, size):
        if path == log:
            assert ledger.snapshot()["phase"] == "publication_admitted"
            assert log.stat().st_size == 0
            writes.append(size)
        with charged(ledger, path, size):
            yield

    monkeypatch.setattr(dispatcher_storage, "charged_write", checked_charge)

    def snapshot(source, target, acceptance_sha, *_args, **_kwargs):
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"sealed fixture projection")
        return {"path": str(target), "sha256": runner.digest(target),
                "source_database": str(source), "worker_acceptance_sha256": acceptance_sha}

    monkeypatch.setattr(runner, "snapshot_helper", lambda _: SimpleNamespace(create_publication_snapshot=snapshot))
    descriptor = os.open(log, os.O_WRONLY | os.O_APPEND)
    try:
        monkeypatch.setattr(runner, "sys", SimpleNamespace(stdout=SimpleNamespace(fileno=lambda: descriptor)))
        result = runner.tick(config, ["fixture"])
    finally:
        os.close(descriptor)
    assert result["publication_status"] == "published", result
    assert len(calls) == 2
    assert json.loads(log.read_text())["run_id"] == result["run_id"]
    assert writes == [log.stat().st_size]
    assert storage_cycle.existing_context(config) is None
