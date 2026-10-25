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
