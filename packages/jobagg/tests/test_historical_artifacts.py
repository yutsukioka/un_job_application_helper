import json
from pathlib import Path
import time

import pytest

from jobagg import historical_artifacts
from test_live_publication import setup, add_detail  # noqa: F401

# ruff: noqa: F811


def selected(setup):
    add_detail(setup)
    with setup["worker"].connect() as conn:
        return conn.execute(
            "SELECT attempt_id,evidence FROM remediation_attempts LIMIT 1"
        ).fetchone()


def test_surviving_bytes_do_not_manufacture_exit_publication_or_completeness(setup):
    attempt, evidence = selected(setup)
    with setup["worker"].connect() as conn:
        before = conn.total_changes
        result = historical_artifacts.assess(conn, attempt, deadline_at=time.monotonic() + 5)
        assert conn.total_changes == before
    assert result["disposition"] == "retained_detail_bytes_and_task_crosswalk_verified"
    assert result["body_bytes_read"] > 0
    for key in (
        "historical_worker_exit_verified",
        "publication_verified",
        "completeness_certified",
        "authorizes_recovery_or_retirement",
    ):
        assert result[key] is False


@pytest.mark.parametrize(
    "change",
    [
        "artifact_missing",
        "capture_missing",
        "artifact_changed",
        "body_changed",
        "crosswalk",
        "byte_bound",
    ],
)
def test_lost_or_changed_evidence_stays_explicitly_held(setup, change):
    attempt, evidence = selected(setup)
    path = Path(json.loads(evidence)["detail_path"])
    value = json.loads(path.read_text())
    meta_path = Path(value["proof"]["captures"][0]["path"])
    meta = json.loads(meta_path.read_text())
    if change == "artifact_missing":
        path.unlink()
    elif change == "capture_missing":
        Path(meta["artifact"]).unlink()
    elif change == "artifact_changed":
        path.write_text("{}")
    elif change == "body_changed":
        Path(meta["artifact"]).write_bytes(b"corrupt gzip")
    with setup["worker"].connect() as conn:
        if change == "crosswalk":
            conn.execute("UPDATE remediation_tasks SET external_id='another identity'")
        result = historical_artifacts.assess(
            conn,
            attempt,
            deadline_at=time.monotonic() + 5,
            max_body_bytes=1 if change == "byte_bound" else 64 * 1024**2,
        )
    assert result["disposition"] in {"missing_evidence", "held"}
    assert not result["historical_worker_exit_verified"] and not result["publication_verified"]


def test_expired_artifact_review_cannot_report_success(setup):
    attempt, _ = selected(setup)
    with setup["worker"].connect() as conn, pytest.raises(TimeoutError):
        historical_artifacts.assess(conn, attempt, deadline_at=time.monotonic() - 1)
