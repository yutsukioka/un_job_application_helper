"""Guarded copy-only removal, real archive reads and interrupted durable receipts."""

import hashlib
import json
import shutil
import sqlite3
import time
from pathlib import Path

import pytest

from jobagg import isolated_archive_removal as removal, historical_storage as h
from jobagg import historical_plans, storage_compression as storage, archive_migration as migration
from jobagg import runtime_authority as authority
from jobagg.remediation_worker import implementation_hash
from test_archive_migration import page, execute as install, write  # noqa: F401

# ruff: noqa: F811
pytestmark = pytest.mark.skipif(
    not storage.zstd_available(), reason="Zstandard optional dependency"
)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def family_page(page, kind):
    root = page["root"]
    generation = Path(page["items"][0]["path"]).parent
    if kind == "snapshot":
        run = root / "dispatcher" / "runs" / "historical-run"
        run.mkdir()
        path = run / "publication_snapshot.sqlite3"
        with sqlite3.connect(path) as conn:
            conn.execute("CREATE TABLE retained(value TEXT)")
            conn.execute("INSERT INTO retained VALUES(?)", ("retained text " * 1000,))
        logical = sha(path)
        producer = run / "publication_request.json"
        acceptance = run / "acceptance.json"
        accepted = write(acceptance, {"accepted": True})
        seal = {
            "schema_version": 1,
            "kind": "sqlite_publication_projection",
            "path": str(path),
            "sha256": logical,
            "size_bytes": path.stat().st_size,
            "worker_acceptance_sha256": accepted,
        }
        producer_sha = write(
            producer,
            {
                "run_id": run.name,
                "publication_snapshot": seal,
                "worker_database_files": [{"path": str(path), "sha256": logical}],
                "worker_acceptance_path": str(acceptance),
                "worker_acceptance_sha256": accepted,
            },
        )
        result = run / "publication_result.json"
        result_sha = write(
            result,
            {
                "run_id": run.name,
                "status": "noop",
                "publication_snapshot": seal,
                "publication_request_sha256": producer_sha,
                "publication": {},
            },
        )
        write(
            run / "outcome.json",
            {
                "run_id": run.name,
                "status": "complete",
                "publication_status": "noop",
                "publication_recovery_allowed": False,
                "publication_result_sha256": result_sha,
                "finished_at": "2026-10-01T00:00:00+00:00",
            },
        )
    elif kind == "export":
        path = generation / "exports-v2" / "all_jobs_current.json"
        logical = write(path, {"retained": "東京" * 4000})
        producer = generation / "export-checkpoints.json"
        producer_sha = write(
            producer,
            {
                "entries": {
                    "current": {
                        "phase": "replaced_verified",
                        "prepared": str(path),
                        "new": {"sha256": logical, "size": path.stat().st_size},
                    }
                }
            },
        )
    else:
        path = generation / "beforeimages" / "retained.json"
        logical = write(path, {"retained": "original description " * 1000})
        producer = generation / "plan.json"
        producer_sha = write(
            producer,
            {
                "changes": [
                    {
                        "destinations": [
                            {"beforeimage_path": str(path), "beforeimage_sha256": logical}
                        ]
                    }
                ]
            },
        )
    item = {
        "path": str(path),
        "kind": kind,
        "sha256": logical,
        "size_bytes": path.stat().st_size,
        "producer_path": str(producer),
        "producer_sha256": producer_sha,
    }
    old = page["review"]
    review = migration.prepare(
        old["configuration_path"],
        config_sha256=old["configuration_sha256"],
        root=root,
        artifacts=[item],
        dependency_review_path=old["dependency_review_path"],
        dependency_review_sha256=old["dependency_review_sha256"],
        implementation_sha256=implementation_hash(),
        owner_fd=page["fd"],
        deadline_at=time.monotonic() + 10,
    )
    control = root / (kind + "-additive-page.json")
    digest = write(control, review)
    return {**page, "review": review, "path": control, "sha256": digest, "items": [item]}


