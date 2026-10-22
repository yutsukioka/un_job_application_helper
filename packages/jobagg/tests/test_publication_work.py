import sqlite3
import time


from jobagg import accepted_evidence, publication_work as work
from jobagg.publication_projection import build_projection
from test_live_publication import setup, add_detail, run  # noqa: F401

# ruff: noqa: F811


def prepare(fixture):
    with fixture["worker"].connect() as conn:
        conn.execute(
            "CREATE TABLE remediation_sources(source_id TEXT PRIMARY KEY,last_list_at REAL,listing_ids TEXT,listing_proof TEXT)"
        )
        accepted_evidence.prepare(conn)
        accepted_evidence.backfill(conn, limit=100, deadline_at=time.monotonic() + 10)
        work.prepare(conn, contract_sha256="contract", epoch="epoch")
        while not work.bootstrap(conn, limit=10, deadline_at=time.monotonic() + 10):
            pass


def select(fixture, conn, **overrides):
    policy = dict(
        max_candidates=10,
        max_units=2,
        max_sources=1,
        max_targets=2,
        max_bytes=100_000_000,
        minimum_seconds=0.01,
        measurement_sha256="a" * 64,
    )
    policy.update(overrides)
    return work.select(
        conn,
        policy=policy,
        contract_sha256="contract",
        output_root=fixture["output"],
        source_paths={"test_custom_html": fixture["output"] / "test_jobs.sqlite3"},
        deadline_at=time.monotonic() + 10,
    )


def test_selected_projection_reads_only_admitted_dependencies_and_preserves_output(setup, tmp_path):
    add_detail(setup)
    prepare(setup)
    with setup["worker"].connect() as conn:
        selection = select(setup, conn)
        assert len(selection["units"]) == 1
    origin = sqlite3.connect(setup["worker"].path)
    destination = sqlite3.connect(tmp_path / "selected.sqlite3")
    try:
        receipt = build_projection(
            origin,
            destination,
            selection=selection,
            deadline_at=time.monotonic() + 10,
            max_bytes=2_000_000,
        )
        assert receipt["manifest"]["version"] == "publication-projection-3"
        assert (
            destination.execute("SELECT count(*) FROM remediation_observations").fetchone()[0] == 1
        )
        assert destination.execute("SELECT count(*) FROM remediation_attempts").fetchone()[0] == 1
        assert (
            destination.execute("SELECT count(*) FROM jobagg_accepted_evidence").fetchone()[0] == 1
        )
    finally:
        origin.close()
        destination.close()
    from jobagg.pipelines.live_publication import plan_publication

    broad = plan_publication(setup["worker"].path, setup["registry"], setup["output"])
    selected = plan_publication(tmp_path / "selected.sqlite3", setup["registry"], setup["output"])
    assert selected["changes"] == broad["changes"]
    assert selected["rejected"] == broad["rejected"]


def test_atomic_dirty_versions_cas_contract_and_held_work_never_false_noop(setup):
    add_detail(setup)
    prepare(setup)
    with setup["worker"].connect() as conn:
        selection = select(setup, conn)
        key = selection["units"][0]["job_key"]
        conn.execute("UPDATE jobs SET location=? WHERE job_key=?", ("newer", key))
        work.acknowledge(conn, selection, result_sha256="b" * 64)
        assert work.vector(conn, contract_sha256="contract", now=time.time())["empty"] is False
        fresh = select(setup, conn)
        work.acknowledge(conn, fresh, result_sha256="c" * 64)
        assert work.vector(conn, contract_sha256="contract", now=time.time())["empty"] is True
        assert (
            work.vector(conn, contract_sha256="changed-parser", now=time.time())["empty"] is False
        )
        conn.execute("UPDATE jobagg_dirty_state SET next_transition_at=1")
        assert work.vector(conn, contract_sha256="contract", now=time.time())[
            "revalidation_reasons"
        ] == ["time_transition_due"]


def test_oversized_unit_remains_explicit_hold_and_cannot_clear_backlog(setup):
    add_detail(setup)
    prepare(setup)
    with setup["worker"].connect() as conn:
        selection = select(setup, conn, max_bytes=1)
        assert not selection["units"]
        assert selection["held"][0]["reason"] == "oversized_unit_requires_reviewed_measured_path"
        work.acknowledge(conn, selection, result_sha256="b" * 64)
        assert work.vector(conn, contract_sha256="contract", now=time.time())["held"]


def test_time_and_delete_invalidation_never_disappear_behind_old_acknowledgement(setup):
    add_detail(setup)
    prepare(setup)
    with setup['worker'].connect() as conn:
        selection=select(setup,conn)
        key=selection['units'][0]['job_key']
        work.acknowledge(conn,selection,result_sha256='a'*64)
        conn.execute("UPDATE jobs SET closes_at='2000-01-01T00:00:00+00:00' WHERE job_key=?",(key,))
        metadata=select(setup,conn)
        work.acknowledge(conn,metadata,result_sha256='b'*64)
        assert work.vector(conn,contract_sha256='contract',now=time.time())['empty'] is False
        assert work.advance_time(conn,now=time.time(),limit=1)==1
        timed=select(setup,conn)
        assert timed['units'][0]['operation']=='time_transition_revalidation'
        work.acknowledge(conn,metadata,result_sha256='c'*64)
        assert work.vector(conn,contract_sha256='contract',now=time.time())['pending']
        conn.execute('DELETE FROM remediation_observations WHERE job_key=?',(key,))
        missing=select(setup,conn)
        assert any(item['reason']=='unknown_or_disabled_scope_requires_review' for item in missing['held'])
