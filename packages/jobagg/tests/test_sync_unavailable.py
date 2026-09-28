"""Unavailable vacancy outcomes stay distinct from detail success and failure."""

import importlib
import json
import sqlite3
from types import SimpleNamespace

import pytest

from jobagg.adapters.base import JobAdapter, register_adapter
from jobagg.db import JobDatabase
from jobagg.models import OrganizationSource
from jobagg.normalize import build_job
from jobagg.robots import RobotsPolicy
from jobagg.vacancy_outcomes import (
    EXPLICIT_VACANCY_UNAVAILABLE,
    DetailIdentityMismatch,
    VacancyUnavailable,
)

pipeline = importlib.import_module("jobagg.pipelines.sync_source")
POLICY = RobotsPolicy(honor_robots_txt=False, min_delay_seconds=0)


def sync(source, db, **kwargs):
    return pipeline.sync_source_with_selective_details(source, db=db, policy=POLICY, **kwargs)


@pytest.mark.parametrize("family", ["opcw", "taleo"])
@pytest.mark.parametrize("existing", [False, True])
def test_real_unavailable_adapter_preserves_listing_and_persists_neutral_outcome(
    tmp_path, monkeypatch, family, existing,
):
    if family == "opcw":
        source = OrganizationSource("opcw_talentsoft_candidatespace", "OPCW", "static_html", "https://jobs.opcw.org")
        external_id = "582"
        detail_url = "https://jobs.opcw.org/job/job-science-policy-adviser-p-5-_582.aspx"
        listing = f'<a href="{detail_url}">Current listing title</a>'
        detail = '<div id="ctl00_defaultValidationSummary"><ul><li>This vacancy does not exist/no longer exists on this site</li></ul></div>'
    else:
        source = OrganizationSource("fao_taleo", "FAO", "taleo", "https://jobs.fao.org")
        external_id = "2601857"
        detail_url = "https://jobs.fao.org/careersection/fao_external/jobdetail.ftl?job=2601857"
        listing = json.dumps([{"jobId": external_id, "title": "Current listing title", "url": detail_url}])
        detail = '<form id="ftlform" action="unavailablerequisition.ftl"><input name="ftlpageid" value="unavaibleRequisitionPage"></form>'
    responses = {source.base_url: listing, detail_url: detail}
    calls = []

    def get(url, **kwargs):
        calls.append(url)
        body = responses[url]
        return SimpleNamespace(text=body, content=body.encode(), headers={"Content-Type": "text/html"})

    monkeypatch.setattr(pipeline, "_http_client_for_source", lambda *args: SimpleNamespace(get=get))
    db = JobDatabase(tmp_path / "jobs.sqlite3")
    db.initialize()
    if existing:
        db.upsert_job(build_job(source, external_id=external_id, title="Previous listing title", apply_url=detail_url))
    result = sync(source, db)
    key = f"{source.id}:{external_id}"
    row = db.get_job(key)
    assert row["title"] == "Current listing title"
    assert row["status"] == "open"
    assert row["raw"]["_jobagg_listing_verification"]["observed_in_latest_listing"] is True
    assert result.vacancies_unavailable == 1
    assert result.diagnostics.detail_attempted == 1
    assert result.diagnostics.detail_succeeded == result.diagnostics.detail_failed == 0
    assert result.errors == []
    assert calls == [source.base_url, detail_url]
    assert db.get_source_breaker(source.id, "detail") is None
    backlog = db.get_detail_backlog(key)
    assert backlog["detail_status"] == "unavailable_pending_inventory"
    assert backlog["attempt_count"] == 1
    assert backlog["last_attempt_at"] and backlog["last_success_at"] is None
    assert backlog["queued_reason"] == EXPLICIT_VACANCY_UNAVAILABLE
    persisted = list(db.iter_source_run_diagnostics(source.id))[0]
    assert persisted["detail_unavailable"] == 1
    assert persisted["unavailable_vacancies"] == result.diagnostics.unavailable_vacancies
    assert persisted["unavailable_vacancies"][0]["job_key"] == key
    assert list(db.iter_source_runs(source.id))[0]["vacancies_unavailable"] == 1