def native_page(page):
    database = page["root"] / "worker.sqlite3"
    with sqlite3.connect(database) as conn:
        conn.execute("CREATE TABLE jobs(job_key TEXT PRIMARY KEY)")
        authority.activate(
            conn,
            epoch="isolated-removal",
            owner_fd=page["fd"],
            owner_path=page["config"]["shared_lock_path"],
            bootstrap_receipt={"reconciled": True, "source_sha256": "a" * 64},
        )
    config = {**page["config"], "publication_worker_database": str(database)}
    config_sha = write(Path(page["review"]["configuration_path"]), config)
    dep_path = Path(page["review"]["dependency_review_path"])
    dependency = json.loads(dep_path.read_text())
    dependency["configuration_sha256"] = config_sha
    dep_sha = write(dep_path, dependency)
    old = page["review"]
    review = migration.prepare(
        old["configuration_path"],
        config_sha256=config_sha,
        root=page["root"],
        artifacts=page["items"][:2],
        dependency_review_path=dep_path,
        dependency_review_sha256=dep_sha,
        implementation_sha256=implementation_hash(),
        owner_fd=page["fd"],
        deadline_at=time.monotonic() + 10,
    )
    control = page["root"] / "native-additive-page.json"
    digest = write(control, review)
    return {**page, "review": review, "path": control, "sha256": digest, "config": config}


@pytest.fixture
def copied(page, monkeypatch, tmp_path_factory, request):
    monkeypatch.setattr(removal, "_drained", lambda _: {"matching_processes": 0})
    kind = getattr(request, "param", "plan")
    if kind == "native":
        page = native_page(page)
    elif kind != "plan":
        page = family_page(page, kind)
    assert install(page)["complete"]
    root = page["root"]
    # A separate retained source namespace. Copy payloads are byte-for-byte copies,
    # never hardlinks; its simulated production controls remain untouched.
    source = tmp_path_factory.mktemp("isolated-removal-source")
    marker, owner = source / "maintenance", source / "owner.lock"
    marker.write_text("retained source operator fence\n")
    owner.touch()
    state, output = source / "dispatcher", source / "output"
    state.mkdir()
    output.mkdir()
    publication = source / "publication"
    publication.mkdir()
    original_config = source / "dispatcher.json"
    original_sha = write(
        original_config,
        {
            "state_dir": str(state),
            "maintenance_file": str(marker),
            "shared_lock_path": str(owner),
            "publication_argv": [
                "publisher",
                "--state-dir",
                str(publication),
                "--output-dir",
                str(output),
            ],
        },
    )
    rows = []
    for item in page["review"]["items"]:
        path = Path(item["path"])
        origin = source / path.relative_to(root)
        origin.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, origin)
        rows.append(
            {
                "copy": str(path),
                "copy_identity": item["identity"],
                "source": str(origin),
                "source_identity": h.identity(origin),
                "sha256": item["sha256"],
            }
        )
    manifest = root / "isolated-copy.json"
    manifest_sha = write(
        manifest,
        {
            "schema": removal.COPY_SCHEMA,
            "snapshot_root": str(root),
            "copy_root_identity": removal._directory(root),
            "source_root": str(source),
            "source_root_identity": removal._directory(source),
            "complete": True,
            "copy_configuration_sha256": page["review"]["configuration_sha256"],
            "production_config": str(original_config),
            "production_config_sha256": original_sha,
            "production_fence": h.record(marker, deadline_at=time.monotonic() + 5, max_bytes=1024),
            "files": rows,
        },
    )
    value = removal.prepare(
        page["path"],
        page["sha256"],
        snapshot_path=manifest,
        snapshot_sha=manifest_sha,
        ordinals=list(range(len(page["review"]["items"]))),
        owner_fd=page["fd"],
        deadline_at=time.monotonic() + 10,
    )
    path = root / "removal-plan.json"
    digest = write(path, value)
    return {
        **page,
        "source_root": source,
        "source_marker": marker,
        "snapshot": manifest,
        "snapshot_sha": manifest_sha,
        "removal_plan": path,
        "removal_sha": digest,
        "selection": value,
    }


def execute(case, **kwargs):
    return removal.execute(
        case["removal_plan"],
        case["removal_sha"],
        owner_fd=case["fd"],
        scratch_parent=case["scratch"],
        deadline_at=time.monotonic() + 10,
        **kwargs,
    )


