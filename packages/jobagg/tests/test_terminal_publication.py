"""Synthetic controls only: no provider, SQLite or production dependencies."""

import hashlib
import json
import os
import time
from pathlib import Path

import pytest

from jobagg import terminal_publication as terminal
from jobagg.publication_snapshot import _recovery_identity

STAMP = "2026-10-01T10:00:00+00:00"
DEADLINE = "2026-10-01T10:06:00+00:00"
GENERATION = "a" * 32
HASH = "b" * 64


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True))
    return value


def read(path):
    return json.loads(path.read_text())


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def modify(path, **changes):
    return write(path, {**read(path), **changes})


def identity(request):
    # Generate proofs with the actual publisher contract, not the classifier's
    # field list: activated requests bind publication_policy as well.
    return _recovery_identity(request)


@pytest.fixture
def run(tmp_path):
    root = tmp_path / "jobagg"
    run = root / "deterministic-dispatcher-proposal" / "runs" / "run-1"
    worker_db = root / "worker.sqlite3"
    write(run / "request.json", {
        "schema_version": 1, "run_id": run.name, "source_manifest_sha256": HASH,
        "expected_source_ids": ["source-1"], "source_registry": {"sha256": HASH},
    })
    write(run / "acceptance.json", {
        "schema_version": 1, "run_id": run.name, "source_manifest_sha256": HASH,
        "database": str(worker_db), "generated_at": STAMP, "status": "incomplete",
    })
    acceptance_sha = digest(run / "acceptance.json")
    snapshot = {
        "schema_version": 1, "kind": "sqlite_publication_projection",
        "source_database": str(worker_db), "path": str(run / "publication_snapshot.sqlite3"),
        "sha256": HASH, "size_bytes": 27, "worker_acceptance_sha256": acceptance_sha,
    }
    # No database or snapshot is created: only its bound metadata is inspected.
    request = write(run / "publication_request.json", {
        "schema_version": 1, "run_id": run.name, "generated_at": STAMP,
        "deadline_at": DEADLINE, "expected_source_ids": ["source-1"],
        "source_manifest_sha256": HASH, "registry_sha256": HASH,
        "worker_acceptance_path": str(run / "acceptance.json"),
        "worker_acceptance_sha256": acceptance_sha, "worker_status": "incomplete",
        "worker_database": str(worker_db), "publication_snapshot": snapshot,
        "worker_database_files": [{"path": snapshot["path"], "sha256": HASH}],
    })
    write(run / "publication_intent.json", {
        "run_id": run.name, "request_sha256": digest(run / "publication_request.json"),
        "created_at": STAMP, "config_sha256": HASH,
        "command_template": ["python", "publish_worker.py"], "cwd": str(root),
    })
    generation = root / "deterministic-live-publication" / "generations" / GENERATION
    write(generation / "plan.json", {"generation_id": GENERATION})
    publication = {
        "state": "complete", "status": "published", "database_transactions_complete": True,
        "generation_id": GENERATION, "plan_path": str(generation / "plan.json"),
        "plan_sha256": digest(generation / "plan.json"), "observation_set_sha256": HASH,
        "completed_at": "2026-10-01T10:01:00+00:00",
    }
    write(generation / "result.json", publication)
    write(run / "publication_result.json", {
        **request, "publication_request_sha256": digest(run / "publication_request.json"),
        "status": "published", "publication": publication, "observation_set_sha256": HASH,
        "whole_job_completeness_certified": False,
    })
    outcome = write(run / "outcome.json", {
        "schema_version": 1, "run_id": run.name, "status": "incomplete",
        "publication_status": "published", "publication_recovery_allowed": False,
        "worker_report_sha256": acceptance_sha,
        "publication_result_sha256": digest(run / "publication_result.json"),
    })
    write(run / "state.json", outcome)
    return run


def reseal_direct(run):
    modify(run / "publication_intent.json",
           request_sha256=digest(run / "publication_request.json"))
    modify(run / "publication_result.json",
           publication_request_sha256=digest(run / "publication_request.json"))
    outcome = modify(run / "outcome.json",
                     publication_result_sha256=digest(run / "publication_result.json"))
    write(run / "state.json", outcome)