@register_adapter
class OutcomeAdapter(JobAdapter):
    family = "unavailable_outcomes_test"
    calls = []

    def fetch_jobs(self):
        self.run_diagnostics.pagination_complete = True
        rows = self.source.extra["outcomes"]
        if not rows:
            self.run_diagnostics.health_status = "ok_empty"
            self.run_diagnostics.empty_reason = "verified_total_zero"
            self.run_diagnostics.zero_fetched_evidence = {"total": 0}
        return [build_job(
            self.source, title=f"Listing {key}", external_id=key,
            apply_url=f"https://example.org/{key}", raw={"id": key},
        ) for key in rows]

    def fetch_detail_for_listing_item(self, item):
        key = item["id"]
        self.calls.append(key)
        outcome = self.source.extra["outcomes"][key]
        if outcome == "unavailable":
            raise VacancyUnavailable("Exact source unavailable template")
        if outcome == "identity_mismatch":
            raise DetailIdentityMismatch("Different native vacancy identity")
        return build_job(
            self.source, title=f"Detail {key}", external_id=key,
            apply_url=f"https://example.org/{key}", closes_at="2099-01-01",
            description="Responsibilities and required qualifications for the advertised vacancy. " * 4,
        )


@pytest.fixture
def scenario(tmp_path):
    OutcomeAdapter.calls = []
    source = OrganizationSource("outcomes", "Outcomes", OutcomeAdapter.family, "https://example.org", extra={"outcomes": {"A1": "unavailable"}})
    db = JobDatabase(tmp_path / "jobs.sqlite3")
    db.initialize()
    return source, db


def test_unavailable_probe_does_not_recover_half_open_breaker(scenario):
    source, db = scenario
    db.set_source_breaker(source_id=source.id, breaker_type="detail", state="half_open", failure_count=3, success_count=1, reason="previous probe")
    before = db.get_source_breaker(source.id, "detail")
    result = sync(source, db)
    assert result.diagnostics.detail_unavailable == 1
    assert db.get_source_breaker(source.id, "detail") == before


def test_unavailable_attempts_still_consume_request_limit_and_pacing(scenario, monkeypatch):
    source, db = scenario
    source.extra.update(outcomes={"A1": "unavailable", "A2": "success"}, max_detail_pages_per_run=1)
    paced = []
    monkeypatch.setattr(pipeline._DetailFetchPacer, "before_attempt", lambda self, attempts: paced.append(attempts))
    result = sync(source, db)
    assert OutcomeAdapter.calls == ["A1"] and paced == [0]
    assert result.diagnostics.detail_attempted == result.diagnostics.detail_skipped == 1
    assert result.diagnostics.detail_succeeded == result.diagnostics.detail_failed == 0
    assert db.get_job("outcomes:A2") is not None
    assert db.get_detail_backlog("outcomes:A2")["queued_reason"] == "detail_run_budget_exhausted"


def test_mixed_success_failure_and_unavailable_counts_stay_distinct(scenario):
    source, db = scenario
    source.extra["outcomes"] = {"A1": "unavailable", "A2": "success", "A3": "identity_mismatch"}
    result = sync(source, db)
    diag = result.diagnostics
    assert (diag.detail_attempted, diag.detail_succeeded, diag.detail_failed, diag.detail_unavailable) == (3, 1, 1, 1)
    assert db.get_detail_backlog("outcomes:A3")["detail_status"] == "permanent_failed"
    assert "Different native vacancy identity" in result.errors[0]
    assert "1/2 jobs" in result.errors[0]


def test_unavailable_does_not_dilute_adapter_failure_breaker(scenario):
    source, db = scenario
    source.extra["outcomes"] = {"A1": "unavailable", **{f"A{i}": "identity_mismatch" for i in range(2, 5)}}
    result = sync(source, db)
    assert result.diagnostics.detail_failed == 3
    assert result.diagnostics.detail_succeeded == 0
    assert db.get_source_breaker(source.id, "detail")["state"] == "open"
    assert db.get_detail_backlog("outcomes:A1")["detail_status"] == "unavailable_pending_inventory"


