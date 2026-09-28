from copy import deepcopy
from datetime import UTC, datetime
import gzip
import hashlib
import json
from pathlib import Path

import pytest

from jobagg.vacancy_outcomes import classify_unavailable, captured_unavailable, unavailable_template


FAO_URL = "https://jobs.fao.org/careersection/fao_external/jobdetail.ftl?job=2601857&lang=es"
UNICEF_URL = "https://jobs.unicef.org/en-us/job/595551/role"
UNICEF_MISSING = "https://jobs.unicef.org/en-us/listing/?jobnotfound=true"
FAO_BODY = b'<form id="ftlform" action="unavailablerequisition.ftl"><input type="hidden" name="ftlpageid" value="unavaibleRequisitionPage"></form>'


def metadata(url=FAO_URL, response=None, body=FAO_BODY, identity="2601857"):
    now = datetime.now(UTC).isoformat()
    return {"state": "response_captured", "status_code": 200, "body_captured": True,
            "phase": {"kind": "detail", "job_id": identity}, "url": url, "response_url": response or url,
            "body_sha256": hashlib.sha256(body).hexdigest(), "started_at": now, "finished_at": now}


def test_fao_active_unavailable_template_is_typed_without_inferred_closure():
    result = classify_unavailable("fao_taleo", "2601857", metadata(), FAO_BODY)
    assert result["detector"] == "fao_active_unavailable_template_v1"
    assert result["closure_inferred"] is result["detail_complete"] is False


@pytest.mark.parametrize("mutation", ["wrong_phase", "wrong_job", "failed", "body_hash", "wrong_host", "wrong_query", "wrong_response_job", "challenge", "generic", "valid_body", "inactive_marker", "duplicate_active"])
def test_unavailable_detector_rejects_ambiguous_or_unbound_evidence(mutation):
    body, meta = FAO_BODY, metadata()
    if mutation == "wrong_phase": meta["phase"]["kind"] = "listing"
    if mutation == "wrong_job": meta["phase"]["job_id"] = "another"
    if mutation == "failed": meta["state"] = "failed"
    if mutation == "body_hash": meta["body_sha256"] = "bad"
    if mutation == "wrong_host": meta["response_url"] = FAO_URL.replace("jobs.fao.org", "example.com")
    if mutation == "wrong_query": meta["url"] += "&job=2601857"
    if mutation == "wrong_response_job": meta["response_url"] = FAO_URL.replace("2601857", "2601999")
    if mutation == "challenge": body += b"<title>Access denied</title>"
    if mutation == "generic": body = b"This job is unavailable."
    if mutation == "valid_body": body += b"<script>api.fillList('requisitionDescriptionInterface','descRequisition',['job']);</script>"
    if mutation == "inactive_marker": body = b"<script>_acts:[['unavaibleRequisitionPage','unavailablerequisition']];</script>"
    if mutation == "duplicate_active": body += FAO_BODY
    if mutation in {"challenge", "generic", "valid_body", "inactive_marker", "duplicate_active"}:
        meta["body_sha256"] = hashlib.sha256(body).hexdigest()
    assert classify_unavailable("fao_taleo", "2601857", meta, body) is None


@pytest.mark.parametrize("fixture", sorted((Path(__file__).parent / "fixtures/taleo_public_bindings").glob("*.html")))
def test_healthy_taleo_pages_contain_inactive_unavailable_markers_but_are_not_unavailable(fixture):
    body = fixture.read_bytes()
    assert b"unavaibleRequisitionPage" in body
    assert unavailable_template("fao_taleo", "2601857", FAO_URL, FAO_URL, body) is None


def test_unicef_requires_exact_expected_id_and_explicit_redirect():
    body = b"<h1>Current opportunities</h1>"
    meta = metadata(UNICEF_URL, UNICEF_MISSING, body, "595551")
    assert classify_unavailable("unicef_pageup", "595551", meta, body)
    assert classify_unavailable("unicef_pageup", "595552", meta, body) is None
    meta["response_url"] = "https://jobs.unicef.org/en-us/listing/"
    assert classify_unavailable("unicef_pageup", "595551", meta, body) is None


def test_guarded_redirect_chain_is_bound_to_final_capture_and_phase(tmp_path):
    body = b"<h1>Current opportunities</h1>"
    final = metadata(UNICEF_MISSING, UNICEF_MISSING, body, "595551")
    hop = {"state": "redirect_captured", "status_code": 302, "url": UNICEF_URL,
           "redirect_url": UNICEF_MISSING, "phase": final["phase"],
           "started_at": final["started_at"], "finished_at": final["finished_at"]}
    assert classify_unavailable("unicef_pageup", "595551", final, body) is None
    assert classify_unavailable("unicef_pageup", "595551", final, body, redirects=[hop])
    wrong = deepcopy(hop); wrong["phase"]["job_id"] = "other"
    assert classify_unavailable("unicef_pageup", "595551", final, body, redirects=[wrong]) is None
    blob = tmp_path / "body.gz"; blob.write_bytes(gzip.compress(body)); final["artifact"] = str(blob)
    paths = []
    for index, meta in enumerate([hop, final]):
        path = tmp_path / f"{index:05}.json"; path.write_text(json.dumps(meta)); paths.append(path)
    evidence = captured_unavailable("unicef_pageup", "595551", paths)
    assert len(evidence["captures"]) == 2 and evidence["request_url"] == UNICEF_URL
    blob.write_bytes(gzip.compress(body + b"changed"))
    assert captured_unavailable("unicef_pageup", "595551", paths) is None


def test_later_different_response_prevents_classifying_old_unavailable_capture(tmp_path):
    paths = []
    for number, body in enumerate([FAO_BODY, b"<html>Unknown public job content</html>"]):
        meta = metadata(body=body)
        path = tmp_path / f"{number:05}.json"
        blob = path.with_suffix(".gz"); blob.write_bytes(gzip.compress(body)); meta["artifact"] = str(blob)
        path.write_text(json.dumps(meta)); paths.append(path)
    assert captured_unavailable("fao_taleo", "2601857", paths) is None
