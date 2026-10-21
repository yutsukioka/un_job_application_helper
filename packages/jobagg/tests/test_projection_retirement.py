"""External accepted projections need registered work authority, never path guessing."""
import fcntl
from pathlib import Path

import pytest
import test_terminal_publication as control_fixture
from test_storage_retention import digest, make_generation, write_json
from test_terminal_publication import read, reseal_direct

from jobagg import terminal_history as history
from jobagg.storage_budget import (
    CATEGORY_LIMITS,
    CONTROL_ENVELOPE_BYTES,
    BudgetLedger,
    assess,
    inventory,
    parse_budget_config,
)


@pytest.fixture
def projected(tmp_path):
    run = control_fixture.run.__wrapped__(tmp_path)
    root = run.parents[2]
    (root / "output").mkdir(exist_ok=True)
    (root / "remediation").mkdir(exist_ok=True)
    lock = root / "remediation" / "manual-fetch-owner.lock"
    lock.touch()
    generations = [make_generation(root, n) for n in (1, 2, 3)]
    write_json(root / "output" / ".jobagg-publication-state.json", {
        "state": "complete", "status": "published", "database_transactions_complete": True,
        "generation_id": generations[-1].name,
    })
    scratch = tmp_path / "scratch"
    target = scratch / run.name / "publication_snapshot.sqlite3"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"bounded accepted projection")
    request = read(run / "publication_request.json")
    request["publication_snapshot"].update(path=str(target), sha256=digest(target), size_bytes=target.stat().st_size)
    request["worker_database_files"] = [{"path": str(target), "sha256": digest(target)}]
    write_json(run / "publication_request.json", request)
    result = read(run / "publication_result.json")
    result.update(request)
    result["publication"] = read(generations[0] / "result.json")
    write_json(run / "publication_result.json", result)
    reseal_direct(run)
    diagnostics = tmp_path / "diagnostics"
    diagnostics.mkdir()
    config = parse_budget_config({"enabled": True, "roots": [
        {"name": "publication", "path": str(root), "category": "publication", "writers": ["runner"]},
        {"name": "work", "path": str(scratch), "category": "work", "writers": ["runner"]},
        {"name": "diagnostics", "path": str(diagnostics), "category": "diagnostics", "writers": ["runner"]}],
        "writer_roles": {"runner": "controller"}, "ledger_path": str(diagnostics / "budget.json")})
    observed = inventory(config)
    envelope = {category: 100_000 for category in CATEGORY_LIMITS}
    envelope["diagnostics"] += CONTROL_ENVELOPE_BYTES
    admission = assess(config, observed, projected_growth=envelope,
                       physical_free_bytes={device: 200_000_000_000 for device in observed.device_charged_bytes})
    assert admission.ready
    ledger = BudgetLedger.create(admission, cycle_id="current-maintenance-cycle")
    return root, run, scratch, target, lock, ledger, generations


def perform(case, **kwargs):
    _root, run, scratch, _target, lock, ledger, _generations = case
    with lock.open("a+") as owner:
        fcntl.flock(owner, fcntl.LOCK_EX)
        return history.retire_published_projection(run, scratch_dir=scratch, budget=ledger,
                 owner_fd=owner.fileno(), shared_lock=lock, **kwargs)


def test_registered_obsolete_projection_retires_with_real_budget(projected):
    _root, run, _scratch, target, _lock, ledger, generations = projected
    before = ledger.remaining_envelope()["publication"]
    assert perform(projected)["status"] == "retired"
    assert not target.exists()
    assert (run / history.PROJECTION_RECEIPT).exists()
    assert ledger.remaining_envelope()["publication"] < before
    assert perform(projected)["status"] == "already_retired"
    assert all((generation / "plan.json").exists() for generation in generations)


def test_current_or_selected_projection_is_never_retired(projected):
    _root, run, _scratch, target, _lock, _ledger, generations = projected
    assert perform(projected, selected_runs=[run.name])["status"] == "deferred"
    result = read(run / "publication_result.json")
    result["publication"] = read(generations[-1] / "result.json")
    write_json(run / "publication_result.json", result)
    reseal_direct(run)
    assert perform(projected)["status"] == "deferred"
    assert target.exists()
    assert not (run / history.PROJECTION_INTENT).exists()


