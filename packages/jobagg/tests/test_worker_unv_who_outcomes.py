from copy import deepcopy
from datetime import UTC, datetime
import json
import time

import pytest
import yaml

from jobagg.http import HttpResponse
from jobagg import remediation_worker as runtime
from jobagg.remediation_worker import Worker
from test_remediation_worker import FixtureClient
from test_unv_who_captured_outcomes import FIXTURES, UNV_ID, UNV_URL, WHO_ID, WHO_URL, incident


class PublicFixtureClient(FixtureClient):
    def _request(self, url, **kwargs):
        response = super()._request(url, **kwargs)
        content_type = "application/json" if isinstance(self.replies[url], dict) else "text/html"
        return HttpResponse(response.url, response.status_code, {"Content-Type": content_type},
                            response.text, response.content)


def setup_source(tmp_path, monkeypatch, source, *, only_unavailable=False, who_ids=None):
    monkeypatch.setattr("jobagg.pipelines.http_checkpoint.time.sleep", lambda _: None)
    unv = source == "unv_uvp"
    identity, detail_url = (UNV_ID, UNV_URL) if unv else (WHO_ID, WHO_URL)
    host = "https://app.unv.org" if unv else "https://careers.who.int"
    api = (host + "/api/doa/doa/SearchDoaAsyncByAzureCognitive" if unv
           else host + "/careersection/rest/jobboard/searchjobs?portal=1&lang=en")
    if unv:
        item = json.loads((FIXTURES / f"unv/public_projection_{UNV_ID}_20260913.json").read_text())
        # Controlled captured-field replay: all public support fields are present,
        # with the explicit no-category-filter state used by public-field tests.
        item["_unv_public_render_context"]["category_configuration_enabled"] = False
        listing = {"value": {"total": 1, "result": [{"id": identity, "name": item["name"]}]}}
        healthy = {"value": item}
        extra = {"api_url": api, "detail_api_url_template": host + "/api/doa/doa/{job_id}",
                 "detail_url_template": host + "/opportunities/{job_id}", "page_size": 50, "max_pages": 2}
    else:
        ids = who_ids or [identity]
        listing = {"requisitionList": [{"contestNo": value, "title": "Public role"} for value in ids],
                   "pagingData": {"currentPageNo": 1, "pageSize": 25, "totalCount": len(ids)}}
        healthy = (FIXTURES / "taleo_public_bindings/who_taleo_2603964_20260913.html").read_bytes()
        extra = {"search_api_url": api, "search_payload": {"pageNo": 1}, "max_pages": 2,
                 "warmup_search_page": False, "detail_url_template": WHO_URL.replace(WHO_ID, "{job_id}")}
    extra.update(fetch_details=False, fetch_attachments=False)
    registry = tmp_path / "sources.yaml"
    registry.write_text(yaml.safe_dump({"sources": [{"id": source, "name": source,
        "ats_family": "unv" if unv else "taleo", "base_url": host, "extra": extra}]}))
    robots = tmp_path / "robots.yaml"
    robots.write_text("default:\n  honor_robots_txt: false\n  min_delay_seconds: 0\n")
    lock, bootstrap = tmp_path / "shared.lock", tmp_path / "bootstrap.json"
    bootstrap.write_text(json.dumps({"schema_version": 1, "shared_lock": str(lock),
        "reviewed_at": datetime.now(UTC).isoformat(), "prior_writers_reviewed": True,
        "no_unmigrated_policy_state": True, "scope_source_ids": [source], "evidence": [],
        "detail_attempts": [], "host_states": {}, "source_holds": {}, "review_note": "Isolated fixture replay"}))
    unavailable = json.loads(incident(source)[1]) if unv else incident(source)[1]
    replies, calls = {api: listing, detail_url: unavailable if only_unavailable else healthy}, []
    for other in who_ids or []:
        replies[detail_url.replace(identity, other)] = unavailable
    kwargs = dict(registry=registry, robots=robots, workspace=tmp_path / "worker", shared_lock=lock,
        max_tasks=4, client_factory=lambda source, policy: PublicFixtureClient(replies, calls), policy_bootstrap=bootstrap)
    return Worker(**kwargs), replies, calls, api, detail_url, unavailable, kwargs


def tasks(worker):
    with worker.db.connect() as conn:
        return [dict(row) for row in conn.execute("SELECT * FROM remediation_tasks WHERE kind='detail' ORDER BY external_id")]


def force_listing(worker):
    with worker.db.connect() as conn:
        conn.execute("UPDATE remediation_sources SET next_list_at=0")


def archived_state(worker, job_key):
    with worker.db.connect() as conn:
        return {"job": dict(conn.execute("SELECT * FROM jobs WHERE job_key=?", (job_key,)).fetchone()),
                "observation": dict(conn.execute("SELECT * FROM remediation_observations WHERE job_key=?", (job_key,)).fetchone()),
                "blobs": [tuple(row) for row in conn.execute("SELECT * FROM attachment_blobs")],
                "attachments": [tuple(row) for row in conn.execute("SELECT * FROM job_attachments")]}


