import pytest
from jobagg.adapters.idb_api import parse_page, reconcile, request_payload
from jobagg.adapters.peoplesoft import _public_date
from jobagg.normalize import parse_datetime


def test_ifad_year_first_slash_date_is_unambiguous():
    assert parse_datetime(_public_date("2026/09/24")).isoformat() == "2026-09-24T00:00:00+00:00"
    assert _public_date("09/10/2026") == "09/10/2026"


def test_idb_duplicate_pages_cannot_certify_total():
    row = {
        "external_id": "1",
        "title": "Role",
        "url": "https://jobs.iadb.org/job/Role/1-en_US",
    }
    with pytest.raises(ValueError, match="union"):
        reconcile([dict(sort=s, page=0, total=2, rows=[row]) for s in ("", "date")])
    assert (
        len(
            reconcile(
                [dict(sort=s, page=0, total=2, rows=[row]) for s in ("", "date")],
                require_total=False,
            )
        )
        == 1
    )
    with pytest.raises(ValueError, match="missing"):
        reconcile([dict(sort="", page=0, total=1, rows=[row])])
    assert list(reconcile([dict(sort=s, page=0, total=1, rows=[row]) for s in ("", "date")])) == [
        "1"
    ]


def test_idb_api_page_requires_all_slots_and_identity():
    with pytest.raises(ValueError, match="page size"):
        parse_page({"totalJobs": 91, "jobSearchResult": []}, 0)
    assert request_payload(1, "date")["facetFilters"] == {}
    assert request_payload(1, "date")["categoryId"] == 9638000


def test_captured_incomplete_inventory_and_tamper_rejection(tmp_path):
    import gzip
    import hashlib
    import json
    from pathlib import Path
    from jobagg.adapters.idb_api import URL, fetch, verify
    from jobagg.adapters.base import AdapterContext
    from jobagg.adapters.successfactors_rmk import SuccessFactorsRMKAdapter
    from jobagg.models import OrganizationSource
    from jobagg.http import HttpResponse

    data = json.loads(
        gzip.decompress(
            (Path(__file__).parent / "fixtures/idb/public_api_20260921.json.gz").read_bytes()
        )
    )
    captures = []

    class HTTP:
        def post_json(self, url, payload):
            entry = next(e for e in data if e["request"] == payload)
            body = json.dumps(entry["response"]).encode()
            artifact = tmp_path / (str(len(captures)) + ".gz")
            artifact.write_bytes(gzip.compress(body))
            meta = tmp_path / (str(len(captures)) + ".json")
            meta.write_text(
                json.dumps(
                    dict(
                        url=url,
                        response_url=url,
                        method="POST",
                        status_code=200,
                        body_captured=True,
                        artifact=str(artifact),
                        body_sha256=hashlib.sha256(body).hexdigest(),
                        phase={"kind": "listing"},
                        public_pagination_request=payload,
                    )
                )
            )
            captures.append(meta)
            return HttpResponse(URL, 200, {}, body.decode(), body)

    source = OrganizationSource(
        "idb_successfactors",
        "IDB",
        "successfactors_rmk",
        "https://jobs.iadb.org",
        extra={"date_locale": "US", "public_search_api": True},
    )
    jobs = fetch(SuccessFactorsRMKAdapter(AdapterContext(source, HTTP())))
    assert len(jobs) == 89
    assert not verify(source, jobs, captures)["complete"]
    assert not verify(source, jobs, captures[:-1])["complete"]
    m = json.loads(captures[0].read_text())
    m["public_pagination_request"]["keywords"] = "finance"
    captures[0].write_text(json.dumps(m))
    assert not verify(source, jobs, captures)["complete"]


# The real 91-advertised/89-observed fixture must remain incomplete; its useful
# records are still returned for detail fetching without closing unseen jobs.


def api_page(count=1):
    return {"totalJobs": count, "jobSearchResult": [
        {"response": {"id": str(i + 1), "unifiedStandardTitle": f"Role {i + 1}",
                      "urlTitle": f"Role-{i + 1}", "supportedLocales": ["en_US"]}}
        for i in range(count)
    ]}


