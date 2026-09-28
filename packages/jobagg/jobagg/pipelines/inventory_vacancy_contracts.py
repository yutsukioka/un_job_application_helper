"""Independent captured-page censuses for reviewed UNICEF and FAO endpoints."""
from copy import deepcopy
from datetime import datetime
import gzip
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import re
from urllib.parse import parse_qsl, quote, urlencode, urljoin, urlsplit, urlunsplit

from jobagg.pipelines.inventory_api_contracts import identifier, jobs_by_id, require, url_signature


def _count(value, label):
    require(type(value) is int and value >= 0, "Invalid " + label)
    return value


def _scope(source):
    names = ("filter_url", "page_size", "query") if source.id == "unicef_pageup" else (
        "search_api_url", "search_payload", "enumerate_job_locales", "max_job_locales")
    return hashlib.sha256(json.dumps({"source_id": source.id,
        "scope": {key: source.extra.get(key) for key in names}}, sort_keys=True,
        separators=(",", ":")).encode()).hexdigest()


def _page(path, meta, expected_url, result, request_body=None):
    require(meta.get("phase", {}).get("kind") == "listing"
            and meta.get("state") == "response_captured" and meta.get("status_code") == 200
            and meta.get("method") == "POST" and meta.get("body_captured") is True,
            "Listing census requires a successful captured POST")
    require(url_signature(meta.get("url")) == url_signature(expected_url)
            == url_signature(meta.get("response_url")), "Listing census scope/response URL changed")
    if request_body is not None:
        digest = hashlib.sha256(json.dumps(request_body, separators=(",", ":")).encode()).hexdigest()
        require(meta.get("request_body_sha256") == digest, "Listing POST body differs from configured page/filter")
    else:
        require(meta.get("request_body_sha256") in (None, hashlib.sha256(b"").hexdigest()),
                "Unexpected PageUp listing POST body")
    body = gzip.decompress(Path(meta["artifact"]).read_bytes())
    require(hashlib.sha256(body).hexdigest() == meta.get("body_sha256"), "Listing body hash differs")
    payload = json.loads(body)
    require(isinstance(payload, dict), "Listing body is not an object")
    for key in ("started_at", "finished_at"):
        stamp = datetime.fromisoformat(str(meta.get(key, "")).replace("Z", "+00:00"))
        require(stamp.tzinfo is not None, "Listing capture timestamp lacks timezone")
    result["capture_paths"].append({"path": str(path), "sha256": hashlib.sha256(Path(path).read_bytes()).hexdigest()})
    result.setdefault("capture_started_at", []).append(meta["started_at"])
    result.setdefault("capture_finished_at", []).append(meta["finished_at"])
    return payload


def _captures(capture_paths, endpoint):
    for path in capture_paths:
        meta = json.loads(Path(path).read_text())
        if (meta.get("phase", {}).get("kind") == "listing"
                and urlsplit(str(meta.get("url", ""))).path == urlsplit(endpoint).path):
            yield path, meta


class _JobLinks(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "a" and "job-link" in attrs.get("class", "").split():
            self.links.append(attrs.get("href", ""))


def _pageup(source, jobs, capture_paths, result):
    endpoint = source.extra["filter_url"]
    require(url_signature(endpoint)[:3] == ("https", "jobs.unicef.org", "/en-us/filter/"),
            "Unsupported UNICEF census endpoint")
    size = _count(source.extra.get("page_size", 20), "PageUp page size")
    require(size > 0, "PageUp page size must be positive")
    params = dict(parse_qsl(urlsplit(endpoint).query, keep_blank_values=True))
    params.update(dict(source.extra.get("query") or {}))
    params.setdefault("search-keyword", "")
    found, totals, pages, terminal = {}, set(), 0, False
    for path, meta in _captures(capture_paths, endpoint):
        require(not terminal, "Unexpected PageUp page after terminal inventory")
        page_no = pages + 1
        parts = urlsplit(endpoint)
        query = {**params, "page": page_no, "page-items": size}
        expected = urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), ""))
        payload = _page(path, meta, expected, result)
        require(_count(payload.get("page"), "PageUp returned page") == page_no
                and _count(payload.get("pageitems"), "PageUp returned page size") == size,
                "PageUp page is skipped, duplicated, or resized")
        total = _count(payload.get("count"), "PageUp total")
        totals.add(total)
        require(isinstance(payload.get("results"), str), "PageUp lacks listing HTML")
        parser = _JobLinks()
        parser.feed(payload["results"])
        require(len(parser.links) <= size, "PageUp returned too many page rows")
        for href in parser.links:
            url = urljoin(source.base_url, href)
            parts = urlsplit(url)
            match = re.fullmatch(r"/en-us/job/(\d+)(?:/[^?#]*)?", parts.path)
            require(parts.scheme == "https" and parts.netloc == "jobs.unicef.org" and match,
                    "PageUp job URL is outside reviewed vacancy route")
            identity = match[1]
            require(identity not in found, "Duplicate PageUp job in census")
            found[identity] = urlunsplit((parts.scheme, parts.netloc, quote(parts.path, safe="/%"),
                                         quote(parts.query, safe="=&%:+,;/?@"), parts.fragment))
        pages += 1
        terminal = page_no * size >= total
        require(terminal or len(parser.links) == size, "Nonterminal PageUp page is truncated")
    actual = jobs_by_id(source, jobs)
    require(pages and terminal and len(totals) == 1 and len(found) == next(iter(totals)),
            "PageUp census lacks terminal page or consistent advertised total")
    require(set(actual) == set(found), "Parsed PageUp IDs differ from captured census")
    for identity, job in actual.items():
        require(url_signature(job.raw.get("_pageup_detail_url")) == url_signature(found[identity]),
                "PageUp dispatch URL differs from captured job link")
    result.update(complete=True, reported_total=len(found), page_count=pages, verified_zero=not found)