def activate_policy(run):
    policy = {"schema_version": 1, "policy_epoch": "fixture-epoch",
              "export_storage": "record_chunks_v1", "semantic_history": True}
    modify(run / "publication_request.json", publication_policy=policy)
    modify(run / "publication_result.json", publication_policy=policy)
    reseal_direct(run)
    return policy


def recovered(run, *, hot=True):
    request = read(run / "publication_request.json")
    publication = read(run / "publication_result.json")["publication"]
    root = run.parents[2]
    binding = write(run / "publication_generation_binding.json", {
        "schema_version": 1, "kind": "existing_publication_generation",
        "request_identity_sha256": identity(request), "generation_id": GENERATION,
        "plan_path": publication["plan_path"], "plan_sha256": publication["plan_sha256"],
        "observation_set_sha256": HASH, "worker_database": request["publication_snapshot"]["path"],
        "output_dir": str(root / "output"), "state_dir": str(root / "deterministic-live-publication"),
        "registry_sha256": HASH, "initial_gate_state": "exporting",
        "expiry_recovery_authorization": None, "request_ownership_verified": True,
        "original_generated_at": STAMP, "original_deadline_at": DEADLINE,
    })
    original = modify(run / "outcome.json", status="process_failure",
                      publication_status="unknown_requires_review_no_replay",
                      publication_recovery_allowed=True)
    (run / "publication_result.json").unlink()
    recovery_path = run / "publication_recovery" / "attempt-1" / "request.json"
    recovery = write(recovery_path, {
        **request, "recover_publication": True, "recovery_generation_binding": binding,
        "generated_at": "2026-10-01T11:00:00+00:00",
        "deadline_at": "2026-10-01T11:06:00+00:00",
        "original_publication_request": {
            "path": str(run / "publication_request.json"),
            "sha256": digest(run / "publication_request.json"),
        },
        "original_publication_intent_sha256": digest(run / "publication_intent.json"),
    })
    result_path = recovery_path.with_name("result.json")
    write(result_path, {
        **recovery, "publication_request_sha256": digest(recovery_path),
        "status": "published", "publication": publication,
        "observation_set_sha256": HASH, "whole_job_completeness_certified": False,
    })
    write(run / "publication_resolution.json", {
        "run_id": run.name, "previous_outcome_sha256": digest(run / "outcome.json"),
        "request": {"path": str(recovery_path.relative_to(run)), "sha256": digest(recovery_path)},
        "result": {"path": str(result_path.relative_to(run)), "sha256": digest(result_path)},
    })
    write(run / "state.json", {
        **original, "status": "incomplete", "publication_status": "published",
        "publication_recovery_allowed": False,
        "publication_resolution_sha256": digest(run / "publication_resolution.json"),
    })
    if not hot:
        (Path(publication["plan_path"]).with_name("result.json")).unlink()
    return recovery_path, result_path


def no_write(run):
    request = read(run / "publication_request.json")
    publication = read(run / "publication_result.json")["publication"]
    publication["completed_at"] = "2026-10-01T09:59:59+00:00"
    publication["request_identity_sha256"] = "c" * 64
    result_path = Path(publication["plan_path"]).with_name("result.json")
    write(result_path, publication)
    original = modify(run / "outcome.json", status="process_failure",
                      publication_status="unknown_requires_review_no_replay",
                      publication_recovery_allowed=True)
    (run / "publication_result.json").unlink()
    proof = {
        "schema_version": 1, "kind": "verified_unstarted_publication",
        "request_identity_sha256": identity(request), "generation_not_started": True,
        "prior_gate": publication, "prior_gate_sha256": digest(result_path),
        "prior_plan": {"path": publication["plan_path"], "sha256": publication["plan_sha256"]},
        "prior_result": {"path": str(result_path), "sha256": digest(result_path)},
    }
    write(run / "publication_no_write_resolution.json", {
        "run_id": run.name, "previous_outcome_sha256": digest(run / "outcome.json"),
        "original_request_sha256": digest(run / "publication_request.json"),
        "original_intent_sha256": digest(run / "publication_intent.json"), "proof": proof,
    })
    write(run / "state.json", {
        **original, "status": "incomplete", "publication_status": "deferred",
        "publication_recovery_allowed": False, "generation_not_started": True,
        "worker_reexecuted": False,
    })