@pytest.mark.parametrize("source", ["unv_uvp", "who_taleo"])
def test_expected_outcome_keeps_accepted_text_archives_and_counters(source, tmp_path, monkeypatch):
    worker, replies, calls, api, url, unavailable, kwargs = setup_source(tmp_path, monkeypatch, source)
    worker.tick(execute=True)
    task = tasks(worker)[0]
    assert task["status"] == "done", task["last_error"]
    key = source + ":" + task["external_id"]
    with worker.db.connect() as conn:
        conn.execute("INSERT INTO attachment_blobs VALUES(?,?,?,?)", ("retained-blob", "text/plain", 8, b"retained"))
        conn.execute("INSERT INTO job_attachments VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                     ("retained-association", key, source, "https://example.org/archive", None,
                      "Prior archive", "historical", 0, "complete", "retained-blob", "retained", "{}"))
        conn.execute("UPDATE remediation_tasks SET status='pending',eligible_at=0 WHERE kind='detail'")
    before = archived_state(worker, key)
    replies[url] = unavailable
    report = worker.tick(execute=True)
    row = tasks(worker)[0]
    assert row["status"] == "unavailable_pending_inventory" and row["attempts"] == task["attempts"] + 1
    assert archived_state(worker, key) == before
    assert worker.verified_unavailable_receipt(row)["closure_inferred"] is False
    counters = report["concurrency"]
    assert counters["vacancies_unavailable"] == 1 and counters["accepted_progress"] == 0
    assert all(counters[key] == 0 for key in ("runtime_errors", "integrity_errors", "new_access_blocks", "transport_failures", "database_errors"))
    assert not any(json.loads(path.read_text()).get("stopped") for path in (worker.shared_policy.root / "hosts").glob("*.json"))
    call_count = len(calls)
    restarted = Worker(**kwargs)
    restarted.tick(execute=True)
    assert len(calls) == call_count and tasks(restarted)[0]["attempts"] == row["attempts"]
    assert archived_state(restarted, key) == before


def test_unv_complete_absence_reconciles_task_without_closing_or_losing_text(tmp_path, monkeypatch):
    worker, replies, _, api, url, unavailable, _ = setup_source(tmp_path, monkeypatch, "unv_uvp")
    worker.tick(execute=True)
    old = worker.db.get_job("unv_uvp:" + UNV_ID)
    replies[url] = unavailable
    with worker.db.connect() as conn:
        conn.execute("UPDATE remediation_tasks SET status='pending',eligible_at=0 WHERE kind='detail'")
    worker.tick(execute=True)
    replies[api] = {"value": {"total": 1, "result": []}}
    force_listing(worker); worker.tick(execute=True)
    assert tasks(worker)[0]["status"] == "unavailable_pending_inventory"
    replies[api]["value"]["total"] = 0
    force_listing(worker); worker.tick(execute=True)
    row = tasks(worker)[0]
    assert row["status"] == "not_observed" and row["attempts"] == 2
    assert json.loads(row["receipt"])["listing_reconciliation"]["closure_inferred"] is False
    current = worker.db.get_job("unv_uvp:" + UNV_ID)
    assert current["description"] == old["description"] and current["first_seen_at"] == old["first_seen_at"]
    assert current["status"] != "closed"


def test_unv_still_listed_null_allows_one_delayed_recheck_then_conflict(tmp_path, monkeypatch):
    worker, _, calls, _, url, _, _ = setup_source(tmp_path, monkeypatch, "unv_uvp", only_unavailable=True)
    worker.tick(execute=True)
    force_listing(worker); worker.tick(execute=True)
    row = tasks(worker)[0]
    assert row["status"] == "pending" and row["eligible_at"] > time.time() + 3500
    assert json.loads(row["receipt"])["unavailable_rechecks"] == 1
    due = row["eligible_at"]
    force_listing(worker); worker.tick(execute=True)
    assert tasks(worker)[0]["eligible_at"] >= due
    with worker.db.connect() as conn:
        conn.execute("UPDATE remediation_tasks SET eligible_at=0 WHERE kind='detail'")
    worker.tick(execute=True)
    force_listing(worker); worker.tick(execute=True)
    assert tasks(worker)[0]["status"] == "listing_detail_conflict" and tasks(worker)[0]["attempts"] == 2
    assert sum(request == url for request, _ in calls) == 2
    force_listing(worker); worker.tick(execute=True)
    assert tasks(worker)[0]["status"] == "listing_detail_conflict"
    assert sum(request == url for request, _ in calls) == 2


