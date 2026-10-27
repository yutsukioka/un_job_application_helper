"""Explicit archive grants preserve original provenance and uncertainty."""

import fcntl
import hashlib
import json
from pathlib import Path
import sqlite3
import time

import pytest

from jobagg import historical_storage as h, storage_compression as storage
from jobagg import historical_plans as plans, historical_occurrences as occurrences
from jobagg import runtime_authority as authority, runtime_control_catalog as catalog
from jobagg.remediation_worker import shared_owner
from test_historical_occurrences import mapped, make_index, artifact  # noqa: F401
from test_live_publication import setup, add_detail, run, body  # noqa: F401

# ruff: noqa: F811
pytestmark = pytest.mark.skipif(
    not storage.zstd_available(), reason="Zstandard optional dependency"
)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def grant_for(root, path, owner, fd, *, kind="plan"):
    original, digest = h.identity(path), sha(path)
    storage.compress_artifact(
        root,
        path,
        codec="zstd",
        level=3,
        execute=True,
        shared_lock=owner,
        owner_fd=fd,
        min_bytes=0,
        retire_legacy=False,
        sealed_sqlite=kind == "snapshot",
        deadline_at=time.monotonic() + 10,
    )
    return h.seal_grant(
        root,
        path,
        kind=kind,
        expected_sha256=digest,
        expected_bytes=original[2],
        original_identity=original,
        review_sha256="a" * 64,
        shared_lock=owner,
        owner_fd=fd,
        deadline_at=time.monotonic() + 10,
    )


@pytest.mark.parametrize("compressed_only", [False, True])
def test_exact_unicode_plan_projection_dual_and_compressed_only(tmp_path, compressed_only):
    path, owner = tmp_path / "plan.json", tmp_path / "owner.lock"
    # Includes skipped Unicode and null/absent fields. Original bytes must hash.
    path.write_text(
        json.dumps(
            {
                "generation_id": "test",
                "changes": [],
                "listing_frames": [],
                "ignored": "東京 🦋" * 10000,
                "beforeimages": None,
            },
            ensure_ascii=False,
        )
    )
    expected, binding = plans.read_plan(
        path, expected_sha256=sha(path), deadline_at=time.monotonic() + 5, max_bytes=1024**2
    )
    with shared_owner(owner) as fd:
        grant = grant_for(tmp_path, path, owner, fd)
        if compressed_only:
            path.unlink()  # Synthetic fixture of an already reviewed handoff.
        actual, archived = plans.read_plan(
            path,
            expected_sha256=binding["sha256"],
            deadline_at=time.monotonic() + 5,
            max_bytes=1024**2,
            archive=grant,
        )
        assert actual == expected
        assert archived["identity"] == binding["identity"]
        assert archived["sha256"] == binding["sha256"]
        assert archived["bytes"] == binding["bytes"]
        # The storage layer must not close/unlock the actual borrowed owner.
        with owner.open("r+") as probe, pytest.raises(BlockingIOError):
            fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)


@pytest.mark.parametrize(
    "damage",
    [
        "original",
        "object",
        "manifest",
        "grant",
        "alias",
        "hash",
        "bound",
        "expiry",
        "partial",
        "codec",
    ],
)
def test_grant_never_accepts_changed_partial_aliased_or_expired_evidence(
    tmp_path, monkeypatch, damage
):
    path, owner = tmp_path / "plan.json", tmp_path / "owner.lock"
    path.write_text('{"changes":[],"ignored":"' + "x" * 100000 + '"}')
    with shared_owner(owner) as fd:
        grant = grant_for(tmp_path, path, owner, fd)
        target, expected, bound, deadline = (
            path,
            grant.value["logical_sha256"],
            h.MAX_ARTIFACT,
            time.monotonic() + 5,
        )
        if damage == "original":
            path.write_bytes(path.read_bytes().replace(b"x", b"y", 1))
        elif damage in {"object", "manifest", "grant"}:
            changed = {
                "object": grant.physical,
                "manifest": grant.manifest,
                "grant": Path(grant.control_path),
            }[damage]
            changed.chmod(0o600)
            changed.write_bytes(changed.read_bytes() + b"x")
        elif damage == "alias":
            target = tmp_path / "alias.json"
            target.symlink_to(path)
        elif damage == "hash":
            expected = "0" * 64
        elif damage == "bound":
            bound = 8
        elif damage == "expiry":
            deadline = time.monotonic() - 1
        elif damage == "codec":
            monkeypatch.setattr(storage, "zstd_available", lambda: False)
        with pytest.raises((ValueError, TimeoutError, storage.ArtifactStorageError)):
            with grant.open(
                target, expected_sha256=expected, deadline_at=deadline, max_bytes=bound
            ) as stream:
                stream.read(1 if damage == "partial" else -1)