def _language_url(url, locale):
    parts = urlsplit(url)
    query = [(key, value) for key, value in parse_qsl(parts.query, keep_blank_values=True) if key != "lang"]
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode([*query, ("lang", locale)]), ""))


def _taleo(source, jobs, capture_paths, result):
    endpoint = source.extra["search_api_url"]
    require(url_signature(endpoint)[:3] == ("https", "jobs.fao.org", "/careersection/rest/jobboard/searchjobs")
            and source.extra.get("enumerate_job_locales") is True, "Unsupported FAO census scope")
    template = source.extra.get("search_payload")
    require(isinstance(template, dict) and type(template.get("pageNo")) is int,
            "FAO census requires its exact configured search payload")
    languages, advertised, union = {}, set(), set()
    for path, meta in _captures(capture_paths, endpoint):
        pairs = dict(parse_qsl(urlsplit(meta.get("url", "")).query, keep_blank_values=True))
        locale = pairs.get("lang", "")
        require(re.fullmatch(r"[a-z]{2,3}(?:_[A-Z]{2})?", locale), "Malformed FAO posting locale")
        state = languages.setdefault(locale, {"ids": set(), "totals": set(), "pages": 0, "terminal": False})
        require(not state["terminal"], "FAO page follows terminal locale inventory")
        page_no = state["pages"] + 1
        expected = deepcopy(template)
        expected["pageNo"] = page_no
        payload = _page(path, meta, _language_url(endpoint, locale), result, expected)
        require(payload.get("careerSectionUnAvailable") in (None, False), "FAO career section unavailable")
        paging = payload.get("pagingData", {})
        require(isinstance(paging, dict), "FAO paging metadata missing")
        total = _count(paging.get("totalCount"), "FAO total")
        size = _count(paging.get("pageSize"), "FAO page size")
        require(size > 0 and _count(paging.get("currentPageNo"), "FAO returned page") == page_no,
                "FAO returned page is skipped, repeated, or resized")
        if "size" in state:
            require(state["size"] == size, "FAO page size changed within locale")
        state["size"] = size
        state["totals"].add(total)
        rows = payload.get("requisitionList")
        require(isinstance(rows, list) and len(rows) <= size, "FAO requisition rows missing/truncated")
        for row in rows:
            require(isinstance(row, dict), "Malformed FAO public row")
            identity = identifier(row.get("contestNo"))
            require(identity not in state["ids"], "Duplicate FAO requisition in locale census")
            state["ids"].add(identity)
        state["pages"] += 1
        state["terminal"] = page_no * size >= total
        require(state["terminal"] or len(rows) == size, "Nonterminal FAO page is truncated")
        facets = payload.get("facetResults")
        require(isinstance(facets, list), "FAO language facets missing")
        require(all(isinstance(facet, dict) for facet in facets), "Malformed FAO facet")
        language_facets = [facet for facet in facets if facet.get("id") == "JOB_LOCALE"]
        require(len(language_facets) == 1, "FAO JOB_LOCALE census facet missing or ambiguous")
        for facet in language_facets:
            values = facet.get("facetValueResults")
            require(isinstance(values, list) and bool(values), "FAO language facet values missing")
            for value in values:
                language = value.get("id", "")
                require(isinstance(language, str) and re.fullmatch(r"[a-z]{2,3}(?:_[A-Z]{2})?", language),
                        "Unsupported advertised FAO locale")
                advertised.add(language)
    initial = dict(parse_qsl(urlsplit(endpoint).query)).get("lang", "en")
    require(languages and set(languages) == advertised | {initial}, "FAO advertised language inventory incomplete")
    for locale, state in languages.items():
        require(state["terminal"] and len(state["totals"]) == 1
                and len(state["ids"]) == next(iter(state["totals"])),
                "FAO locale census lacks a consistent total and terminal page")
        union.update(state["ids"])
    actual = jobs_by_id(source, jobs)
    require(set(actual) == union, "FAO parsed union differs from captured locale censuses")
    for identity, job in actual.items():
        available = sorted(locale for locale, state in languages.items() if identity in state["ids"])
        require(sorted(job.raw.get("_taleo_available_locales", [])) == available,
                "FAO parsed locale membership differs from captured census")
    result.update(complete=True, reported_total=len(union), page_count=sum(s["pages"] for s in languages.values()),
                  locale_counts={locale: len(state["ids"]) for locale, state in languages.items()},
                  locale_totals_must_not_be_summed=True, verified_zero=not union)


def verify_vacancy_listing(source, jobs, capture_paths):
    result = {"complete": False, "method": "unicef_pageup_v1" if source.id == "unicef_pageup" else "fao_taleo_locales_v1",
              "observed_count": len(jobs), "scope": "configured endpoint, public filters and advertised posting locales",
              "scope_signature": _scope(source), "reasons": [], "capture_paths": []}
    try:
        (_pageup if source.id == "unicef_pageup" else _taleo)(source, jobs, capture_paths, result)
        starts, finishes = result.pop("capture_started_at"), result.pop("capture_finished_at")
        result.update(started_at=min(starts), finished_at=max(finishes))
    except (ValueError, TypeError, KeyError, OSError, EOFError) as exc:
        result["complete"] = False
        result["reasons"].append(str(exc))
        result.pop("capture_started_at", None)
        result.pop("capture_finished_at", None)
    return result
