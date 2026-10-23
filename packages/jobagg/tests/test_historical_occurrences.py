import hashlib
import json
from pathlib import Path
import sqlite3
import time

import pytest

from jobagg import historical_destinations as destinations, historical_occurrences as occurrences
from jobagg import publication_fence as fence, runtime_authority as authority
from jobagg.remediation_worker import shared_owner
from jobagg.db import JobDatabase
from test_live_publication import setup, add_detail, run  # noqa: F401

# ruff: noqa: F811


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


@pytest.fixture
def mapped(setup):
    root = setup["root"]
    old = setup["output"]
    new = root / "output"
    old.rename(new)
    setup["output"] = new
    targets = [new / "test_jobs.sqlite3", new / "all_jobs.sqlite3"]
    owner = root / "owner.lock"
    with shared_owner(owner) as fd:
        for p in targets:
            with JobDatabase(p).connect() as c:
                fence.prepare_target_schema(c)
                fence.activate(c, owner_fd=fd, owner_path=owner, epoch="fixture")
        add_detail(setup)
        with fence.writer_scope(fd, owner, new):
            result = run(setup, execute=True)
        plan_path = Path(result["plan_path"])
        result_path = plan_path.parent / "result.json"
        plan = json.loads(plan_path.read_text())
        producer = json.loads(result_path.read_text())
        original = {
            str(p): "/original-production/" + root.name + "/output/" + p.name for p in targets
        }
        for change in plan["changes"]:
            for d in change["destinations"]:
                d["path"] = original[d["path"]]
        plan_path.write_text(json.dumps(plan))
        producer["plan_sha256"] = sha(plan_path)
        producer.pop("readback_verified", None)
        result_path.write_text(json.dumps(producer))
        snapshot = root / "snapshot.json"
        snapshot.write_text(
            json.dumps(
                {
                    "schema": "jobagg-fenced-rehearsal-snapshot-1",
                    "complete": True,
                    "snapshot_root": str(root),
                    "owner": str(owner),
                    "files": [
                        {
                            "copy": "output/" + p.name,
                            "source": original[str(p)],
                            "sha256": sha(p),
                            "source_identity": [1, 2, p.stat().st_size, 3, 4],
                        }
                        for p in targets
                    ],
                }
            )
        )
        with setup["worker"].connect() as c:
            authority.activate(
                c,
                epoch="fixture",
                owner_fd=fd,
                owner_path=owner,
                bootstrap_receipt={"reconciled": True, "source_sha256": sha(snapshot)},
            )
        scope = fence.publication_scope(setup["registry"], new)
        with setup["worker"].connect() as c:
            receipt = destinations.prepare(
                c,
                snapshot,
                boundary=result["publication_boundary"],
                scope=scope,
                owner_fd=fd,
                deadline_at=time.monotonic() + 10,
            )
            mapping = destinations.Destinations(
                c,
                receipt,
                boundary=result["publication_boundary"],
                owner_fd=fd,
                deadline_at=time.monotonic() + 10,
            )
        yield {
            "setup": setup,
            "targets": targets,
            "owner": owner,
            "fd": fd,
            "mapping": mapping,
            "receipt": receipt,
            "snapshot": snapshot,
            "boundary": result["publication_boundary"],
            "plan": plan_path,
            "result": result_path,
        }