def test_published_with_incomplete_worker_and_absent_snapshot_is_terminal(run):
    before = {p: p.read_bytes() for p in run.rglob("*") if p.is_file()}
    result = terminal.classify_run(run)
    assert result["disposition"] == "terminal"
    assert result["terminal_kind"] == "published"
    assert result["result"] == read(run / "publication_result.json")
    assert result["generation_ids"] == [GENERATION]
    assert result["known_deficits"] == []
    assert run / "publication_intent.json" in result["controls"]
    assert run / "acceptance.json" in result["controls"]
    assert {p: p.read_bytes() for p in run.rglob("*") if p.is_file()} == before


@pytest.mark.parametrize("missing", [False, True])
def test_immutable_no_write_proof_can_validate_before_state_replacement(run, missing):
    no_write(run)
    if missing:
        (run / "state.json").unlink()
    else:
        (run / "state.json").write_bytes((run / "outcome.json").read_bytes())
    before = {p: p.read_bytes() for p in run.rglob("*") if p.is_file()}
    classified = terminal.classify_run(run, allow_unfinished_no_write_state=True)
    assert classified["terminal_kind"] == "verified_unstarted"
    assert classified["no_write_state_pending"] is True
    assert before == {p: p.read_bytes() for p in run.rglob("*") if p.is_file()}
    # This opt-in never discharges a borrowed/corrupt immutable proof.
    resolution = read(run / "publication_no_write_resolution.json")
    resolution["original_intent_sha256"] = "f" * 64
    write(run / "publication_no_write_resolution.json", resolution)
    with pytest.raises(ValueError):
        terminal.classify_run(run, allow_unfinished_no_write_state=True)


def test_no_write_state_repair_rejects_a_different_mutable_state(run):
    no_write(run)
    write(run / "state.json", {**read(run / "outcome.json"), "status": "different_failure"})
    with pytest.raises(ValueError, match="resolved run state is not terminal"):
        terminal.classify_run(run, allow_unfinished_no_write_state=True)


def test_noop_preserves_real_no_changes_result(run):
    modify(run / "publication_result.json", status="noop",
           publication={"status": "no_changes", "generation_not_started": True})
    modify(run / "outcome.json", publication_status="noop")
    reseal_direct(run)
    assert terminal.classify_run(run)["terminal_kind"] == "noop"


@pytest.mark.parametrize("control,changes", [
    ("publication_intent.json", {"request_sha256": "c" * 64}),
    ("publication_intent.json", {"run_id": "other-run"}),
    ("outcome.json", {"run_id": "other-run"}),
    ("outcome.json", {"publication_result_sha256": "c" * 64}),
    ("acceptance.json", {"run_id": "other-run"}),
    ("request.json", {"expected_source_ids": ["other-source"]}),
    ("state.json", {"publication_status": "noop"}),
])
def test_borrowed_or_modified_original_controls_rejected(run, control, changes):
    modify(run / control, **changes)
    with pytest.raises(ValueError):
        terminal.classify_run(run)


@pytest.mark.parametrize("field,value", [
    ("worker_database", "/other.sqlite3"),
    ("source_manifest_sha256", "c" * 64),
    ("publication_snapshot", {}),
    ("whole_job_completeness_certified", True),
    ("generated_at", "2026-09-01T00:00:00+00:00"),
    ("publication", {"state": "exporting", "status": "published", "generation_id": GENERATION}),
])
def test_result_is_validated_beyond_outcome_digest(run, field, value):
    modify(run / "publication_result.json", **{field: value})
    reseal_direct(run)
    with pytest.raises(ValueError):
        terminal.classify_run(run)


