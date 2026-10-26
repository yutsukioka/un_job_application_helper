"""Resume the real index builder after sealing, without reopening it for writes."""

import importlib.util
import json
import sqlite3
from pathlib import Path

import pytest
from test_historical_occurrences import make_index, mapped  # noqa: F401
from test_live_publication import setup  # noqa: F401
from test_wrapper_reconciliation_operator import prepare_copy

from jobagg import runtime_authority as authority
from jobagg.publication_snapshot import _stamp
from jobagg.remediation_worker import shared_owner

# ruff: noqa: F811


@pytest.fixture
def index_operation(tmp_path, monkeypatch):
    scripts = Path(__file__).resolve().parents[1] / "scripts"
    monkeypatch.syspath_prepend(str(scripts))
    spec = importlib.util.spec_from_file_location(
        "tested_index_operator", scripts / "m1_historical_index.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    root, review_path, production, marker = prepare_copy(tmp_path)
    generation = root / ("a" * 32)
    generation.mkdir()
    plan, result = generation / "plan.json", generation / "result.json"
    plan.write_text(
        json.dumps(
            {
                "version": "incremental-live-publication-1",
                "generation_id": generation.name,
                "generation_root": str(generation),
                "changes": [],
                "listing_frames": [],
            }
        )
    )
    result.write_text(
        json.dumps(
            {
                "version": "incremental-live-publication-1",
                "generation_id": generation.name,
                "plan_path": str(plan),
                "plan_sha256": module.sha(plan),
                "changed_jobs": 0,
                "published_listing_frames": 0,
                "status": "published",
                "state": "complete",
                "database_transactions_complete": True,
            }
        )
    )
    references = [
        {
            "plan_path": str(plan),
            "plan_sha256": module.sha(plan),
            "result_path": str(result),
            "result_sha256": module.sha(result),
        }
    ]
    review = json.loads(review_path.read_text())
    review.update(
        schema="reviewed-historical-generations-1", references=references, publication_scope={}
    )
    review_path.write_text(json.dumps(review))
    renewal = root / "pr143-reboot-renewal"
    renewal.mkdir()
    (renewal / "publication-boundary.json").write_text("{}")
    config = json.loads((tmp_path / "production-config.json").read_text())
    with (
        shared_owner(Path(config["shared_lock_path"])) as fd,
        sqlite3.connect(root / "worker/jobs.sqlite3") as conn,
        authority.controller(conn, run_id=None, owner_fd=fd),
    ):
        for path in (plan, result):
            conn.execute(
                "INSERT INTO jobagg_legacy_dependencies(path,sha256,identity_json) VALUES(?,?,?)",
                (str(path), module.sha(path), json.dumps(list(_stamp(path.stat())))),
            )
        conn.commit()

    # Empty producer fixture: destination mapping has no records to resolve.
    # The native owner/catalogue gates and real occurrence index remain active.
    mapping_receipt = {"sha256": "b" * 64}

    class Mapping:
        def __init__(self, *args, **kwargs):
            self.receipt = mapping_receipt

        def validate(self):
            pass

    monkeypatch.setattr(module.destinations, "prepare", lambda *a, **kw: mapping_receipt)
    monkeypatch.setattr(module.destinations, "Destinations", Mapping)
    operation = root / "index-operation"

    def execute(resume=False):
        return module.execute(
            root,
            operation,
            review_path,
            module.sha(review_path),
            limit=1,
            seconds=5,
            max_plan_bytes=1024**2,
            max_total_plan_bytes=1024**2,
            resume=resume,
        )

    return module, operation, execute, production, marker, review_path


def interrupt_receipt(case, monkeypatch):
    module, operation, execute, _, _, _ = case
    save = module.save

    def interrupted(path, value):
        if path.name == "index-receipt.json":
            raise InterruptedError("crash after index chmod")
        return save(path, value)

    monkeypatch.setattr(module, "save", interrupted)
    with pytest.raises(InterruptedError, match="after index chmod"):
        execute()
    monkeypatch.setattr(module, "save", save)
    index = operation / "occurrences.sqlite3"
    assert index.stat().st_mode & 0o222 == 0
    assert not (operation / "index-receipt.json").exists()
    return index


def test_resume_after_chmod_finalizes_read_only_without_replaying(index_operation, monkeypatch):
    module, operation, execute, production, marker, _ = index_operation
    index = interrupt_receipt(index_operation, monkeypatch)
    before = index.read_bytes(), _stamp(index.stat()), production.read_bytes(), marker.read_bytes()

    def no_replay(*args, **kwargs):
        pytest.fail("sealed index must never reenter prepare/step")

    monkeypatch.setattr(module.occurrences, "prepare", no_replay)
    monkeypatch.setattr(module.occurrences, "step", no_replay)
    resumed = execute(resume=True)
    assert resumed["complete"] and resumed["processed"] == 0
    assert resumed["receipt"]["plans"] == 1
    assert resumed["receipt"]["database_sha256"] == module.sha(index)
    assert resumed["receipt"]["payloads_copied"] == 0
    assert resumed["receipt"]["production_activated"] is False
    assert json.loads((operation / "index-receipt.json").read_text()) == resumed["receipt"]
    assert execute(resume=True)["already_sealed"]
    assert before == (
        index.read_bytes(),
        _stamp(index.stat()),
        production.read_bytes(),
        marker.read_bytes(),
    )


@pytest.mark.parametrize("damage", ["incomplete", "contract", "missing_plan", "sidecar"])
def test_read_only_resume_rejects_unfinished_or_changed_index(index_operation, monkeypatch, damage):
    _, operation, execute, production, marker, _ = index_operation
    index = interrupt_receipt(index_operation, monkeypatch)
    if damage == "sidecar":
        Path(str(index) + "-wal").write_bytes(b"unresolved sidecar")
    else:
        index.chmod(0o600)
        with sqlite3.connect(index) as conn:
            if damage == "incomplete":
                conn.execute("UPDATE index_state SET complete=0")
            elif damage == "contract":
                contract = json.loads(
                    conn.execute("SELECT contract FROM index_state").fetchone()[0]
                )
                contract["references"][0]["plan_sha256"] = "0" * 64
                conn.execute("UPDATE index_state SET contract=?", (json.dumps(contract),))
            else:
                conn.execute("DELETE FROM plans")
        index.chmod(0o400)
    before = index.read_bytes(), production.read_bytes(), marker.read_bytes()
    with pytest.raises(ValueError):
        execute(resume=True)
    assert not (operation / "index-receipt.json").exists()
    assert before == (index.read_bytes(), production.read_bytes(), marker.read_bytes())


@pytest.mark.parametrize("damage", [None, "plan", "result", "mapping"])
def test_sealed_receipt_rechecks_real_nonempty_index_and_dependencies(mapped, monkeypatch, damage):
    import time

    scripts = Path(__file__).resolve().parents[1] / "scripts"
    monkeypatch.syspath_prepend(str(scripts))
    import m1_historical_index as module

    index = make_index(mapped)
    with sqlite3.connect(index.path.as_uri() + "?mode=ro&immutable=1", uri=True) as conn:
        contract = json.loads(conn.execute("SELECT contract FROM index_state").fetchone()[0])
        expected_counts = dict(
            conn.execute(
                "SELECT journal_verified,count(*) FROM occurrences GROUP BY journal_verified"
            )
        )
    assert expected_counts[1] > 0
    if damage in {"plan", "result"}:
        with mapped[damage].open("a") as stream:
            stream.write(" ")
    elif damage == "mapping":
        monkeypatch.setitem(mapped["mapping"].receipt, "sha256", "0" * 64)
    kwargs = {
        "review": {"references": contract["references"]},
        "review_sha256": "c" * 64,
        "mapping": mapped["mapping"],
        "total": mapped["plan"].stat().st_size,
        "deadline": time.monotonic() + 5,
    }
    before = index.path.read_bytes(), _stamp(index.path.stat())
    if damage:
        with pytest.raises(ValueError):
            module.sealed_receipt(index.path, **kwargs)
    else:
        receipt = module.sealed_receipt(index.path, **kwargs)
        assert receipt["journal_status_counts"] == expected_counts
        assert receipt["database_sha256"] == index.sha256
    assert before == (index.path.read_bytes(), _stamp(index.path.stat()))
