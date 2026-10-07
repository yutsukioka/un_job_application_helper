"""Exact source evidence for unavailable postings; absence is a separate census fact.

This module never infers unavailability from an exception string, a requested
URL used as a synthetic job identity, or a generic portal/challenge page.
"""
from __future__ import annotations

import hashlib
import gzip
from datetime import datetime
from html.parser import HTMLParser
import re
import json
from pathlib import Path
from urllib.parse import parse_qs, urlsplit, unquote


class DetailIdentityMismatch(ValueError):
    """A returned public identity is absent or disagrees with the queued job."""


class IncompleteDetailResponse(ValueError):
    """Successful transport returned no identifiable vacancy; retry is bounded."""


class VacancyUnavailable(ValueError):
    """A source's explicit unavailable template; worker must bind its capture."""


EXPLICIT_VACANCY_UNAVAILABLE = "explicit_vacancy_unavailable"
EMPTY_PUBLIC_ASSIGNMENT = "vacancy_detail_empty"
UNAVAILABLE_STATUSES = frozenset({"unavailable_pending_inventory", "listing_detail_conflict"})


class _ActiveTemplate(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.page_ids = []
        self.form_actions = []
        self.unavailable_interfaces = 0
        self.description_interfaces = 0
        self.active_page_ids = []
        self.active_unavailable_interfaces = 0
        self.forms = []

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag == "form":
            self.forms.append(values.get("id"))
        if tag == "input" and values.get("name") == "ftlpageid":
            self.page_ids.append(values.get("value"))
            if self.forms == ["ftlform"]:
                self.active_page_ids.append(values.get("value"))
        if tag == "form" and values.get("id") == "ftlform":
            self.form_actions.append(values.get("action", ""))
        if values.get("id") == "requisitionUnavailableInterface":
            self.unavailable_interfaces += 1
            if self.forms == ["ftlform"]:
                self.active_unavailable_interfaces += 1
        if values.get("id") == "requisitionDescriptionInterface":
            self.description_interfaces += 1

    def handle_endtag(self, tag):
        if tag == "form" and self.forms:
            self.forms.pop()


def unv_empty_assignment_template(source_id, external_id, request_url, response_url, body):
    """Structural observation only; callers must separately bind captured provenance.

    A successful null result contains no assignment, not an assertion of closure.
    Runtime classification requires current source binding. Reviewed legacy repair
    may use this pure detector only after validating the original task/frame chain.
    """
    if source_id != "unv_uvp" or not re.fullmatch(r"[0-9]+", str(external_id)):
        return None
    expected = "https://app.unv.org/api/doa/doa/" + str(external_id)
    if request_url != expected or response_url != expected:
        return None

    def unique_object(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise ValueError("Ambiguous duplicate JSON member")
            value[key] = item
        return value

    try:
        payload = json.loads(body, object_pairs_hook=unique_object)
    except (ValueError, TypeError, UnicodeError):
        return None
    if (not isinstance(payload, dict)
            or set(payload) != {"traceRegistries", "isSuccess", "value"}
            or payload["traceRegistries"] != [] or payload["isSuccess"] is not True
            or payload["value"] is not None):
        return None
    return {"category": EMPTY_PUBLIC_ASSIGNMENT,
            "detector": "unv_exact_successful_null_assignment_v1"}


def unavailable_template(source_id, external_id, request_url, response_url, body):
    """Pure structural detector. HTTP/capture provenance is checked separately."""
    text = body.decode("utf-8", errors="replace") if isinstance(body, bytes) else body
    if not isinstance(text, str) or not external_id:
        return None
    request, response = urlsplit(request_url), urlsplit(response_url)
    host = {"unicef_pageup": "jobs.unicef.org", "fao_taleo": "jobs.fao.org",
            "who_taleo": "careers.who.int",
            "opcw_talentsoft_candidatespace": "jobs.opcw.org"}.get(source_id)
    if not host or any(parts.scheme != "https" or parts.netloc != host
                       or parts.username or parts.password or parts.fragment
                       for parts in (request, response)):
        return None
    if re.search(r"awswaf|cf-chl-|verify (?:you are human|that you're not a robot)|"
                 r"checking your browser|<title[^>]*>\s*(?:just a moment|access denied)", text, re.I):
        return None
    if source_id == "opcw_talentsoft_candidatespace":
        match = re.fullmatch(r"/job/job-[^/]+_(\d+)\.aspx", request.path)
        panel = re.search(r'<div\b[^>]*id=[\"\'][^\"\']*defaultValidationSummary[\"\'][^>]*>(.*?)</div>', text, re.I | re.S)
        if (not match or match[1] != str(external_id) or request != response
                or request.query or not panel
                or not re.search(r'<li>\s*This vacancy does not exist/no longer exists on this site\s*</li>', panel[1])):
            return None
        return {"category": EXPLICIT_VACANCY_UNAVAILABLE, "detector": "opcw_bound_unavailable_panel_v1"}
    if source_id == "unicef_pageup":
        match = re.fullmatch(r"/en-us/job/(\d+)(?:/[^?#]*)?", request.path)
        if (not match or match[1] != str(external_id)
                or response.path.rstrip("/") != "/en-us/listing"
                or parse_qs(response.query).get("jobnotfound") != ["true"]
                or "job-externaljobno" in text.casefold()):
            return None
        return {"category": EXPLICIT_VACANCY_UNAVAILABLE, "detector": "unicef_jobnotfound_redirect_v1"}
    if source_id == "who_taleo":
        query = parse_qs(request.query, keep_blank_values=True)
        if (not re.fullmatch(r"[0-9]+", str(external_id))
                or request.path != "/careersection/ex/jobdetail.ftl" or request != response
                or query.get("job") != [str(external_id)]
                or set(query) - {"job", "lang", "tz", "tzname"}
                or any(len(values) != 1 or not values[0] for values in query.values())):
            return None
        parsed = _ActiveTemplate()
        parsed.feed(text)
        if (parsed.page_ids != ["unavaibleRequisitionPage"]
                or parsed.active_page_ids != ["unavaibleRequisitionPage"]
                or parsed.form_actions != ["unavailablerequisition.ftl"]
                or parsed.unavailable_interfaces != 1 or parsed.active_unavailable_interfaces != 1
                or parsed.description_interfaces
                or re.search(r"<input[^>]+type\s*=\s*['\"]password", text, re.I)
                or re.search(r"api\.fill(?:List|Form|Interface)\s*\(\s*['\"]requisitionDescriptionInterface['\"]", text)
                or not re.search(r"api\.fillInterface\s*\(\s*['\"]requisitionUnavailableInterface['\"]\s*,\s*\[[^\]]*"
                                 r"['\"]The job description you are trying to view is no longer available\.['\"][^\]]*\]", text)):
            return None
        return {"category": EXPLICIT_VACANCY_UNAVAILABLE,
                "detector": "who_active_unavailable_template_v1"}
    if (request.path != "/careersection/fao_external/jobdetail.ftl"
            or parse_qs(request.query).get("job") != [str(external_id)]
            or response.path not in {request.path, "/careersection/fao_external/unavailablerequisition.ftl"}
            or (response.path == request.path and parse_qs(response.query).get("job") != [str(external_id)])):
        return None
    parsed = _ActiveTemplate()
    parsed.feed(text)
    # General Taleo `_acts` JavaScript lists this template even on healthy jobs.
    # Only the active form/page controls establish an unavailable response.
    if (parsed.page_ids != ["unavaibleRequisitionPage"]
            or len(parsed.form_actions) != 1
            or urlsplit(parsed.form_actions[0]).path != "unavailablerequisition.ftl"
            or re.search(r"api\.fillList\(\s*['\"]requisitionDescriptionInterface['\"]", text)):
        return None
    return {"category": EXPLICIT_VACANCY_UNAVAILABLE, "detector": "fao_active_unavailable_template_v1"}



class _UnavailableHeading(HTMLParser):
    """Read visible headings, excluding script/style/template contents."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tag = None
        self.parts = []
        self.headings = []
        self.ignored = 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style", "template"}:
            self.ignored += 1
        if not self.ignored and tag in {"h1", "h2"}:
            self.tag, self.parts = tag, []

    def handle_data(self, value):
        if self.tag and not self.ignored:
            self.parts.append(value)

    def handle_endtag(self, tag):
        if self.ignored and tag in {"script", "style", "template"}:
            self.ignored -= 1
        if tag == self.tag:
            self.headings.append(" ".join("".join(self.parts).split()).casefold())
            self.tag = None


def detail_denial_template(source_id, external_id, metadata, body, original_url, *, redirected=False):
    """Narrow provider errors are job scoped; they never establish closure.

    Source binding is supplied by the guarded worker, not by a response body.
    A Workday S22 can affect a still-open vacancy, so a fresh complete inventory
    and bounded detail recheck must resolve the task independently.
    """
    binding = metadata.get("source_binding") or {}
    if (binding.get("source_id") != source_id or metadata.get("method") != "GET"
            or metadata.get("status_code") != 403):
        return None
    request = urlsplit(original_url)
    response = urlsplit(metadata.get("response_url", ""))
    if any(p.scheme != "https" or not p.hostname or p.username or p.password
           or p.query or p.fragment or p.port not in (None, 443) for p in (request, response)):
        return None
    text = body.decode("utf-8", errors="replace")
    if re.search(r"awswaf|cf-chl-|verify (?:you are human|that you're not a robot)|"
                 r"checking your browser|<title[^>]*>\s*(?:just a moment|access denied|access forbidden)|"
                 r"<input[^>]+type=[\"']password", text, re.I):
        return None
    if binding.get("ats_family") == "workday":
        endpoint = urlsplit(binding.get("cxs_base_url", ""))
        if (redirected or request != response or request.netloc != endpoint.netloc
                or endpoint.scheme != "https" or endpoint.query or endpoint.fragment
                or not re.fullmatch(r"/wday/cxs/[^/]+/[^/]+", endpoint.path)
                or not request.hostname.endswith((".myworkdayjobs.com", ".myworkdaysite.com"))
                or not request.path.startswith(endpoint.path + "/job/")
                or not unquote(request.path).rsplit("/", 1)[-1].endswith("_" + str(external_id))):
            return None
        content_type = next((v for k, v in (metadata.get("response_headers") or {}).items()
                             if k.lower() == "content-type"), "")
        if content_type.split(";", 1)[0].strip().lower() != "application/json":
            return None
        try:
            value = json.loads(body)
        except (ValueError, UnicodeError):
            return None
        if (not isinstance(value, dict)
                or set(value) != {"errorCode", "errorCaseId", "httpStatus", "message", "messageParams"}
                or value.get("errorCode") != "S22" or value.get("httpStatus") != 403
                or value.get("message") != "permission denied" or value.get("messageParams") != {}
                or not isinstance(value.get("errorCaseId"), str) or not value["errorCaseId"]):
            return None
        return {"category": "vacancy_detail_denied", "detector": "workday_exact_cxs_s22_v1"}
    if source_id != "unops_avature" or binding.get("ats_family") != "avature":
        return None
    if (not redirected or request.netloc != "careers.unops.org" or response.netloc != request.netloc
            or response.path != "/careersmarketplace/Error"
            or not re.fullmatch(r"/careersmarketplace/JobDetail/[^/]+/" + re.escape(str(external_id)), request.path)
            or re.search(r"Position Title|Posting End Date|<input[^>]+(?:name|id)=[\"'](?:password|login)", text, re.I)):
        return None
    parsed = _UnavailableHeading()
    parsed.feed(text)
    if parsed.headings != ["page not found"]:
        return None
    return {"category": "vacancy_detail_denied", "detector": "unops_exact_detail_error_not_found_v1"}

def classify_unavailable(source_id, external_id, metadata, body, *, redirects=()):
    """Return typed evidence only for an exact captured provider detail outcome.

    Callers must additionally bind the metadata file hash and original queued
    listing frame. This function is usable by reviewed old-task migrations.
    """
    if not isinstance(body, bytes):
        return None
    phase = metadata.get("phase", {})
    if (metadata.get("state") != "response_captured" or metadata.get("status_code") not in {200, 403}
            or metadata.get("body_captured") is not True
            or phase.get("kind") != "detail"
            or str(phase.get("job_id")) != str(external_id)
            or not metadata.get("started_at") or not metadata.get("finished_at")
            or hashlib.sha256(body).hexdigest() != metadata.get("body_sha256")):
        return None
    original_url = metadata.get("url", "")
    if redirects:
        # Bind every hop to the same detail phase, and the last hop to this body.
        # No synthetic request URL may be substituted without recorded redirects.
        for index, hop in enumerate(redirects):
            following = redirects[index + 1] if index + 1 < len(redirects) else metadata
            if (hop.get("state") != "redirect_captured"
                    or hop.get("status_code") not in {301, 302, 303, 307, 308}
                    or hop.get("phase") != phase
                    or hop.get("redirect_url") != following.get("url")
                    or urlsplit(hop.get("url", "")).scheme != "https"
                    or urlsplit(hop.get("url", "")).netloc != urlsplit(metadata.get("response_url", "")).netloc
                    or not hop.get("started_at") or not hop.get("finished_at")):
                return None
        original_url = redirects[0].get("url", "")
    if source_id in {"unv_uvp", "who_taleo"}:
        binding = metadata.get("source_binding") or {}
        content_type = next((value for key, value in (metadata.get("response_headers") or {}).items()
                             if key.lower() == "content-type"), "")
        expected_type = "application/json" if source_id == "unv_uvp" else "text/html"
        if (redirects or metadata.get("status_code") != 200 or metadata.get("method") != "GET"
                or binding.get("source_id") != source_id
                or binding.get("ats_family") != ("unv" if source_id == "unv_uvp" else "taleo")
                or type(metadata.get("body_bytes")) is not int or metadata["body_bytes"] != len(body)
                or not isinstance(content_type, str)
                or content_type.split(";", 1)[0].strip().lower() != expected_type):
            return None
        try:
            stamps = [datetime.fromisoformat(metadata[key].replace("Z", "+00:00"))
                      for key in ("started_at", "finished_at")]
            if any(stamp.tzinfo is None for stamp in stamps) or stamps[0] > stamps[1]:
                return None
        except (ValueError, TypeError, AttributeError):
            return None
    result = (unv_empty_assignment_template(source_id, external_id, original_url,
                                            metadata.get("response_url", ""), body)
              if source_id == "unv_uvp" else
              unavailable_template(source_id, external_id, original_url,
                                   metadata.get("response_url", ""), body)
              if metadata.get("status_code") == 200 else
              detail_denial_template(source_id, external_id, metadata, body, original_url,
                                     redirected=bool(redirects)))
    if result is None:
        return None
    return {**result, "source_id": source_id, "external_id": str(external_id),
            "request_url": original_url, "response_url": metadata["response_url"],
            "body_sha256": metadata["body_sha256"], "observed_at": metadata["finished_at"],
            "closure_inferred": False, "detail_complete": False}


def captured_unavailable(source_id, external_id, capture_paths):
    """Bind a pure outcome to exact metadata files and a complete redirect chain."""
    captures = [(Path(path), json.loads(Path(path).read_text())) for path in sorted(capture_paths)]
    for position in range(len(captures) - 1, -1, -1):
        path, meta = captures[position]
        if (meta.get("phase", {}).get("kind") != "detail"
                or str(meta.get("phase", {}).get("job_id")) != str(external_id)):
            continue
        if meta.get("state") != "response_captured" or not meta.get("artifact"):
            return None
        redirects, url = [], meta.get("url")
        for prior_path, prior in reversed(captures[:position]):
            if (prior.get("state") == "redirect_captured" and prior.get("redirect_url") == url
                    and prior.get("phase") == meta.get("phase")):
                redirects.insert(0, (prior_path, prior))
                url = prior.get("url")
        body = gzip.decompress(Path(meta["artifact"]).read_bytes())
        evidence = classify_unavailable(source_id, external_id, meta, body,
                                        redirects=[value for _, value in redirects])
        if evidence:
            evidence["captures"] = [{"path": str(item), "sha256": hashlib.sha256(item.read_bytes()).hexdigest()}
                                    for item, _ in [*redirects, (path, meta)]]
            return evidence
        # An earlier unavailable response cannot override a later different,
        # malformed or valid response from the same guarded detail attempt.
        return None
    return None