@pytest.mark.parametrize(
    "stage",
    [
        None,
        "after_intent",
        "before_removal",
        "after_removal",
        "after_removal_receipt",
        "after_checkpoint",
    ],
)
def test_same_plan_crash_recovery_preserves_source_and_exact_once_credit(copied, stage):
    before = {str(p): sha(p) for p in copied["source_root"].rglob("*") if p.is_file()}
    if stage:

        def crash(actual):
            if actual == stage:
                raise RuntimeError("interrupted copy removal")

        with pytest.raises(RuntimeError, match="interrupted"):
            execute(copied, max_items=1, fault=crash)
    first = execute(copied, max_items=1)
    assert first["next_index"] == 1
    final = execute(copied, start=1, max_items=1)
    assert final["complete"] and final["completed"] == 2
    expected = sum(i["size_bytes"] for i in copied["items"][:2])
    if stage == "after_removal":
        assert final["confirmed_removed_bytes"] == expected - copied["items"][0]["size_bytes"]
        assert final["uncredited_absent_bytes"] == copied["items"][0]["size_bytes"]
    else:
        assert (
            final["confirmed_removed_bytes"] == expected and final["uncredited_absent_bytes"] == 0
        )
    again = execute(copied, max_items=2)
    assert again["new_confirmed_removed_bytes"] == 0
    assert again["confirmed_removed_bytes"] == final["confirmed_removed_bytes"]
    assert not final["headroom_granted"] and not final["production_readiness_granted"]
    assert {str(p): sha(p) for p in copied["source_root"].rglob("*") if p.is_file()} == before
    for ordinal, item in enumerate(copied["review"]["items"]):
        assert not Path(item["path"]).exists()
        grant = removal._grant(copied["review"], copied["sha256"], ordinal, time.monotonic() + 5)
        value, record = historical_plans.read_plan(
            item["path"],
            expected_sha256=item["sha256"],
            deadline_at=time.monotonic() + 5,
            max_bytes=1024**2,
            archive=grant,
        )
        assert value["changes"] == [] and record["identity"] == item["identity"]
        assert storage.resolve_artifact(copied["root"], item["path"]).sha256 == item["sha256"]
    assert all(Path(i["path"]).exists() for i in copied["items"][2:])


@pytest.mark.parametrize(
    "defect",
    [
        "source",
        "copy",
        "hardlink",
        "production_fence",
        "copy_fence",
        "manifest",
        "object",
        "source_alias",
        "busy_writer",
        "low_reserve",
        "missing_without_intent",
    ],
)
def test_wrong_or_changed_copy_boundary_never_removes_original(copied, monkeypatch, defect):
    item = copied["items"][0]
    path = Path(item["path"])
    source = Path(copied["selection"]["items"][0]["copy"]["source"])
    grant = removal._grant(copied["review"], copied["sha256"], 0, time.monotonic() + 5)
    if defect == "source":
        source.write_text("changed source")
    elif defect == "copy":
        path.write_text("changed copy")
    elif defect == "hardlink":
        (path.parent / "retained-link").hardlink_to(path)
    elif defect == "production_fence":
        copied["source_marker"].write_text("another operator")
    elif defect == "copy_fence":
        copied["marker"].write_text("another operation")
    elif defect == "manifest":
        grant.manifest.chmod(0o600)
        grant.manifest.write_text("{}")
    elif defect == "object":
        grant.physical.chmod(0o600)
        grant.physical.write_bytes(b"truncated")
    elif defect == "source_alias":
        moved = source.with_suffix(".original")
        source.rename(moved)
        source.symlink_to(moved)
    elif defect == "busy_writer":
        monkeypatch.setattr(
            removal, "_drained", lambda _: (_ for _ in ()).throw(ValueError("active writer"))
        )
    elif defect == "low_reserve":
        monkeypatch.setattr(
            removal.shutil, "disk_usage", lambda _: type("Usage", (), {"free": 0})()
        )
    else:
        path.unlink()
    with pytest.raises((ValueError, FileNotFoundError)):
        execute(copied)
    if defect != "missing_without_intent":
        assert path.exists()
    assert Path(copied["items"][1]["path"]).exists()


