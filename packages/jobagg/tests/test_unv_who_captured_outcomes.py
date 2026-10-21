from copy import deepcopy
import gzip
import hashlib
import json
from pathlib import Path

import pytest

from jobagg.adapters.base import AdapterContext
from jobagg.adapters.taleo import TaleoAdapter
from jobagg.adapters.unv import UNVAdapter
from jobagg.http import JobAggHTTPClient
from jobagg.models import OrganizationSource
from jobagg.vacancy_outcomes import (
    DetailIdentityMismatch, VacancyUnavailable, captured_unavailable,
    classify_unavailable, unavailable_template, unv_empty_assignment_template,
)

FIXTURES = Path(__file__).parent / "fixtures"
UNV_ID = "1784888021272041"
WHO_ID = "2603964"
UNV_URL = f"https://app.unv.org/api/doa/doa/{UNV_ID}"
WHO_URL = f"https://careers.who.int/careersection/ex/jobdetail.ftl?job={WHO_ID}&tz=GMT%2B00%3A00&tzname=Etc%2FUTC"


def incident(source):
    unv = source == "unv_uvp"
    body = (FIXTURES / "source_outcomes" /
            ("unv_successful_null.json" if unv else "who_active_unavailable.html")).read_bytes()
    url, identity = (UNV_URL, UNV_ID) if unv else (WHO_URL, WHO_ID)
    return identity, body, {
        "state": "response_captured", "status_code": 200, "method": "GET",
        "phase": {"kind": "detail", "job_id": identity}, "url": url, "response_url": url,
        "external_id": identity,
        "request_url_sha256": hashlib.sha256(url.encode()).hexdigest(),
        "response_url_sha256": hashlib.sha256(url.encode()).hexdigest(),
        "source_binding": {"source_id": source, "ats_family": "unv" if unv else "taleo"},
        "body_captured": True, "body_bytes": len(body), "body_sha256": hashlib.sha256(body).hexdigest(),
        "response_headers": {"Content-Type": ("application/json" if unv else "text/html") + "; charset=utf-8"},
        "started_at": "2026-10-07T00:00:01+00:00", "finished_at": "2026-10-07T00:00:02+00:00",
    }


@pytest.mark.parametrize("source,category,detector", [
    ("unv_uvp", "vacancy_detail_empty", "unv_exact_successful_null_assignment_v1"),
    ("who_taleo", "explicit_vacancy_unavailable", "who_active_unavailable_template_v1"),
])
def test_exact_captured_outcome_is_typed_without_closure_or_detail_credit(source, category, detector):
    identity, body, meta = incident(source)
    result = classify_unavailable(source, identity, meta, body)
    assert result["category"] == category and result["detector"] == detector
    assert result["closure_inferred"] is result["detail_complete"] is False
    assert result["external_id"] == identity and result["body_sha256"] == meta["body_sha256"]


@pytest.mark.parametrize("source", ["unv_uvp", "who_taleo"])
@pytest.mark.parametrize("field", ["external_id", "request_url_sha256", "response_url_sha256"])
@pytest.mark.parametrize("value", [None, "contradictory"])
def test_recorded_identity_and_url_hashes_must_agree(source, field, value):
    identity, body, meta = incident(source)
    if value is None:
        meta.pop(field)
    else:
        meta[field] = value
    assert classify_unavailable(source, identity, meta, body) is None


