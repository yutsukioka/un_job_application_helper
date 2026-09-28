from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime
import gzip
import hashlib
import json
from urllib.parse import urlencode

import pytest

from jobagg.adapters.base import AdapterContext
from jobagg.adapters.pageup import PageUpAdapter
from jobagg.adapters.taleo import TaleoAdapter
from jobagg.models import OrganizationSource
from jobagg.pipelines.inventory_vacancy_contracts import verify_vacancy_listing


def capture(tmp, number, url, payload, request=None):
    body = json.dumps(payload).encode()
    path = tmp / f"{number:05}.json"
    blob = path.with_suffix(".gz"); blob.write_bytes(gzip.compress(body))
    now = datetime.now(UTC).isoformat()
    meta = {"state": "response_captured", "status_code": 200, "method": "POST", "body_captured": True,
            "phase": {"kind": "listing"}, "url": url, "response_url": url, "artifact": str(blob),
            "body_sha256": hashlib.sha256(body).hexdigest(), "started_at": now, "finished_at": now}
    if request is not None:
        meta["request_body_sha256"] = hashlib.sha256(json.dumps(request, separators=(",", ":")).encode()).hexdigest()
    path.write_text(json.dumps(meta))
    return path


def pageup_case(tmp, *, total=2):
    source = OrganizationSource(id="unicef_pageup", name="UNICEF", ats_family="pageup",
        base_url="https://jobs.unicef.org/en-us/listing/",
        extra={"filter_url": "https://jobs.unicef.org/en-us/filter/", "page_size": 1, "query": {"search-keyword": ""}})
    adapter = PageUpAdapter(AdapterContext(source, object()))
    jobs, paths = [], []
    for page in range(1, total + 1):
        html = f'<div class="list-view--item"><a class="job-link" href="/en-us/job/{page}/révision">Role</a></div>'
        jobs += adapter.parse_listing_html(html)
        url = source.extra["filter_url"] + "?" + urlencode({"search-keyword": "", "page": page, "page-items": 1})
        paths.append(capture(tmp, page, url, {"results": html, "page": page, "pageitems": 1, "count": total}))
    return source, jobs, paths


def test_pageup_count_pages_identity_and_unicode_url_are_independently_bound(tmp_path):
    source, jobs, paths = pageup_case(tmp_path)
    result = verify_vacancy_listing(source, jobs, paths)
    assert result["complete"] and result["reported_total"] == 2 and result["page_count"] == 2
    assert result["started_at"] and result["scope_signature"]


@pytest.mark.parametrize("mutation", ["missing_page", "duplicate_page", "wrong_scope", "changed_count", "changed_job", "changed_hash"])
def test_pageup_incomplete_inventory_never_proves_absence(tmp_path, mutation):
    source, jobs, paths = pageup_case(tmp_path)
    if mutation == "missing_page": paths.pop()
    if mutation == "duplicate_page": paths[1] = paths[0]
    if mutation == "wrong_scope": source.extra["query"]["search-keyword"] = "different"
    if mutation == "changed_job": jobs[0].external_id = "other"
    if mutation == "changed_hash":
        meta = json.loads(paths[0].read_text()); meta["body_sha256"] = "bad"; paths[0].write_text(json.dumps(meta))
    if mutation == "changed_count":
        meta = json.loads(paths[1].read_text()); payload = json.loads(gzip.decompress(paths[1].with_suffix('.gz').read_bytes())); payload["count"] = 3
        paths[1] = capture(tmp_path, 2, meta["url"], payload)
    assert not verify_vacancy_listing(source, jobs, paths)["complete"]


def taleo_case(tmp):
    source = OrganizationSource(id="fao_taleo", name="FAO", ats_family="taleo", base_url="https://jobs.fao.org/careersection/fao_external/jobsearch.ftl",
        extra={"search_api_url": "https://jobs.fao.org/careersection/rest/jobboard/searchjobs?lang=en&portal=1",
               "search_payload": {"pageNo": 1, "fieldData": {"fields": {}, "valid": True}},
               "enumerate_job_locales": True})
    adapter = TaleoAdapter(AdapterContext(source, object()))
    paths, by_id = [], {}
    for number, (locale, ids) in enumerate([("en", ["1", "2"]), ("es", ["2", "3"])], 1):
        rows = [{"contestNo": identity, "title": "Role"} for identity in ids]
        for row in rows:
            identity = row["contestNo"]
            if identity not in by_id:
                job = adapter.parse_listing_item(row); adapter._bind_listing_locale(job, locale); by_id[identity] = job
            else:
                by_id[identity].raw["_taleo_available_locales"].append(locale)
        url = "https://jobs.fao.org/careersection/rest/jobboard/searchjobs?portal=1&lang=" + locale
        payload = {"pagingData": {"currentPageNo": 1, "pageSize": 25, "totalCount": len(rows)},
                   "requisitionList": rows, "facetResults": [{"id": "JOB_LOCALE", "facetValueResults": [{"id": "en"}, {"id": "es"}]}]}
        paths.append(capture(tmp, number, url, payload, source.extra["search_payload"]))
    return source, list(by_id.values()), paths


def test_taleo_multilingual_union_counts_jobs_once_and_requires_every_locale(tmp_path):
    source, jobs, paths = taleo_case(tmp_path)
    result = verify_vacancy_listing(source, jobs, paths)
    assert result["complete"] and result["reported_total"] == 3
    assert result["locale_counts"] == {"en": 2, "es": 2}
    assert result["locale_totals_must_not_be_summed"]


@pytest.mark.parametrize("mutation", ["missing_locale", "wrong_request", "wrong_locale_membership", "changed_count", "wrong_page", "duplicate_id", "unavailable_section", "missing_language_facet", "empty_language_facet"])
def test_taleo_missing_or_inconsistent_census_never_proves_absence(tmp_path, mutation):
    source, jobs, paths = taleo_case(tmp_path)
    if mutation == "missing_locale": paths.pop()
    if mutation == "wrong_request": source.extra["search_payload"]["fieldData"]["fields"]["KEYWORD"] = "narrow"
    if mutation == "wrong_locale_membership": jobs[0].raw["_taleo_available_locales"] = ["es"]
    if mutation in {"changed_count", "wrong_page", "duplicate_id", "unavailable_section", "missing_language_facet", "empty_language_facet"}:
        meta = json.loads(paths[0].read_text()); payload = json.loads(gzip.decompress(paths[0].with_suffix('.gz').read_bytes()))
        if mutation == "changed_count": payload["pagingData"]["totalCount"] = 3
        if mutation == "wrong_page": payload["pagingData"]["currentPageNo"] = 2
        if mutation == "duplicate_id": payload["requisitionList"][1] = deepcopy(payload["requisitionList"][0])
        if mutation == "unavailable_section": payload["careerSectionUnAvailable"] = True
        if mutation == "missing_language_facet": payload["facetResults"] = []
        if mutation == "empty_language_facet": payload["facetResults"][0]["facetValueResults"] = []
        paths[0] = capture(tmp_path, 1, meta["url"], payload, source.extra["search_payload"])
    assert not verify_vacancy_listing(source, jobs, paths)["complete"]