def test_replaced_original_at_last_guard_is_not_unlinked(copied):
    path = Path(copied["items"][0]["path"])

    def replace(stage):
        if stage == "before_removal":
            path.write_text("replacement must survive")

    with pytest.raises(ValueError, match="changed_or_linked"):
        execute(copied, fault=replace)
    assert path.read_text() == "replacement must survive"


def test_missing_original_after_unlink_fsync_failure_has_no_invented_credit(copied, monkeypatch):
    path = Path(copied["items"][0]["path"])
    sync = storage._sync_dir

    def crash(directory):
        if directory == path.parent and not path.exists():
            raise OSError("directory fsync interrupted")
        return sync(directory)

    with monkeypatch.context() as patch:
        patch.setattr(storage, "_sync_dir", crash)
        with pytest.raises(OSError, match="fsync interrupted"):
            execute(copied, max_items=1)
    result = execute(copied, max_items=1)
    assert result["confirmed_removed_bytes"] == 0
    assert result["uncredited_absent_bytes"] == copied["items"][0]["size_bytes"]
    assert not path.exists()


@pytest.mark.parametrize(
    "defect",
    [
        "root_overlap",
        "source_inventory_escape",
        "configuration_overlap",
        "wrong_root_identity",
        "unmapped",
        "duplicate_copy",
    ],
)
def test_newly_reviewed_but_invalid_isolation_manifest_is_rejected(copied, defect):
    snapshot = json.loads(copied["snapshot"].read_text())
    if defect == "root_overlap":
        snapshot["source_root"] = str(copied["root"])
    elif defect == "source_inventory_escape":
        snapshot["files"][0]["source"] = str(copied["marker"])
    elif defect == "configuration_overlap":
        snapshot["production_config"] = copied["review"]["configuration_path"]
        snapshot["production_config_sha256"] = copied["review"]["configuration_sha256"]
    elif defect == "wrong_root_identity":
        snapshot["copy_root_identity"][1] += 1
    elif defect == "unmapped":
        snapshot["files"] = snapshot["files"][1:]
    else:
        snapshot["files"].append(snapshot["files"][0])
    path = copied["root"] / "wrong-isolation.json"
    digest = write(path, snapshot)
    with pytest.raises(ValueError):
        removal.prepare(
            copied["path"],
            copied["sha256"],
            snapshot_path=path,
            snapshot_sha=digest,
            ordinals=[0],
            owner_fd=copied["fd"],
            deadline_at=time.monotonic() + 10,
        )
    assert all(Path(item["path"]).exists() for item in copied["items"])


def test_damaged_old_receipt_stops_next_page_before_more_removal(copied):
    execute(copied, max_items=1)
    directory = (
        copied["root"] / storage.STORAGE_DIRECTORY / "isolated-removals" / copied["removal_sha"]
    )
    path = directory / "0000.removed.json"
    path.chmod(0o600)
    value = json.loads(path.read_text())
    value["removed_bytes"] += 1
    path.write_bytes(h.dump(value))
    with pytest.raises(ValueError, match="credit|receipt"):
        execute(copied, start=1)
    assert Path(copied["items"][1]["path"]).exists()


def test_byte_page_deadline_and_missing_codec_never_remove(copied, monkeypatch):
    result = execute(copied, max_bytes=1)
    assert result["visited"] == 0 and not result["complete"]
    with pytest.raises(TimeoutError):
        removal.execute(
            copied["removal_plan"],
            copied["removal_sha"],
            owner_fd=copied["fd"],
            scratch_parent=copied["scratch"],
            deadline_at=time.monotonic() - 1,
        )
    monkeypatch.setattr(
        storage,
        "_require_zstd",
        lambda: (_ for _ in ()).throw(storage.CompressionUnavailableError("codec missing")),
    )
    with pytest.raises(storage.ArtifactStorageError):
        execute(copied)
    assert all(Path(item["path"]).exists() for item in copied["items"])


