"""Capture-based API censuses, independent of adapter pagination diagnostics.

Only the configured public endpoint/site/filter population is assessed. Counts
do not certify public descriptions, metadata interpretation or documents.
"""
from collections import defaultdict
import gzip
import hashlib
import json
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

ORACLE_EXPAND = (
    "requisitionList.workLocation,requisitionList.otherWorkLocations,"
    "requisitionList.secondaryLocations,flexFieldsFacet.values,"
    "requisitionList.requisitionFlexFields"
)
ORACLE_FACETS = (
    "LOCATIONS;WORK_LOCATIONS;WORKPLACE_TYPES;TITLES;CATEGORIES;ORGANIZATIONS;"
    "POSTING_DATES;FLEX_FIELDS"
)


def require(condition, message):
    if not condition:
        raise ValueError(message)


def url_signature(value):
    parts = urlsplit(str(value))
    require(parts.scheme == "https" and bool(parts.netloc) and not parts.fragment
            and parts.username is None and parts.password is None, "Invalid public API URL")
    pairs = parse_qsl(parts.query, keep_blank_values=True)
    require(len({key for key, _ in pairs}) == len(pairs), "Duplicate API query field")
    return parts.scheme, parts.netloc, parts.path, tuple(sorted(pairs))


def captured_page(path, meta, expected_url):
    require(meta.get("method") == "GET" and type(meta.get("status_code")) is int
            and meta["status_code"] == 200, "Census requires a successful GET capture")
    require(url_signature(meta.get("url")) == url_signature(expected_url)
            == url_signature(meta.get("response_url")), "Census URL/site/filter/offset differs")
    require(meta.get("body_captured") is True, "Census response body was not captured")
    body = gzip.decompress(Path(meta["artifact"]).read_bytes())
    require(hashlib.sha256(body).hexdigest() == meta.get("body_sha256"),
            "Captured census response hash differs")
    if "body_bytes" in meta:
        require(type(meta["body_bytes"]) is int and meta["body_bytes"] == len(body),
                "Captured census response size differs")
    payload = json.loads(body)
    require(isinstance(payload, dict), "Census response is not an object")
    return payload, {"path": str(path), "sha256": hashlib.sha256(Path(path).read_bytes()).hexdigest()}


def integer(value, name):
    require(type(value) is int and value >= 0, "Invalid census " + name)
    return value


def identifier(value):
    require(type(value) in (str, int) and str(value).strip() == str(value) and bool(str(value)),
            "Missing or malformed public identity")
    return str(value)


def jobs_by_id(source, jobs):
    result = {}
    for job in jobs:
        key = identifier(job.external_id)
        require(job.source_id == source.id and key not in result,
                "Duplicate or wrong-source parsed vacancy")
        require(isinstance(job.raw, dict), "Parsed vacancy lacks retained listing object")
        result[key] = job
    return result


def raw_contains(raw, captured):
    return isinstance(raw, dict) and all(key in raw and raw[key] == value for key, value in captured.items())


def verify_api_listing(source, jobs, capture_paths, family):
    method = "oracle_ce_v1" if family == "oracle_hcm" else "smartrecruiters_postings_v1"
    result = {"complete": False, "method": method, "observed_count": len(jobs),
              "scope": "configured source endpoint and filters", "reasons": [], "capture_paths": []}
    try:
        if family == "oracle_hcm":
            verify_oracle(source, jobs, capture_paths, result)
        else:
            verify_smartrecruiters(source, jobs, capture_paths, result)
    except (ValueError, TypeError, KeyError, OSError, EOFError) as exc:
        result["reasons"].append(str(exc))
    return result


def listing_captures(capture_paths, endpoint_path):
    for path in capture_paths:
        meta = json.loads(Path(path).read_text())
        require(isinstance(meta, dict) and isinstance(meta.get("phase"), dict),
                "Malformed capture metadata/phase")
        if meta["phase"].get("kind") != "listing":
            continue
        # Site-settings and other public bootstrap responses are not job pages.
        # A matching route on the wrong host must fail the URL check, not vanish.
        if urlsplit(str(meta.get("url", ""))).path == endpoint_path:
            yield path, meta


