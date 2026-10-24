"""Current shared-state containment retains historical debt and exact gates."""

from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import time

import pytest

from jobagg import runtime_authority as authority, runtime_control_catalog as catalogue
from jobagg import runtime_holds as holds, runtime_containment as containment, historical_artifacts
from jobagg import publication_fence as fence, accepted_evidence
from jobagg.db import JobDatabase
from jobagg.pipelines.worker_policy import SharedPolicy
from jobagg.policy_authority import migrate
from jobagg.remediation_worker import shared_owner, implementation_hash
from jobagg.remediation_scheduling import task_key
from test_live_publication import setup, add_detail, run, sha  # noqa: F401
from test_publication_work import prepare
from test_legacy_wrapper_reconciliation import setup_catalog

# ruff: noqa: F811


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True))
    return sha(path)


@contextmanager
def staged(setup, *, missing_charge=False):
    outer = setup["root"]
    root = outer / "isolated"
    root.mkdir()
    (root / "worker").mkdir()
    old = setup["worker"].path
    new = root / "worker/jobs.sqlite3"
    old.rename(new)
    setup["worker"] = JobDatabase(new)
    setup["output"].rename(root / "output")
    setup["output"] = root / "output"
    record, proof = add_detail(setup)
    with setup["worker"].connect() as conn:
        conn.execute("ALTER TABLE remediation_tasks ADD COLUMN attempts INTEGER NOT NULL DEFAULT 1")
    original = outer / "original"
    original.mkdir()
    (original / "jobs.sqlite3").write_bytes(b"independent original fixture")
    marker = original / "maintenance"
    marker.write_text("operator fixture fence")
    owner = original / "owner.lock"
    with shared_owner(owner) as fd:
        prepare(setup, owner_path=owner, owner_fd=fd)
        for path in setup["output"].glob("*.sqlite3"):
            with JobDatabase(path).connect() as c:
                fence.prepare_target_schema(c)
                fence.activate(c, owner_fd=fd, owner_path=owner, epoch="fixture")
        with fence.writer_scope(fd, owner, setup["output"]):
            published = run(setup, execute=True)
        bootstrap = outer / "policy-bootstrap.json"
        write(
            bootstrap,
            {
                "schema_version": 1,
                "shared_lock": str(owner),
                "scope_source_ids": [record.source_id],
                "reviewed_at": __import__("datetime")
                .datetime.now(__import__("datetime").timezone.utc)
                .isoformat(),
                "prior_writers_reviewed": True,
                "no_unmigrated_policy_state": True,
                "evidence": [],
                "detail_attempts": [],
                "host_states": {},
                "source_holds": {},
            },
        )
        policy = SharedPolicy(owner, [record.source_id], bootstrap)
        policy.root = root / "policy"
        policy.marker = root / "legacy-policy.json"
        policy.initialize()
        if not missing_charge:
            policy.reserve(
                "accepted-001", record.source_id, "detail", time.time() - 10, root, "detail-001"
            )
        migrate(
            policy,
            owner_fd=fd,
            epoch="fixture-policy",
            pauses={record.source_id: 1},
            reviewed_writers=[{"name": "fixture", "fenced": True}],
        )
        cfg = original / "dispatcher.json"
        cfgsha = write(
            cfg,
            {
                "maintenance_file": str(marker),
                "shared_lock_path": str(owner),
                "publication_worker_database": str(original / "jobs.sqlite3"),
                "state_dir": str(original / "runs"),
            },
        )
        snapshot = root / "snapshot.json"
        snapshotsha = write(
            snapshot,
            {
                "snapshot_root": str(root),
                "complete": True,
                "production_config": str(cfg),
                "production_config_sha256": cfgsha,
                "fence_sha256": sha(marker),
            },
        )
        location = {"root": str(policy.root), "epoch": "fixture-policy"}
        configpath = root / "candidate.json"
        configsha = write(configpath, {"policy_authority_location": location})
        with setup["worker"].connect() as conn:
            evidence = root / "historical-controls"
            evidence.mkdir()
            setup_catalog(evidence, conn, fd, owner)
            imported = catalogue.receipt(conn)
            heads = root / "heads"
            heads.mkdir(mode=0o700)
            authority.activate(
                conn,
                epoch="fixture",
                owner_fd=fd,
                owner_path=owner,
                restore_head_root=heads,
                bootstrap_receipt={
                    "reconciled": True,
                    "source_sha256": snapshotsha,
                    "control_catalogue_sha256": imported["sha256"],
                    "publication_boundary": published["publication_boundary"],
                },
            )
            conn.commit()
            assessment = historical_artifacts.assess(
                conn, "accepted-001", deadline_at=time.monotonic() + 5
            )
            result = root / "current-detail.json"
            write(
                result,
                {
                    "job_key": record.identity_key(),
                    "observation_sha256": accepted_evidence.digest(proof),
                    "disposition": "matched",
                    "retained_evidence": [assessment],
                },
            )
            with authority.controller(conn, run_id=None, owner_fd=fd):
                for scope, identity in (
                    ("unit", "listing:" + record.source_id),
                    ("task", task_key(record.source_id, "listing")),
                ):
                    holds.put(
                        conn,
                        scope=scope,
                        identity=identity,
                        reason="listing not independently accepted",
                        evidence_sha256="a" * 64,
                        next_condition="review exact retained listing",
                        owner_fd=fd,
                    )
                conn.commit()
            controls = [
                {
                    "run_id": r[0],
                    "disposition": r[1],
                    "proof_sha256": hashlib.sha256(r[2].encode()).hexdigest(),
                    "action": "retain_history_hold_no_replay",
                    "next_condition": "review original historical identity without replay or retirement",
                }
                for r in conn.execute(
                    "SELECT run_id,disposition,proof_json FROM jobagg_legacy_controls ORDER BY ordinal"
                )
            ]
            review = {
                "schema": "current-shared-state-containment-review-1",
                "epoch": "fixture",
                "snapshot_root": str(root),
                "inventory_sha256": imported["inventory_sha256"],
                "catalogue_sha256": imported["sha256"],
                "configuration_path": str(configpath),
                "config_sha256": configsha,
                "implementation_sha256": implementation_hash(),
                "policy_location": location,
                "registry_path": str(setup["registry"]),
                "registry_sha256": sha(setup["registry"]),
                "controls": controls,
                "current_details": [
                    {"job_key": record.identity_key(), "path": str(result), "sha256": sha(result)}
                ],
            }
            yield conn, fd, root, review, record