@pytest.mark.parametrize("data", [None, [], {"totalJobs": 1, "jobSearchResult": [None]},
                                    {"totalJobs": 1, "jobSearchResult": [{"response": []}]}])
def test_malformed_api_pages_fail_closed(data):
    with pytest.raises(ValueError):
        parse_page(data, 0)


@pytest.mark.parametrize("field,value", [
    ("id", "١"), ("unifiedStandardTitle", {}), ("urlTitle", []),
    ("supportedLocales", None), ("jobLocationShort", "Washington"),
])
def test_malformed_api_identity_fields_fail_closed(field, value):
    data = api_page()
    data["jobSearchResult"][0]["response"][field] = value
    with pytest.raises(ValueError):
        parse_page(data, 0)


@pytest.mark.parametrize("count", [0, 1])
def test_capture_pipeline_certifies_exact_complete_scope_and_rejects_tampering(tmp_path, monkeypatch, count):
    import json
    from jobagg.adapters.base import AdapterContext
    from jobagg.adapters.idb_api import URL
    from jobagg.adapters.successfactors_rmk import SuccessFactorsRMKAdapter
    from jobagg.http import JobAggHTTPClient, HttpResponse
    from jobagg.models import OrganizationSource
    from jobagg.pipelines.http_checkpoint import DurableCapture
    from jobagg.pipelines.inventory_checks import verify_listing
    from jobagg.robots import load_policy

    monkeypatch.setattr("jobagg.pipelines.http_checkpoint.time.sleep", lambda _: None)
    policy = tmp_path / "policy.yaml"
    policy.write_text("default:\n  honor_robots_txt: false\n  min_delay_seconds: 0\n")
    body = json.dumps(api_page(count)).encode()
    client = JobAggHTTPClient()
    client._request = lambda url, **kwargs: HttpResponse(url, 200, {}, body.decode(), body)
    recorder = DurableCapture(client, load_policy(policy), tmp_path / "captures", {},
                              lock_root=tmp_path / "locks", phase={"kind": "listing"})
    source = OrganizationSource("idb_successfactors", "IDB", "successfactors_rmk", URL,
                                extra={"public_search_api": True, "date_locale": "US"})
    adapter = SuccessFactorsRMKAdapter(AdapterContext(source, client))
    client._request = recorder.request
    jobs = adapter.fetch_jobs()
    captures = sorted((tmp_path / "captures/http").glob("*.json"))
    assert len(captures) == 2
    proof = verify_listing(source, jobs, captures)
    assert proof["complete"] and proof["reported_total"] == count
    assert proof["verified_zero"] is (count == 0)
    assert adapter.run_diagnostics.pagination_complete is True
    if count == 0:
        assert adapter.run_diagnostics.empty_reason == "verified_total_zero"
    assert not verify_listing(source, jobs, captures[:-1])["complete"]
    metadata = json.loads(captures[0].read_text())
    metadata["body_sha256"] = "0" * 64
    captures[0].write_text(json.dumps(metadata))
    assert not verify_listing(source, jobs, captures)["complete"]


def test_filtered_idb_requests_are_not_recorded_as_public_pagination(tmp_path, monkeypatch):
    import json
    from jobagg.adapters.idb_api import URL
    from jobagg.http import JobAggHTTPClient, HttpResponse
    from jobagg.pipelines.http_checkpoint import DurableCapture
    from jobagg.robots import load_policy

    monkeypatch.setattr("jobagg.pipelines.http_checkpoint.time.sleep", lambda _: None)
    policy = tmp_path / "policy.yaml"
    policy.write_text("default:\n  honor_robots_txt: false\n  min_delay_seconds: 0\n")
    client = JobAggHTTPClient()
    client._request = lambda url, **kwargs: HttpResponse(url, 200, {}, "{}", b"{}")
    recorder = DurableCapture(client, load_policy(policy), tmp_path / "captures", {},
                              lock_root=tmp_path / "locks", phase={"kind": "listing"})
    client._request = recorder.request
    client.post_json(URL, {**request_payload(0, ""), "keywords": "private search"})
    metadata = json.loads(next((tmp_path / "captures/http").glob("*.json")).read_text())
    assert "public_pagination_request" not in metadata
    assert "private search" not in json.dumps(metadata)
