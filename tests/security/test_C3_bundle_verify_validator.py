from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "packages" / "jobagg"))

from jobagg.db import JobDatabase  # noqa: E402
from jobagg.scheduler import main  # noqa: E402


def test_C3_bundle_verify_rejects_truncated_sqlite_fixture(
    tmp_path: Path,
    capsys,
) -> None:
    bundle = tmp_path / "bad_jobs.sqlite3"
    bundle.write_bytes(b"SQLite format 3\x00")

    assert main(["bundle", "verify", str(bundle)]) == 1

    captured = capsys.readouterr()
    assert "integrity" in captured.err.lower() or "truncated" in captured.err.lower()


def test_C3_bundle_verify_rejects_oversized_fixture_before_sqlite_open(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    bundle = tmp_path / "huge_jobs.sqlite3"
    bundle.write_bytes(b"SQLite format 3\x00" + b"x" * 64)

    def fail_if_opened(*args, **kwargs):
        raise AssertionError("oversized bundle must be rejected before sqlite open")

    monkeypatch.setattr(sqlite3, "connect", fail_if_opened)

    assert main(["bundle", "verify", str(bundle), "--max-bytes", "16"]) == 1

    captured = capsys.readouterr()
    assert "exceeds" in captured.err.lower()


def test_C3_bundle_verify_accepts_minimal_valid_bundle(tmp_path: Path, capsys) -> None:
    bundle = tmp_path / "valid_jobs.sqlite3"
    db = JobDatabase(bundle)
    db.initialize()
    (tmp_path / "valid_jobs_current.json").write_text(json.dumps([]), encoding="utf-8")
    (tmp_path / "valid_jobs_history.json").write_text(json.dumps([]), encoding="utf-8")

    assert main(["bundle", "verify", str(bundle)]) == 0

    captured = capsys.readouterr()
    assert "ok" in captured.out.lower()
