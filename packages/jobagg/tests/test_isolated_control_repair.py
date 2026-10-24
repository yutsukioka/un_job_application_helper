"""Real native idle control rollback; no production-state or head resets."""

from contextlib import contextmanager
import json
from pathlib import Path
import shutil
import sqlite3
import time

import pytest

from jobagg import isolated_control_repair as repair, runtime_authority as authority
from jobagg import runtime_source_head as source
from jobagg.remediation_worker import implementation_hash, shared_owner
from test_runtime_containment import staged, write
from test_live_publication import setup, sha  # noqa: F401

# ruff: noqa: F811


@contextmanager
def case(setup, monkeypatch):
    monkeypatch.setattr(repair, "_drained", lambda _: {"matching_processes": 0})
    with staged(setup) as (conn, fd, root, _, record):
        before = authority.validate(conn)
        marker = root / "copy-maintenance"
        marker.write_text("copy-owned operator token")
        config = root / "repair-configuration.json"
        configsha = write(
            config,
            {
                "shared_lock_path": before["owner_path"],
                "publication_worker_database": str(root / "worker/jobs.sqlite3"),
                "state_dir": str(root / "dispatcher"),
                "maintenance_file": str(marker),
            },
        )
        before_path = root / "before.json"
        beforesha = write(
            before_path,
            {
                "source_state": before,
                "configuration_sha256": configsha,
                "copied_marker_sha256": sha(marker),
            },
        )
        with pytest.raises(sqlite3.OperationalError, match="disk I/O"):
            with authority.controller(conn, run_id=None, owner_fd=fd):
                authority.begin_publication_only(
                    conn,
                    "never-admitted",
                    configsha,
                    control_dir=root / "dispatcher/runs/never-admitted",
                    publication_scope={},
                    started_at="fixture",
                )
                raise sqlite3.OperationalError("disk I/O error")
        conn.rollback()
        # Test preparation matches the already checkpointed real isolated copy.
        assert tuple(conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()) == (0, 0, 0)
        assert authority.state(conn) == before
        with pytest.raises(ValueError, match="uncertain_transaction"):
            authority.validate(conn)
        head_path = Path(json.loads(before["bootstrap_json"])["restore_head_path"])
        failure_path = root / "failed-rehearsal.json"
        failuresha = write(
            failure_path,
            {
                "configuration_sha256": configsha,
                "production_activated": False,
                "production_fence_removed": False,
                "copy_marker_restored": True,
                "failure": "OperationalError('disk I/O error')",
                "traceback": "fixture: begin_publication_only",
                "worker_dispatched": False,
                "publisher_dispatched": False,
            },
        )
        review = {
            "schema": "isolated-idle-control-rollback-review-1",
            "control_only_rollback_reviewed": True,
            "implementation_sha256": implementation_hash(),
            "snapshot_root": str(root),
            "database": source._database(conn),
            "before_path": str(before_path),
            "before_sha256": beforesha,
            "configuration_path": str(config),
            "failure_path": str(failure_path),
            "failure_sha256": failuresha,
            "registry_path": str(setup["registry"]),
            "registry_sha256": sha(setup["registry"]),
            "head_sha256": sha(head_path),
        }
        yield conn, fd, root, review, head_path


def preview(root, review, fd):
    path = root / "review.json"
    write(path, review)
    return repair.preview(
        review_path=path, review_sha256=sha(path), owner_fd=fd, deadline_at=time.monotonic() + 20
    )


def execute(root, plan, fd, fault=lambda _: None):
    path = root / "plan.json"
    write(path, plan)
    return repair.execute(
        plan_path=path,
        plan_sha256=sha(path),
        intent_path=root / "repair-intent.json",
        owner_fd=fd,
        deadline_at=time.monotonic() + 20,
        fault=fault,
    )


def test_exact_idle_control_rollback_changes_only_token_and_keeps_head_and_all_debt(
    setup, monkeypatch
):
    with case(setup, monkeypatch) as (conn, fd, root, review, head):
        original_head = head.read_bytes()
        snapshots = {
            t: [tuple(r) for r in conn.execute('SELECT * FROM "' + t + '"')]
            for (t,) in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
            if t != "jobagg_runtime_state"
        }
        plan = preview(root, review, fd)
        assert authority.state(conn) == plan["before"]
        result = execute(root, plan, fd)
        assert authority.validate(conn) == plan["after"] and head.read_bytes() == original_head
        assert (
            result["independent_head_rewritten"] is False and not result["domain_writes_permitted"]
        )
        assert execute(root, plan, fd) == result
        for table, expected in snapshots.items():
            assert [tuple(r) for r in conn.execute('SELECT * FROM "' + table + '"')] == expected
        assert (root / "repair-intent.json.completed.json").is_file()


