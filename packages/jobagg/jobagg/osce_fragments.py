"""HAR-observed OSCE public pagination contract; no user session import."""

import gzip
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import re
from urllib.parse import parse_qsl, urlencode, urlsplit

from jobagg.adapters.osce_inventory import captured_scope, pagination_state

DATA_ROUTE = "osce_job_results_v1"
CSRF_CONTRACT = "osce_tss_token_v1"
ORIGIN = "https://vacancies.osce.org"
ENDPOINT = "/ajax/content/job_results"


def csrf_token(html):
    """Read the current anonymous page token; never include its value in errors."""

    class Inputs(HTMLParser):
        def __init__(self):
            super().__init__(convert_charrefs=True)
            self.values = []

        def handle_starttag(self, tag, attrs):
            values = dict(attrs)
            if tag == "input" and (
                values.get("id") == "tsstoken" or values.get("name") == "tsstoken"
            ):
                if (
                    len(values) != len(attrs)
                    or values.get("id") != "tsstoken"
                    or values.get("name") != "tsstoken"
                    or values.get("type", "").lower() != "hidden"
                ):
                    raise ValueError("OSCE CSRF field is ambiguous")
                self.values.append(values.get("value"))

    parser = Inputs()
    parser.feed(html)
    if len(parser.values) != 1 or not re.fullmatch(r"[\x21-\x7e]{1,4096}", parser.values[0] or ""):
        raise ValueError("OSCE CSRF token missing, ambiguous or invalid")
    return parser.values[0]


def validate_csrf_header(headers, expected):
    values = [v for k, v in headers.items() if k.lower() == "tss-token"]
    if not expected or values != [expected]:
        raise ValueError("OSCE CSRF header differs from current document")


def session_parts(url):
    match = re.fullmatch(r"/jobs/search/(\d+)(?:/page([1-9]\d*))?/?", urlsplit(url).path)
    if not match:
        raise ValueError("OSCE generated search session missing")
    return match[1], int(match[2]) if match[2] else None


def site_name(html):
    values = re.findall(
        r'\bsite\s*:\s*\{[^{}]*\bshort_name\s*:\s*["\']([A-Za-z0-9_-]{1,80})["\']', html
    )
    if len(values) != 1:
        raise ValueError("OSCE public site name missing or ambiguous")
    return values[0]


def page_url(session, page, origin=ORIGIN):
    return origin + "/jobs/search/" + session + (f"/page{page}" if page != 1 else "")


def request_url(session, site, page, uid, origin=ORIGIN):
    return (
        origin
        + ENDPOINT
        + "?"
        + urlencode(
            {
                "JobSearch.id": session,
                "page_index": str(page),
                "site-name": site,
                "include_site": "true",
                "uid": str(uid),
            }
        )
    )


def validate_request(url, method, body, *, session, site, page, origin=ORIGIN):
    parts = urlsplit(url)
    if (
        parts.scheme + "://" + parts.netloc != origin
        or parts.path != ENDPOINT
        or parts.fragment
        or method != "POST"
        or body not in (None, "", b"")
    ):
        raise ValueError("OSCE fragment request outside read-only contract")
    pairs = parse_qsl(parts.query, keep_blank_values=True)
    query = dict(pairs)
    if (
        len(pairs) != len(query)
        or set(query) != {"JobSearch.id", "page_index", "site-name", "include_site", "uid"}
        or query["JobSearch.id"] != session
        or not re.fullmatch(r"\d+", session)
        or query["site-name"] != site
        or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", site)
        or not 2 <= page <= 50
        or query["page_index"] != str(page)
        or query["include_site"] != "true"
        or not re.fullmatch(r"\d{1,3}", query["uid"])
    ):
        raise ValueError("OSCE fragment parameters differ from bound session/site/page")
    return query


def result_html(text, expected_page):
    payload = json.loads(text)
    if (
        not isinstance(payload, dict)
        or payload.get("Status") != "OK"
        or not isinstance(payload.get("Result"), str)
    ):
        raise ValueError("OSCE fragment response is not successful result HTML")
    html = payload["Result"]
    if pagination_state(html)[0] != expected_page:
        raise ValueError("OSCE fragment page index differs from request")
    if captured_scope(html) != {"new_jobs": False, "unfiltered": True}:
        raise ValueError("OSCE fragment is filtered")
    return html


def verify_captures(bundle, capture_paths):
    """Bind each reconciled page to its actual native HTTP response bytes."""
    match = re.fullmatch(
        r'<script type="application/json" id="jobagg-osce-inventory">(.*)</script>', bundle, re.S
    )
    if not match:
        raise ValueError("OSCE page bundle missing")
    pages = json.loads(match[1])
    allowed = {Path(p).resolve() for p in capture_paths}
    records = []
    session = site = None
    used = set()
    for number, page in enumerate(pages, 1):
        path = Path(page["capture_path"]).resolve()
        if path not in allowed or path in used:
            raise ValueError("OSCE page capture is unbound or reused")
        used.add(path)
        meta = json.loads(path.read_text())
        if (
            meta.get("status_code") != 200
            or meta.get("state") != "response_captured"
            or meta.get("phase", {}).get("kind") != "listing"
            or meta.get("transport") != "chromium_cdp_native_v1"
            or meta.get("body_captured") is not True
            or meta.get("url") != page["request_url"]
            or meta.get("response_url") != meta["url"]
        ):
            raise ValueError("OSCE page requires a successful bound native capture")
        artifact = Path(meta["artifact"]).resolve()
        if artifact != path.with_suffix(".body.gz"):
            raise ValueError("OSCE response artifact outside capture")
        raw = gzip.decompress(artifact.read_bytes())
        if hashlib.sha256(raw).hexdigest() != meta["body_sha256"]:
            raise ValueError("OSCE response hash differs")
        if number == 1:
            if meta["method"] != "GET" or (
                urlsplit(meta["url"]).scheme,
                urlsplit(meta["url"]).netloc,
            ) != ("https", "vacancies.osce.org"):
                raise ValueError("OSCE first page is not the public search document")
            session, index = session_parts(meta["url"])
            if index not in (None, 1):
                raise ValueError("OSCE first response is not page one")
            html = raw.decode("utf-8")
            site = site_name(html)
            csrf_token(html)
        else:
            validate_request(
                meta["url"], meta["method"], None, session=session, site=site, page=number
            )
            if meta.get("transport_diagnostics", {}).get("request_body_bytes") != 0:
                raise ValueError("OSCE pagination body must be empty")
            csrf = meta.get("transport_diagnostics", {}).get("csrf_observation", {})
            if csrf != {"available": True, "present": True, "matches_document": True}:
                raise ValueError("OSCE pagination lacks verified current-document CSRF header")
            html = result_html(raw.decode("utf-8"), number)
        if page["url"] != page_url(session, number) or html != page["html"]:
            raise ValueError("OSCE page differs from captured response")
        records.append({"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
    return records
