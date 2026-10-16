import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from jobagg import pending_publication as pending


def put(path, value):
    path.write_text(json.dumps(value))
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def accepted(tmp_path):
    run = tmp_path / "runs" / "accepted-batch"
    run.mkdir(parents=True)
    put(run / "request.json", {"run_id": run.name})
    report_sha = put(run / "acceptance.json", {"run_id": run.name, "accepted_jobs": ["job-a"]})
    put(run / "worker_result.json", {"run_id": run.name, "worker_exit_code": 0,
                                    "report_sha256": report_sha})
    db = tmp_path / "jobs.sqlite3"
    db.write_bytes(b"synthetic database identity fixture")
    binding = {"files": [{"path": str(db), "size": db.stat().st_size}],
               "semantic": {"writer_fenced": True, "revision": 7}}
    params = {"config_sha256": "configuration", "database": db, "binding": binding}
    pending.seal(tmp_path, run.name, lease={"cycle_id": run.name}, **params)
    return tmp_path, run, params


def test_restart_preserves_exact_accepted_work_without_fetching(accepted):
    state, run, params = accepted
    result = pending.validate(state, **params)
    assert result["run_id"] == run.name
    assert result["publisher_intent_recorded"] is False
    assert not (run / "publication_intent.json").exists()
    assert result["projection"] is None
    assert json.loads((run / "acceptance.json").read_text())["accepted_jobs"] == ["job-a"]


def test_changed_database_revision_blocks_unsealed_replay(accepted):
    state, _, params = accepted
    changed = {**params["binding"], "semantic": {"writer_fenced": True, "revision": 8}}
    with pytest.raises(pending.PendingPublicationError, match="database changed"):
        pending.validate(state, **{**params, "binding": changed})
    assert pending.inspect(state) is not None


def test_timestamp_only_legacy_binding_cannot_certify_replay(accepted):
    state, _, params = accepted
    changed = {**params["binding"], "semantic": {"writer_fenced": False}}
    with pytest.raises(pending.PendingPublicationError, match="writer fence"):
        pending.validate(state, **{**params, "binding": changed})


def test_changed_acceptance_preserves_hold(accepted):
    state, run, params = accepted
    put(run / "acceptance.json", {"accepted_jobs": ["other-job"]})
    with pytest.raises(pending.PendingPublicationError, match="proof changed"):
        pending.validate(state, **params)
    assert pending.marker_path(state).exists()


def test_sealed_projection_is_reused_and_tampering_rejected(accepted):
    state, run, params = accepted
    projection = run / "publication_snapshot.sqlite3"
    projection.write_bytes(b"bounded sealed projection")
    snapshot = {"path": str(projection),
                "sha256": hashlib.sha256(projection.read_bytes()).hexdigest()}
    pending.record_projection(state, snapshot)
    assert pending.validate(state, **params)["projection"] == snapshot
    projection.write_bytes(b"changed")
    with pytest.raises(pending.PendingPublicationError, match="projection changed"):
        pending.validate(state, **params)


def test_fence_remains_until_exact_durable_intent_takes_over(accepted):
    state, run, params = accepted
    with pytest.raises(pending.PendingPublicationError):
        pending.release_after_intent(state, run_id=run.name)
    assert pending.marker_path(state).exists()
    request_sha = put(run / "publication_request.json", {"run_id": run.name})
    put(run / "publication_intent.json", {"run_id": run.name, "request_sha256": request_sha})
    with pytest.raises(pending.PendingPublicationError, match="publisher intent"):
        pending.validate(state, **params)
    pending.release_after_intent(state, run_id=run.name)
    assert pending.inspect(state) is None
    assert (run / "acceptance.json").exists()


def test_another_batch_cannot_replace_the_fence(accepted):
    state, run, params = accepted
    with pytest.raises(pending.PendingPublicationError, match="already owns"):
        pending.seal(state, run.name, lease={"cycle_id": "different"}, **params)


def test_symlink_marker_and_parent_escape_fail_closed(tmp_path):
    other = tmp_path / "other.json"
    put(other, {"schema_version": 1, "state": "accepted_pending", "run_id": "../escape"})
    pending.marker_path(tmp_path).symlink_to(other)
    with pytest.raises(pending.PendingPublicationError, match="aliased"):
        pending.inspect(tmp_path)


def process_proof():
    return {"checked_at": datetime.now(UTC).isoformat(), "ps_sha256": "a" * 64,
            "former_pids_groups_and_commands_absent": True}


def deferred_controls(state, run):
    marker = pending.inspect(state)
    controls = {
        "outcome.json": b'{\r\n "run_id": "accepted-batch", "status": "process_failure"\r\n}\n',
        "publication_request.json": json.dumps({"run_id": run.name,
            "worker_acceptance_sha256": marker["acceptance_sha256"]}, indent=1).encode(),
    }
    for name, data in controls.items():
        (run / name).write_bytes(data)
    return controls


def test_external_projection_requires_exact_trusted_target_binding(accepted):
    state, run, params = accepted
    pending.marker_path(state).unlink()
    target = state / "bounded-scratch" / run.name / "publication_snapshot.sqlite3"
    pending.seal(state, run.name, lease={"cycle_id": run.name}, projection_target=target, **params)
    with pytest.raises(pending.PendingPublicationError, match="target binding"):
        pending.validate(state, **params)
    target.parent.mkdir(parents=True)
    target.write_bytes(b"exact bounded projection")
    snapshot = {"path": str(target), "sha256": hashlib.sha256(target.read_bytes()).hexdigest()}
    pending.record_projection(state, snapshot)
    assert pending.validate(state, projection_target=target, **params)["projection"] == snapshot
    wrong = run / "publication_snapshot.sqlite3"
    wrong.write_bytes(target.read_bytes())
    with pytest.raises(pending.PendingPublicationError, match="accepted run"):
        pending.record_projection(state, {"path": str(wrong), "sha256": snapshot["sha256"]})


