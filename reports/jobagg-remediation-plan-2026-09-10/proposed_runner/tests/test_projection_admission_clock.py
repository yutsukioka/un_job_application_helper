"""The publisher floor must not consume the separate projection window."""
import importlib.util
from datetime import datetime, timezone
from pathlib import Path
import sqlite3

import pytest
from jobagg import publication_work as work


def load_runner():
    path = Path(__file__).resolve().parents[1] / "runner.py"
    spec = importlib.util.spec_from_file_location("admission_clock_runner", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_equal_projection_and_publisher_budgets_allow_bounded_selection(monkeypatch, tmp_path):
    runner = load_runner()
    monkeypatch.setattr(runner.time, "monotonic", lambda: 100.)
    monkeypatch.setattr(runner.time, "time", lambda: 1000.)
    monkeypatch.setattr(work, "vector", lambda *a, **k: {"epoch":"test", "revalidation_reasons":[]})
    policy = dict(max_candidates=10,max_units=1,max_sources=1,max_targets=2,
                  max_bytes=4096,minimum_seconds=180,measurement_sha256="a"*64)
    deadline = datetime.fromtimestamp(1180, timezone.utc)
    config = {"phase_budgets":{"publisher_min_seconds":180}}
    conn = sqlite3.connect(":memory:")
    conn.executescript("""CREATE TABLE jobagg_dirty_current(unit,version,source_id,job_key,kind,first_changed_at,disposition);
    CREATE TABLE jobagg_dirty_versions(version,operation);
    CREATE TABLE jobagg_dirty_cohorts(source_id,last_service);
    INSERT INTO jobagg_dirty_current VALUES('health:test',1,'test',NULL,'health',0,'pending');
    INSERT INTO jobagg_dirty_versions VALUES(1,'fixture');""")
    args = dict(policy=policy,contract_sha256="a"*64,output_root=tmp_path,source_paths={"test":str(tmp_path/"test.sqlite3")})
    assert work.select(conn,**args,deadline_at=280.)["units"] == []
    result = work.select(conn,**args,deadline_at=runner.publication_selection_deadline(config,deadline,policy))
    assert len(result["units"]) == 1 and result["policy"] == policy
    assert runner.publication_selection_deadline(config,deadline,policy) == 460.


def test_unreserved_publisher_floor_still_refuses():
    runner = load_runner()
    with pytest.raises(runner.InvalidReport, match="exceeds reserved"):
        runner.publication_selection_deadline({"phase_budgets":{"publisher_min_seconds":179}},
            datetime.now(timezone.utc),{"minimum_seconds":180})
