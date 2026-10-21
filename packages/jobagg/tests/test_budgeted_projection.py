"""Sealed projection recovery uses real bounded SQLite and durable escrow."""

import fcntl
import hashlib
import json
import os
import sqlite3
from pathlib import Path

import pytest
from test_storage_budget import fixture_config

from jobagg import publication_projection
from jobagg import publication_snapshot as snapshot
from jobagg.dispatcher_storage import controller_scope
from jobagg.storage_budget import (
    CATEGORY_LIMITS,
    CONTROL_ENVELOPE_BYTES,
    PROJECTION_CONTROL_BYTES,
    TOTAL_LIMIT,
    BudgetLedger,
    OperationReservation,
    StorageBudgetDeferred,
    StorageBudgetError,
    assess,
    inventory,
)


@pytest.fixture
def bounded(tmp_path):
    config = fixture_config(tmp_path)
    source = tmp_path / "essential" / "worker.sqlite3"
    with sqlite3.connect(source) as connection:
        connection.executescript("CREATE TABLE jobs(job_key TEXT PRIMARY KEY, description TEXT);"
                                 "CREATE TABLE remediation_observations(job_key TEXT PRIMARY KEY);")
        connection.execute("INSERT INTO jobs VALUES('accepted','preserved main description')")
        connection.execute("INSERT INTO remediation_observations VALUES('accepted')")
    connection.close()
    observed = inventory(config)
    envelope = {**dict.fromkeys(CATEGORY_LIMITS, 0), "work": 2 * 1024**2,
                "diagnostics": CONTROL_ENVELOPE_BYTES + 100_000}
    admission = assess(config, observed, projected_growth=envelope,
                       physical_free_bytes={device: TOTAL_LIMIT * 2 for device in observed.device_charged_bytes})
    ledger = BudgetLedger.create(admission, cycle_id="accepted-original")
    ledger.transition("worker_admitted", "accepted_pending")
    target = tmp_path / "work" / "accepted-original" / "publication_snapshot.sqlite3"
    lock = tmp_path / "diagnostics" / "owner.lock"

    def create(*, maximum=1024**2, acceptance="a" * 64):
        with lock.open("a+") as owner, controller_scope(ledger):
            fcntl.flock(owner, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return snapshot.create_publication_snapshot(
                source, target, acceptance, owner.fileno(), lock,
                projection=True, budget=ledger, max_bytes=maximum)

    return {"source": source, "target": target, "ledger": ledger, "create": create,
            "receipt": target.with_name(target.name + ".sealed.json")}


def test_completed_projection_is_adopted_after_pending_marker_crash(bounded, monkeypatch):
    first = bounded["create"]()
    target, ledger = bounded["target"], bounded["ledger"]
    identity, before = target.stat(), ledger.snapshot()
    assert not before["operations"]
    assert len(before["completions"]) == 1
    proof = next(iter(before["completions"].values()))
    assert proof["proof_sha256"] == hashlib.sha256(bounded["receipt"].read_bytes()).hexdigest()
    assert proof["maximum"] == proof["consumed"] == before["settled"]["work"] == 1024**2
    monkeypatch.setattr(publication_projection, "build_projection", lambda *a, **k: pytest.fail("must not rebuild accepted work"))
    assert bounded["create"]() == first
    assert target.stat().st_ino == identity.st_ino
    assert ledger.snapshot() == before
    with sqlite3.connect(target.as_uri() + "?mode=ro", uri=True) as readback:
        assert readback.execute("SELECT description FROM jobs").fetchone()[0] == "preserved main description"
    readback.close()


@pytest.mark.parametrize("boundary", ["receipt", "link", "before_settle", "after_settle"])
def test_crash_at_each_sealed_boundary_resumes_original_grant(bounded, monkeypatch, boundary):
    if boundary == "receipt":
        original = snapshot._write_projection_receipt
        def interrupted(*args):
            original(*args)
            raise SystemExit("after durable receipt")
        monkeypatch.setattr(snapshot, "_write_projection_receipt", interrupted)
    elif boundary == "link":
        monkeypatch.setattr(snapshot, "_sync_snapshot_directory", lambda *args: (_ for _ in ()).throw(SystemExit("after link")))
    else:
        original = OperationReservation.settle
        def interrupted(self, *args, **kwargs):
            if boundary == "after_settle":
                original(self, *args, **kwargs)
            raise SystemExit("settlement boundary")
        monkeypatch.setattr(OperationReservation, "settle", interrupted)
    with pytest.raises(SystemExit):
        bounded["create"]()
    state = bounded["ledger"].snapshot()
    assert len(state["operations"] or state["completions"]) == 1
    assert next(iter((state["operations"] or state["completions"]).values()))["consumed"] == 1024**2
    if boundary == "receipt":
        assert not bounded["target"].exists()
    else:
        assert bounded["target"].exists()
    monkeypatch.undo()
    monkeypatch.setattr(publication_projection, "build_projection", lambda *a, **k: pytest.fail("must adopt exact sealed inode"))
    result = bounded["create"]()
    assert result["sha256"] == hashlib.sha256(bounded["target"].read_bytes()).hexdigest()
    assert bounded["target"].stat().st_nlink == 1
    assert not list(bounded["target"].parent.glob(".*.sqlite3"))
    final = bounded["ledger"].snapshot()
    assert not final["operations"] and len(final["completions"]) == 1
    assert final["settled"]["work"] == 1024**2


@pytest.mark.parametrize("change", ["payload", "replaced_inode", "sidecar", "alias", "receipt", "acceptance", "limit", "missing_receipt"])
def test_existing_projection_requires_exact_receipt_payload_and_grant(bounded, change):
    bounded["create"]()
    target, receipt = bounded["target"], bounded["receipt"]
    before = bounded["ledger"].snapshot()
    kwargs = {}
    if change == "payload":
        os.chmod(target, 0o644)
        with target.open("r+b") as handle:
            handle.seek(100)
            handle.write(b"changed bytes")
        os.chmod(target, 0o444)
    elif change == "replaced_inode":
        replacement = target.with_name("replacement")
        replacement.write_bytes(target.read_bytes())
        os.chmod(replacement, 0o444)
        os.replace(replacement, target)
    elif change == "sidecar":
        Path(str(target) + "-wal").touch()
    elif change == "alias":
        os.link(target, target.with_name("unrelated-alias"))
    elif change == "receipt":
        value = json.loads(receipt.read_bytes())
        value["binding"]["cycle_id"] = "foreign-cycle"
        receipt.write_text(json.dumps(value))
    elif change == "acceptance":
        kwargs["acceptance"] = "b" * 64
    elif change == "limit":
        kwargs["maximum"] = 1024**2 + 4096
    else:
        receipt.unlink()
    with pytest.raises((ValueError, StorageBudgetError)):
        bounded["create"](**kwargs)
    assert bounded["ledger"].snapshot() == before


def test_unproven_target_never_creates_or_refunds_an_operation(bounded):
    bounded["target"].parent.mkdir()
    bounded["target"].write_bytes(b"unique unproven bytes")
    before = bounded["ledger"].snapshot()
    with pytest.raises(StorageBudgetDeferred, match="unsealed_artifact"):
        bounded["create"]()
    assert bounded["target"].read_bytes() == b"unique unproven bytes"
    assert bounded["ledger"].snapshot() == before


def test_unsealed_interruption_keeps_full_grant_and_partial_file(bounded, monkeypatch):
    def interrupted(origin, destination, **kwargs):
        destination.execute("CREATE TABLE partial(value TEXT)")
        destination.commit()
        raise SystemExit("before proof")
    monkeypatch.setattr(publication_projection, "build_projection", interrupted)
    with pytest.raises(SystemExit):
        bounded["create"]()
    before = bounded["ledger"].snapshot()
    partial = list(bounded["target"].parent.glob(".*.sqlite3"))
    assert len(partial) == 1 and partial[0].stat().st_size > 0
    assert not bounded["receipt"].exists() and not bounded["target"].exists()
    assert not before["completions"] and before["settled"]["work"] == 0
    assert next(iter(before["operations"].values()))["consumed"] == 1024**2
    with pytest.raises(StorageBudgetDeferred, match="unsealed_artifact_requires_review"):
        bounded["create"]()
    assert bounded["ledger"].snapshot() == before
    assert partial[0].exists()


@pytest.mark.parametrize("maximum", [None, True, float("inf"), 32768, PROJECTION_CONTROL_BYTES + 8191])
def test_projection_limit_includes_receipt_and_rejects_before_writes(bounded, maximum):
    before = bounded["ledger"].snapshot()
    with pytest.raises(StorageBudgetError, match="Finite projection"):
        bounded["create"](maximum=maximum)
    assert not bounded["target"].parent.exists()
    assert bounded["ledger"].snapshot() == before


def test_sqlite_full_is_storage_deferral_with_accepted_rows_and_charge_preserved(bounded):
    with sqlite3.connect(bounded["source"]) as conn:
        conn.execute("UPDATE jobs SET description=?", ("accepted main text" * 100_000,))
    conn.close()
    maximum = PROJECTION_CONTROL_BYTES + 2 * 32768
    with pytest.raises(StorageBudgetDeferred, match="projection_limit_exceeded"):
        bounded["create"](maximum=maximum)
    state = bounded["ledger"].snapshot()
    assert not bounded["target"].exists() and not state["completions"]
    assert next(iter(state["operations"].values()))["consumed"] == maximum
    with sqlite3.connect(bounded["source"].as_uri() + "?mode=ro", uri=True) as conn:
        assert conn.execute("SELECT length(description) FROM jobs").fetchone()[0] == 1_800_000
    conn.close()
