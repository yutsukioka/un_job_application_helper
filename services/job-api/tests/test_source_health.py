from datetime import datetime, timedelta, timezone
from pathlib import Path

from job_api.app import _source_summaries
from jobagg.db import JobDatabase
from jobagg.models import JobRecord


def database(tmp_path):
    db = JobDatabase(tmp_path / "all_jobs.sqlite3")
    db.initialize()
    db.upsert_job(
        JobRecord(
            source_id="test",
            org_id="Test",
            ats_family="test",
            external_id="1",
            title="Role",
            apply_url="https://example.org/1",
        )
    )
    with db.connect() as conn:
        # Actual API diagnostic lookup needs only these fields.
        conn.execute("DROP TABLE IF EXISTS source_run_diagnostics")
        conn.execute(
            "CREATE TABLE source_run_diagnostics(source_id TEXT,health_status TEXT,observed_at TEXT)"
        )
        conn.execute(
            "INSERT INTO source_run_diagnostics VALUES(?,?,?)",
            (
                "test",
                "ok",
                (datetime.now(timezone.utc) - timedelta(days=8)).isoformat(),
            ),
        )
    return db.path


def test_old_ok_diagnostic_expires_and_current_hold_wins(tmp_path, monkeypatch):
    path = database(tmp_path)
    assert _source_summaries(path)[0]["health_status"] == "stale"
    monkeypatch.setattr(
        "jobagg.source_health.read_worker_health",
        lambda path: {
            "test": {
                "health_status": "held",
                "observed_at": "2026-09-21T12:00:00Z",
                "active_hold": True,
            },
            "not-yet-published": {"health_status": "not_checked"},
        },
    )
    sources = {
        row["source_id"]: row for row in _source_summaries(path, Path("worker.sqlite3"))
    }
    assert sources["test"]["health_status"] == "held" and sources["test"]["active_hold"]
    assert sources["not-yet-published"]["total_jobs"] == 0


def test_missing_configured_worker_never_falls_back_to_old_ok(tmp_path):
    sources = _source_summaries(database(tmp_path), tmp_path / "missing.sqlite3")
    assert sources[0]["health_status"] == "unavailable"
    assert not (tmp_path / "missing.sqlite3").exists()
