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
from jobagg.adapters.idb_api import URL, fetch, reconcile, verify
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
    monkeypatch, first_total, second_total,
):
    source = idb_source()
    adapter = SuccessFactorsRMKAdapter(AdapterContext(source, None))
    calls = []

    def response(url, payload):
        calls.append(payload)
        return page(first_total if len(calls) == 1 else second_total, payload["pageNumber"])

    adapter.post_json = response
    monkeypatch.setattr("jobagg.adapters.idb_api.time.time", lambda: 1000)
    with pytest.raises(HostIneligible, match="total changed") as error:
        fetch(adapter)
    assert error.value.category == "cooldown" and error.value.eligible_at == 1900
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


def test_idb_worker_preserves_attempt_and_pending_retry_without_listing_writes(tmp_path, monkeypatch):
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

    class Client(JobAggHTTPClient):
        def _request(self, url, **kwargs):
            payload = json.loads(kwargs["body"])
            calls.append(payload)
            body = json.dumps(page(1 if payload["sortBy"] == "" else 2)).encode()
            return HttpResponse(url, 200, {"Content-Type": "application/json"},
                                body.decode(), body)

    worker = Worker(registry=registry, robots=robots, workspace=tmp_path / "workspace",
                    shared_lock=lock, policy_bootstrap=bootstrap,
                    client_factory=lambda source, policy: Client(), max_tasks=1)
    worker.initialize()
    worker.seed_listings()
    task = worker.choose()
    before = time.time()
    result = worker.perform(task, time.time() + 60)
    assert result["status"] == "pending" and result["error_type"] == "HostIneligible"
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
    assert receipt["retry_decision"]["category"] == "eligibility_deferred"
    captures = sorted((Path(receipt["capture_directory"]) / "http").glob("*.json"))
    assert len(captures) == 2
    assert all(json.loads(path.read_text())["status_code"] == 200 for path in captures)
    assert not (Path(receipt["capture_directory"]) / "listing.json").exists()