@pytest.mark.parametrize("copied", ["beforeimage", "export", "snapshot"], indirect=True)
def test_each_producer_family_restores_exact_bytes_after_copy_only_removal(copied):
    item = copied["review"]["items"][0]
    before = Path(item["path"]).read_bytes()
    result = execute(copied)
    assert result["complete"] and result["confirmed_removed_bytes"] == len(before)
    grant = removal._grant(copied["review"], copied["sha256"], 0, time.monotonic() + 5)
    with grant.open(
        item["path"],
        expected_sha256=item["sha256"],
        deadline_at=time.monotonic() + 5,
        max_bytes=1024**2,
    ) as stream:
        assert stream.read() == before
    if item["kind"] == "snapshot":
        with grant.restore_snapshot(
            scratch_parent=copied["scratch"], deadline_at=time.monotonic() + 5, reserve_bytes=0
        ) as restored:
            with restored.open_readonly() as conn:
                assert (
                    conn.execute("SELECT value FROM retained").fetchone()[0]
                    == "retained text " * 1000
                )
    assert not Path(item["path"]).exists()
    assert Path(copied["selection"]["items"][0]["copy"]["source"]).read_bytes() == before
    assert list(copied["scratch"].iterdir()) == []


@pytest.mark.parametrize("copied", ["beforeimage", "export", "snapshot"], indirect=True)
def test_changed_producer_for_every_removal_family_is_held(copied):
    item = copied["review"]["items"][0]
    Path(item["producer_path"]).write_text("{}")
    with pytest.raises(ValueError):
        execute(copied)
    assert Path(item["path"]).exists()


def test_resume_requires_durable_absence_before_checkpoint(copied, monkeypatch):
    path = Path(copied["items"][0]["path"])

    def crash(stage):
        if stage == "after_removal":
            raise RuntimeError("lost receipt")

    with pytest.raises(RuntimeError):
        execute(copied, max_items=1, fault=crash)
    sync = storage._sync_dir

    def cannot_sync(directory):
        if directory == path.parent:
            raise OSError("absence not durable")
        return sync(directory)

    with monkeypatch.context() as patch:
        patch.setattr(storage, "_sync_dir", cannot_sync)
        with pytest.raises(OSError, match="not durable"):
            execute(copied, max_items=1)
    result = execute(copied, max_items=1)
    assert (
        result["confirmed_removed_bytes"] == 0
        and result["uncredited_absent_bytes"] == copied["items"][0]["size_bytes"]
    )


@pytest.mark.parametrize("copied", ["native"], indirect=True)
def test_idle_native_copy_removes_only_independent_inputs_without_mutating_authority(copied):
    database = Path(copied["config"]["publication_worker_database"])
    with sqlite3.connect(database) as conn:
        before = authority.validate(conn)
    result = execute(copied)
    assert result["complete"]
    with sqlite3.connect(database) as conn:
        assert authority.validate(conn) == before


@pytest.mark.parametrize("copied", ["native"], indirect=True)
def test_native_admitted_work_keeps_inputs_hot(copied):
    database = Path(copied["config"]["publication_worker_database"])
    with sqlite3.connect(database) as conn:
        with authority.controller(conn, run_id=None, owner_fd=copied["fd"]):
            authority.begin(
                conn, "accepted-copy-run", "config", control_dir=copied["root"] / "control-only"
            )
            conn.commit()
        before = authority.validate(conn)
    with pytest.raises(ValueError, match="not_idle"):
        execute(copied)
    with sqlite3.connect(database) as conn:
        assert authority.validate(conn) == before
    assert all(Path(item["path"]).exists() for item in copied["items"])


@pytest.mark.parametrize("copied", ["snapshot"], indirect=True)
def test_snapshot_recovery_evidence_becomes_held_if_terminal_controls_change(copied):
    run = Path(copied["items"][0]["path"]).parent
    value = json.loads((run / "outcome.json").read_text())
    value["publication_recovery_allowed"] = True
    write(run / "outcome.json", value)
    with pytest.raises(ValueError, match="unresolved_snapshot"):
        execute(copied)
    assert Path(copied["items"][0]["path"]).exists()