def review(conn, fd, root, value):
    path = root / "containment-review.json"
    write(path, value)
    return containment.prepare(
        conn,
        review_path=path,
        review_sha256=sha(path),
        owner_fd=fd,
        deadline_at=time.monotonic() + 20,
    )


def test_complete_review_contains_history_without_closing_it_and_hot_gate_is_compact(setup):
    with staged(setup) as (conn, fd, root, value, record):
        before = catalogue.receipt(conn)
        with authority.controller(conn, run_id=None, owner_fd=fd):
            result = review(conn, fd, root, value)
            conn.commit()
        assert result["held_records"] == 2
        assert catalogue.receipt(conn) == before and catalogue.oldest_unresolved(conn)
        trace = []
        conn.set_trace_callback(trace.append)
        assert containment.approved(
            conn,
            config_sha256=value["config_sha256"],
            implementation_sha256=value["implementation_sha256"],
            policy_location=value["policy_location"],
        )
        assert not any("FROM jobagg_legacy_controls" in sql for sql in trace)
        assert containment.summary(conn)["historical_control_records_held"] == 2
        assert not containment.summary(conn)["historical_replay_authorized"]
        assert not containment.approved(
            conn,
            config_sha256="b" * 64,
            implementation_sha256=value["implementation_sha256"],
            policy_location=value["policy_location"],
        )
        with authority.controller(conn, run_id=None, owner_fd=fd):
            containment.invalidate(conn, "explicit new review required")
            conn.commit()
        assert not containment.approved(
            conn,
            config_sha256=value["config_sha256"],
            implementation_sha256=value["implementation_sha256"],
            policy_location=value["policy_location"],
        )