def test_who_unsupported_census_coalesces_one_followup_and_preserves_review_state(tmp_path, monkeypatch):
    worker, replies, calls, api, _, _, kwargs = setup_source(
        tmp_path, monkeypatch, "who_taleo", only_unavailable=True, who_ids=[WHO_ID, "2604310"])
    report = worker.tick(execute=True)
    assert len(tasks(worker)) == 2 and {task["status"] for task in tasks(worker)} == {"unavailable_pending_inventory"}
    assert report["concurrency"]["vacancies_unavailable"] == 2
    with worker.db.connect() as conn:
        followup_due = conn.execute("SELECT next_list_at FROM remediation_sources").fetchone()[0]
    assert time.time() < followup_due <= time.time() + 60
    assert sum(request == api for request, _ in calls) == 1
    replies[api]["requisitionList"] = []
    replies[api]["pagingData"]["totalCount"] = 0
    monkeypatch.setattr(time, "time", lambda: followup_due + 1)
    restarted = Worker(**kwargs)
    report = restarted.tick(execute=True)
    assert report["sources"][0]["enumeration"]["complete"] is False
    assert report["sources"][0]["enumeration"]["method"] == "unsupported"
    assert {task["status"] for task in tasks(restarted)} == {"unavailable_pending_inventory"}
    assert {task["attempts"] for task in tasks(restarted)} == {1}
    with restarted.db.connect() as conn:
        next_list = conn.execute("SELECT next_list_at FROM remediation_sources").fetchone()[0]
    assert next_list > followup_due + 3600
    before_calls = len(calls)
    restarted.tick(execute=True); restarted.tick(execute=True)
    assert len(calls) == before_calls and sum(request == api for request, _ in calls) == 2


def test_unv_wrong_native_identity_keeps_integrity_pressure(tmp_path, monkeypatch):
    worker, replies, _, _, url, _, _ = setup_source(tmp_path, monkeypatch, "unv_uvp")
    replies[url] = deepcopy(replies[url])
    replies[url]["value"]["id"] = "1234567"
    report = worker.tick(execute=True)
    assert tasks(worker)[0]["status"] == "blocked"
    assert report["concurrency"]["runtime_errors"] == report["concurrency"]["integrity_errors"] == 1
    assert report["concurrency"]["vacancies_unavailable"] == 0
    with worker.db.connect() as conn:
        assert conn.execute("SELECT count(*) FROM remediation_observations").fetchone()[0] == 0


@pytest.mark.parametrize("source", ["unv_uvp", "who_taleo"])
@pytest.mark.parametrize("field", ["external_id", "request_url_sha256", "response_url_sha256"])
def test_contradictory_capture_cannot_enter_unavailable_lifecycle(source, field, tmp_path, monkeypatch):
    worker, replies, _, _, url, unavailable, _ = setup_source(tmp_path, monkeypatch, source)
    worker.tick(execute=True)
    accepted = tasks(worker)[0]
    key = source + ":" + accepted["external_id"]
    before = archived_state(worker, key)
    with worker.db.connect() as conn:
        conn.execute("UPDATE remediation_tasks SET status='pending',eligible_at=0 WHERE kind='detail'")
    replies[url] = unavailable
    classify = runtime.captured_unavailable

    def corrupt_capture(source_id, identity, paths):
        paths = list(paths)
        for path in paths:
            meta = json.loads(path.read_text())
            if meta.get("phase", {}).get("kind") == "detail":
                meta[field] = "contradictory"
                path.write_text(json.dumps(meta))
        return classify(source_id, identity, paths)

    monkeypatch.setattr(runtime, "captured_unavailable", corrupt_capture)
    if field == "request_url_sha256":
        # The existing measurement guard also refuses an acceptance report when
        # its independently recorded request hash contradicts capture metadata.
        with pytest.raises(ValueError, match="Concurrency measurement/capture identity mismatch"):
            worker.tick(execute=True)
        assert not (worker.workspace / "ticks" / f"{worker.tick_id}.json").exists()
    else:
        report = worker.tick(execute=True)
        assert report["concurrency"]["vacancies_unavailable"] == 0
    task = tasks(worker)[0]
    assert task["status"] in {"blocked", "pending"}
    assert "vacancy_unavailable" not in json.loads(task["receipt"])
    assert task["attempts"] == accepted["attempts"] + 1
    assert archived_state(worker, key) == before


def test_who_ambiguous_portal_keeps_three_response_limit(tmp_path, monkeypatch):
    worker, replies, calls, _, url, _, _ = setup_source(tmp_path, monkeypatch, "who_taleo", only_unavailable=True)
    replies[url] = b"<title>Job portal</title><p>This job is unavailable.</p>"
    for expected, count in [("pending", 1), ("pending", 2), ("blocked", 3)]:
        if count > 1:
            with worker.db.connect() as conn:
                conn.execute("UPDATE remediation_tasks SET eligible_at=0 WHERE kind='detail'")
        report = worker.tick(execute=True)
        task = tasks(worker)[0]
        assert task["status"] == expected and task["attempts"] == count
        assert json.loads(task["receipt"])["incomplete_response_count"] == count
        assert report["concurrency"]["vacancies_unavailable"] == 0
        assert report["concurrency"]["integrity_errors"] == 0
    assert report["concurrency"]["runtime_errors"] == 1
    before = len(calls)
    worker.tick(execute=True)
    assert len(calls) == before
