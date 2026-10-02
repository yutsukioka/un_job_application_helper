"""Regression coverage for exact EU redirects and changing IDB enumeration."""

from datetime import UTC, datetime
import gzip
import hashlib
import json
from pathlib import Path
import time
import urllib.request

import pytest

from jobagg.adapters.base import AdapterContext
from jobagg.adapters.idb_api import IDBInventoryChanged, URL, fetch, reconcile, verify
from jobagg.adapters.successfactors_rmk import SuccessFactorsRMKAdapter
from jobagg.http import HttpResponse, JobAggHTTPClient
from jobagg.http_safe import SafeHTTPPolicy, SSRFProtectionError, allowed_hosts_for_source
from jobagg.models import OrganizationSource
from jobagg.pipelines.http_checkpoint import DurableCapture, HostIneligible, RedirectHop
from jobagg.pipelines.sync_source import load_sources
from jobagg.remediation_worker import Worker
from jobagg.robots import load_policy


EU_LISTING = "https://eu-careers.europa.eu/en/job-opportunities/open-vacancies/cast"
EU_REDIRECT = "https://selection.eu-careers.europa.eu/en/job-opportunities/open-vacancies/cast"


def eu_source():
    return next(source for source in load_sources(
        Path(__file__).parents[1] / "config/organizations.yaml"
    ) if source.id == "eu_careers_static")


def page(total, index=0):
    return {"totalJobs": total, "jobSearchResult": [
        {"response": {"id": str(index + 1), "unifiedStandardTitle": f"Role {index + 1}",
                      "urlTitle": f"Role-{index + 1}", "supportedLocales": ["en_US"]}}
        for index in range(index * 10, min(total, index * 10 + 10))
    ]}


def idb_source():
    return OrganizationSource("idb_successfactors", "IDB", "successfactors_rmk",
                              "https://jobs.iadb.org", extra={"public_search_api": True})


def test_eu_registry_adds_only_exact_observed_redirect_host():
    source = eu_source()
    assert source.extra["reviewed_redirect_urls"] == [EU_REDIRECT]
    robots = load_policy(Path(__file__).parents[1] / "config/robots_policy.yaml")
    assert robots.honor_robots_for("selection.eu-careers.europa.eu") is True
    assert robots.min_delay_for("selection.eu-careers.europa.eu") >= 2.0
    before = OrganizationSource(source.id, source.name, source.ats_family, source.base_url,
                                extra={key: value for key, value in source.extra.items()
                                       if key != "reviewed_redirect_urls"})
    hosts = allowed_hosts_for_source(source)
    assert hosts - allowed_hosts_for_source(before) == {"selection.eu-careers.europa.eu"}
    policy = SafeHTTPPolicy(hosts, resolver=lambda _: ["8.8.8.8"])
    assert policy.validate_url(EU_REDIRECT) == "selection.eu-careers.europa.eu"
    for url in ["https://evil.selection.eu-careers.europa.eu/jobs",
                "https://selection.eu-careers.europa.eu.attacker.example/jobs"]:
        with pytest.raises(SSRFProtectionError, match="allowlist"):
            policy.validate_url(url)
    policy.resolver = lambda _: ["127.0.0.1"]
    with pytest.raises(SSRFProtectionError, match="denied network"):
        policy.validate_url(EU_REDIRECT)