@pytest.mark.parametrize("source", ["unv_uvp", "who_taleo"])
@pytest.mark.parametrize("damage", [
    "source", "family", "missing_binding", "source_argument", "method", "phase", "job", "identity_argument",
    "status403", "status500", "state", "uncaptured", "body_hash", "body_size", "boolean_size", "mime",
    "wrong_host", "wrong_route", "response_id", "request_id", "credentials", "fragment", "redirect",
    "missing_start", "naive_time", "reversed_time",
])
def test_new_contracts_reject_unbound_or_ambiguous_capture(source, damage):
    identity, body, meta = incident(source)
    redirects = ()
    if damage == "source": meta["source_binding"]["source_id"] = "another"
    elif damage == "family": meta["source_binding"]["ats_family"] = "another"
    elif damage == "missing_binding": meta.pop("source_binding")
    elif damage == "source_argument": source = "another"
    elif damage == "method": meta["method"] = "POST"
    elif damage == "phase": meta["phase"]["kind"] = "listing"
    elif damage == "job": meta["phase"]["job_id"] = "another"
    elif damage == "identity_argument": identity = "1234567"
    elif damage.startswith("status"): meta["status_code"] = int(damage[6:])
    elif damage == "state": meta["state"] = "failed"
    elif damage == "uncaptured": meta["body_captured"] = False
    elif damage == "body_hash": meta["body_sha256"] = "changed"
    elif damage == "body_size": meta["body_bytes"] += 1
    elif damage == "boolean_size": meta["body_bytes"] = True
    elif damage == "mime": meta["response_headers"]["Content-Type"] = "text/plain"
    elif damage == "wrong_host": meta["response_url"] = meta["response_url"].replace(".org", ".example").replace(".int", ".example")
    elif damage == "wrong_route": meta["url"] = meta["response_url"] = meta["url"].replace("/api/doa/doa/", "/api/doa/profile/").replace("/ex/", "/internal/")
    elif damage == "response_id": meta["response_url"] = meta["response_url"].replace(identity, "1234567")
    elif damage == "request_id": meta["url"] = meta["url"].replace(identity, "1234567")
    elif damage == "credentials": meta["url"] = meta["response_url"] = meta["url"].replace("https://", "https://user@")
    elif damage == "fragment": meta["url"] = meta["response_url"] = meta["url"] + "#fragment"
    elif damage == "redirect":
        redirects = [{"state": "redirect_captured", "status_code": 302, "url": meta["url"],
                      "redirect_url": meta["url"], "phase": deepcopy(meta["phase"]),
                      "started_at": meta["started_at"], "finished_at": meta["finished_at"]}]
    elif damage == "missing_start": meta.pop("started_at")
    elif damage == "naive_time": meta["started_at"] = "2026-10-07T00:00:01"
    elif damage == "reversed_time": meta["started_at"] = "2026-10-08T00:00:01+00:00"
    # Route cases carry internally consistent hashes so they still exercise the
    # source/identity route guards rather than the new contradiction guard.
    for field, url_field in (("request_url_sha256", "url"), ("response_url_sha256", "response_url")):
        meta[field] = hashlib.sha256(meta[url_field].encode()).hexdigest()
    assert classify_unavailable(source, identity, meta, body, redirects=redirects) is None


@pytest.mark.parametrize("body", [
    b"null", b"[]", b"{}", b'{"isSuccess":true,"value":null}',
    b'{"traceRegistries":[],"isSuccess":false,"value":null}',
    b'{"traceRegistries":[],"isSuccess":1,"value":null}',
    b'{"traceRegistries":["access denied"],"isSuccess":true,"value":null}',
    b'{"traceRegistries":[],"isSuccess":true,"value":{}}',
    b'{"traceRegistries":[],"isSuccess":true,"value":[]}',
    b'{"traceRegistries":[],"isSuccess":true,"value":null,"error":"challenge"}',
    b'{"traceRegistries":[],"isSuccess":true,"value":{},"value":null}',
    b'{"traceRegistries":[],"isSuccess":true,"value":null} trailing',
    b'<title>Access denied</title>',
])
def test_unv_only_exact_successful_null_envelope_is_structural_observation(body):
    assert unv_empty_assignment_template("unv_uvp", UNV_ID, UNV_URL, UNV_URL, body) is None


@pytest.mark.parametrize("damage", ["duplicate_job", "empty_job", "extra_query", "missing_panel", "inactive_page",
                                       "duplicate_form", "missing_message", "native_payload", "native_panel", "challenge", "login", "outside_form"])
