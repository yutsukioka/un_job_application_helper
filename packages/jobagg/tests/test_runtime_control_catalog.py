import hashlib
import json
import sqlite3
import time

import pytest

from jobagg import runtime_control_catalog as catalog, runtime_authority as authority
from jobagg.remediation_worker import shared_owner


def test_checkpointed_all_root_import_retains_uncertain_claim_and_fences_it_before_fetch(tmp_path):
    owner = tmp_path / "owner.lock"
    root = tmp_path / "legacy-worker"
    root.mkdir()
    values = {
        "request.json": {"run_id": root.name},
        "worker_result.json": {"run_id": root.name, "worker_exit_code": -9},
        "state.json": {"run_id": root.name, "status": "process_failure", "process_groups": []},
    }
    controls = {}
    for name, value in values.items():
        path = root / name
        path.write_text(json.dumps(value))
        controls[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    manifest = {
        "schema": catalog.SCHEMA,
        "epoch": "epoch",
        "shared_owner": str(owner),
        "complete_control_root_inventory": True,
        "writers": [{"name": "fixture worker/manual/browser/recovery", "fenced": True}],
        "runs": [{"path": str(root), "run_id": root.name, "controls": controls}],
    }
    db = tmp_path / "worker.sqlite3"
    with shared_owner(owner) as fd, sqlite3.connect(db) as conn:
        catalog.prepare(conn, manifest=manifest, owner_fd=fd, owner_path=owner)
        with pytest.raises(ValueError, match="incomplete"):
            catalog.receipt(conn)
        assert catalog.step(
            conn,
            owner_fd=fd,
            owner_path=owner,
            limit=1,
            deadline_at=time.monotonic() + 5,
            reviewed_implementations=set(),
        )["complete"]
        conn.commit()
        before = catalog.receipt(conn)
        assert (
            catalog.step(
                conn,
                owner_fd=fd,
                owner_path=owner,
                limit=1,
                deadline_at=time.monotonic() + 5,
                reviewed_implementations=set(),
            )["processed"]
            == 0
        )
        assert catalog.receipt(conn) == before
        unresolved = catalog.oldest_unresolved(conn)
        assert (
            unresolved["run_id"] == root.name
            and unresolved["disposition"] == "worker_claim_uncertain"
        )
        conn.execute("CREATE TABLE jobs(job_key TEXT PRIMARY KEY)")
        authority.activate(
            conn,
            epoch="epoch",
            owner_fd=fd,
            owner_path=owner,
            bootstrap_receipt={
                "reconciled": True,
                "source_sha256": "a" * 64,
                "unresolved_runs": [root.name],
                "control_catalogue_sha256": before["sha256"],
            },
        )
    with sqlite3.connect(db) as conn:
        assert catalog.oldest_unresolved(conn)["run_id"] == root.name
        with pytest.raises(sqlite3.OperationalError):
            conn.execute("UPDATE jobagg_legacy_controls SET disposition='terminal_verified'")
    assert all((root / name).exists() for name in values)


def test_changed_inventory_or_missing_control_never_becomes_empty(tmp_path):
    owner = tmp_path / "owner.lock"
    manifest = {
        "schema": catalog.SCHEMA,
        "epoch": "epoch",
        "shared_owner": str(owner),
        "complete_control_root_inventory": True,
        "writers": [{"name": "reviewed fixture", "fenced": True}],
        "runs": [],
    }
    with shared_owner(owner) as fd, sqlite3.connect(":memory:") as conn:
        catalog.prepare(conn, manifest=manifest, owner_fd=fd, owner_path=owner)
        with pytest.raises(ValueError, match="inventory_changed"):
            catalog.prepare(
                conn, manifest={**manifest, "epoch": "changed"}, owner_fd=fd, owner_path=owner
            )
        assert catalog.step(
            conn,
            owner_fd=fd,
            owner_path=owner,
            limit=1,
            deadline_at=time.monotonic() + 5,
            reviewed_implementations=set(),
        )["complete"]
        assert catalog.oldest_unresolved(conn) is None


def test_new_worker_run_does_not_inherit_previous_atomic_or_projection_controls(tmp_path):
    owner = tmp_path / "owner.lock"
    with shared_owner(owner) as fd, sqlite3.connect(tmp_path / "source.sqlite3") as conn:
        conn.execute("CREATE TABLE jobs(job_key TEXT PRIMARY KEY)")
        authority.activate(
            conn,
            epoch="test",
            owner_fd=fd,
            owner_path=owner,
            bootstrap_receipt={"reconciled": True, "source_sha256": "a" * 64},
        )
        conn.execute(
            "UPDATE jobagg_runtime_state SET control_json=?",
            (
                json.dumps(
                    {
                        "publication_only": True,
                        "proof_files": {"worker_result": "old"},
                        "reusable_projection": {"path": "old"},
                        "accepted_root": "old",
                        "atomic_recovery": {"run_id": "old"},
                        "publication_cursor": {"kept": True},
                    }
                ),
            ),
        )
        authority.begin(conn, "new", "config", control_dir=tmp_path / "new")
        control = authority.publication_control(conn)
        assert control == {
            "publication_cursor": {"kept": True},
            "publication_sequence": 0,
            "publication_control_dir": str(tmp_path / "new"),
            "publication_scope": None,
        }
