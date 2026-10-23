"""Current full-export proof from real fenced stores; never inferred history."""

import json
import sqlite3
import time

import pytest

from jobagg import publication_bootstrap as bootstrap, publication_fence as fence
from jobagg.db import JobDatabase
from jobagg.remediation_worker import shared_owner
from test_live_publication import setup as live_setup


@pytest.fixture
def prepared(tmp_path):
    fixture = live_setup.__wrapped__(tmp_path)
    owner = tmp_path / "owner.lock"
    with shared_owner(owner) as fd:
        for path in fence.publication_scope(fixture["registry"], fixture["output"])["databases"]:
            with JobDatabase(path).connect() as conn:
                fence.prepare_target_schema(conn)
                fence.activate(conn, owner_path=owner, owner_fd=fd, epoch="test")
        yield fixture, owner, fd


def prepare(fixture, owner, fd, **limits):
    root = fixture["root"] / "bootstrap"
    bootstrap.prepare(
        root,
        registry=fixture["registry"],
        output_root=fixture["output"],
        owner_fd=fd,
        owner_path=owner,
        max_export_bytes=limits.get("maximum", 10_000_000),
        max_total_export_bytes=limits.get("total", 100_000_000),
        deadline_at=time.monotonic() + 10,
    )
    return root


def test_partial_export_bootstrap_resumes_and_certifies_all_eight_exports(prepared):
    fixture, owner, fd = prepared
    root = prepare(fixture, owner, fd)
    first = bootstrap.step(
        root, owner_fd=fd, owner_path=owner, limit=1, deadline_at=time.monotonic() + 10
    )
    assert first["after"] == 1 and not first["complete"] and "boundary" not in first
    initial = first["page_boundaries"][0]
    result = bootstrap.step(
        root, owner_fd=fd, owner_path=owner, limit=1, deadline_at=time.monotonic() + 10
    )
    assert result["complete"] and len(result["boundary"]["exports"]) == 8
    assert result["page_boundaries"][0] == initial
    assert fence.verify_boundary(result["boundary"], expected_scope=result["contract"]["scope"])
    assert (
        bootstrap.step(
            root, owner_fd=fd, owner_path=owner, limit=1, deadline_at=time.monotonic() + 10
        )
        == result
    )
    assert not any(
        key in result
        for key in ("accepted_attempts", "pending_backlog_empty", "activation_reconciled")
    )


@pytest.mark.parametrize("changed", ["database", "export", "receipt"])
def test_changed_prior_page_cannot_certify_full_scope(prepared, changed):
    fixture, owner, fd = prepared
    root = prepare(fixture, owner, fd)
    value = bootstrap.step(
        root, owner_fd=fd, owner_path=owner, limit=1, deadline_at=time.monotonic() + 10
    )
    if changed == "database":
        path = value["contract"]["stores"][0]["path"]
        with fence.writer_scope(fd, owner, fixture["output"]), JobDatabase(path).connect() as conn:
            conn.execute("UPDATE jobs SET title='newer work'")
    elif changed == "export":
        from pathlib import Path

        path = Path(value["page_boundaries"][0]["exports"][0]["path"])
        path.chmod(0o600)
        path.write_bytes(b"changed")
    else:
        key = next(iter(value["exports"]))
        value["exports"][key]["sha256"] = "a" * 64
        (root / "bootstrap.json").write_text(json.dumps(value))
    with pytest.raises(ValueError, match="changed"):
        bootstrap.step(
            root, owner_fd=fd, owner_path=owner, limit=1, deadline_at=time.monotonic() + 10
        )


def test_other_owner_and_oversize_or_expired_page_never_certify(prepared):
    fixture, owner, fd = prepared
    with shared_owner(fixture["root"] / "unrelated.lock") as other:
        with pytest.raises(ValueError, match="actual_destination_owner"):
            prepare(fixture, fixture["root"] / "unrelated.lock", other)
    root = prepare(fixture, owner, fd, maximum=2)
    with pytest.raises(TimeoutError):
        bootstrap.step(
            root, owner_fd=fd, owner_path=owner, limit=1, deadline_at=time.monotonic() - 1
        )
    with pytest.raises(ValueError, match="byte_limit"):
        bootstrap.step(
            root, owner_fd=fd, owner_path=owner, limit=1, deadline_at=time.monotonic() + 10
        )
    value = json.loads((root / "bootstrap.json").read_text())
    assert not value["complete"] and value["after"] == 0 and not value["exports"]
    assert not list(fixture["output"].glob("*.json"))


def test_export_budget_interrupts_inside_row_stream_before_replacement(prepared, monkeypatch):
    from jobagg.pipelines import publication_exports

    fixture, owner, fd = prepared
    root = prepare(fixture, owner, fd)
    original = publication_exports.export_jobs

    def render(*args, **kwargs):
        # Advance the inherited deadline after entering the actual exporter.
        monkeypatch.setattr("jobagg.verification_budget.expired", lambda _: True)
        original(*args, **kwargs)

    monkeypatch.setattr(publication_exports, "export_jobs", render)
    with pytest.raises(TimeoutError):
        bootstrap.step(
            root, owner_fd=fd, owner_path=owner, limit=1, deadline_at=time.monotonic() + 10
        )
    assert json.loads((root / "bootstrap.json").read_text())["after"] == 0
    with sqlite3.connect(fixture["output"] / "all_jobs.sqlite3") as conn:
        assert conn.execute("SELECT count(*) FROM jobs").fetchone()[0] == 1