@pytest.mark.parametrize("change", ["charge", "scope", "control", "body", "fields", "inventory"])
def test_unaccounted_or_changed_shared_state_cannot_exempt_history(setup, change):
    with staged(setup, missing_charge=change == "charge") as (conn, fd, root, value, record):
        if change == "scope":
            with authority.controller(conn, run_id=None, owner_fd=fd):
                conn.execute(
                    "DELETE FROM jobagg_legacy_review_holds WHERE run_id=?",
                    (holds.key("unit", "listing:" + record.source_id),),
                )
                conn.commit()
        if change == "control":
            value["controls"][0]["proof_sha256"] = "b" * 64
        if change == "inventory":
            value["controls"].pop()
        if change == "body":
            evidence = json.loads((root / "current-detail.json").read_text())
            Path(
                evidence["retained_evidence"][0]["captures"][0]["compressed_body"]["path"]
            ).write_bytes(b"changed")
        if change == "fields":
            with authority.controller(conn, run_id=None, owner_fd=fd):
                conn.execute("UPDATE jobs SET title='unreviewed change'")
                conn.commit()
        before = conn.total_changes
        expected = {
            "charge": "unaccounted_charge",
            "scope": "unscoped_listing",
            "control": "control_review_changed",
            "body": "evidence_identity_changed",
            "fields": "public_fields_changed",
            "inventory": "control_inventory_incomplete",
        }[change]
        with (
            authority.controller(conn, run_id=None, owner_fd=fd),
            pytest.raises(ValueError, match=expected),
        ):
            review(conn, fd, root, value)
        assert containment.get(conn) is None and conn.total_changes == before


@pytest.mark.parametrize("new_event", [False, True])
def test_held_only_current_batch_can_finish_without_empty_credit_or_hiding_acceptance(
    setup, new_event
):
    from jobagg import publication_work as work

    with staged(setup) as (conn, fd, root, value, record):
        with authority.controller(conn, run_id=None, owner_fd=fd):
            review(conn, fd, root, value)
            conn.commit()
        unit = conn.execute("SELECT unit,version FROM jobagg_dirty_current LIMIT 1").fetchone()
        with authority.controller(conn, run_id=None, owner_fd=fd):
            work.acknowledge(
                conn,
                {
                    "units": [],
                    "held": [
                        {
                            "unit": unit[0],
                            "version": unit[1],
                            "reason": "reviewed scoped current gap",
                        }
                    ],
                },
                result_sha256="a" * 64,
            )
            authority.begin(
                conn,
                "new-run",
                value["config_sha256"],
                publication_scope=fence.publication_scope(setup["registry"], setup["output"]),
            )
            conn.commit()
        args = {
            "accepted_progress": 0,
            "contract_sha256": "contract",
            "config_sha256": value["config_sha256"],
            "implementation_sha256": value["implementation_sha256"],
            "policy_location": value["policy_location"],
        }
        assert not authority.proven_empty(
            conn, "new-run", accepted_progress=0, contract_sha256="contract"
        )
        if new_event:
            with authority.controller(conn, run_id="new-run", owner_fd=fd):
                conn.execute(
                    "INSERT INTO jobagg_accepted_events VALUES('event','new-run','new-attempt','new-task',?,'detail','{}')",
                    (record.source_id,),
                )
                conn.commit()
            assert not containment.can_close_scoped_noop(conn, "new-run", **args)
            with pytest.raises(ValueError, match="zero_acceptance"):
                containment.close_scoped_noop(conn, "new-run", **args)
        else:
            assert containment.can_close_scoped_noop(conn, "new-run", **args)
            with authority.controller(conn, run_id="new-run", owner_fd=fd):
                containment.close_scoped_noop(conn, "new-run", **args)
                conn.commit()
            assert authority.validate(conn)["phase"] == "idle"
            assert not work.vector(conn, contract_sha256="contract", now=time.time())["empty"]
            assert catalogue.oldest_unresolved(conn) is not None
