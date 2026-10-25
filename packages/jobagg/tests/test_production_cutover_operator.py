"""Small one-time cutover sequencing and preservation regressions."""
import importlib.util
from pathlib import Path
import sqlite3

import pytest


@pytest.fixture
def operator(monkeypatch):
    scripts = Path(__file__).resolve().parents[1] / "scripts"
    monkeypatch.syspath_prepend(str(scripts))
    spec = importlib.util.spec_from_file_location("cutover_operator", scripts / "m1_production_cutover.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_pages_continue_without_manual_reinvocation(operator):
    seen = []
    def step():
        seen.append(len(seen))
        return {"complete": len(seen) == 5, "processed": 2}
    checks = []
    assert operator.loop_pages(step, lambda: checks.append(True))["complete"]
    assert len(seen) == len(checks) == 5


def test_page_invariant_failure_stops_without_retrying(operator):
    def step():
        raise ValueError("source changed")
    with pytest.raises(ValueError, match="source changed"):
        operator.loop_pages(step, lambda: None)


def test_domain_inventory_ignores_only_new_listing_reference(operator):
    conn = sqlite3.connect(":memory:")
    for table in ("jobs", "remediation_tasks", "remediation_attempts", "remediation_observations", "remediation_documents", "remediation_sources"):
        conn.execute("CREATE TABLE " + table + "(identity TEXT PRIMARY KEY, payload TEXT)")
        conn.executemany("INSERT INTO " + table + " VALUES(?,?)", [(str(n),"retained") for n in range(10)])
    before = operator.domain(conn)
    conn.execute("ALTER TABLE remediation_sources ADD COLUMN listing_event_version TEXT")
    conn.execute("UPDATE remediation_sources SET listing_event_version='imported-reference'")
    assert operator.domain(conn) == before
    conn.execute("UPDATE jobs SET payload='changed' WHERE identity='0'")
    assert operator.domain(conn) != before


@pytest.mark.parametrize("change", [None, "cursor", "epoch"])
def test_reviewed_reseal_preserves_completed_checkpoints(operator, tmp_path, change):
    import json
    database = tmp_path / "worker.sqlite3"
    policy = tmp_path / "policy"
    policy.mkdir()
    with sqlite3.connect(database) as conn:
        conn.execute("CREATE TABLE jobagg_legacy_control_state(after_ordinal INTEGER)")
        conn.execute("INSERT INTO jobagg_legacy_control_state VALUES(?)", (831 if change == "cursor" else 832,))
    with sqlite3.connect(policy / "authority.sqlite3") as conn:
        for table, count in (("attempts", 71888), ("evidence", 72357)):
            conn.execute("CREATE TABLE " + table + "(n INTEGER)")
            conn.execute("WITH RECURSIVE rows(n) AS (VALUES(1) UNION ALL SELECT n+1 FROM rows WHERE n<?) INSERT INTO " + table + " SELECT n FROM rows", (count,))
    old = {"epoch": "production", "operation_root": str(tmp_path), "implementation_sha256": "old"}
    old_path = tmp_path / "old.json"
    operator.save(old_path, old)
    journal = tmp_path / "progress.json"
    progress = {"descriptor_sha256": operator.sha(old_path), "completed": ["backups", "policy", "worker_schema"], "failure": {"message":"old guard"}}
    operator.save(journal, progress)
    operation = {**old, "implementation_sha256":"new", "resume_from": {
        "descriptor":str(old_path), "sha256":operator.sha(old_path),
        "progress_sha256":operator.sha(journal), "catalogue_after":832}}
    if change == "epoch":
        operation["epoch"] = "other"
    descriptor = tmp_path / "new.json"
    operator.save(descriptor, operation)
    if change:
        before = journal.read_bytes()
        with pytest.raises(ValueError, match="checkpoint_changed|descriptor_changed"):
            operator.resume_checkpoint(operation, descriptor, journal, progress, database,
                {"policy_authority_location":{"root":str(policy)}})
        assert journal.read_bytes() == before
    else:
        operator.resume_checkpoint(operation, descriptor, journal, progress, database,
            {"policy_authority_location":{"root":str(policy)}})
        actual = json.loads(journal.read_text())
        assert actual["completed"] == ["backups", "policy", "worker_schema"]
        assert actual["descriptor_sha256"] == operator.sha(descriptor)
        with sqlite3.connect(database) as conn:
            assert conn.execute("SELECT after_ordinal FROM jobagg_legacy_control_state").fetchone()[0] == 832