@pytest.mark.parametrize("target,accepted", [
    (EU_REDIRECT, True),
    ("https://evil.selection.eu-careers.europa.eu/jobs", False),
    ("http://selection.eu-careers.europa.eu/en/job-opportunities/open-vacancies/cast", False),
])
def test_guarded_eu_redirect_remains_exact_https_only(tmp_path, monkeypatch, target, accepted):
    monkeypatch.setattr("jobagg.pipelines.http_checkpoint.time.sleep", lambda _: None)
    policy_path = tmp_path / "robots.yaml"
    policy_path.write_text("default:\n  honor_robots_txt: false\n  min_delay_seconds: 0\n")
    calls = []
    client = JobAggHTTPClient(safe_policy=SafeHTTPPolicy(
        allowed_hosts_for_source(eu_source()), resolver=lambda _: ["8.8.8.8"]
    ))

    def transport(url, **kwargs):
        calls.append(url)
        if url == EU_LISTING:
            raise RedirectHop(urllib.request.Request(target), 302)
        return HttpResponse(url, 200, {}, "<main>Public vacancy page</main>",
                            b"<main>Public vacancy page</main>")

    client._request = transport
    capture = DurableCapture(client, load_policy(policy_path), tmp_path / "capture", {},
                             lock_root=tmp_path / "hosts", phase={"kind": "listing"})
    if accepted:
        assert capture.request(EU_LISTING).url == EU_REDIRECT
        assert calls == [EU_LISTING, EU_REDIRECT]
    else:
        with pytest.raises((SSRFProtectionError, HostIneligible)):
            capture.request(EU_LISTING)
        assert calls == [EU_LISTING]


@pytest.mark.parametrize("first_total,second_total", [(1, 2), (11, 12)])
def test_idb_changing_total_defers_without_returning_partial_jobs(
    first_total, second_total,
):
    source = idb_source()
    adapter = SuccessFactorsRMKAdapter(AdapterContext(source, None))
    calls = []

    def response(url, payload):
        calls.append(payload)
        return page(first_total if len(calls) == 1 else second_total, payload["pageNumber"])

    adapter.post_json = response
    with pytest.raises(IDBInventoryChanged, match="total changed") as error:
        fetch(adapter)
    assert error.value.reason == "idb_inventory_total_changed"
    assert len(calls) == 2
    assert not adapter.run_diagnostics.pagination_complete


def test_idb_changing_total_is_still_rejected_by_capture_verification(tmp_path):
    source = idb_source()
    paths = []
    from jobagg.adapters.idb_api import request_payload
    for number, (sort, total) in enumerate([("", 1), ("date", 2)], 1):
        body = json.dumps(page(total)).encode()
        artifact = tmp_path / f"{number}.body.gz"
        artifact.write_bytes(gzip.compress(body))
        metadata = tmp_path / f"{number}.json"
        metadata.write_text(json.dumps({
            "url": URL, "response_url": URL, "method": "POST", "status_code": 200,
            "body_captured": True, "artifact": str(artifact),
            "body_sha256": hashlib.sha256(body).hexdigest(), "phase": {"kind": "listing"},
            "public_pagination_request": request_payload(0, sort),
        }))
        paths.append(metadata)
    proof = verify(source, [], paths)
    assert proof["complete"] is False and "union" in proof["reasons"][0]


def test_idb_stable_total_shortage_and_identity_change_still_fail_closed():
    row = {"external_id": "1", "title": "Role", "url": "https://jobs.iadb.org/job/Role/1"}
    pages = [dict(sort=sort, page=0, total=2, rows=[row]) for sort in ("", "date")]
    with pytest.raises(ValueError, match="union"):
        reconcile(pages)
    pages[1] = {**pages[1], "rows": [{**row, "title": "Changed identity"}]}
    with pytest.raises(ValueError, match="identity changed"):
        reconcile(pages)


