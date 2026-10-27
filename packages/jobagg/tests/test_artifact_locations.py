"""Reviewed aliases preserve bytes without inventing acceptance or schema."""

import json
import sqlite3
import time
from pathlib import Path

import pytest

from jobagg import (
    artifact_locations as locations,
    historical_artifacts,
    runtime_authority as authority,
)
from jobagg.remediation_worker import shared_owner
from test_live_publication import setup, add_detail  # noqa: F401
from test_legacy_wrapper_reconciliation import setup_catalog

# ruff: noqa: F811


@pytest.fixture
def aliased(setup):
    add_detail(setup)
    root = setup["root"]
    with setup["worker"].connect() as conn:
        receipt = json.loads(
            conn.execute("SELECT evidence FROM remediation_attempts").fetchone()[0]
        )
        original = Path(receipt["detail_path"])
        physical = original.with_name("retained-detail.json")
        original.rename(physical)
        original.symlink_to(physical)
        owner = root / "owner.lock"
        with shared_owner(owner) as fd:
            evidence = root / "controls"
            evidence.mkdir()
            setup_catalog(evidence, conn, fd, owner)
            from jobagg.runtime_control_catalog import receipt as catalogue

            authority.activate(
                conn,
                epoch="fixture",
                owner_fd=fd,
                owner_path=owner,
                bootstrap_receipt={
                    "reconciled": True,
                    "source_sha256": "a" * 64,
                    "control_catalogue_sha256": catalogue(conn)["sha256"],
                },
            )
            conn.commit()
            yield conn, fd, original, physical, receipt


def bind(value, **kwargs):
    conn, fd, original, physical, receipt = value
    return locations.bind(
        conn,
        attempt_id="accepted-001",
        role="artifact",
        physical_path=physical,
        owner_fd=fd,
        review_sha256="a" * 64,
        deadline_at=time.monotonic() + 5,
        **kwargs,
    )


def test_exact_reviewed_alias_verifies_bytes_without_acceptance_or_schema_change(aliased):
    conn, fd, original, physical, receipt = aliased
    before = authority.validate(conn)
    result = historical_artifacts.assess(conn, "accepted-001", deadline_at=time.monotonic() + 5)
    assert result["disposition"] == "held"
    with authority.controller(conn, run_id=None, owner_fd=fd):
        value = bind(aliased)
        assert bind(aliased) == value
        conn.commit()
    assert authority.validate(conn) == before
    assert locations.resolve(conn, original, receipt["detail_sha256"]) == physical
    result = historical_artifacts.assess(conn, "accepted-001", deadline_at=time.monotonic() + 5)
    assert result["disposition"] == "retained_detail_bytes_and_task_crosswalk_verified"
    assert not result["historical_worker_exit_verified"] and not result["publication_verified"]
    assert not value["certifies_acceptance_or_publication"]
    assert not conn.execute(
        "SELECT 1 FROM sqlite_master WHERE name='jobagg_accepted_exits'"
    ).fetchone()
    with pytest.raises(sqlite3.DatabaseError):
        conn.execute("DELETE FROM jobagg_legacy_review_holds")


@pytest.mark.parametrize("change", ["bytes", "retarget", "review", "deadline", "owner"])
def test_wrong_or_unreviewed_locator_never_changes_native_controls(aliased, change):
    conn, fd, original, physical, receipt = aliased
    if change == "bytes":
        physical.write_text("{}")
    if change == "retarget":
        other = physical.with_name("other.json")
        other.write_bytes(physical.read_bytes())
        original.unlink()
        original.symlink_to(other)
    args = dict(
        attempt_id="accepted-001",
        role="artifact",
        physical_path=physical,
        owner_fd=-1 if change == "owner" else fd,
        review_sha256="unreviewed" if change == "review" else "a" * 64,
        deadline_at=time.monotonic() + (-1 if change == "deadline" else 5),
    )
    before = conn.total_changes
    with authority.controller(conn, run_id=None, owner_fd=fd):
        with pytest.raises((ValueError, OSError, TimeoutError)):
            locations.bind(conn, **args)
    assert conn.total_changes == before


def test_changed_locator_is_rejected_after_review(aliased):
    conn, fd, original, physical, receipt = aliased
    with authority.controller(conn, run_id=None, owner_fd=fd):
        bind(aliased)
        conn.commit()
    physical.write_text("{}")
    with pytest.raises(ValueError, match="locator_changed"):
        locations.resolve(conn, original, receipt["detail_sha256"])
