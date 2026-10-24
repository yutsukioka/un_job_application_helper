"""Synthetic additive pages: real owner, fence, producer and crash boundaries."""

import hashlib
import time
from pathlib import Path

import pytest

from jobagg import archive_migration as migration, historical_storage as h
from jobagg import storage_compression as storage
from jobagg.remediation_worker import shared_owner, implementation_hash

pytestmark = pytest.mark.skipif(
    not storage.zstd_available(), reason="Zstandard optional dependency"
)


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(h.dump(value))
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def page(tmp_path, monkeypatch):
    # Model reviewed capacity for these tiny synthetic artifacts. Hosted runners
    # can have less than the production 20 GiB floor even with no test buildup.
    # Explicit low-reserve cases override this model and must still be rejected.
    actual_usage = migration.shutil.disk_usage

    def reviewed_fixture_capacity(path):
        actual = actual_usage(path)
        extra = 64 * 1024**3
        return type(actual)(actual.total + extra, actual.used, actual.free + extra)

    monkeypatch.setattr(migration.shutil, "disk_usage", reviewed_fixture_capacity)
    root = tmp_path
    state, dispatcher, output = root / "publication", root / "dispatcher", root / "output"
    (dispatcher / "runs").mkdir(parents=True)
    output.mkdir()
    items = []
    for ordinal in range(1, 5):
        generation = state / "generations" / f"{ordinal:032x}"
        path = generation / "plan.json"
        digest = write(path, {"changes": [], "listing_frames": [], "ignored": "x" * 10000})
        producer = generation / "result.json"
        producer_sha = write(
            producer,
            {
                "generation_id": generation.name,
                "state": "complete",
                "status": "published",
                "database_transactions_complete": True,
                "readback_verified": True,
                "plan_path": str(path),
                "plan_sha256": digest,
                "completed_at": f"2026-10-{ordinal:02}T00:00:00+00:00",
            },
        )
        items.append(
            {
                "path": str(path),
                "sha256": digest,
                "size_bytes": path.stat().st_size,
                "kind": "plan",
                "producer_path": str(producer),
                "producer_sha256": producer_sha,
            }
        )
    write(
        output / ".jobagg-publication-state.json",
        {
            "state": "complete",
            "status": "published",
            "database_transactions_complete": True,
            "unresolved_publication": False,
            "generation_id": f"{4:032x}",
        },
    )
    owner, marker = root / "owner.lock", root / "maintenance"
    marker.write_text("synthetic operation-owned fence\n")
    marker_sha = hashlib.sha256(marker.read_bytes()).hexdigest()
    config = {
        "state_dir": str(dispatcher),
        "maintenance_file": str(marker),
        "shared_lock_path": str(owner),
        "publication_argv": ["publisher", "--state-dir", str(state), "--output-dir", str(output)],
    }
    config_path = root / "config.json"
    config_sha = write(config_path, config)
    dependency = root / "dependency-review.json"
    dependency_sha = write(
        dependency,
        {
            "schema": "historical-archive-dependency-review-1",
            "configuration_sha256": config_sha,
            "complete_writer_inventory": True,
            "reader_compatibility_verified": True,
            "protected_paths": [],
            "maintenance_sha256": marker_sha,
        },
    )
    scratch = root / "scratch"
    scratch.mkdir()
    with shared_owner(owner) as fd:
        review = migration.prepare(
            config_path,
            config_sha256=config_sha,
            root=root,
            artifacts=items[:2],
            dependency_review_path=dependency,
            dependency_review_sha256=dependency_sha,
            implementation_sha256=implementation_hash(),
            owner_fd=fd,
            deadline_at=time.monotonic() + 10,
        )
        review_path = root / "page.json"
        review_sha = write(review_path, review)
        yield {
            "root": root,
            "fd": fd,
            "marker": marker,
            "items": items,
            "scratch": scratch,
            "review": review,
            "path": review_path,
            "sha256": review_sha,
            "config": config,
        }


def execute(page, **kwargs):
    return migration.execute_page(
        page["path"],
        page["sha256"],
        owner_fd=page["fd"],
        scratch_parent=page["scratch"],
        deadline_at=time.monotonic() + 10,
        **kwargs,
    )


@pytest.mark.parametrize("stage", ["after_install", "after_grant", "after_checkpoint"])
def test_crash_resume_exact_additive_intent_without_headroom_or_lost_original(page, stage):
    def crash(actual):
        if actual == stage:
            raise RuntimeError("injected interruption")

    with pytest.raises(RuntimeError, match="injected"):
        execute(page, max_items=1, fault=crash)
    first = execute(page, max_items=1)
    final = execute(page, max_items=1)
    assert final["complete"] and final["completed"] == 2
    assert final["removed_bytes"] == 0 and not final["headroom_granted"]
    assert not final["production_readiness_granted"] and final["originals_retained"]
    assert first["processed"] <= 1
    for item in page["items"]:
        assert hashlib.sha256(Path(item["path"]).read_bytes()).hexdigest() == item["sha256"]
    assert list(page["scratch"].iterdir()) == []


