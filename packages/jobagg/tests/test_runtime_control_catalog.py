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


def _accepted_bundle(root, owner, *, intervals=True, implementation="f" * 64):
    root.mkdir(exist_ok=True)
    manifest = {"expected_source_ids": ["source"]}

    def write(name, value):
        (root / name).write_text(json.dumps(value))

    write("source_manifest.json", manifest)

    def sha(name):
        return hashlib.sha256((root / name).read_bytes()).hexdigest()

    request = {
        "run_id": root.name,
        "source_manifest_sha256": sha("source_manifest.json"),
        "expected_source_ids": ["source"],
    }
    write("request.json", request)
    report = {
        "run_id": root.name,
        "status": "incomplete",
        "source_manifest_sha256": request["source_manifest_sha256"],
        "binding": {"implementation_sha256": implementation, "shared_lock": str(owner)},
    }
    if intervals:
        report["concurrency"] = {
            "task_intervals": [
                {
                    "attempt_id": "attempt",
                    "task_id": "task",
                    "source_id": "source",
                    "kind": "listing",
                    "outcome": {"listing_observed": True},
                }
            ]
        }
    write("acceptance.json", report)
    write(
        "worker_result.json",
        {
            "run_id": root.name,
            "worker_exit_code": 0,
            "report_sha256": sha("acceptance.json"),
            "request_sha256": sha("request.json"),
        },
    )
    return {p.name: sha(p.name) for p in root.glob("*.json")}


def _manifest(root, owner, controls):
    return {
        "schema": catalog.SCHEMA,
        "epoch": "epoch",
        "shared_owner": str(owner),
        "complete_control_root_inventory": True,
        "writers": [{"name": "fixture", "fenced": True}],
        "runs": [{"path": str(root), "run_id": root.name, "controls": controls}],
    }


@pytest.mark.parametrize(
    "intervals,reviewed,reason",
    [
        (False, {"f" * 64}, "accepted_exit_attempt_identities_missing"),
        (True, set(), "accepted_exit_implementation_review_required"),
    ],
)
def test_intact_legacy_proof_deficit_is_checkpointed_hold_without_acceptance(
    tmp_path, intervals, reviewed, reason
):
    owner = tmp_path / "owner.lock"
    root = tmp_path / "old-run"
    controls = _accepted_bundle(root, owner, intervals=intervals)
    with shared_owner(owner) as fd, sqlite3.connect(":memory:") as conn:
        catalog.prepare(
            conn, manifest=_manifest(root, owner, controls), owner_fd=fd, owner_path=owner
        )
        assert catalog.step(
            conn,
            owner_fd=fd,
            owner_path=owner,
            limit=1,
            deadline_at=time.monotonic() + 5,
            reviewed_implementations=reviewed,
        )["complete"]
        held = catalog.oldest_unresolved(conn)
        assert held["disposition"] == "repair_required" and held["repair_reason"] == reason
        assert conn.execute("SELECT count(*) FROM jobagg_accepted_exits").fetchone()[0] == 0
        proof = json.loads(
            conn.execute("SELECT proof_json FROM jobagg_legacy_controls").fetchone()[0]
        )
        assert proof["acceptance_hold"] == [reason] and "accepted_exit_sha256" not in proof
        assert len(proof["controls"]) == 4


def test_proof_deficit_never_hides_contradictory_exit_binding(tmp_path):
    owner = tmp_path / "owner.lock"
    root = tmp_path / "old-run"
    controls = _accepted_bundle(root, owner, intervals=False)
    result = root / "worker_result.json"
    result.write_text(json.dumps({**json.loads(result.read_text()), "report_sha256": "0" * 64}))
    controls[result.name] = hashlib.sha256(result.read_bytes()).hexdigest()
    with shared_owner(owner) as fd, sqlite3.connect(":memory:") as conn:
        catalog.prepare(
            conn, manifest=_manifest(root, owner, controls), owner_fd=fd, owner_path=owner
        )
        with pytest.raises(ValueError, match="successful_bound_report"):
            catalog.step(
                conn,
                owner_fd=fd,
                owner_path=owner,
                limit=1,
                deadline_at=time.monotonic() + 5,
                reviewed_implementations=set(),
            )
        assert (
            conn.execute("SELECT after_ordinal FROM jobagg_legacy_control_state").fetchone()[0] == 0
        )
        assert conn.execute("SELECT count(*) FROM jobagg_legacy_controls").fetchone()[0] == 0


