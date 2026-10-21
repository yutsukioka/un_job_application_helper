"""Sanitized incident fixtures; no network or live database access."""

from copy import deepcopy
import gzip
import hashlib
import json
from pathlib import Path

import pytest

from jobagg.captured_transient_repair import (
    IFAD_LISTING_URL, IOM_LISTING_URL, UNV_SUPPORT_URL, validate_captured_transient,
)


def sha(data):
    return hashlib.sha256(data).hexdigest()


SCOPES = {
    "iom_oracle_hcm": ("listing", "oracle_hcm", "GET", IOM_LISTING_URL, 503,
                       b"<title>Planned Outage</title><p>Service undergoing scheduled maintenance.</p>"),
    "ifad_peoplesoft": ("listing", "peoplesoft", "GET", IFAD_LISTING_URL, 503,
                       b"<p>The IFAD eRecruitment system is currently offline for maintenance.</p>"),
    "unv_uvp": ("detail", "unv", "POST", UNV_SUPPORT_URL, 502,
                b"<h1>502</h1><p>Bad Gateway: Azure Front Door OriginConnectionAborted</p>"),
}


def fixture(tmp_path, source):
    kind, family, method, url, status, body = SCOPES[source]
    identity = "1784888021272636" if kind == "detail" else ""
    target = tmp_path / "captures" / "incident-claim"
    (target / "http").mkdir(parents=True)
    error = f"HTTPError: HTTP Error {status}: Captured HTTP denial/error"
    task = {"source_id": source, "kind": kind, "external_id": identity,
            "status": "blocked", "claim": target.name, "last_error": error,
            "receipt": json.dumps({"capture_directory": str(target), "error": error})}

    def capture(number, captured_body, route, verb, code, *, failed=False):
        path = target / "http" / f"{number:05d}.json"
        artifact = path.with_suffix(".body.gz")
        artifact.write_bytes(gzip.compress(captured_body))
        meta = {"number": number, "external_id": identity or None,
                "phase": {"kind": kind, "job_id": identity or None},
                "source_binding": {"source_id": source, "ats_family": family, "cxs_base_url": None},
                "method": verb, "url": route, "response_url": route,
                "request_url_sha256": sha(route.encode()), "response_url_sha256": sha(route.encode()),
                "artifact": str(artifact), "body_captured": True, "body_bytes": len(captured_body),
                "body_sha256": sha(captured_body), "state": "failed" if failed else "response_captured",
                "status_code": code, "started_at": "2026-10-01T12:30:00+00:00",
                "finished_at": "2026-10-01T12:30:01+00:00"}
        if failed:
            meta.update(failure_category="transient_transport", error_type="HTTPError", error=error)
        path.write_text(json.dumps(meta))
        return path, meta

    if source == "unv_uvp":
        native = {"isSuccess": True, "value": {"id": int(identity),
                  "volunteersCategoryDetails": {"volunteersCategory": {"value": {"code": "NAT_ASO_PRE_FTM_LTR"}}},
                  "status": {"value": {"code": "DOA_SOURCING"}}}}
        capture(1, json.dumps(native).encode(), "https://app.unv.org/api/doa/doa/" + identity, "GET", 200)
        path, meta = capture(2, body, url, method, status, failed=True)
        meta["request_body_sha256"] = sha(json.dumps({"volunteerCategoryCode": "NAT_ASO_PRE_FTM_LTR",
                  "entityName": "doa,doaCandidate"}, separators=(",", ":")).encode())
        path.write_text(json.dumps(meta))
    else:
        path, meta = capture(1, body, url, method, status, failed=True)
    return task, path, meta


def bytes_snapshot(root):
    return {str(path.relative_to(root)): path.read_bytes() for path in root.rglob("*") if path.is_file()}


@pytest.mark.parametrize("source", SCOPES)
def test_bound_incidents_are_validated_without_mutation(tmp_path, source):
    task, path, meta = fixture(tmp_path, source)
    before, task_before = bytes_snapshot(tmp_path), deepcopy(task)
    result = validate_captured_transient(tmp_path, task)
    assert result["status_code"] == meta["status_code"]
    assert result["source_id"] == source and result["capture_kind"] == task["kind"]
    assert result["retry_url"] == meta["url"]
    assert {ref["path"] for ref in result["evidence"]} >= {str(path), meta["artifact"]}
    assert all(sha(Path(ref["path"]).read_bytes()) == ref["sha256"] for ref in result["evidence"])
    assert before == bytes_snapshot(tmp_path) and task == task_before


