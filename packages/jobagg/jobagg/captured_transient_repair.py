"""Pure evidence checks for explicitly reviewed legacy captured-response repairs.

This is not an automatic retry classifier. It recognizes only the observed IOM
and IFAD listing maintenance failures and UNV category-support gateway failure.
The caller must bind the durable attempt/task, source configuration, current
policy gates and repair journal before changing any queue state.
"""

from __future__ import annotations

from datetime import datetime
import gzip
import hashlib
import json
from pathlib import Path
import re
import zlib


IOM_LISTING_URL = (
    "https://fa-evlj-saasfaprod1.fa.ocs.oraclecloud.com"
    "/hcmRestApi/CandidateExperience/en/siteSettings/CX_1001"
)
IFAD_LISTING_URL = (
    "https://job.ifad.org/psc/IFHRPRDE/CAREERS/JOBS/c/"
    "HRS_HRAM_FL.HRS_CG_SEARCH_FL.GBL?Page=HRS_APP_SCHJOB_FL&Action=U"
)
UNV_SUPPORT_URL = (
    "https://app.unv.org/api/doa/taskExecutionManager/"
    "getRelatedActionsAndSectionsConfiguration"
)
_SCOPES = {
    ("iom_oracle_hcm", "listing"): ("oracle_hcm", "GET", IOM_LISTING_URL, 503),
    ("ifad_peoplesoft", "listing"): ("peoplesoft", "GET", IFAD_LISTING_URL, 503),
    ("unv_uvp", "detail"): ("unv", "POST", UNV_SUPPORT_URL, 502),
}
_MAX_METADATA_BYTES = 256 * 1024
_MAX_BODY_BYTES = 2 * 1024 * 1024
_MAX_CAPTURE_FILES = 32
_CHALLENGE = re.compile(
    r"awswaf|cf-chl-|captcha|verify (?:you are human|that you're not a robot)|"
    r"checking your browser|<title[^>]*>\s*(?:just a moment|access denied|access forbidden)|"
    r"<input[^>]+type=[\"']password", re.I,
)


def _sha(value):
    return hashlib.sha256(value).hexdigest()


def _file(path, root, *, limit):
    """Reject aliases before reading; retain bounded compressed/decoded work."""
    if not path.is_absolute() or path.resolve() != path or not path.is_relative_to(root):
        raise ValueError("Captured repair artifact escapes its unaliased attempt directory")
    if not path.is_file() or path.stat().st_size > limit:
        raise ValueError("Captured repair artifact is missing or exceeds its read limit")
    with path.open("rb") as stream:
        value = stream.read(limit + 1)
    if len(value) > limit:
        raise ValueError("Captured repair artifact exceeds its read limit")
    return value


def _reference(path, raw):
    return {"path": str(path), "sha256": _sha(raw)}


def _body(path, meta, target):
    artifact = path.with_suffix(".body.gz")
    if meta.get("artifact") != str(artifact) or meta.get("body_captured") is not True:
        raise ValueError("Captured repair body does not bind its metadata path")
    size = meta.get("body_bytes")
    if type(size) is not int or not 0 < size <= _MAX_BODY_BYTES:
        raise ValueError("Captured repair body has invalid bounded size")
    raw = _file(artifact, target, limit=_MAX_BODY_BYTES)
    try:
        # A bounded decoded read prevents a small gzip bomb expanding freely.
        import io
        with gzip.GzipFile(fileobj=io.BytesIO(raw)) as stream:
            body = stream.read(size + 1)
    except (OSError, EOFError, zlib.error) as exc:
        raise ValueError("Captured repair body is not a complete gzip artifact") from exc
    if len(body) != size or _sha(body) != meta.get("body_sha256"):
        raise ValueError("Captured repair body differs from its recorded size/hash")
    if _CHALLENGE.search(body.decode("utf-8", errors="replace")):
        raise ValueError("Access challenges are not captured transient repair evidence")
    return body, _reference(artifact, raw)