def test_resume_archives_exact_bytes_preserves_worker_proofs_and_is_idempotent(accepted):
    state, run, params = accepted
    originals = {name: (run / name).read_bytes() for name in ("request.json", "acceptance.json", "worker_result.json")}
    controls = deferred_controls(state, run)
    result = pending.prepare_resume(state, process_proof())
    receipt = Path(result["archive_receipt"])
    assert result["run_id"] == run.name and result["projection"] is None
    for name, data in controls.items():
        assert not (run / name).exists()
        assert (receipt.parent / name).read_bytes() == data
        assert json.loads(receipt.read_text())["files"][name]["sha256"] == hashlib.sha256(data).hexdigest()
    assert (receipt.parent / "completion.json").is_file()
    assert pending.prepare_resume(state, process_proof()) == result
    assert originals == {name: (run / name).read_bytes() for name in originals}
    assert pending.validate(state, **params)["run_id"] == run.name


def test_interruption_between_removals_resumes_only_from_sealed_archives(accepted, monkeypatch):
    state, run, _ = accepted
    controls = deferred_controls(state, run)
    original = Path.unlink
    def interrupt(path, *args, **kwargs):
        if path == run / "publication_request.json":
            raise OSError("simulated interruption after first unlink")
        return original(path, *args, **kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(Path, "unlink", interrupt)
        with pytest.raises(OSError, match="interruption"):
            pending.prepare_resume(state, process_proof())
    assert not (run / "outcome.json").exists()
    assert (run / "publication_request.json").exists()
    head_before = json.loads((run / "accepted_work_continuation.json").read_text())
    assert head_before["state"] == "archiving"
    result = pending.prepare_resume(state, process_proof())
    assert Path(result["archive_receipt"]).parent.name == head_before["transaction_id"]
    assert not (run / "publication_request.json").exists()
    for name, data in controls.items():
        assert (Path(result["archive_receipt"]).parent / name).read_bytes() == data


def test_interruption_before_archive_receipt_never_removes_sources(accepted, monkeypatch):
    state, run, _ = accepted
    controls = deferred_controls(state, run)
    original = pending.atomic_write_text
    def interrupt(path, *args, **kwargs):
        if Path(path).name == "receipt.json":
            raise OSError("simulated receipt failure")
        return original(path, *args, **kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(pending, "atomic_write_text", interrupt)
        with pytest.raises(OSError, match="receipt failure"):
            pending.prepare_resume(state, process_proof())
    assert controls == {name: (run / name).read_bytes() for name in controls}
    (run / "outcome.json").unlink()
    with pytest.raises(pending.PendingPublicationError, match="missing before durable"):
        pending.prepare_resume(state, process_proof())


@pytest.mark.parametrize("corruption", ["source", "archive", "receipt", "acceptance"])
def test_interrupted_continuation_rejects_changed_evidence(accepted, monkeypatch, corruption):
    state, run, _ = accepted
    deferred_controls(state, run)
    original = Path.unlink
    def interrupt(path, *args, **kwargs):
        if path == run / "outcome.json":
            raise OSError("stop before any unlink")
        return original(path, *args, **kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(Path, "unlink", interrupt)
        with pytest.raises(OSError):
            pending.prepare_resume(state, process_proof())
    head = json.loads((run / "accepted_work_continuation.json").read_text())
    archive = run / "accepted_work_deferrals" / head["transaction_id"]
    target = {"source": run / "outcome.json", "archive": archive / "outcome.json",
              "receipt": archive / "receipt.json", "acceptance": run / "acceptance.json"}[corruption]
    put(target, {"changed": True})
    with pytest.raises(pending.PendingPublicationError):
        pending.prepare_resume(state, process_proof())
    assert (run / "outcome.json").exists() and (run / "publication_request.json").exists()


@pytest.mark.parametrize("invalid", ["no_proof", "false_proof", "old_proof", "intent", "foreign_run", "wrong_acceptance"])
def test_resume_rejects_unproven_process_absence_or_conflicting_control(accepted, invalid):
    state, run, _ = accepted
    controls = deferred_controls(state, run)
    proof = process_proof()
    if invalid == "no_proof":
        proof = {}
    elif invalid == "false_proof":
        proof["former_pids_groups_and_commands_absent"] = False
    elif invalid == "old_proof":
        proof["checked_at"] = "2000-01-01T00:00:00+00:00"
    elif invalid == "intent":
        put(run / "publication_intent.json", {"run_id": run.name})
    elif invalid == "foreign_run":
        put(run / "outcome.json", {"run_id": "other", "status": "process_failure"})
    else:
        put(run / "publication_request.json", {"run_id": run.name, "worker_acceptance_sha256": "0" * 64})
    with pytest.raises(pending.PendingPublicationError):
        pending.prepare_resume(state, proof)
    assert all((run / name).exists() for name in controls)


def test_later_deferral_keeps_prior_exact_archive_and_completion(accepted):
    state, run, _ = accepted
    deferred_controls(state, run)
    first = pending.prepare_resume(state, process_proof())
    first_archive = Path(first["archive_receipt"]).parent
    original = {path.name: path.read_bytes() for path in first_archive.iterdir()}
    deferred_controls(state, run)
    second = pending.prepare_resume(state, process_proof())
    assert second["archive_receipt"] != first["archive_receipt"]
    assert original == {path.name: path.read_bytes() for path in first_archive.iterdir()}