@pytest.mark.parametrize("source", SCOPES)
@pytest.mark.parametrize("field,value", [
    ("source_binding", {"source_id": "foreign", "ats_family": "unv", "cxs_base_url": None}),
    ("phase", {"kind": "detail", "job_id": "wrong"}),
    ("external_id", "wrong"), ("method", "DELETE"),
    ("url", "https://example.org/unrelated"), ("response_url", "https://example.org/login"),
    ("request_url_sha256", "0" * 64), ("response_url_sha256", "0" * 64),
    ("body_sha256", "0" * 64), ("body_bytes", 1), ("body_captured", False),
    ("state", "response_captured"), ("status_code", 403), ("status_code", 401),
    ("failure_category", "access_denied"), ("error_type", "SSLCertVerificationError"),
    ("started_at", "2026-10-01T12:30:00"), ("finished_at", "2026-09-30T12:00:00+00:00"),
])
def test_mismatched_metadata_is_rejected(tmp_path, source, field, value):
    task, path, meta = fixture(tmp_path, source)
    meta[field] = value
    path.write_text(json.dumps(meta))
    with pytest.raises(ValueError):
        validate_captured_transient(tmp_path, task)


@pytest.mark.parametrize("source", SCOPES)
@pytest.mark.parametrize("suffix", [b"<title>Just a moment</title>", b"verify you are human", b"cf-chl-token",
                                    b"<input type='password'>", b"captcha"])
def test_challenges_remain_rejected_even_with_maintenance_words(tmp_path, source, suffix):
    task, path, meta = fixture(tmp_path, source)
    body = gzip.decompress(Path(meta["artifact"]).read_bytes()) + suffix
    Path(meta["artifact"]).write_bytes(gzip.compress(body))
    meta.update(body_sha256=sha(body), body_bytes=len(body))
    path.write_text(json.dumps(meta))
    with pytest.raises(ValueError, match="Access challenges"):
        validate_captured_transient(tmp_path, task)


@pytest.mark.parametrize("source", SCOPES)
def test_complete_gzip_crc_and_artifact_binding_are_required(tmp_path, source):
    task, path, meta = fixture(tmp_path, source)
    artifact = Path(meta["artifact"])
    artifact.write_bytes(artifact.read_bytes()[:-5])
    with pytest.raises(ValueError):
        validate_captured_transient(tmp_path, task)


@pytest.mark.parametrize("alias", ["body", "http", "attempt"])
def test_symlink_artifacts_are_rejected(tmp_path, alias):
    workspace = tmp_path / "workspace"
    task, path, meta = fixture(workspace, "iom_oracle_hcm")
    original = Path(meta["artifact"]) if alias == "body" else path.parent if alias == "http" else path.parent.parent
    moved = tmp_path / "aliased"
    original.rename(moved)
    original.symlink_to(moved)
    with pytest.raises(ValueError):
        validate_captured_transient(workspace, task)


@pytest.mark.parametrize("source", SCOPES)
def test_receipt_and_task_claim_must_match(tmp_path, source):
    task, _, _ = fixture(tmp_path, source)
    task["claim"] = "different"
    with pytest.raises(ValueError, match="durable claim"):
        validate_captured_transient(tmp_path, task)


@pytest.mark.parametrize("source", ["iom_oracle_hcm", "ifad_peoplesoft"])
def test_healthy_listing_response_is_not_a_transient_failure(tmp_path, source):
    task, path, meta = fixture(tmp_path, source)
    # Sanitized shapes from the healthy listing routes: site settings and a job grid.
    body = (b'{"siteNumber":"CX_1001","siteName":"IOM Careers"}' if source == "iom_oracle_hcm"
            else b'<span>11 jobs found</span><div id="HRS_JOB_OPENING_ID">38204</div>')
    Path(meta["artifact"]).write_bytes(gzip.compress(body))
    meta.update(status_code=200, state="response_captured", body_bytes=len(body), body_sha256=sha(body))
    for key in ("error_type", "error", "failure_category"):
        meta.pop(key)
    path.write_text(json.dumps(meta))
    with pytest.raises(ValueError):
        validate_captured_transient(tmp_path, task)
    task["status"] = "done"
    assert validate_captured_transient(tmp_path, task) is None