def _binding(meta, task, family):
    if meta.get("source_binding") != {
        "source_id": task["source_id"], "ats_family": family, "cxs_base_url": None,
    }:
        raise ValueError("Captured repair source binding differs")
    identity = task["external_id"] or None
    if meta.get("external_id") != identity or meta.get("phase") != {
        "kind": task["kind"], "job_id": identity,
    }:
        raise ValueError("Captured repair phase/identity differs")


def _route(meta, method, url):
    if (meta.get("method") != method or meta.get("url") != url
            or meta.get("response_url") != url
            or meta.get("request_url_sha256") != _sha(url.encode())
            or meta.get("response_url_sha256") != _sha(url.encode())
            or meta.get("redirect_url")):
        raise ValueError("Captured repair request/response route differs")


def _code(value):
    if not isinstance(value, dict):
        return None
    code = value.get("value")
    return code.get("code") if isinstance(code, dict) else None


def _unv_request(paths, metadata, task, target, failure):
    """Reconstruct the support POST from its same-attempt native assignment."""
    url = "https://app.unv.org/api/doa/doa/" + task["external_id"]
    found = [(p, m, raw) for p, m, raw in metadata if m.get("url") == url]
    if len(found) != 1 or found[0][0] >= paths[-1]:
        raise ValueError("UNV support failure lacks one earlier exact native detail")
    path, meta, raw = found[0]
    _binding(meta, task, "unv")
    _route(meta, "GET", url)
    if (meta.get("state") != "response_captured" or meta.get("status_code") != 200
            or meta.get("error_type") or meta.get("failure_category")):
        raise ValueError("UNV native detail was not successfully captured")
    body, body_ref = _body(path, meta, target)
    response = json.loads(body)
    value = response.get("value") if isinstance(response, dict) else None
    if (not isinstance(value, dict) or response.get("isSuccess") is not True
            or str(value.get("id") or value.get("doaRequestNo")) != task["external_id"]):
        raise ValueError("UNV native assignment identity differs")
    category = value.get("volunteersCategoryDetails")
    if not isinstance(category, dict):
        raise ValueError("UNV native assignment lacks its category object")
    category_code = _code(category.get("volunteersCategory"))
    status = _code(value.get("status"))
    if (not isinstance(category_code, str) or not re.fullmatch(r"[A-Z0-9_]+", category_code)
            or not isinstance(status, str) or not re.fullmatch(r"DOA_[A-Z_]+", status)):
        raise ValueError("UNV native assignment cannot bind its support request")
    request = {"volunteerCategoryCode": category_code,
               "entityName": "doa,doaCandidate" if status in {"DOA_RECRUITED", "DOA_SOURCING"} else "doa"}
    encoded = json.dumps(request, separators=(",", ":")).encode()
    if failure.get("request_body_sha256") != _sha(encoded):
        raise ValueError("UNV support request body differs from its native assignment")
    return [_reference(path, raw), body_ref]