@pytest.fixture
def idb_worker(tmp_path, monkeypatch):
    monkeypatch.setattr("jobagg.pipelines.http_checkpoint.time.sleep", lambda _: None)
    registry = tmp_path / "registry.yaml"
    registry.write_text("""sources:
  - id: idb_successfactors
    name: IDB
    ats_family: successfactors_rmk
    base_url: https://jobs.iadb.org
    extra:
      public_search_api: true
      fetch_attachments: false
      max_pages: 2
""")
    robots = tmp_path / "robots.yaml"
    robots.write_text("default:\n  honor_robots_txt: false\n  min_delay_seconds: 0\n")
    lock = tmp_path / "shared.lock"
    bootstrap = tmp_path / "bootstrap.json"
    bootstrap.write_text(json.dumps({
        "schema_version": 1, "shared_lock": str(lock),
        "reviewed_at": datetime.now(UTC).isoformat(), "prior_writers_reviewed": True,
        "no_unmigrated_policy_state": True, "scope_source_ids": ["idb_successfactors"],
        "evidence": [], "detail_attempts": [], "host_states": {}, "source_holds": {},
    }))
    calls = []
    totals = {"": 1, "date": 2}

    class Client(JobAggHTTPClient):
        def _request(self, url, **kwargs):
            payload = json.loads(kwargs["body"])
            calls.append(payload)
            body = json.dumps(page(totals[payload["sortBy"]])).encode()
            return HttpResponse(url, 200, {"Content-Type": "application/json"},
                                body.decode(), body)

    def start():
        worker = Worker(registry=registry, robots=robots, workspace=tmp_path / "workspace",
                        shared_lock=lock, policy_bootstrap=bootstrap,
                        client_factory=lambda source, policy: Client(), max_tasks=1)
        worker.initialize()
        worker.seed_listings()
        return worker

    return start, calls, totals


def listing_task(worker):
    with worker.db.connect() as conn:
        return dict(conn.execute("SELECT * FROM remediation_tasks WHERE kind='listing'").fetchone())


def test_idb_worker_preserves_attempt_and_pending_retry_without_listing_writes(idb_worker):
    start, calls, _ = idb_worker
    worker = start()
    task = worker.choose()
    before = time.time()
    result = worker.perform(task, time.time() + 60)
    assert result["status"] == "pending" and result["error_type"] == "IDBInventoryChanged"
    assert before + 900 <= result["eligible_at"] < time.time() + 901
    assert len(calls) == 2 and worker.choose() is None
    with worker.db.connect() as conn:
        saved = dict(conn.execute("SELECT * FROM remediation_tasks").fetchone())
        attempt = dict(conn.execute("SELECT * FROM remediation_attempts").fetchone())
        assert conn.execute("SELECT count(*) FROM jobs").fetchone()[0] == 0
        assert conn.execute("SELECT last_list_at FROM remediation_sources").fetchone()[0] is None
    assert saved["status"] == "pending" and saved["attempts"] == 1 and saved["claim"] == attempt["attempt_id"]
    assert attempt["status"] == "pending" and attempt["finished_at"] is not None
    receipt = json.loads(attempt["evidence"])
    assert receipt["retry_decision"]["category"] == "idb_inventory_total_changed"
    assert receipt["inventory_change_count"] == 1
    assert receipt["inventory_change_reason"] == "idb_inventory_total_changed"
    captures = sorted((Path(receipt["capture_directory"]) / "http").glob("*.json"))
    assert len(captures) == 2
    assert all(json.loads(path.read_text())["status_code"] == 200 for path in captures)
    assert not (Path(receipt["capture_directory"]) / "listing.json").exists()


def test_idb_transient_instability_retries_stable_inventory_and_resets_episode(
    idb_worker, monkeypatch,
):
    start, calls, totals = idb_worker
    worker = start()
    result = worker.perform(worker.choose(), time.time() + 60)
    assert result["status"] == "pending"
    future = result["eligible_at"] + 1
    monkeypatch.setattr(time, "time", lambda: future)
    totals[""] = 2
    worker = start()
    assert worker.perform(worker.choose(), time.time() + 60) == {
        "listing_observed": 2, "independent_enumeration": True,
    }
    saved = listing_task(worker)
    assert saved["status"] == "done" and saved["attempts"] == 2
    assert "inventory_change_count" not in json.loads(saved["receipt"])
    with worker.db.connect() as conn:
        assert conn.execute("SELECT count(*) FROM jobs").fetchone()[0] == 2
        assert conn.execute("SELECT last_list_at FROM remediation_sources").fetchone()[0] is not None
        conn.execute("UPDATE remediation_sources SET next_list_at=0")
    totals[""] = 1
    worker.seed_listings()
    result = worker.perform(listing_task(worker), time.time() + 60)
    assert result["status"] == "pending"
    assert json.loads(listing_task(worker)["receipt"])["inventory_change_count"] == 1
    assert len(calls) == 6