@pytest.mark.parametrize("tamper", ["native_id", "request_hash", "native_hash", "native_status", "foreign_binding"])
def test_unv_support_post_requires_same_assignment_and_request(tmp_path, tamper):
    task, path, meta = fixture(tmp_path, "unv_uvp")
    native_path = path.with_name("00001.json")
    native = json.loads(native_path.read_text())
    if tamper == "request_hash":
        meta["request_body_sha256"] = "0" * 64
        path.write_text(json.dumps(meta))
    elif tamper == "native_id":
        body = json.loads(gzip.decompress(Path(native["artifact"]).read_bytes()))
        body["value"]["id"] += 1
        body = json.dumps(body).encode()
        Path(native["artifact"]).write_bytes(gzip.compress(body))
        native.update(body_bytes=len(body), body_sha256=sha(body))
    elif tamper == "native_hash":
        native["body_sha256"] = "0" * 64
    elif tamper == "native_status":
        native["status_code"] = 403
    else:
        native["source_binding"]["ats_family"] = "foreign"
    native_path.write_text(json.dumps(native))
    with pytest.raises(ValueError):
        validate_captured_transient(tmp_path, task)


def test_unrelated_sources_and_task_kinds_do_not_enter_repair(tmp_path):
    task, _, _ = fixture(tmp_path, "ifad_peoplesoft")
    task["source_id"] = "who_taleo"
    assert validate_captured_transient(tmp_path, task) is None
    task["source_id"], task["kind"] = "ifad_peoplesoft", "detail"
    assert validate_captured_transient(tmp_path, task) is None


@pytest.mark.parametrize("source", SCOPES)
def test_arbitrary_error_body_and_unrecorded_gzip_bytes_do_not_qualify(tmp_path, source):
    task, path, meta = fixture(tmp_path, source)
    body = b"<p>Unknown error or identity failure</p>"
    Path(meta["artifact"]).write_bytes(gzip.compress(body))
    meta.update(body_bytes=len(body), body_sha256=sha(body))
    path.write_text(json.dumps(meta))
    with pytest.raises(ValueError, match="observed maintenance/gateway template"):
        validate_captured_transient(tmp_path, task)
    # Matching the hash of an advertised prefix cannot conceal appended data.
    Path(meta["artifact"]).write_bytes(gzip.compress(body + b"unexpected extra bytes"))
    with pytest.raises(ValueError, match="size/hash"):
        validate_captured_transient(tmp_path, task)


@pytest.mark.parametrize("tamper", ["outside_artifact", "oversize_body", "error", "receipt_error", "number", "source_family"])
def test_repair_does_not_infer_missing_or_contradictory_bindings(tmp_path, tamper):
    task, path, meta = fixture(tmp_path, "iom_oracle_hcm")
    if tamper == "outside_artifact":
        outside = tmp_path / "unbound.body.gz"
        outside.write_bytes(Path(meta["artifact"]).read_bytes())
        meta["artifact"] = str(outside)
    elif tamper == "oversize_body":
        meta["body_bytes"] = 2 * 1024 * 1024 + 1
    elif tamper == "error":
        task["last_error"] = "SSLCertVerificationError: verification failed"
    elif tamper == "receipt_error":
        receipt = json.loads(task["receipt"])
        receipt["error"] = "HTTPError: HTTP Error 403: Captured HTTP denial/error"
        task["receipt"] = json.dumps(receipt)
    elif tamper == "number":
        meta["number"] = 12
    else:
        meta["source_binding"]["ats_family"] = "other"
    path.write_text(json.dumps(meta))
    with pytest.raises(ValueError):
        validate_captured_transient(tmp_path, task)


def test_unv_observed_missing_robots_response_is_not_an_access_bypass(tmp_path):
    task, failure_path, failure = fixture(tmp_path, "unv_uvp")
    # Insert the actual historical 400 robots-not-present shape before the failure.
    old_body = Path(failure["artifact"])
    new_body = failure_path.with_name("00003.body.gz")
    old_body.rename(new_body)
    failure.update(number=3, artifact=str(new_body))
    failure_path.with_name("00003.json").write_text(json.dumps(failure))
    url = "https://uvpprdpublicstorage01.blob.core.windows.net/robots.txt"
    robots = {"number": 2, "source_binding": failure["source_binding"], "external_id": task["external_id"],
              "phase": {"kind": "robots", "job_id": task["external_id"], "originating_phase": "detail"},
              "state": "dispatched_before_response", "method": "GET", "url": url, "response_url": url,
              "request_url_sha256": sha(url.encode()), "response_url_sha256": sha(url.encode()),
              "status_code": 400, "body_captured": False, "robots_policy_result": "robots_not_present_allow"}
    failure_path.write_text(json.dumps(robots))
    assert validate_captured_transient(tmp_path, task)["status_code"] == 502
    robots["status_code"] = 403
    failure_path.write_text(json.dumps(robots))
    with pytest.raises(ValueError, match="robots outcome"):
        validate_captured_transient(tmp_path, task)
