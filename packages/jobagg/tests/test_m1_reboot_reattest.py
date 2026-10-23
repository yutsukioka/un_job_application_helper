"""Explicit copy-only renewal never approves a non-device identity change."""

import importlib.util
from pathlib import Path
import sqlite3

import pytest

from jobagg import publication_fence as fence
from jobagg.db import JobDatabase
from jobagg.remediation_worker import shared_owner


@pytest.fixture
def renewal(monkeypatch):
    scripts = Path(__file__).resolve().parents[1] / "scripts"
    monkeypatch.syspath_prepend(str(scripts))
    spec = importlib.util.spec_from_file_location(
        "tested_reboot_renewal", scripts / "m1_reboot_reattest.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("changed", ["inode", "size", "mtime_ns", "ctime_ns", "device"])
def test_only_exact_device_change_on_reviewed_mount_is_admissible(renewal, tmp_path, changed):
    path = tmp_path / "evidence"
    path.write_text("retained")
    original = dict(device=1, inode=2, size=3, mtime_ns=4, ctime_ns=5)
    current = {**original, "device": 6}
    assert (
        renewal.renew_identity(
            path, original, current, mount_root=tmp_path, old_device=1, new_device=6
        )
        == current
    )
    current[changed] += 10
    with pytest.raises(ValueError, match="non_device_change"):
        renewal.renew_identity(
            path, original, current, mount_root=tmp_path, old_device=1, new_device=6
        )
    with pytest.raises(ValueError, match="non_device_change"):
        renewal.renew_identity(
            path,
            original,
            {**original, "device": 6},
            mount_root=tmp_path / "other",
            old_device=1,
            new_device=6,
        )
    alias = tmp_path / "alias"
    alias.symlink_to(path)
    with pytest.raises(ValueError, match="aliased"):
        renewal.renew_identity(
            alias,
            original,
            {**original, "device": 6},
            mount_root=tmp_path,
            old_device=1,
            new_device=6,
        )


def test_destination_renewal_is_idempotent_and_preserves_store_clock_and_rows(renewal, tmp_path):
    path = tmp_path / "target.sqlite3"
    owner_path = tmp_path / "actual.lock"
    database = JobDatabase(path)
    database.initialize()
    with shared_owner(owner_path) as fd:
        with database.connect() as conn:
            fence.prepare_target_schema(conn)
            fence.activate(conn, owner_path=owner_path, owner_fd=fd, epoch="retained-epoch")
        current = fence.binding(path)
        old_device = current["device"] + 100
        # Synthetic pre-reboot owner metadata; production never uses this setup.
        with sqlite3.connect(path) as conn:
            conn.create_function("jobagg_publication_control_allowed", 0, lambda: 1)
            conn.execute(f"UPDATE {fence.TABLE} SET owner_device=?", (old_device,))
        before = {**current, "device": old_device}
        for _ in range(2):
            renewal.renew_destination(
                path,
                before,
                owner_fd=fd,
                owner_path=owner_path,
                old_device=old_device,
                new_device=current["device"],
            )
            assert fence.binding(path) == current
        with database.connect() as conn:
            assert conn.execute("SELECT count(*) FROM jobs").fetchone()[0] == 0
            state = fence._validate(conn)
            assert state["epoch"] == "retained-epoch" and state["revision"] == 0
        with shared_owner(tmp_path / "unrelated.lock") as unrelated:
            with pytest.raises(ValueError, match="owner"):
                renewal.renew_destination(
                    path,
                    before,
                    owner_fd=unrelated,
                    owner_path=owner_path,
                    old_device=old_device,
                    new_device=current["device"],
                )
        with pytest.raises(ValueError, match="binding_changed"):
            renewal.renew_destination(
                path,
                {**before, "revision": 1},
                owner_fd=fd,
                owner_path=owner_path,
                old_device=old_device,
                new_device=current["device"],
            )