def test_idb_instability_bound_survives_restarts_seeding_and_budget_deferral(
    idb_worker, monkeypatch,
):
    start, calls, _ = idb_worker
    worker = start()
    result = worker.perform(worker.choose(), time.time() + 60)
    assert result["status"] == "pending"
    now = [result["eligible_at"] + 1]
    monkeypatch.setattr(time, "time", lambda: now[0])

    worker = start()
    original = worker.do_listing

    def no_budget(*args):
        raise HostIneligible("Fixture task budget exhausted", category="budget")

    monkeypatch.setattr(worker, "do_listing", no_budget)
    result = worker.perform(worker.choose(), time.time() + 60)
    assert result["status"] == "pending" and result["error_type"] == "HostIneligible"
    receipt = json.loads(listing_task(worker)["receipt"])
    assert receipt["retry_decision"]["category"] == "eligibility_deferred"
    budget_receipt = receipt
    monkeypatch.setattr(worker, "do_listing", original)
    now[0] = result["eligible_at"] + 1

    instability_receipts = []
    for count in (2, 3):
        worker = start()
        result = worker.perform(worker.choose(), time.time() + 60)
        assert result["status"] == ("pending" if count < 3 else "blocked")
        saved = listing_task(worker)
        receipt = json.loads(saved["receipt"])
        instability_receipts.append(receipt)
        assert saved["attempts"] == count + 1
        now[0] = (result["eligible_at"] or now[0]) + 901

    assert budget_receipt["inventory_change_count"] == 1
    for count, saved_receipt in zip((2, 3), instability_receipts):
        assert saved_receipt["inventory_change_count"] == count
        assert saved_receipt["inventory_change_reason"] == "idb_inventory_total_changed"
    assert receipt["retry_decision"] is None and result["eligible_at"] is None
    assert len(calls) == 6
    now[0] += 86400
    worker = start()
    assert worker.choose() is None and listing_task(worker)["status"] == "blocked"
    assert worker.host_state("jobs.iadb.org").get("stopped", False) is False
    assert worker.shared_policy.source_hold("idb_successfactors") is None
    with worker.db.connect() as conn:
        assert conn.execute("SELECT count(*) FROM jobs").fetchone()[0] == 0
        assert conn.execute("SELECT last_list_at FROM remediation_sources").fetchone()[0] is None
        attempts = list(conn.execute("SELECT * FROM remediation_attempts ORDER BY started_at"))
    assert [attempt["status"] for attempt in attempts] == ["pending"] * 3 + ["blocked"]
    assert all(attempt["finished_at"] is not None for attempt in attempts)
    for attempt in (attempts[0], attempts[2], attempts[3]):
        directory = Path(json.loads(attempt["evidence"])["capture_directory"])
        assert len(list((directory / "http").glob("*.json"))) == 2
        assert not (directory / "listing.json").exists()


def test_real_host_cooldown_remains_eligibility_deferral_after_many_task_attempts(idb_worker):
    start, _, _ = idb_worker
    worker = start()
    task = worker.choose()
    task["attempts"] = 20
    task["receipt"] = json.dumps({
        "inventory_change_count": 3,
        "retry_input_sha256": worker.retry_input_fingerprint(json.loads(task["payload"])),
    })
    eligible_at = time.time() + 7200
    decision = worker.retry_after_error(
        task, worker.workspace / "captures",
        HostIneligible("Actual host cooldown", category="cooldown", eligible_at=eligible_at),
    )
    assert decision == {"category": "eligibility_deferred", "eligible_at": eligible_at}