def test_strict_duplicate_skipped_field_still_rejected_from_compressed_plan(tmp_path):
    path, owner = tmp_path / "plan.json", tmp_path / "owner.lock"
    path.write_text(
        '{"changes":[],"padding":"' + "x" * 10000 + '","ignored":{"duplicate":1,"duplicate":2}}'
    )
    with shared_owner(owner) as fd:
        grant = grant_for(tmp_path, path, owner, fd)
        path.unlink()
        with pytest.raises(ValueError, match="duplicate"):
            plans.read_plan(
                path,
                expected_sha256=grant.value["logical_sha256"],
                deadline_at=time.monotonic() + 5,
                max_bytes=1024**2,
                archive=grant,
            )


def test_snapshot_restore_verifies_seal_and_removes_scratch_on_exception(tmp_path):
    path, owner = tmp_path / "snapshot.sqlite3", tmp_path / "owner.lock"
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE values_kept(value TEXT)")
        conn.execute("INSERT INTO values_kept VALUES('東京')")
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    with shared_owner(owner) as fd:
        grant = grant_for(tmp_path, path, owner, fd, kind="snapshot")
        path.unlink()
        with pytest.raises(RuntimeError, match="consumer"):
            with grant.restore_snapshot(
                scratch_parent=scratch, deadline_at=time.monotonic() + 5, reserve_bytes=0
            ) as restored:
                with sqlite3.connect(restored.path) as conn:
                    assert conn.execute("SELECT value FROM values_kept").fetchone()[0] == "東京"
                raise RuntimeError("consumer interrupted")
        assert list(scratch.iterdir()) == []
        with pytest.raises(TimeoutError):
            with grant.restore_snapshot(
                scratch_parent=scratch, deadline_at=time.monotonic() - 1, reserve_bytes=0
            ):
                pytest.fail("expired restore admitted")


def test_occurrence_index_reopens_and_builds_with_original_compressed_only_plan(mapped):
    old = make_index(mapped)
    root, path = mapped["setup"]["root"], mapped["plan"]
    grant = grant_for(root, path, mapped["owner"], mapped["fd"])
    digest = grant.value["logical_sha256"]
    path.unlink()
    with pytest.raises((ValueError, FileNotFoundError)):
        occurrences.Index(
            old.path,
            expected_sha256=old.sha256,
            mapping=mapped["mapping"],
            deadline_at=time.monotonic() + 5,
        )
    archives = {str(path): grant}
    index = occurrences.Index(
        old.path,
        expected_sha256=old.sha256,
        mapping=mapped["mapping"],
        deadline_at=time.monotonic() + 5,
        archives=archives,
    )
    row, value, evidence_sha = artifact(mapped)
    assert index.matches(
        row,
        value,
        artifact_sha256=evidence_sha,
        destinations=mapped["targets"],
        deadline_at=time.monotonic() + 5,
    )
    reference = {
        "plan_path": str(path),
        "plan_sha256": digest,
        "result_path": str(mapped["result"]),
        "result_sha256": sha(mapped["result"]),
    }
    rebuilt = root / "rebuilt.sqlite3"
    with sqlite3.connect(rebuilt) as conn:
        occurrences.prepare(
            conn, references=[reference], mapping_sha256=mapped["receipt"]["sha256"]
        )
        assert occurrences.step(
            conn,
            mapping=mapped["mapping"],
            limit=1,
            deadline_at=time.monotonic() + 5,
            max_plan_bytes=1024**2,
            archives=archives,
        )["complete"]
    rebuilt.chmod(0o400)
    rebuilt_index = occurrences.Index(
        rebuilt,
        expected_sha256=sha(rebuilt),
        mapping=mapped["mapping"],
        deadline_at=time.monotonic() + 5,
        archives=archives,
    )
    assert rebuilt_index.matches(
        row,
        value,
        artifact_sha256=evidence_sha,
        destinations=mapped["targets"],
        deadline_at=time.monotonic() + 5,
    )