def validate_captured_transient(workspace, task):
    """Return bound evidence for these reviewed scopes; never mutate or fetch.

    Unrelated tasks return None. A task in scope with malformed or contradictory
    capture evidence raises ValueError, so a repair caller must fail closed.
    Current host/source/task eligibility remains the caller's responsibility.
    """
    scope = _SCOPES.get((task.get("source_id"), task.get("kind")))
    if scope is None or task.get("status") != "blocked":
        return None
    family, method, url, status = scope
    identity = task.get("external_id")
    if (not isinstance(identity, str) or (task["kind"] == "listing" and identity != "")
            or (task["kind"] == "detail" and not re.fullmatch(r"[0-9]+", identity))):
        raise ValueError("Captured repair task identity is invalid")
    workspace = Path(workspace)
    claim = task.get("claim")
    if not isinstance(claim, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", claim):
        raise ValueError("Captured repair claim is invalid")
    receipt = json.loads(task.get("receipt") or "{}")
    target = workspace / "captures" / claim
    if (not isinstance(receipt, dict) or receipt.get("capture_directory") != str(target)
            or not workspace.is_absolute() or workspace.resolve() != workspace
            or target.resolve() != target):
        raise ValueError("Captured repair directory does not bind the durable claim")
    paths = sorted((target / "http").glob("*.json"))
    if not paths or len(paths) > _MAX_CAPTURE_FILES:
        raise ValueError("Captured repair has no bounded metadata sequence")
    metadata = []
    for number, path in enumerate(paths, 1):
        if not re.fullmatch(r"[0-9]{5}\.json", path.name):
            raise ValueError("Captured repair metadata sequence is malformed")
        raw = _file(path, target, limit=_MAX_METADATA_BYTES)
        meta = json.loads(raw)
        if (not isinstance(meta, dict) or meta.get("number") != int(path.stem)
                or int(path.stem) != number):
            raise ValueError("Captured repair metadata number differs")
        metadata.append((path, meta, raw))
    path, meta, raw = metadata[-1]
    _binding(meta, task, family)
    _route(meta, method, url)
    error = f"HTTPError: HTTP Error {status}: Captured HTTP denial/error"
    if (meta.get("state") != "failed" or meta.get("status_code") != status
            or meta.get("failure_category") != "transient_transport"
            or meta.get("error_type") != "HTTPError" or meta.get("error") != error
            or task.get("last_error") != error or receipt.get("error") != error):
        raise ValueError("Captured repair is not the typed incident HTTP failure")
    try:
        start, end = (datetime.fromisoformat(meta[key].replace("Z", "+00:00"))
                      for key in ("started_at", "finished_at"))
        if start.tzinfo is None or end.tzinfo is None or end < start:
            raise ValueError("Captured repair timestamps are invalid")
    except (KeyError, TypeError, AttributeError) as exc:
        raise ValueError("Captured repair timestamps are missing or malformed") from exc
    body, body_ref = _body(path, meta, target)
    text = " ".join(re.sub(r"<[^>]+>", " ", body.decode("utf-8", errors="replace")).lower().split())
    markers = {
        "iom_oracle_hcm": ("planned outage", "scheduled maintenance"),
        "ifad_peoplesoft": ("ifad erecruitment system is currently offline for maintenance",),
        "unv_uvp": ("502", "bad gateway", "azure front door", "originconnectionaborted"),
    }[task["source_id"]]
    if not all(marker in text for marker in markers):
        raise ValueError("Captured repair body is not the observed maintenance/gateway template")
    refs = [_reference(path, raw), body_ref]
    if task["source_id"] == "unv_uvp":
        refs.extend(_unv_request(paths, metadata, task, target, meta))
        for earlier_path, earlier, earlier_raw in metadata[:-1]:
            if earlier.get("source_binding") != meta["source_binding"]:
                raise ValueError("Earlier UNV capture has a foreign source binding")
            phase = earlier.get("phase")
            robots = phase == {"kind": "robots", "job_id": identity, "originating_phase": "detail"}
            if robots:
                if (earlier.get("robots_policy_result") != "robots_not_present_allow"
                        or earlier.get("status_code") != 400
                        or earlier.get("external_id") != identity
                        or earlier.get("error_type") or earlier.get("failure_category")):
                    raise ValueError("Earlier UNV robots outcome is not the observed policy allowance")
                _route(earlier, "GET", "https://uvpprdpublicstorage01.blob.core.windows.net/robots.txt")
            else:
                _binding(earlier, task, family)
                if (earlier.get("state") != "response_captured" or earlier.get("status_code") != 200
                        or earlier.get("error_type") or earlier.get("failure_category")):
                    raise ValueError("Earlier UNV capture contains another failure")
                _, earlier_body_ref = _body(earlier_path, earlier, target)
                refs.append(earlier_body_ref)
            refs.append(_reference(earlier_path, earlier_raw))
    elif len(paths) != 1 or meta.get("request_body_sha256"):
        raise ValueError("Listing maintenance repair must be its first bodyless GET")
    refs = list({ref["path"]: ref for ref in refs}.values())
    return {"category": "captured_transient_response", "evidence": refs,
            "retry_url": url, "capture_kind": task["kind"], "status_code": status,
            "source_id": task["source_id"]}