@pytest.mark.parametrize("missing", ["outcome.json", "publication_intent.json", "acceptance.json",
                                     "publication_result.json", "request.json"])
def test_missing_control_is_protected_and_never_fabricated(run, missing):
    (run / missing).unlink()
    answer = terminal.classify_run(run)
    assert answer["disposition"] == "unresolved"
    assert answer["terminal_kind"] is None
    assert answer["known_deficits"] == [{"code": "missing_control", "path": str(run / missing)}]
    if missing == "publication_result.json":
        assert answer["result"] is None


def test_running_state_is_active_despite_terminal_outcome(run):
    modify(run / "state.json", status="running")
    assert terminal.classify_run(run)["disposition"] == "active"


def test_unresolved_generation_pin_is_retained(run):
    modify(run / "outcome.json", publication_status="incomplete", publication_recovery_allowed=True)
    modify(run / "state.json", publication_status="incomplete", publication_recovery_allowed=True)
    answer = terminal.classify_run(run)
    assert answer["disposition"] == "unresolved"
    assert answer["generation_ids"] == [GENERATION]


def test_recovered_published_verifies_hot_receipt(run):
    _, result_path = recovered(run)
    answer = terminal.classify_run(run)
    assert answer["terminal_kind"] == "recovered_published"
    assert answer["result"] == read(result_path)
    assert answer["known_deficits"] == []


def test_missing_recovered_hot_receipt_is_opt_in_with_explicit_deficit(run):
    _, result_path = recovered(run, hot=False)
    default = terminal.classify_run(run)
    assert default["disposition"] == "unresolved"
    reviewed = terminal.classify_run(run, allow_missing_generation_result=True)
    assert reviewed["terminal_kind"] == "recovered_published"
    assert reviewed["result"] == default["result"] == read(result_path)
    assert reviewed["known_deficits"] == default["known_deficits"]
    assert reviewed["known_deficits"][0]["code"] == "missing_generation_result"


@pytest.mark.parametrize("bad_control", ["publication_intent.json", "publication_generation_binding.json",
                                       "publication_resolution.json", "state.json"])
def test_missing_hot_result_opt_in_does_not_waive_other_bindings(run, bad_control):
    recovered(run, hot=False)
    modify(run / bad_control, run_id="borrowed", generation_id="d" * 32)
    with pytest.raises(ValueError):
        terminal.classify_run(run, allow_missing_generation_result=True)


def test_changed_recovery_result_with_resealed_resolution_still_rejected(run):
    _, result_path = recovered(run)
    modify(result_path, registry_sha256="d" * 64)
    resolution = read(run / "publication_resolution.json")
    resolution["result"]["sha256"] = digest(result_path)
    write(run / "publication_resolution.json", resolution)
    modify(run / "state.json", publication_resolution_sha256=digest(run / "publication_resolution.json"))
    with pytest.raises(ValueError, match="registry_sha256"):
        terminal.classify_run(run)


def test_recovery_reference_cannot_escape_run(run):
    recovered(run)
    resolution = read(run / "publication_resolution.json")
    resolution["result"]["path"] = "../other/result.json"
    write(run / "publication_resolution.json", resolution)
    modify(run / "state.json", publication_resolution_sha256=digest(run / "publication_resolution.json"))
    with pytest.raises(ValueError, match="traverses"):
        terminal.classify_run(run)


def test_verified_unstarted_requires_prior_exact_controls(run):
    no_write(run)
    answer = terminal.classify_run(run)
    assert answer["terminal_kind"] == "verified_unstarted"
    assert answer["result"] is None
    assert answer["generation_ids"] == [GENERATION]


@pytest.mark.parametrize("mode", ["published", "recovered_published", "verified_unstarted"])
def test_activated_policy_uses_publisher_recovery_identity(run, mode):
    activate_policy(run)
    request = read(run / "publication_request.json")
    assert terminal._request_identity(request) == _recovery_identity(request)
    if mode == "recovered_published":
        recovered(run)
    elif mode == "verified_unstarted":
        no_write(run)
    assert terminal.classify_run(run)["terminal_kind"] == mode