def test_unavailable_listing_reconciliation_and_reappearance(scenario):
    source, db = scenario
    sync(source, db)
    repeat = sync(source, db)
    assert repeat.diagnostics.detail_attempted == 0
    assert db.get_detail_backlog("outcomes:A1")["detail_status"] == "listing_detail_conflict"
    assert db.get_job("outcomes:A1")["status"] == "open"
    source.extra["outcomes"] = {}
    sync(source, db)
    # One missing observation remains below the normal closure threshold.
    assert db.get_job("outcomes:A1")["status"] == "open"
    source.extra["outcomes"] = {"A1": "success"}
    recovered = sync(source, db)
    assert recovered.diagnostics.detail_succeeded == 1
    assert db.get_detail_backlog("outcomes:A1")["detail_status"] == "complete"
    assert OutcomeAdapter.calls == ["A1", "A1"]


def test_unavailable_explicit_refresh_and_changed_listing_allow_recovery(scenario, monkeypatch):
    source, db = scenario
    sync(source, db)
    sync(source, db, refresh_all_details=True)
    assert len(OutcomeAdapter.calls) == 2
    original = OutcomeAdapter.fetch_jobs

    def changed_listing(self):
        jobs = original(self)
        jobs[0].title = "Changed listing title"
        return jobs

    monkeypatch.setattr(OutcomeAdapter, "fetch_jobs", changed_listing)
    source.extra["outcomes"]["A1"] = "success"
    recovered = sync(source, db)
    assert recovered.diagnostics.detail_succeeded == 1
    assert db.get_detail_backlog("outcomes:A1")["attempt_count"] == 3


def test_unavailable_diagnostics_sample_is_bounded_without_losing_count(scenario):
    source, db = scenario
    source.extra["outcomes"] = {f"A{i}": "unavailable" for i in range(25)}
    result = sync(source, db)
    assert result.vacancies_unavailable == 25
    assert len(result.diagnostics.unavailable_vacancies) == 20
    assert result.diagnostics.detail_succeeded == 0
    assert len(list(db.iter_detail_backlog(source.id, status="unavailable_pending_inventory"))) == 25


def test_legacy_backlog_and_diagnostics_upgrade_preserves_existing_rows(tmp_path):
    path = tmp_path / "legacy.sqlite3"
    db = JobDatabase(path)
    db.initialize()
    # Recreate the pre-change backlog using its SQL with the new statuses removed.
    with sqlite3.connect(path) as conn:
        sql = conn.execute("SELECT sql FROM sqlite_master WHERE name='detail_backlog'").fetchone()[0]
        sql = sql.replace(",\n                            'unavailable_pending_inventory'", "").replace(",\n                            'listing_detail_conflict'", "")
        conn.execute("DROP TABLE detail_backlog")
        conn.execute(sql)
        conn.execute("INSERT INTO detail_backlog (job_key,source_id,detail_status,attempt_count,updated_at) VALUES ('outcomes:A1','outcomes','permanent_failed',2,'2026-09-21T00:00:00+00:00')")
        conn.execute("ALTER TABLE source_runs DROP COLUMN vacancies_unavailable")
        conn.execute("ALTER TABLE source_run_diagnostics DROP COLUMN detail_unavailable")
        conn.execute("ALTER TABLE source_run_diagnostics DROP COLUMN unavailable_vacancies")
    db.initialize()
    assert db.get_detail_backlog("outcomes:A1")["attempt_count"] == 2
    db.record_detail_backlog_attempt(job_key="outcomes:A1", source_id="outcomes", status="unavailable_pending_inventory", listing_hash="new", reason=EXPLICIT_VACANCY_UNAVAILABLE)
    db.initialize()
    assert db.get_detail_backlog("outcomes:A1")["attempt_count"] == 3
    source = OrganizationSource("outcomes", "Outcomes", OutcomeAdapter.family, "https://example.org", extra={"outcomes": {"A2": "unavailable"}})
    result = sync(source, db)
    assert list(db.iter_source_run_diagnostics(source.id))[0]["detail_unavailable"] == result.vacancies_unavailable == 1