def test_who_active_template_excludes_inactive_markers_and_mixed_identity(damage):
    identity, body, meta = incident("who_taleo")
    if damage == "duplicate_job": meta["url"] = meta["response_url"] = WHO_URL + "&job=" + identity
    elif damage == "empty_job": meta["url"] = meta["response_url"] = WHO_URL + "&job="
    elif damage == "extra_query": meta["url"] = meta["response_url"] = WHO_URL + "&portal=another"
    elif damage == "missing_panel": body = body.replace(b'id="requisitionUnavailableInterface"', b'id="other"')
    elif damage == "inactive_page": body = body.replace(b'value="unavaibleRequisitionPage"', b'value="requisitionDescriptionPage"')
    elif damage == "duplicate_form": body += body
    elif damage == "missing_message": body = body.replace(b"The job description you are trying to view is no longer available.", b"Please sign in.")
    elif damage == "native_payload": body += b"<script>api.fillList('requisitionDescriptionInterface','descRequisition',['job']);</script>"
    elif damage == "native_panel": body += b'<div id="requisitionDescriptionInterface"></div>'
    elif damage == "challenge": body += b'<title>Access denied</title>'
    elif damage == "login": body += b'<input type="password" name="password">'
    elif damage == "outside_form": body = body.replace(b'<input type="hidden"', b'</form><input type="hidden"')
    meta.update(body_bytes=len(body), body_sha256=hashlib.sha256(body).hexdigest())
    meta["request_url_sha256"] = hashlib.sha256(meta["url"].encode()).hexdigest()
    meta["response_url_sha256"] = hashlib.sha256(meta["response_url"].encode()).hexdigest()
    assert classify_unavailable("who_taleo", identity, meta, body) is None


def test_historical_healthy_who_keeps_native_identity_and_inactive_marker():
    body = (FIXTURES / "taleo_public_bindings/who_taleo_2603964_20260913.html").read_text()
    assert "unavaibleRequisitionPage" in body
    assert unavailable_template("who_taleo", WHO_ID, WHO_URL, WHO_URL, body) is None
    source = OrganizationSource("who_taleo", "WHO", "taleo", "https://careers.who.int/careersection/ex/jobsearch.ftl")
    adapter = TaleoAdapter(AdapterContext(source, JobAggHTTPClient()))
    adapter.fetch_text = lambda url: body
    job = adapter.fetch_detail_for_listing_item({"contestNo": WHO_ID, "_taleo_detail_url": WHO_URL})
    assert job.external_id == WHO_ID and job.description
    with pytest.raises(DetailIdentityMismatch):
        adapter.fetch_detail_for_listing_item({"contestNo": "1234567", "_taleo_detail_url": WHO_URL.replace(WHO_ID, "1234567")})
    adapter.fetch_text = lambda url: incident("who_taleo")[1].decode()
    with pytest.raises(VacancyUnavailable):
        adapter.fetch_detail_for_listing_item({"contestNo": WHO_ID, "_taleo_detail_url": WHO_URL})


def test_historical_healthy_unv_native_assignment_still_parses_and_mismatch_rejects():
    item = json.loads((FIXTURES / f"unv/public_projection_{UNV_ID}_20260913.json").read_text())
    source = OrganizationSource("unv_uvp", "UNV", "unv", "https://app.unv.org")
    adapter = UNVAdapter(AdapterContext(source, JobAggHTTPClient()))
    adapter.fetch_json = lambda url: {"value": item}
    job = adapter.fetch_detail_for_listing_item({"id": UNV_ID})
    assert job.external_id == UNV_ID and job.title
    with pytest.raises(ValueError, match="identity does not match"):
        adapter.fetch_detail_for_listing_item({"id": "1234567"})
    adapter.fetch_json = lambda url: json.loads(incident("unv_uvp")[1])
    with pytest.raises(ValueError, match="not a single public assignment"):
        adapter.fetch_detail_for_listing_item({"id": UNV_ID})


@pytest.mark.parametrize("source", ["unv_uvp", "who_taleo"])
def test_capture_replay_binds_original_bytes_and_rejects_later_ambiguous_response(source, tmp_path):
    identity, body, meta = incident(source)
    path = tmp_path / "00001.json"
    blob = path.with_suffix(".body.gz"); blob.write_bytes(gzip.compress(body))
    meta["artifact"] = str(blob); path.write_text(json.dumps(meta))
    result = captured_unavailable(source, identity, [path])
    assert result["captures"] == [{"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}]
    blob.write_bytes(gzip.compress(body + b"changed"))
    assert captured_unavailable(source, identity, [path]) is None
    blob.write_bytes(gzip.compress(body))
    later = tmp_path / "00002.json"; other = b"{}"
    other_blob = later.with_suffix(".body.gz"); other_blob.write_bytes(gzip.compress(other))
    meta.update(artifact=str(other_blob), body_bytes=len(other), body_sha256=hashlib.sha256(other).hexdigest())
    later.write_text(json.dumps(meta))
    assert captured_unavailable(source, identity, [path, later]) is None
