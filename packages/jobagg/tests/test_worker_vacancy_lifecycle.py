from datetime import UTC, datetime
import json
import time

import pytest
import yaml

from jobagg.remediation_worker import Worker, dump
from test_remediation_worker import FixtureClient


API = "https://jobs.fao.org/careersection/rest/jobboard/searchjobs?portal=1&lang=en"
DETAIL = "https://jobs.fao.org/careersection/fao_external/jobdetail.ftl?job=2601857&lang=en"
UNAVAILABLE = b'<form id="ftlform" action="unavailablerequisition.ftl"><input type="hidden" name="ftlpageid" value="unavaibleRequisitionPage"></form>'


@pytest.fixture
def case(tmp_path, monkeypatch):
    from jobagg.pipelines import http_checkpoint
    monkeypatch.setattr(http_checkpoint.time, "sleep", lambda seconds: None)
    registry = tmp_path / "sources.yaml"
    registry.write_text(yaml.safe_dump({"sources": [{"id": "fao_taleo", "name": "FAO", "ats_family": "taleo",
        "base_url": "https://jobs.fao.org/careersection/fao_external/jobsearch.ftl", "enabled": True,
        "extra": {"search_api_url": API, "search_payload": {"pageNo": 1},
                  "enumerate_job_locales": True, "warmup_search_page": False, "fetch_details": False}}]}))
    robots = tmp_path / "robots.yaml"; robots.write_text("default:\n  honor_robots_txt: false\n  min_delay_seconds: 0\n")
    lock = tmp_path / "shared.lock"
    bootstrap = tmp_path / "bootstrap.json"
    bootstrap.write_text(json.dumps({"schema_version": 1, "shared_lock": str(lock),
        "reviewed_at": datetime.now(UTC).isoformat(), "prior_writers_reviewed": True,
        "no_unmigrated_policy_state": True, "scope_source_ids": ["fao_taleo"], "evidence": [],
        "detail_attempts": [], "host_states": {}, "source_holds": {}, "review_note": "Isolated test"}))
    listing = {"requisitionList": [{"contestNo": "2601857", "title": "Role"}],
        "pagingData": {"currentPageNo": 1, "pageSize": 25, "totalCount": 1},
        "facetResults": [{"id": "JOB_LOCALE", "facetValueResults": [{"id": "en"}]}]}
    replies, calls = {API: listing, DETAIL: UNAVAILABLE,
        "https://jobs.fao.org/careersection/fao_external/jobsearch.ftl?lang=en": b"<html>Search</html>"}, []
    worker = Worker(registry=registry, robots=robots, workspace=tmp_path / "worker", shared_lock=lock,
        max_tasks=3, client_factory=lambda source, policy: FixtureClient(replies, calls), policy_bootstrap=bootstrap)
    return worker, replies, calls


def detail_task(worker):
    with worker.db.connect() as conn:
        return dict(conn.execute("SELECT * FROM remediation_tasks WHERE kind='detail'").fetchone())


def force_listing(worker):
    with worker.db.connect() as conn:
        conn.execute("UPDATE remediation_sources SET next_list_at=0")


def test_unavailability_is_not_detail_success_or_global_integrity_pressure(case):
    worker, replies, calls = case
    report = worker.tick(execute=True)
    row = detail_task(worker)
    assert row["status"] == "unavailable_pending_inventory"
    assert worker.verified_unavailable_receipt(row)["detector"] == "fao_active_unavailable_template_v1"
    assert report["concurrency"]["vacancies_unavailable"] == 1
    assert report["concurrency"]["runtime_errors"] == report["concurrency"]["integrity_errors"] == 0
    assert report["concurrency"]["accepted_progress"] == 1  # The listing only.
    with worker.db.connect() as conn:
        assert conn.execute("SELECT count(*) FROM remediation_observations").fetchone()[0] == 0
    worker.tick(execute=True)
    assert len(calls) == 3


def test_generic_portal_is_still_an_integrity_failure(case):
    worker, replies, calls = case
    replies[DETAIL] = b"<title>Job portal</title><p>This job is unavailable.</p>"
    report = worker.tick(execute=True)
    assert detail_task(worker)["status"] == "blocked"
    assert report["concurrency"]["integrity_errors"] == 1
    assert report["concurrency"]["vacancies_unavailable"] == 0


def test_incomplete_census_cannot_retire_but_fresh_complete_absence_can(case):
    worker, replies, calls = case
    worker.tick(execute=True)
    listing = replies[API]
    listing["requisitionList"] = []
    # Reported count still says one: absence cannot be established.
    force_listing(worker); worker.tick(execute=True)
    assert detail_task(worker)["status"] == "unavailable_pending_inventory"
    listing["pagingData"]["totalCount"] = 0
    force_listing(worker); worker.tick(execute=True)
    row = detail_task(worker)
    assert row["status"] == "not_observed"
    receipt = json.loads(row["receipt"])
    assert receipt["listing_reconciliation"]["closure_inferred"] is False
    assert worker.db.get_job("fao_taleo:2601857") is not None


def test_still_listed_conflict_gets_only_one_bounded_recheck(case):
    worker, replies, calls = case
    worker.tick(execute=True)
    force_listing(worker); worker.tick(execute=True)
    row = detail_task(worker)
    assert row["status"] == "pending"
    assert row["eligible_at"] > time.time() + 3500
    assert json.loads(row["receipt"])["unavailable_rechecks"] == 1
    with worker.db.connect() as conn:
        conn.execute("UPDATE remediation_tasks SET eligible_at=0 WHERE kind='detail'")
    worker.tick(execute=True)
    force_listing(worker); worker.tick(execute=True)
    row = detail_task(worker)
    assert row["status"] == "listing_detail_conflict" and row["attempts"] == 2
    count = len(calls)
    worker.tick(execute=True)
    assert len(calls) == count


def test_reappearance_requeues_but_generic_blocked_failure_is_not_unblocked(case):
    worker, replies, calls = case
    worker.tick(execute=True)
    listing = replies[API]; original = list(listing["requisitionList"])
    listing["requisitionList"] = []; listing["pagingData"]["totalCount"] = 0
    force_listing(worker); worker.tick(execute=True)
    assert detail_task(worker)["status"] == "not_observed"
    listing["requisitionList"] = original; listing["pagingData"]["totalCount"] = 1
    worker.max_tasks = 1
    force_listing(worker); worker.tick(execute=True)
    assert detail_task(worker)["status"] == "pending"
    with worker.db.connect() as conn:
        conn.execute("UPDATE remediation_tasks SET status='blocked',last_error='identity mismatch' WHERE kind='detail'")
    force_listing(worker); worker.tick(execute=True)
    assert detail_task(worker)["status"] == "blocked"


def test_tampered_unavailable_evidence_cannot_remove_task_from_backlog(case):
    worker, replies, calls = case
    worker.tick(execute=True)
    row = detail_task(worker); receipt = json.loads(row["receipt"])
    receipt["vacancy_unavailable"]["observed_at"] = "2020-01-01T00:00:00+00:00"
    with worker.db.connect() as conn:
        conn.execute("UPDATE remediation_tasks SET receipt=? WHERE task_id=?", (dump(receipt), row["task_id"]))
    replies[API]["requisitionList"] = []; replies[API]["pagingData"]["totalCount"] = 0
    force_listing(worker)
    report = worker.tick(execute=True)
    assert detail_task(worker)["status"] == "unavailable_pending_inventory"
    assert report["concurrency"]["integrity_errors"] >= 1