def make_index(mapped, *, relocated=False, reviewed=True):
    root = mapped["setup"]["root"]
    path = root / "index.sqlite3"
    ref = {
        "plan_path": str(mapped["plan"]),
        "plan_sha256": sha(mapped["plan"]),
        "result_path": str(mapped["result"]),
        "result_sha256": sha(mapped["result"]),
    }
    if relocated:
        backup = root / "backup"
        backup.mkdir()
        for label in ("plan", "result"):
            original = Path(ref[label + "_path"])
            target = backup / original.name
            original.rename(target)
            ref[label + "_location"] = str(target)
        if reviewed:
            ref["locator_review_sha256"] = "a" * 64
    with sqlite3.connect(path) as c:
        occurrences.prepare(c, references=[ref], mapping_sha256=mapped["receipt"]["sha256"])
        assert occurrences.step(
            c,
            mapping=mapped["mapping"],
            limit=1,
            deadline_at=time.monotonic() + 10,
            max_plan_bytes=16 * 1024**2,
        )["complete"]
    path.chmod(0o400)
    return occurrences.Index(
        path,
        expected_sha256=sha(path),
        mapping=mapped["mapping"],
        deadline_at=time.monotonic() + 10,
    )


def artifact(mapped):
    with mapped["setup"]["worker"].connect() as c:
        row = dict(c.execute("SELECT * FROM remediation_attempts LIMIT 1").fetchone())
    receipt = json.loads(row["evidence"])
    p = Path(receipt["detail_path"])
    return row, json.loads(p.read_text()), sha(p)


def test_exact_original_mapping_and_legacy_journals_prove_occurrence(mapped):
    index = make_index(mapped)
    attempt, value, digest = artifact(mapped)
    assert index.matches(
        attempt, value, digest, mapped["targets"], deadline_at=time.monotonic() + 5
    )
    value["proof"]["observed_at"] = "a different occurrence"
    assert not index.matches(
        attempt, value, digest, mapped["targets"], deadline_at=time.monotonic() + 5
    )
    with pytest.raises(ValueError, match="not_reviewed"):
        mapped["mapping"].resolve("/same-basename/" + mapped["targets"][0].name)


def test_rehashed_forged_mapping_is_rejected_against_actual_snapshot(mapped):
    value = json.loads(json.dumps(mapped["receipt"]))
    k = next(iter(value["destinations"]))
    value["destinations"]["/forged/" + Path(k).name] = value["destinations"].pop(k)
    value["sha256"] = destinations.digest({k: v for k, v in value.items() if k != "sha256"})
    with (
        mapped["setup"]["worker"].connect() as c,
        pytest.raises(ValueError, match="not_snapshot_derived"),
    ):
        destinations.Destinations(
            c,
            value,
            boundary=mapped["boundary"],
            owner_fd=mapped["fd"],
            deadline_at=time.monotonic() + 5,
        )


@pytest.mark.parametrize("change", ["plan", "result", "index", "target"])
def test_changed_selected_dependency_or_destination_cannot_reuse_index(mapped, change):
    index = make_index(mapped)
    attempt, value, digest = artifact(mapped)
    if change in ("plan", "result"):
        with mapped[change].open("a") as stream:
            stream.write(" ")
    elif change == "index":
        index.path.chmod(0o600)
        with index.path.open("ab") as stream:
            stream.write(b"x")
    else:
        with fence.writer_scope(mapped["fd"], mapped["owner"], mapped["setup"]["output"]):
            with JobDatabase(mapped["targets"][0]).connect() as c:
                c.execute("UPDATE jobs SET title='legitimate later mutation'")
    with pytest.raises(ValueError, match="changed"):
        index.matches(attempt, value, digest, mapped["targets"], deadline_at=time.monotonic() + 5)


def test_missing_exact_commit_is_held_even_with_published_producer(mapped):
    with fence.writer_scope(mapped["fd"], mapped["owner"], mapped["setup"]["output"]):
        with JobDatabase(mapped["targets"][0]).connect() as c:
            c.execute("DELETE FROM jobagg_publication_receipts")
    # The original destination binding must fail before an index can be rebuilt.
    with pytest.raises(ValueError, match="fence_changed"):
        mapped["mapping"].validate()