def test_publication_controls_are_retained_as_paths_and_acceptance_still_imports(
    tmp_path, monkeypatch
):
    # Classification's real API returns Path objects, not hash dictionaries.
    from jobagg import terminal_publication

    owner = tmp_path / "owner.lock"
    root = tmp_path / "old-run"
    controls = _accepted_bundle(root, owner)
    path = root / "publication_intent.json"
    path.write_text("{}")
    controls[path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
    monkeypatch.setattr(
        terminal_publication,
        "classify_run",
        lambda *a, **kw: {
            "disposition": "terminal",
            "controls": [path],
            "generation_ids": [],
            "control_sha256": {str(path): controls[path.name]},
        },
    )
    with shared_owner(owner) as fd, sqlite3.connect(":memory:") as conn:
        catalog.prepare(
            conn, manifest=_manifest(root, owner, controls), owner_fd=fd, owner_path=owner
        )
        catalog.step(
            conn,
            owner_fd=fd,
            owner_path=owner,
            limit=1,
            deadline_at=time.monotonic() + 5,
            reviewed_implementations={"f" * 64},
        )
        assert conn.execute("SELECT count(*) FROM jobagg_accepted_exits").fetchone()[0] == 1
        assert (
            catalog.oldest_unresolved(conn)["disposition"] == "publication_reconciliation_required"
        )
        assert conn.execute("SELECT count(*) FROM jobagg_legacy_dependencies").fetchone()[0] == 5


def test_auxiliary_control_set_is_durable_repair_work_and_never_acceptance(tmp_path):
    owner = tmp_path / "owner.lock"
    root = tmp_path / "ticks"
    root.mkdir()
    path = root / "standalone.json"
    path.write_text(json.dumps({"status": "complete", "run_id": "old-worker"}))
    controls = {path.name: hashlib.sha256(path.read_bytes()).hexdigest()}
    manifest = _manifest(root, owner, controls)
    record_id = "held-" + hashlib.sha256(str(root).encode()).hexdigest()
    manifest["runs"][0].update(kind="held_control_set", run_id=record_id)
    with shared_owner(owner) as fd, sqlite3.connect(":memory:") as conn:
        catalog.prepare(conn, manifest=manifest, owner_fd=fd, owner_path=owner)
        catalog.step(
            conn,
            owner_fd=fd,
            owner_path=owner,
            limit=1,
            deadline_at=time.monotonic() + 5,
            reviewed_implementations=set(),
        )
        assert catalog.receipt(conn)["runs"] == 1
        assert catalog.oldest_unresolved(conn)["run_id"] == record_id
        assert catalog.oldest_unresolved(conn)["disposition"] == "repair_required"
        assert not conn.execute(
            "SELECT 1 FROM sqlite_master WHERE name='jobagg_accepted_exits'"
        ).fetchone()


def test_integrity_change_after_publication_classification_stops_checkpoint(tmp_path, monkeypatch):
    from jobagg import terminal_publication

    owner = tmp_path / "owner.lock"
    root = tmp_path / "old-run"
    controls = _accepted_bundle(root, owner)
    path = root / "publication_intent.json"
    path.write_text("{}")
    controls[path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
    external = tmp_path / "generation-result.json"
    external.write_text("{}")
    original_sha = hashlib.sha256(external.read_bytes()).hexdigest()

    def classify(*args, **kwargs):
        external.write_text('{"changed": true}')
        return {
            "disposition": "terminal",
            "controls": [path, external],
            "control_sha256": {str(path): controls[path.name], str(external): original_sha},
        }

    monkeypatch.setattr(terminal_publication, "classify_run", classify)
    with shared_owner(owner) as fd, sqlite3.connect(":memory:") as conn:
        catalog.prepare(
            conn, manifest=_manifest(root, owner, controls), owner_fd=fd, owner_path=owner
        )
        conn.commit()
        with pytest.raises(ValueError, match="changed_before_checkpoint"):
            with conn:
                catalog.step(
                    conn,
                    owner_fd=fd,
                    owner_path=owner,
                    limit=1,
                    deadline_at=time.monotonic() + 5,
                    reviewed_implementations={"f" * 64},
                )
        assert (
            conn.execute("SELECT after_ordinal FROM jobagg_legacy_control_state").fetchone()[0] == 0
        )


def test_unverified_auxiliary_artifacts_retain_identity_without_hash_or_acceptance(
    tmp_path, monkeypatch
):
    owner = tmp_path / "owner.lock"
    root = tmp_path / "archive"
    root.mkdir()
    path = root / "large-plan.json"
    path.write_bytes(b"not parsed or certified as valid JSON")
    info = path.stat()
    identity = [info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns]
    manifest = _manifest(root, owner, {})
    record_id = "held-" + hashlib.sha256(str(root).encode()).hexdigest()
    manifest["runs"][0].update(
        kind="held_control_set", run_id=record_id, unverified_files={path.name: identity}
    )
    monkeypatch.setattr(catalog, "stable_file", lambda *a, **kw: pytest.fail("payload was read"))
    with shared_owner(owner) as fd, sqlite3.connect(":memory:") as conn:
        catalog.prepare(conn, manifest=manifest, owner_fd=fd, owner_path=owner)
        catalog.step(
            conn,
            owner_fd=fd,
            owner_path=owner,
            limit=1,
            deadline_at=time.monotonic() + 5,
            reviewed_implementations=set(),
        )
        assert (
            catalog.oldest_unresolved(conn)["repair_reason"]
            == "legacy_auxiliary_content_not_verified_requires_review"
        )
        assert conn.execute("SELECT sha256 FROM jobagg_legacy_dependencies").fetchone()[0] is None
        proof = json.loads(
            conn.execute("SELECT proof_json FROM jobagg_legacy_controls").fetchone()[0]
        )
        assert proof["unverified_controls"] == {str(path): identity} and proof["controls"] == {}


@pytest.mark.parametrize(
    "started,finished,reused", [(200, 100, True), (50, 100, False), (200, None, False)]
)
def test_legacy_pid_reuse_requires_observed_newer_start_and_retains_live_worker_gate(
    monkeypatch, started, finished, reused
):
    from datetime import datetime, timezone

    signals = []
    monkeypatch.setattr(catalog.os, "kill", lambda pid, sig: signals.append((pid, sig)))
    monkeypatch.setattr(catalog, "_boot_started_at", lambda: 0)
    monkeypatch.setattr(catalog, "_process_started_at", lambda pid, deadline: started)
    state = {"worker_pid": 123}
    if finished is not None:
        state["finished_at"] = datetime.fromtimestamp(finished, timezone.utc).isoformat()
    if reused:
        result = catalog._processes_absent(state, deadline_at=time.monotonic() + 5)
        assert result[0]["disposition"] == "pid_reused"
    else:
        with pytest.raises(ValueError, match="still_present"):
            catalog._processes_absent(state, deadline_at=time.monotonic() + 5)
    assert all(sig == 0 for _, sig in signals)


def test_surviving_group_without_leader_is_never_declared_drained(monkeypatch):
    monkeypatch.setattr(catalog.os, "killpg", lambda *args: None)

    def absent(*args):
        raise ProcessLookupError()

    monkeypatch.setattr(catalog.os, "kill", absent)
    with pytest.raises(ValueError, match="group_identity_unresolved"):
        catalog._processes_absent({"process_groups": [123]}, deadline_at=time.monotonic() + 5)


def test_kernel_process_identity_preserves_live_current_process_gate():
    import os
    from datetime import datetime, timezone

    started = catalog._process_started_at(os.getpid(), time.monotonic() + 5)
    assert 0 < started <= time.time()
    with pytest.raises(ValueError, match="still_present"):
        catalog._processes_absent(
            {"worker_pid": os.getpid(), "finished_at": datetime.now(timezone.utc).isoformat()},
            deadline_at=time.monotonic() + 5,
        )


def test_unsignalable_pid_still_requires_proven_start_identity(monkeypatch):
    from datetime import datetime, timezone

    def denied(*args):
        raise PermissionError()

    monkeypatch.setattr(catalog.os, "kill", denied)
    monkeypatch.setattr(catalog, "_boot_started_at", lambda: 0)
    monkeypatch.setattr(catalog, "_process_started_at", lambda *args: 200)
    result = catalog._processes_absent(
        {"worker_pid": 123, "finished_at": datetime.fromtimestamp(100, timezone.utc).isoformat()},
        deadline_at=time.monotonic() + 5,
    )
    assert result[0]["disposition"] == "pid_reused"
    monkeypatch.setattr(catalog, "_process_started_at", denied)
    with pytest.raises(PermissionError):
        catalog._processes_absent(
            {
                "worker_pid": 123,
                "finished_at": datetime.fromtimestamp(100, timezone.utc).isoformat(),
            },
            deadline_at=time.monotonic() + 5,
        )


def test_prior_boot_reference_does_not_require_signalling_or_reading_other_user_process(
    monkeypatch,
):
    from datetime import datetime, timezone

    monkeypatch.setattr(catalog.os, "kill", lambda *args: None)
    monkeypatch.setattr(catalog, "_boot_started_at", lambda: 200)
    monkeypatch.setattr(
        catalog, "_process_started_at", lambda *a: pytest.fail("unnecessary process inspection")
    )
    result = catalog._processes_absent(
        {"worker_pid": 123, "finished_at": datetime.fromtimestamp(100, timezone.utc).isoformat()},
        deadline_at=time.monotonic() + 5,
    )
    assert result[0]["disposition"] == "prior_boot_pid_reference" and result[0]["boot_domain"]


@pytest.mark.parametrize("legacy_owner", [False, True])
def test_explicit_locator_review_hold_preserves_acceptance_and_never_certifies_publication(
    tmp_path, legacy_owner
):
    owner = tmp_path / "owner.lock"
    root = tmp_path / "old-run"
    controls = _accepted_bundle(root, owner)
    original = tmp_path / "former" / "dispatcher" / "runs" / root.name
    if legacy_owner:
        legacy_path = original.parents[2] / "remediation/manual-fetch-owner.lock"
        path = root / "acceptance.json"
        report = json.loads(path.read_text())
        report["binding"]["shared_lock"] = str(legacy_path)
        path.write_text(json.dumps(report))
        controls[path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
        path = root / "worker_result.json"
        result = json.loads(path.read_text())
        result["report_sha256"] = controls["acceptance.json"]
        path.write_text(json.dumps(result))
        controls[path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
    request = {
        "worker_acceptance_path": str(original / "acceptance.json"),
        "worker_acceptance_sha256": controls["acceptance.json"],
    }
    path = root / "publication_request.json"
    path.write_text(json.dumps(request))
    controls[path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
    path = root / "publication_intent.json"
    path.write_text(json.dumps({"request_sha256": controls["publication_request.json"]}))
    controls[path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
    with shared_owner(owner) as fd, sqlite3.connect(":memory:") as conn:
        sha = catalog.prepare(
            conn, manifest=_manifest(root, owner, controls), owner_fd=fd, owner_path=owner
        )
        with pytest.raises(ValueError, match="inventory_changed"):
            catalog.hold_relocated_publication(
                conn,
                run_id=root.name,
                original_control_root=original,
                owner_fd=fd,
                owner_path=owner,
                expected_inventory_sha256="0" * 64,
                deadline_at=time.monotonic() + 5,
            )
        review = catalog.hold_relocated_publication(
            conn,
            run_id=root.name,
            original_control_root=original,
            owner_fd=fd,
            owner_path=owner,
            expected_inventory_sha256=sha,
            deadline_at=time.monotonic() + 5,
        )
        catalog.step(
            conn,
            owner_fd=fd,
            owner_path=owner,
            limit=1,
            deadline_at=time.monotonic() + 5,
            reviewed_implementations={"f" * 64},
        )
        assert catalog.oldest_unresolved(conn)["disposition"] == "repair_required"
        assert "locator_relocated" in catalog.oldest_unresolved(conn)["repair_reason"]
        if legacy_owner:
            assert not conn.execute(
                "SELECT 1 FROM sqlite_master WHERE name='jobagg_accepted_exits'"
            ).fetchone()
        else:
            assert conn.execute("SELECT count(*) FROM jobagg_accepted_exits").fetchone()[0] == 1
        proof = json.loads(
            conn.execute("SELECT proof_json FROM jobagg_legacy_controls").fetchone()[0]
        )
        assert ("acceptance_original_owner" in proof) == legacy_owner
        assert proof["publication_review_hold"] == review and "publication" not in proof
        with pytest.raises(ValueError, match="complete"):
            catalog.hold_relocated_publication(
                conn,
                run_id=root.name,
                original_control_root=original,
                owner_fd=fd,
                owner_path=owner,
                expected_inventory_sha256=sha,
                deadline_at=time.monotonic() + 5,
            )


def test_explicit_contradiction_hold_requires_reproduction_and_never_imports_acceptance(
    tmp_path, monkeypatch
):
    from jobagg import terminal_publication

    owner = tmp_path / "owner.lock"
    root = tmp_path / "old-run"
    controls = _accepted_bundle(root, owner)
    path = root / "publication_intent.json"
    path.write_text("{}")
    controls[path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
    failure = "Publication classification: recovery generation immutable binding differs"

    def classify(*args, **kwargs):
        raise ValueError(failure)

    monkeypatch.setattr(terminal_publication, "classify_run", classify)
    with shared_owner(owner) as fd, sqlite3.connect(":memory:") as conn:
        sha = catalog.prepare(
            conn, manifest=_manifest(root, owner, controls), owner_fd=fd, owner_path=owner
        )
        with pytest.raises(ValueError, match="failure_changed"):
            catalog.hold_publication_contradiction(
                conn,
                run_id=root.name,
                expected_failure="Publication classification: other",
                owner_fd=fd,
                owner_path=owner,
                expected_inventory_sha256=sha,
                deadline_at=time.monotonic() + 5,
            )
        assert conn.execute("SELECT count(*) FROM jobagg_legacy_review_holds").fetchone()[0] == 0
        catalog.hold_publication_contradiction(
            conn,
            run_id=root.name,
            expected_failure=failure,
            owner_fd=fd,
            owner_path=owner,
            expected_inventory_sha256=sha,
            deadline_at=time.monotonic() + 5,
        )
        catalog.step(
            conn,
            owner_fd=fd,
            owner_path=owner,
            limit=1,
            deadline_at=time.monotonic() + 5,
            reviewed_implementations={"f" * 64},
        )
        assert catalog.oldest_unresolved(conn)["disposition"] == "repair_required"
        assert not conn.execute(
            "SELECT 1 FROM sqlite_master WHERE name='jobagg_accepted_exits'"
        ).fetchone()


def test_changed_unverified_identity_cannot_advance_import(tmp_path):
    owner = tmp_path / "owner.lock"
    root = tmp_path / "archive"
    root.mkdir()
    path = root / "large-plan.json"
    path.write_bytes(b"original")
    info = path.stat()
    manifest = _manifest(root, owner, {})
    manifest["runs"][0].update(
        kind="held_control_set",
        run_id="held-" + hashlib.sha256(str(root).encode()).hexdigest(),
        unverified_files={
            path.name: [info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns]
        },
    )
    with shared_owner(owner) as fd, sqlite3.connect(":memory:") as conn:
        catalog.prepare(conn, manifest=manifest, owner_fd=fd, owner_path=owner)
        path.write_bytes(b"changed")
        with pytest.raises(ValueError, match="identity_changed"):
            catalog.step(
                conn,
                owner_fd=fd,
                owner_path=owner,
                limit=1,
                deadline_at=time.monotonic() + 5,
                reviewed_implementations=set(),
            )
        assert (
            conn.execute("SELECT after_ordinal FROM jobagg_legacy_control_state").fetchone()[0] == 0
        )