@pytest.mark.parametrize(
    "damage",
    [
        "marker",
        "producer",
        "gate",
        "generation_inventory",
        "dependency",
        "implementation",
        "original",
        "low_reserve",
    ],
)
def test_changed_boundary_never_completes_page(page, monkeypatch, damage):
    if damage == "marker":
        page["marker"].write_text("another operator fence\n")
    elif damage == "producer":
        Path(page["items"][0]["producer_path"]).write_text("{}")
    elif damage == "gate":
        write(
            page["root"] / "output" / ".jobagg-publication-state.json",
            {"unresolved_publication": True},
        )
    elif damage == "generation_inventory":
        (page["root"] / "publication" / "generations" / ("f" * 32)).mkdir()
    elif damage == "dependency":
        Path(page["review"]["dependency_review_path"]).write_text("{}")
    elif damage == "implementation":
        monkeypatch.setattr("jobagg.remediation_worker.implementation_hash", lambda: "0" * 64)
    elif damage == "original":
        Path(page["items"][0]["path"]).write_text("{}")
    elif damage == "low_reserve":
        monkeypatch.setattr(
            migration.shutil, "disk_usage", lambda _: type("Usage", (), {"free": 0})()
        )
    with pytest.raises(ValueError):
        execute(page)
    assert all(Path(item["path"]).exists() for item in page["items"])
    assert not list((page["root"] / ".jobagg-storage").rglob("*.complete.json"))


def test_current_previous_and_dispatcher_bound_generation_are_protected(page):
    protected = migration.protection(page["config"], deadline_at=time.monotonic() + 5)["protected"]
    assert str(Path(page["items"][3]["path"]).parent) in protected
    assert str(Path(page["items"][2]["path"]).parent) in protected
    bound = (
        page["root"]
        / "dispatcher"
        / "runs"
        / "retained-run"
        / "publication_generation_binding.json"
    )
    write(bound, {"generation_id": f"{1:032x}"})
    updated = migration.protection(page["config"], deadline_at=time.monotonic() + 5)["protected"]
    assert str(Path(page["items"][0]["path"]).parent) in updated
    with pytest.raises(ValueError, match="protected_boundary_changed"):
        execute(page)


def test_no_expired_or_unbounded_page(page):
    for deadline in (time.monotonic() - 1, float("inf"), time.monotonic() + 1000):
        with pytest.raises((ValueError, TimeoutError)):
            migration.execute_page(
                page["path"],
                page["sha256"],
                owner_fd=page["fd"],
                scratch_parent=page["scratch"],
                deadline_at=deadline,
            )
    assert not (page["root"] / ".jobagg-storage").exists()


@pytest.mark.parametrize("kind", ["beforeimage", "export", "snapshot"])
def test_other_artifact_families_require_their_original_producer_seal(page, kind):
    import sqlite3

    root = page["root"]
    generation = Path(page["items"][0]["path"]).parent
    if kind == "beforeimage":
        path = generation / "beforeimages" / "retained.json"
        logical = write(path, {"retained": "東京" * 2000})
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
    elif kind == "export":
        path = generation / "exports-v2" / "all_jobs_current.json"
        logical = write(path, {"description": "useful original text " * 1000})
        producer = generation / "export-checkpoints.json"
        producer_sha = write(
            producer,
            {
                "entries": {
                    "all_jobs_current.json": {
                        "phase": "replaced_verified",
                        "prepared": str(path),
                        "new": {"sha256": logical, "size": path.stat().st_size},
                    }
                }
            },
        )
    else:
        run = root / "dispatcher" / "runs" / "historical-run"
        run.mkdir()
        path = run / "publication_snapshot.sqlite3"
        with sqlite3.connect(path) as conn:
            conn.execute("CREATE TABLE retained(value TEXT)")
            conn.execute("INSERT INTO retained VALUES(?)", ("useful" * 1000,))
        logical = hashlib.sha256(path.read_bytes()).hexdigest()
        producer = run / "publication_request.json"
        producer_sha = write(
            producer,
            {
                "publication_snapshot": {
                    "path": str(path),
                    "sha256": logical,
                    "size_bytes": path.stat().st_size,
                }
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
    config_sha = page["review"]["configuration_sha256"]

    def prepare(selected):
        return migration.prepare(
            page["review"]["configuration_path"],
            config_sha256=config_sha,
            root=root,
            artifacts=[selected],
            dependency_review_path=page["review"]["dependency_review_path"],
            dependency_review_sha256=page["review"]["dependency_review_sha256"],
            implementation_sha256=implementation_hash(),
            owner_fd=page["fd"],
            deadline_at=time.monotonic() + 10,
        )

    with pytest.raises(ValueError):
        prepare({**item, "sha256": "f" * 64})
    review = prepare(item)
    review_path = root / (kind + "-page.json")
    reviewed = write(review_path, review)
    result = migration.execute_page(
        review_path,
        reviewed,
        owner_fd=page["fd"],
        scratch_parent=page["scratch"],
        deadline_at=time.monotonic() + 10,
    )
    assert result["complete"] and result["originals_retained"]
    checkpoint = result["checkpoints"][0]
    grant = h.load_grant(
        checkpoint["grant_path"], checkpoint["grant_sha256"], deadline_at=time.monotonic() + 5
    )
    assert grant.value["kind"] == kind
    assert hashlib.sha256(path.read_bytes()).hexdigest() == logical