def verify_oracle(source, jobs, capture_paths, result):
    site = source.extra.get("site_number")
    require(isinstance(site, str) and bool(site), "Oracle census requires configured site number")
    require(not source.extra.get("list_url_template"), "Custom Oracle listing templates require a census contract")
    api_url = source.extra.get("api_url") or (
        source.base_url.rstrip("/") + "/hcmRestApi/resources/latest/recruitingCEJobRequisitions"
    )
    signature = url_signature(api_url)
    require(not signature[3] and signature[2].endswith("/recruitingCEJobRequisitions"),
            "Custom Oracle query/route requires a census contract")
    limit = integer(source.extra.get("page_size", 25), "page size")
    require(limit > 0, "Census page size must be positive")
    cap = integer(source.extra.get("max_pages", 25), "page cap")
    parsed = jobs_by_id(source, jobs)
    ids, totals, pages = [], set(), 0
    terminal = False
    for path, meta in listing_captures(capture_paths, signature[2]):
        require(not terminal and pages < cap, "Extra Oracle page after terminal/cap")
        offset = pages * limit
        finder = (f"findReqs;siteNumber={site},facetsList={ORACLE_FACETS},"
                  f"limit={limit},offset={offset},sortBy={source.extra.get('sort_by') or 'POSTING_DATES_DESC'}")
        expected = str(api_url) + "?" + urlencode({"onlyData": "true", "expand": ORACLE_EXPAND, "finder": finder})
        payload, ref = captured_page(path, meta, expected)
        items = payload.get("items")
        require(isinstance(items, list) and len(items) == 1 and isinstance(items[0], dict),
                "Oracle census requires one search-result envelope")
        search = items[0]
        require(search.get("SiteNumber") == site, "Oracle response site differs")
        require(integer(search.get("Offset"), "Oracle offset") == offset
                and integer(search.get("Limit"), "Oracle limit") == limit, "Oracle response page differs")
        total = integer(search.get("TotalJobsCount"), "TotalJobsCount")
        totals.add(total)
        rows = search.get("requisitionList")
        require(isinstance(rows, list) and len(rows) <= limit, "Invalid Oracle requisition page")
        for row in rows:
            require(isinstance(row, dict), "Malformed Oracle requisition")
            key = identifier(row.get("Id"))
            require(key not in ids and key in parsed, "Duplicate/unparsed Oracle vacancy identity")
            require(identifier(parsed[key].raw.get("Id")) == key and raw_contains(parsed[key].raw, row),
                    "Parsed Oracle listing differs from exact captured requisition")
            ids.append(key)
        pages += 1
        result["capture_paths"].append(ref)
        require(len(totals) == 1 and len(ids) <= total, "Oracle advertised total changed")
        terminal = len(ids) == total
        require(terminal or len(rows) == limit, "Oracle partial page before advertised total")
    require(pages > 0 and terminal and set(ids) == set(parsed),
            "Oracle capture truncated, missing vacancies, or missing terminal total")
    result.update(complete=True, reported_total=next(iter(totals)), page_count=pages,
                  verified_zero=not ids, identity_unit="vacancy_id",
                  scope=f"Configured Oracle CE site {site}; outer count/hasMore describe envelopes only")


def verify_smartrecruiters(source, jobs, capture_paths, result):
    company = source.extra.get("company")
    api_url = source.extra.get("api_url") or f"https://api.smartrecruiters.com/v1/companies/{company}/postings"
    signature = url_signature(api_url)
    require(isinstance(company, str) and bool(company)
            and signature[:3] == ("https", "api.smartrecruiters.com", f"/v1/companies/{company}/postings"),
            "SmartRecruiters census requires its configured public company endpoint")
    limit = integer(source.extra.get("page_size", 100), "page size")
    require(limit > 0, "Census page size must be positive")
    cap = integer(source.extra.get("max_pages", 10), "page cap")
    parsed = jobs_by_id(source, jobs)
    variants, posting_ids = defaultdict(dict), set()
    totals, pages, terminal = set(), 0, False
    for path, meta in listing_captures(capture_paths, signature[2]):
        require(not terminal and pages < cap, "Extra SmartRecruiters page after terminal/cap")
        offset = pages * limit
        query = dict(signature[3])
        query["limit"] = str(limit)
        if offset or "offset" in query:
            query["offset"] = str(offset)
        expected = urlunsplit((*signature[:3], urlencode(query), ""))
        payload, ref = captured_page(path, meta, expected)
        require(integer(payload.get("offset"), "SmartRecruiters offset") == offset
                and integer(payload.get("limit"), "SmartRecruiters limit") == limit,
                "SmartRecruiters response page differs")
        total = integer(payload.get("totalFound"), "totalFound")
        totals.add(total)
        rows = payload.get("content")
        require(isinstance(rows, list) and len(rows) <= limit, "Invalid SmartRecruiters posting page")
        for row in rows:
            require(isinstance(row, dict), "Malformed SmartRecruiters posting")
            posting = identifier(row.get("id"))
            vacancy = identifier(row.get("refNumber") or row.get("id"))
            require(posting not in posting_ids and vacancy in parsed,
                    "Duplicate or unparsed SmartRecruiters posting")
            posting_ids.add(posting)
            variants[vacancy][posting] = row
        pages += 1
        result["capture_paths"].append(ref)
        require(len(totals) == 1 and len(posting_ids) <= total, "SmartRecruiters advertised total changed")
        terminal = len(posting_ids) == total
        require(terminal or len(rows) == limit, "SmartRecruiters partial page before total")
    require(pages > 0 and terminal and set(variants) == set(parsed),
            "SmartRecruiters capture truncated or missing canonical vacancies")
    for vacancy, expected in variants.items():
        raw = parsed[vacancy].raw
        actual = raw.get("_smartrecruiters_listing_variants", [raw])
        require(isinstance(actual, list) and len(actual) == len(expected),
                "Missing public language/posting variants")
        seen = set()
        for row in actual:
            require(isinstance(row, dict), "Malformed retained listing variant")
            posting = identifier(row.get("id"))
            require(posting not in seen and posting in expected and raw_contains(row, expected[posting]),
                    "Parsed SmartRecruiters variant differs from captured posting")
            require(identifier(row.get("refNumber") or row.get("id")) == vacancy,
                    "Wrong canonical vacancy for public posting")
            seen.add(posting)
    result.update(complete=True, reported_total=next(iter(totals)), page_count=pages,
                  verified_zero=not posting_ids, identity_unit="public_posting_id",
                  observed_posting_count=len(posting_ids), canonical_vacancy_count=len(parsed),
                  scope=f"Configured SmartRecruiters company {company} and query filters; totalFound counts language postings")