def test_interruption_before_checkpoint_resumes_exact_contract(mapped):
    path = mapped["setup"]["root"] / "resume.sqlite3"
    ref = {
        "plan_path": str(mapped["plan"]),
        "plan_sha256": sha(mapped["plan"]),
        "result_path": str(mapped["result"]),
        "result_sha256": sha(mapped["result"]),
    }
    with sqlite3.connect(path) as c:
        occurrences.prepare(c, references=[ref], mapping_sha256=mapped["receipt"]["sha256"])
        with pytest.raises(TimeoutError):
            occurrences.step(
                c,
                mapping=mapped["mapping"],
                limit=1,
                deadline_at=time.monotonic() - 1,
                max_plan_bytes=16 * 1024**2,
            )
        assert c.execute("SELECT count(*) FROM plans").fetchone()[0] == 0
        occurrences.prepare(c, references=[ref], mapping_sha256=mapped["receipt"]["sha256"])
        assert occurrences.step(
            c,
            mapping=mapped["mapping"],
            limit=1,
            deadline_at=time.monotonic() + 10,
            max_plan_bytes=16 * 1024**2,
        )["complete"]
        ref["result_sha256"] = "0" * 64
        with pytest.raises(ValueError, match="contract_changed"):
            occurrences.prepare(c, references=[ref], mapping_sha256=mapped["receipt"]["sha256"])


def test_current_boundary_does_not_replace_missing_historical_commit(mapped):
    with fence.writer_scope(mapped["fd"], mapped["owner"], mapped["setup"]["output"]):
        with JobDatabase(mapped["targets"][0]).connect() as c:
            c.execute("DELETE FROM jobagg_publication_receipts")
    # Explicitly derive a fresh, valid copy boundary; it cannot conjure a missing
    # original publication-key/generation journal entry.
    boundary = fence.seal_boundary(
        mapped["targets"],
        {r["path"]: {"sha256": r["sha256"]} for r in mapped["boundary"]["exports"]},
        output_root=mapped["setup"]["output"],
    )
    with mapped["setup"]["worker"].connect() as c:
        receipt = destinations.prepare(
            c,
            mapped["snapshot"],
            boundary=boundary,
            scope=mapped["receipt"]["scope"],
            owner_fd=mapped["fd"],
            deadline_at=time.monotonic() + 10,
        )
        mapped["receipt"] = receipt
        mapped["mapping"] = destinations.Destinations(
            c, receipt, boundary=boundary, owner_fd=mapped["fd"], deadline_at=time.monotonic() + 10
        )
    index = make_index(mapped)
    attempt, value, digest = artifact(mapped)
    assert not index.matches(
        attempt, value, digest, mapped["targets"], deadline_at=time.monotonic() + 5
    )


def test_index_cannot_close_unknown_implementation_or_partial_generation(mapped):
    from jobagg import runtime_control_catalog as catalogue

    index = make_index(mapped)
    for disposition in (
        "repair_required",
        "generation_pending",
        "publication_reconciliation_required",
    ):
        with sqlite3.connect(":memory:") as c:
            c.executescript(
                "CREATE TABLE jobagg_legacy_controls(run_id,control_root,proof_json,disposition);"
                "CREATE TABLE jobagg_accepted_exit_runs(run_id,proof_sha256);"
            )
            c.execute(
                "INSERT INTO jobagg_legacy_controls VALUES('run','/absent','{}',?)", (disposition,)
            )
            with pytest.raises(ValueError, match="reviewed_accepted_terminal"):
                catalogue.close_reconciled(
                    c,
                    "run",
                    worker_database="/absent",
                    destinations=[],
                    publication_boundary=mapped["boundary"],
                    expected_publication_scope=mapped["receipt"]["scope"],
                    owner_fd=mapped["fd"],
                    owner_path=mapped["owner"],
                    deadline_at=time.monotonic() + 5,
                    historical_index=index,
                )


