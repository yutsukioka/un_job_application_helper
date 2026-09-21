"""Exact source evidence for unavailable postings; absence is a separate census fact.

This module never infers unavailability from an exception string, a requested
URL used as a synthetic job identity, or a generic portal/challenge page.
"""
from __future__ import annotations

import hashlib
import gzip
from html.parser import HTMLParser
import re
import json
from pathlib import Path
from urllib.parse import parse_qs, urlsplit


class DetailIdentityMismatch(ValueError):
    """A returned public identity is absent or disagrees with the queued job."""


class IncompleteDetailResponse(ValueError):
    """Successful transport returned no identifiable vacancy; retry is bounded."""


class VacancyUnavailable(ValueError):
    """A source's explicit unavailable template; worker must bind its capture."""


UNAVAILABLE_STATUSES = frozenset({"unavailable_pending_inventory", "listing_detail_conflict"})


class _ActiveTemplate(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.page_ids = []
        self.form_actions = []

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag == "input" and values.get("name") == "ftlpageid":
            self.page_ids.append(values.get("value"))
        if tag == "form" and values.get("id") == "ftlform":
            self.form_actions.append(values.get("action", ""))


def unavailable_template(source_id, external_id, request_url, response_url, body):
    """Pure structural detector. HTTP/capture provenance is checked separately."""
    text = body.decode("utf-8", errors="replace") if isinstance(body, bytes) else body
    if not isinstance(text, str) or not external_id:
        return None
    request, response = urlsplit(request_url), urlsplit(response_url)
    host = {"unicef_pageup": "jobs.unicef.org", "fao_taleo": "jobs.fao.org",
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
        return {"category": "explicit_vacancy_unavailable", "detector": "opcw_bound_unavailable_panel_v1"}
    if source_id == "unicef_pageup":
        match = re.fullmatch(r"/en-us/job/(\d+)(?:/[^?#]*)?", request.path)
        if (not match or match[1] != str(external_id)
                or response.path.rstrip("/") != "/en-us/listing"
                or parse_qs(response.query).get("jobnotfound") != ["true"]
                or "job-externaljobno" in text.casefold()):
            return None
        return {"category": "explicit_vacancy_unavailable", "detector": "unicef_jobnotfound_redirect_v1"}
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
    return {"category": "explicit_vacancy_unavailable", "detector": "fao_active_unavailable_template_v1"}


def classify_unavailable(source_id, external_id, metadata, body, *, redirects=()):
    """Return typed evidence only for an exact successful captured detail response.

    Callers must additionally bind the metadata file hash and original queued
    listing frame. This function is usable by reviewed old-task migrations.
    """
    if not isinstance(body, bytes):
        return None
    phase = metadata.get("phase", {})
    if (metadata.get("state") != "response_captured" or metadata.get("status_code") != 200
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
    result = unavailable_template(source_id, external_id, original_url,
                                  metadata.get("response_url", ""), body)
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