def test_projection_source_corruption_and_aliases_fail_before_intent(projected):
    _root, run, _scratch, target, _lock, _ledger, _generations = projected
    target.write_bytes(b"changed source")
    with pytest.raises(ValueError, match="projection bytes changed"):
        perform(projected)
    assert not (run / history.PROJECTION_INTENT).exists()


def test_external_projection_durable_intent_resumes_after_unlink(projected, monkeypatch):
    _root, run, _scratch, target, _lock, _ledger, _generations = projected
    original = history._retention()._sync_dir
    def fail(path):
        if path == target.parent:
            raise OSError("projection crash after unlink")
        return original(path)
    monkeypatch.setattr(history._retention(), "_sync_dir", fail)
    with pytest.raises(OSError, match="projection crash"):
        perform(projected)
    assert not target.exists()
    assert (run / history.PROJECTION_INTENT).exists()
    assert not (run / history.PROJECTION_RECEIPT).exists()
    monkeypatch.setattr(history._retention(), "_sync_dir", original)
    assert perform(projected)["status"] == "retired"


def test_projection_intent_cannot_escape_registered_cycle_path(projected, monkeypatch):
    _root, run, _scratch, target, _lock, _ledger, _generations = projected
    original = Path.unlink
    def fail(path, *args, **kwargs):
        if path == target:
            raise OSError("pause")
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, "unlink", fail)
    with pytest.raises(OSError, match="pause"):
        perform(projected)
    monkeypatch.setattr(Path, "unlink", original)
    intent_path = run / history.PROJECTION_INTENT
    intent = read(intent_path)
    unrelated = target.parent / "other.sqlite3"
    unrelated.write_bytes(b"unrelated")
    intent["target"] = str(unrelated)
    write_json(intent_path, intent)
    with pytest.raises(ValueError, match="intent binding changed"):
        perform(projected)
    assert target.exists() and unrelated.exists()


def test_batch_is_bounded_idempotent_and_legacy_copy_reader_skips_external(projected):
    from jobagg import storage_retention
    root, run, scratch, _target, lock, ledger, _generations = projected
    group, result = storage_retention._run_group(run)
    assert group is None and result["status"] == "published"
    with lock.open("a+") as owner:
        fcntl.flock(owner, fcntl.LOCK_EX)
        first = history.retire_published_projections(root, scratch_dir=scratch, budget=ledger,
                    owner_fd=owner.fileno(), shared_lock=lock, max_groups=1)
        second = history.retire_published_projections(root, scratch_dir=scratch, budget=ledger,
                    owner_fd=owner.fileno(), shared_lock=lock, max_groups=1)
    assert len(first["selected"]) == 1
    assert first["deleted_bytes"] > 0
    assert second["selected"] == [] and second["deleted_bytes"] == 0


def test_unregistered_scratch_and_hardlink_alias_are_rejected(projected, tmp_path):
    _root, run, _scratch, target, lock, ledger, _generations = projected
    unknown = tmp_path / "unregistered"
    unknown.mkdir()
    with lock.open("a+") as owner:
        fcntl.flock(owner, fcntl.LOCK_EX)
        with pytest.raises(ValueError, match="unregistered"):
            history.retire_published_projection(run, scratch_dir=unknown, budget=ledger,
                    owner_fd=owner.fileno(), shared_lock=lock)
    alias = target.parent / "alias.sqlite3"
    alias.hardlink_to(target)
    with pytest.raises(ValueError, match="filesystem aliases"):
        perform(projected)
    assert target.exists() and alias.exists()


def test_unsealed_sqlite_sidecars_are_not_retired(projected):
    _root, run, _scratch, target, _lock, _ledger, _generations = projected
    journal = Path(str(target) + "-wal")
    journal.write_bytes(b"uncommitted evidence")
    with pytest.raises(ValueError, match="unsealed SQLite sidecars"):
        perform(projected)
    assert target.exists() and journal.exists()
    assert not (run / history.PROJECTION_INTENT).exists()