def test_reviewed_backup_location_preserves_logical_generation_and_hash(mapped):
    attempt, value, digest = artifact(mapped)
    index = make_index(mapped, relocated=True)
    assert index.matches(
        attempt, value, digest, mapped["targets"], deadline_at=time.monotonic() + 5
    )
    assert not mapped["plan"].exists() and not mapped["result"].exists()
    physical = mapped["setup"]["root"] / "backup/plan.json"
    physical.write_text("{}")
    with pytest.raises(ValueError, match="dependency_changed"):
        index.matches(attempt, value, digest, mapped["targets"], deadline_at=time.monotonic() + 5)


def test_unreviewed_backup_location_cannot_become_a_generation_proof(mapped):
    with pytest.raises(ValueError, match="relocation_review_required"):
        make_index(mapped, relocated=True, reviewed=False)


def test_generation_review_requires_exact_committed_journals(mapped):
    index = make_index(mapped)
    generation_id = json.loads(mapped["plan"].read_text())["generation_id"]
    proof = index.generation(generation_id, deadline_at=time.monotonic() + 5)
    assert proof["producer_complete"]
    assert proof["changes"] == 1
    assert not proof["legacy_readback_flag_present"]
    with pytest.raises(ValueError, match="not_indexed"):
        index.generation("f" * 32, deadline_at=time.monotonic() + 5)


def test_recovered_generation_review_keeps_acceptance_and_export_debt(mapped, tmp_path):
    from jobagg import runtime_control_catalog as catalogue, acceptance_catalog
    from test_legacy_wrapper_reconciliation import setup_catalog

    index = make_index(mapped, relocated=True)
    result = json.loads((tmp_path / "backup/result.json").read_text())
    # Seed an already imported catalogue fixture; its prior accepted-exit
    # authority is an input, never produced by this generation-only review.
    with sqlite3.connect(tmp_path / "review-worker.sqlite3") as conn:
        evidence = tmp_path / "native-controls"
        evidence.mkdir()
        setup_catalog(evidence, conn, mapped["fd"], mapped["owner"])
        acceptance_catalog.prepare(conn)
        conn.execute("INSERT INTO jobagg_accepted_exit_runs VALUES('worker',?,'{}')", ("d" * 64,))
        cursor = conn.execute("SELECT proof_json FROM jobagg_legacy_controls WHERE run_id='worker'")
        proof = json.loads(cursor.fetchone()[0])
        proof.update(
            accepted_exit_sha256="d" * 64,
            publication={
                "known_deficits": [{"code": "missing_generation_result"}],
                "generation_ids": [result["generation_id"]],
                "result": {"publication": result},
            },
        )
        conn.execute(
            "UPDATE jobagg_legacy_controls SET disposition='generation_pending',proof_json=? WHERE run_id='worker'",
            (json.dumps(proof),),
        )
        original = catalogue.receipt(conn)
        authority.activate(
            conn,
            epoch="fixture",
            owner_fd=mapped["fd"],
            owner_path=mapped["owner"],
            bootstrap_receipt={
                "reconciled": True,
                "source_sha256": "a" * 64,
                "control_catalogue_sha256": original["sha256"],
            },
        )
        conn.commit()
        with authority.controller(conn, run_id=None, owner_fd=mapped["fd"]):
            review = catalogue.reconcile_recovered_generation(
                conn,
                "worker",
                historical_index=index,
                owner_fd=mapped["fd"],
                owner_path=mapped["owner"],
                deadline_at=time.monotonic() + 5,
            )
            conn.commit()
        assert review["same_generation_committed"] and not review["publication_replayed"]
        assert review["current_accepted_exports_reconciliation_required"]
        assert not review["completeness_certified"]
        assert catalogue.oldest_unresolved(conn) is not None
        assert (
            conn.execute(
                "SELECT disposition FROM jobagg_legacy_controls WHERE run_id='worker'"
            ).fetchone()[0]
            == "publication_reconciliation_required"
        )
        assert catalogue.receipt(conn) == original
        assert (
            conn.execute("SELECT proof_sha256 FROM jobagg_accepted_exit_runs").fetchone()[0]
            == "d" * 64
        )
