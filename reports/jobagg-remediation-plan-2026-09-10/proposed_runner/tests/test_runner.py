import fcntl
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

RUNNER = Path(__file__).resolve().parents[1] / "runner.py"
WORKER = Path(__file__).resolve().parent / "fake_worker.py"


class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.config_path = self.root / "config.json"
        registry = self.root / "registry.json"
        registry.write_text('{"synthetic_test_registry": true}')
        (self.root / "manifest.json").write_text(json.dumps({"schema_version": 1,
            "registry": {"path": registry.name, "sha256": hashlib.sha256(registry.read_bytes()).hexdigest()}, "sources": [
            {"source_id": "fixture_source", "enabled": True},
            {"source_id": "disabled_source", "enabled": False}]}))
        self.configure()

    def tearDown(self):
        self.temp.cleanup()

    def configure(self, mode="happy", **overrides):
        config = {"schema_version": 1,
                  "worker_argv": [sys.executable, str(WORKER), "--mode", mode,
                                  "--request", "{request_path}", "--report", "{report_path}"],
                  "worker_cwd": str(self.root), "source_manifest_path": "manifest.json",
                  "state_dir": "state", "shared_lock_path": "shared.lock", "timeout_seconds": 5,
                  "terminate_grace_seconds": 0.15, "listing_max_age_seconds": 3600,
                  "detail_max_age_seconds": 86400}
        config.update(overrides)
        self.config_path.write_text(json.dumps(config))

    def invoke(self, execute=True):
        command = [sys.executable, str(RUNNER), "--config", str(self.config_path)]
        if execute:
            command.append("--execute")
        result = subprocess.run(command, text=True, capture_output=True, timeout=10)
        return result.returncode, json.loads(result.stdout)

    def assert_state(self, code, state):
        actual_code, result = self.invoke()
        self.assertEqual(actual_code, code, result)
        self.assertEqual(result["status"], state)
        persisted = json.loads((self.root / "state/state.json").read_text())
        self.assertEqual(persisted, result)
        return result

    def test_default_dry_run_writes_nothing(self):
        before = set(self.root.iterdir())
        code, result = self.invoke(execute=False)
        self.assertEqual(code, 0)
        self.assertEqual(result["status"], "dry_run")
        self.assertEqual(before, set(self.root.iterdir()))

    def test_happy_and_manifest_enabled_scope(self):
        result = self.assert_state(0, "complete")
        request = json.loads(Path(result["request_path"]).read_text())
        self.assertEqual(request["expected_source_ids"], ["fixture_source"])

    def test_independent_terminal_census_can_complete_without_numeric_total(self):
        self.configure("independent")
        self.assert_state(0, "complete")

    def test_verified_required_attachment_can_complete(self):
        self.configure("attachment")
        self.assert_state(0, "complete")

    def test_incomplete_invariants_override_worker_complete_boolean(self):
        for mode in ("incomplete", "content_mismatch", "attachment_missing", "declared_incomplete", "blocked"):
            with self.subTest(mode=mode):
                self.configure(mode)
                self.assert_state(2, "incomplete")

    def test_invalid_or_stale_reports_fail_closed(self):
        for mode in ("bad_run_id", "bad_manifest", "missing_source", "bad_count", "duplicate_id",
                     "stale_report", "stale_detail", "tampered_evidence", "missing_report", "nonzero"):
            with self.subTest(mode=mode):
                self.configure(mode)
                self.assert_state(3, "process_failure")

    def test_unconfigured_or_missing_worker_fails_closed(self):
        for argv in ([], ["/nonexistent/future-worker", "{request_path}", "{report_path}"]):
            with self.subTest(argv=argv):
                self.configure(worker_argv=argv)
                self.assert_state(3, "process_failure")

    def test_timeout_kills_descendants_and_releases_lock(self):
        self.configure("timeout", timeout_seconds=0.5)
        started = time.monotonic()
        result = self.assert_state(3, "process_failure")
        self.assertLess(time.monotonic() - started, 3)
        heartbeat = Path(result["run_dir"]) / "child-heartbeat.txt"
        last = heartbeat.read_text()
        time.sleep(0.15)
        self.assertEqual(last, heartbeat.read_text(), "SIGTERM-ignoring child survived cleanup")
        # The grandchild inherits the flock too: a surviving child blocks this run.
        self.configure("happy")
        self.assert_state(0, "complete")

    def test_failure_preserves_last_complete_record(self):
        first = self.assert_state(0, "complete")
        self.configure("incomplete")
        self.assert_state(2, "incomplete")
        self.assertEqual(json.loads((self.root / "state/last_complete.json").read_text()), first)

    def test_registry_drift_during_work_is_rejected(self):
        self.configure("registry_drift")
        self.assert_state(3, "process_failure")

    def test_busy_shared_lock_skips_without_rewriting_running_state(self):
        lock = os.open(self.root / "shared.lock", os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            code, result = self.invoke()
            self.assertEqual(code, 75)
            self.assertEqual(result["status"], "lock_busy")
            self.assertFalse((self.root / "state/state.json").exists())
            self.assertEqual(len(list((self.root / "state/attempts").glob("*/outcome.json"))), 1)
        finally:
            os.close(lock)


if __name__ == "__main__":
    unittest.main()