def test_prior_complete_detail_is_retained_but_reappearance_can_recover(scenario):
    source, db = scenario
    source.extra["outcomes"]["A1"] = "success"
    sync(source, db)
    original = db.get_job("outcomes:A1")
    success_at = db.get_detail_backlog("outcomes:A1")["last_success_at"]
    source.extra["outcomes"]["A1"] = "unavailable"
    unavailable = sync(source, db, refresh_all_details=True)
    assert unavailable.diagnostics.detail_succeeded == 0
    retained = db.get_job("outcomes:A1")
    assert retained["description"] == original["description"]
    assert retained["closes_at"] == original["closes_at"]
    assert db.get_detail_backlog("outcomes:A1")["last_success_at"] == success_at
    source.extra["outcomes"] = {}
    sync(source, db)
    source.extra["outcomes"] = {"A1": "success"}
    recovered = sync(source, db)
    assert recovered.diagnostics.detail_succeeded == 1
    assert db.get_detail_backlog("outcomes:A1")["attempt_count"] == 3


def test_backlog_migration_copy_failure_rolls_back_table_replacement(tmp_path):
    db = JobDatabase(tmp_path / "migration.sqlite3")
    db.initialize()
    with db.connect() as conn:
        sql = conn.execute("SELECT sql FROM sqlite_master WHERE name='detail_backlog'").fetchone()[0]
        sql = sql.replace(",\n                            'unavailable_pending_inventory'", "").replace(",\n                            'listing_detail_conflict'", "")
        assert "listing_detail_conflict" not in sql
        conn.execute("DROP TABLE detail_backlog")
        conn.execute(sql)
        conn.execute("INSERT INTO detail_backlog (job_key,source_id,detail_status,attempt_count,updated_at) VALUES ('outcomes:A1','outcomes','blocked_by_circuit_breaker',2,'2026-09-21T00:00:00+00:00')")
    with db.connect() as conn:
        def deny_copy(action, table, *args):
            return sqlite3.SQLITE_DENY if action == sqlite3.SQLITE_INSERT and table == "detail_backlog" else sqlite3.SQLITE_OK

        conn.set_authorizer(deny_copy)
        with pytest.raises(sqlite3.DatabaseError, match="not authorized"):
            db._ensure_detail_backlog_status_schema(conn)
        conn.set_authorizer(None)
        assert conn.execute("SELECT attempt_count FROM detail_backlog").fetchone()[0] == 2
        assert conn.execute("SELECT name FROM sqlite_master WHERE name='detail_backlog_old'").fetchone() is None
    db.initialize()
    row = db.get_detail_backlog("outcomes:A1")
    assert row["detail_status"] == "blocked_by_circuit_breaker" and row["attempt_count"] == 2


def test_scheduler_reports_preserve_unavailable_request_accounting(tmp_path, scenario):
    from jobagg.pipelines.bundles import write_source_bundle
    from jobagg.scheduler import _source_health_dry_run_row, _write_sync_bundles_health_report

    source, _ = scenario
    bundle = write_source_bundle(source, output_dir=tmp_path, policy=POLICY, classify=False)
    report = _write_sync_bundles_health_report(
        [bundle], output_dir=tmp_path, output_path=None, consolidated=None,
        fatal_errors_count=0, exit_code=0,
    )
    persisted = json.loads((tmp_path / "sync_bundles_health.json").read_text())
    dry_run = _source_health_dry_run_row(source, output_dir=tmp_path)
    for row in (report["sources"][0], persisted["sources"][0], dry_run):
        assert row["detail_unavailable"] == row["detail_attempted"] == 1
        assert row["detail_succeeded"] == row["detail_failed"] == 0
    # The read-only report also supports bundles created before this column.
    with sqlite3.connect(bundle.paths["db"]) as conn:
        conn.execute("ALTER TABLE source_run_diagnostics DROP COLUMN detail_unavailable")
    legacy = _source_health_dry_run_row(source, output_dir=tmp_path)
    assert legacy["detail_attempted"] == 1 and legacy["detail_unavailable"] == 0
    assert not legacy["last_error_summary"] or "read failed" not in legacy["last_error_summary"]