def test_owner_catalogue_attestation_keeps_original_uncertain_proof_and_dependency(tmp_path):
    owner, root = tmp_path / "owner.lock", tmp_path / "legacy-worker"
    root.mkdir()
    values = {
        "request.json": {"run_id": root.name, "padding": "x" * 10000},
        "worker_result.json": {"run_id": root.name, "worker_exit_code": -9},
        "state.json": {"run_id": root.name, "status": "process_failure", "process_groups": []},
    }
    for name, value in values.items():
        (root / name).write_text(json.dumps(value))
    controls = {name: sha(root / name) for name in values}
    manifest = {
        "schema": catalog.SCHEMA,
        "epoch": "test",
        "shared_owner": str(owner),
        "complete_control_root_inventory": True,
        "writers": [{"name": "fixture", "fenced": True}],
        "runs": [{"path": str(root), "run_id": root.name, "controls": controls}],
    }
    with shared_owner(owner) as fd, sqlite3.connect(tmp_path / "source.sqlite3") as conn:
        catalog.prepare(conn, manifest=manifest, owner_fd=fd, owner_path=owner)
        catalog.step(
            conn,
            owner_fd=fd,
            owner_path=owner,
            limit=1,
            deadline_at=time.monotonic() + 5,
            reviewed_implementations=set(),
        )
        imported = catalog.receipt(conn)
        before = conn.execute("SELECT * FROM jobagg_legacy_controls").fetchall()
        dependencies = conn.execute(
            "SELECT * FROM jobagg_legacy_dependencies ORDER BY path"
        ).fetchall()
        conn.execute("CREATE TABLE jobs(job_key TEXT PRIMARY KEY)")
        authority.activate(
            conn,
            epoch="test",
            owner_fd=fd,
            owner_path=owner,
            bootstrap_receipt={
                "reconciled": True,
                "source_sha256": "a" * 64,
                "unresolved_runs": [root.name],
                "control_catalogue_sha256": imported["sha256"],
            },
        )
        path = root / "request.json"
        grant = grant_for(tmp_path, path, owner, fd, kind="export")
        state = authority.validate(conn)
        with authority.controller(conn, run_id=state["run_id"], owner_fd=fd):
            h.attest_catalogue(
                conn, grant, owner_path=owner, owner_fd=fd, deadline_at=time.monotonic() + 5
            )
            conn.commit()
        path.unlink()  # Synthetic compressed-only dependency, original row unchanged.
        with catalog.read_archived_dependency(
            conn,
            path,
            expected_sha256=controls[path.name],
            deadline_at=time.monotonic() + 5,
            max_bytes=1024**2,
        ) as stream:
            assert json.load(stream) == values[path.name]
        assert conn.execute("SELECT * FROM jobagg_legacy_controls").fetchall() == before
        assert (
            conn.execute("SELECT * FROM jobagg_legacy_dependencies ORDER BY path").fetchall()
            == dependencies
        )
        assert catalog.oldest_unresolved(conn)["disposition"] == "worker_claim_uncertain"
        with pytest.raises(ValueError, match="binding_changed"):
            h.catalogue_grant(
                conn, path, expected_sha256="0" * 64, deadline_at=time.monotonic() + 5
            )