@pytest.mark.parametrize("damage", ["absent", "changed", "unexpected"])
def test_result_cannot_drop_change_or_add_publication_policy(run, damage):
    policy = activate_policy(run)
    result = read(run / "publication_result.json")
    if damage == "absent":
        result.pop("publication_policy")
    elif damage == "changed":
        result["publication_policy"] = {**policy, "policy_epoch": "another-epoch"}
    else:
        request = read(run / "publication_request.json")
        request.pop("publication_policy")
        write(run / "publication_request.json", request)
    write(run / "publication_result.json", result)
    reseal_direct(run)
    with pytest.raises(ValueError, match="publication_policy"):
        terminal.classify_run(run)


@pytest.mark.parametrize("damage", ["absent", "changed"])
def test_recovered_policy_must_equal_original_even_with_resealed_controls(run, damage):
    policy = activate_policy(run)
    request_path, result_path = recovered(run)
    request, result = read(request_path), read(result_path)
    for document in (request, result):
        if damage == "absent":
            document.pop("publication_policy")
        else:
            document["publication_policy"] = {**policy, "policy_epoch": "another-epoch"}
    write(request_path, request)
    result["publication_request_sha256"] = digest(request_path)
    write(result_path, result)
    resolution = read(run / "publication_resolution.json")
    resolution["request"]["sha256"] = digest(request_path)
    resolution["result"]["sha256"] = digest(result_path)
    write(run / "publication_resolution.json", resolution)
    modify(run / "state.json", publication_resolution_sha256=digest(run / "publication_resolution.json"))
    with pytest.raises(ValueError, match="recovery original binding differs: publication_policy"):
        terminal.classify_run(run)


@pytest.mark.parametrize("damage", ["ordering", "plan", "result", "original_intent", "missing_plan"])
def test_no_write_proof_cannot_be_inferred_from_missing_result(run, damage):
    no_write(run)
    path = run / "publication_no_write_resolution.json"
    resolution = read(path)
    proof = resolution["proof"]
    if damage == "ordering":
        proof["prior_gate"]["completed_at"] = STAMP
        write(path, resolution)
    elif damage == "original_intent":
        resolution["original_intent_sha256"] = "d" * 64
        write(path, resolution)
    elif damage == "missing_plan":
        Path(proof["prior_plan"]["path"]).unlink()
        assert terminal.classify_run(run)["disposition"] == "unresolved"
        return
    else:
        modify(Path(proof["prior_" + damage]["path"]), unexpected=True)
    with pytest.raises(ValueError):
        terminal.classify_run(run)


@pytest.mark.parametrize("link_kind", ["symlink", "hardlink", "parent_symlink"])
def test_control_links_are_rejected(run, link_kind):
    if link_kind == "parent_symlink":
        recovered(run)
        directory = run / "publication_recovery" / "attempt-1"
        moved = directory.with_name("other-attempt")
        directory.rename(moved)
        directory.symlink_to(moved, target_is_directory=True)
    else:
        path = run / "publication_intent.json"
        other = path.with_name("linked-intent.json")
        if link_kind == "symlink":
            path.rename(other)
            path.symlink_to(other)
        else:
            os.link(path, other)
    with pytest.raises(ValueError):
        terminal.classify_run(run)


def test_deadline_is_checked_before_read(run):
    with pytest.raises(TimeoutError):
        terminal.classify_run(run, deadline_at=time.monotonic() - 1)


def test_control_replacement_during_read_is_rejected(run, monkeypatch):
    original = terminal._Controls.read
    replaced = False

    def read_and_replace(self, path, **kwargs):
        nonlocal replaced
        value = original(self, path, **kwargs)
        if Path(path).name == "publication_intent.json" and not replaced:
            replaced = True
            modify(run / "outcome.json", replaced=True)
        return value

    monkeypatch.setattr(terminal._Controls, "read", read_and_replace)
    with pytest.raises(ValueError, match="changed"):
        terminal.classify_run(run)