@pytest.mark.parametrize(
    "change",
    [
        "review",
        "before_hash",
        "active",
        "revision",
        "schema",
        "replacement",
        "alias",
        "head",
        "marker",
        "accepted",
        "run",
        "generation",
        "registry",
    ],
)
def test_unknown_or_changed_state_never_reaches_token_mutation(setup, monkeypatch, change):
    with case(setup, monkeypatch) as (conn, fd, root, review, head):
        if change == "review":
            review["control_only_rollback_reviewed"] = False
        if change == "before_hash":
            review["before_sha256"] = "a" * 64
        if change == "active":
            p = Path(review["before_path"])
            v = json.loads(p.read_text())
            v["source_state"]["phase"] = "worker"
            review["before_sha256"] = write(p, v)
        if change == "revision":
            conn.create_function("jobagg_runtime_mutation_allowed", 0, lambda: 1)
            conn.execute("UPDATE jobagg_runtime_state SET revision=revision+1")
            conn.commit()
        if change == "schema":
            conn.execute("DROP TRIGGER runtime_state_control_guard")
            conn.commit()
        if change == "replacement":
            db = root / "worker/jobs.sqlite3"
            replacement = root / "replacement.sqlite3"
            shutil.copyfile(db, replacement)
            replacement.replace(db)
        if change == "alias":
            alias = root / "alias.sqlite3"
            alias.symlink_to(root / "worker/jobs.sqlite3")
            review["database"]["path"] = str(alias)
        if change == "head":
            v = json.loads(head.read_text())
            v["minimum_revision"] += 1
            review["head_sha256"] = write(head, v)
        if change == "marker":
            (root / "copy-maintenance").write_text("another operator")
        if change == "accepted":
            p = root / "dispatcher/pending_accepted_work.json"
            p.parent.mkdir()
            p.write_text("{}")
        if change == "run":
            (root / "dispatcher/runs/unresolved").mkdir(parents=True)
        if change == "generation":
            p = root / "output/.jobagg-publication-state.json"
            v = json.loads(p.read_text())
            v["state"] = "publishing"
            write(p, v)
        if change == "registry":
            review["registry_sha256"] = "a" * 64
        token = authority.state(conn)["source_transaction_id"]
        expected = {
            "review": "explicit_matching_review",
            "before_hash": "control_hash_changed",
            "active": "only_initial_idle",
            "revision": "state_changed_or_partial_commit",
            "schema": "schema_binding_changed",
            "replacement": "database_identity_changed",
            "alias": "direct_path_required",
            "head": "unknown_or_domain_transaction",
            "marker": "maintenance_fence_changed",
            "accepted": "accepted_marker_present",
            "run": "run_or_publication_controls_present",
            "generation": "unresolved_generation",
            "registry": "registry_changed",
        }[change]
        with pytest.raises((ValueError, OSError), match=expected):
            preview(root, review, fd)
        assert authority.state(conn)["source_transaction_id"] == token
        assert not (root / "repair-intent.json").exists()


@pytest.mark.parametrize("phase", ["before_update", "before_commit", "after_commit"])
def test_same_intent_resumes_at_either_side_of_one_token_commit(setup, monkeypatch, phase):
    with case(setup, monkeypatch) as (conn, fd, root, review, head):
        plan = preview(root, review, fd)
        original_head = head.read_bytes()

        def fault(at):
            if at == phase:
                raise RuntimeError("injected repair interruption")

        with pytest.raises(RuntimeError, match="injected"):
            execute(root, plan, fd, fault)
        assert head.read_bytes() == original_head
        assert authority.state(conn) == (
            plan["after"] if phase == "after_commit" else plan["before"]
        )
        assert execute(root, plan, fd)["state"] == plan["after"]


def test_domain_writes_are_refused_by_the_repair_authorizer(setup, monkeypatch):
    with case(setup, monkeypatch) as (conn, fd, root, review, head):
        plan = preview(root, review, fd)
        correct = repair._cas

        def forbidden(c, before, after):
            c.execute("UPDATE jobs SET description='must not overwrite' ")

        monkeypatch.setattr(repair, "_cas", forbidden)
        with pytest.raises(sqlite3.DatabaseError, match="authorized"):
            execute(root, plan, fd)
        assert authority.state(conn) == plan["before"]
        monkeypatch.setattr(repair, "_cas", correct)
        execute(root, plan, fd)


def test_wrong_owner_and_late_fence_change_refuse_commit(setup, monkeypatch):
    with case(setup, monkeypatch) as (conn, fd, root, review, head):
        with shared_owner(root / "wrong-owner") as wrong:
            with pytest.raises(ValueError, match="descriptor_differs"):
                preview(root, review, wrong)
        plan = preview(root, review, fd)

        def fault(at):
            if at == "before_commit":
                (root / "copy-maintenance").write_text("changed fence")

        with pytest.raises(sqlite3.DatabaseError, match="authorized"):
            execute(root, plan, fd, fault)
        assert authority.state(conn) == plan["before"]


def test_expired_budget_and_process_pressure_cannot_certify_preview(setup, monkeypatch):
    with case(setup, monkeypatch) as (conn, fd, root, review, head):

        def live(_):
            raise ValueError("isolated_repair_writer_or_publisher_present")

        monkeypatch.setattr(repair, "_drained", live)
        with pytest.raises(ValueError, match="publisher_present"):
            preview(root, review, fd)
        p = root / "review.json"
        with pytest.raises(TimeoutError):
            repair.preview(
                review_path=p, review_sha256=sha(p), owner_fd=fd, deadline_at=time.monotonic() - 1
            )
        assert not (root / "repair-intent.json").exists()