def test_interrupted_publication_recovers_with_older_compressed_only_history(setup):
    owner = setup["root"] / "owner.lock"
    add_detail(setup)
    oldest = run(setup, execute=True)
    for index in (2, 3):
        add_detail(
            setup,
            description=body("Changed" + str(index)),
            observed=f"2025-09-{14 + index}T12:00:00+00:00",
        )
        assert run(setup, execute=True)["status"] == "published"
    path = Path(oldest["plan_path"])
    with shared_owner(owner) as fd:
        grant = grant_for(setup["root"], path, owner, fd)
        path.unlink()
        add_detail(
            setup,
            description=body("Newest accepted complete description"),
            observed="2025-09-20T12:00:00+00:00",
        )

        def fail(stage, destination):
            if stage == "after_database_commit":
                raise RuntimeError("interrupted new publication")

        with pytest.raises(RuntimeError, match="interrupted"):
            run(setup, execute=True, fault=fail)
        recovered = run(setup, execute=True)
        assert recovered["status"] == "published"
        grant.verify(deadline_at=time.monotonic() + 5)
        with sqlite3.connect(setup["output"] / "test_jobs.sqlite3") as conn:
            assert conn.execute("SELECT count(*) FROM vacancy_snapshots").fetchone()[0] == 5


def test_expiry_inside_archive_parse_cannot_return_a_projection(tmp_path, monkeypatch):
    path, owner = tmp_path / "plan.json", tmp_path / "owner.lock"
    path.write_text(json.dumps({"changes": [], "ignored": "x" * 200000}))
    with shared_owner(owner) as fd:
        grant = grant_for(tmp_path, path, owner, fd)
        path.unlink()
        clock = [time.monotonic()]
        deadline = clock[0] + 5
        real_read = h._Raw.readinto

        def expire_after_chunk(self, buffer):
            count = real_read(self, buffer)
            if count:
                clock[0] = deadline
            return count

        with monkeypatch.context() as patch:
            patch.setattr(h.time, "monotonic", lambda: clock[0])
            patch.setattr(h._Raw, "readinto", expire_after_chunk)
            with pytest.raises(TimeoutError):
                plans.read_plan(
                    path,
                    expected_sha256=grant.value["logical_sha256"],
                    deadline_at=deadline,
                    max_bytes=1024**2,
                    archive=grant,
                )


def test_expiry_during_snapshot_staging_cleans_private_scratch(tmp_path, monkeypatch):
    path, owner = tmp_path / "snapshot.sqlite3", tmp_path / "owner.lock"
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE retained(value TEXT)")
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    with shared_owner(owner) as fd:
        grant = grant_for(tmp_path, path, owner, fd, kind="snapshot")
        clock = [time.monotonic()]
        deadline = clock[0] + 5
        real_copy = storage.copy_artifact

        def expire_copy(*args, **kwargs):
            clock[0] = deadline
            return real_copy(*args, **kwargs)

        with monkeypatch.context() as patch:
            patch.setattr(storage.time, "monotonic", lambda: clock[0])
            patch.setattr(storage, "copy_artifact", expire_copy)
            with pytest.raises(TimeoutError):
                with grant.restore_snapshot(
                    scratch_parent=scratch, deadline_at=deadline, reserve_bytes=0
                ):
                    pytest.fail("expired restore returned")
        assert list(scratch.iterdir()) == []


def test_archived_plan_retains_projection_memory_bound(tmp_path):
    path, owner = tmp_path / "plan.json", tmp_path / "owner.lock"
    path.write_text(json.dumps({"generation_id": "x" * 10000, "changes": []}))
    with shared_owner(owner) as fd:
        grant = grant_for(tmp_path, path, owner, fd)
        with pytest.raises(ValueError, match="retained|memory"):
            plans.read_plan(
                path,
                expected_sha256=grant.value["logical_sha256"],
                deadline_at=time.monotonic() + 5,
                max_bytes=1024**2,
                retained_bytes=16,
                archive=grant,
            )
