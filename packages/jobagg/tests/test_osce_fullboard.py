import json
import pytest
from jobagg.adapters.osce_inventory import reconcile, parse_bundle
from jobagg.models import OrganizationSource


SCOPE = '<div class="jResultsContent" data-keywords="" data-location-ids="" data-keyword-string="All jobs" data-location-string="All locations"></div><input type="checkbox" aria-label="New Jobs">'


def page(number, total, *ids, session="123456", total_pages=2):
    return {
        "number": number,
        "url": f"https://vacancies.osce.org/jobs/search/{session}/",
        "scope": {"new_jobs": False, "unfiltered": True},
        "html": SCOPE
        + f'<div id="jPaginateCurrPage">{number}</div><div id="jPaginateNumPages">{total_pages}.0</div>'
        + f"<strong>{total}</strong> results"
        + "".join(
            f'<a class="job_link" href="https://vacancies.osce.org/jobs/role-{i}">Role {i}</a>'
            for i in ids
        ),
    }


def test_five_pages_reconcile_without_a_hardcoded_count_or_session():
    pages = [
        page(i + 1, 13, *range(i * 3 + 1, min(i * 3 + 4, 14)), session="987654", total_pages=5) for i in range(5)
    ]
    rows, total = reconcile(pages)
    assert total == 13 and len(rows) == 13
    source = OrganizationSource(
        "osce_custom_html", "OSCE", "static_html", "https://vacancies.osce.org/jobs/search/"
    )
    jobs, count, n = parse_bundle(
        source,
        '<script type="application/json" id="jobagg-osce-inventory">'
        + json.dumps(pages)
        + "</script>",
    )
    assert (
        count == 13 and n == 5 and {j.external_id for j in jobs} == {str(i) for i in range(1, 14)}
    )


@pytest.mark.parametrize(
    "pages",
    [
        [page(1, 3, 1, 2)],
        [page(1, 3, 1, 2), page(2, 3, 2)],
        [page(1, 3, 1, 2), page(3, 3, 3)],
        [page(1, 3, 1, 2), page(2, 4, 3, 4)],
        [page(1, 3, 1, 2), page(2, 3, 3, session="999")],
        [{**page(1, 2, 1, 2), "scope": {"new_jobs": True, "unfiltered": False}}],
        [{**page(1, 2, 1, 2), "url": "https://vacancies.osce.org/latest-jobs"}],
    ],
)
def test_partial_feed_or_inconsistent_walk_cannot_certify(pages):
    with pytest.raises(ValueError):
        reconcile(pages)


def test_real_browser_waits_for_changed_ids_and_uses_generated_session(tmp_path, monkeypatch):
    pytest.importorskip("playwright.sync_api")
    from test_browser_fetch import make_browser
    from jobagg.http_safe import SafeHTTPPolicy

    start = "https://vacancies.osce.org/jobs/search/"
    body = """<html><head><link rel="stylesheet" href="/styles/core.css"></head><body><main class="ready"><p>All jobs / All locations</p>
    <label><input id="new_jobs" type="checkbox" aria-label="New Jobs">New Jobs</label>
    <div class="jResultsContent" data-keywords="" data-location-ids="" data-keyword-string="All jobs" data-location-string="All locations"></div><div id="jPaginateCurrPage">1</div><div id="jPaginateNumPages">2.0</div><div class="number_of_results"><strong>3</strong> results</div>
    <div id="cards"><a class="job_link" href="https://vacancies.osce.org/jobs/role-1">One</a>
    <a class="job_link" href="https://vacancies.osce.org/jobs/role-2">Two</a></div>
    <button onclick="throw Error('unrelated numeric control')">2</button>
    <div id="jPaginationHldr"><button onclick="setTimeout(()=>{document.querySelector('#cards').innerHTML='<a class=job_link href=https://vacancies.osce.org/jobs/role-3>Three</a>';setTimeout(()=>document.querySelector('#jPaginateCurrPage').innerText='2',150)},100)">2</button></div>
    </main><script>history.replaceState(null,'','/jobs/search/847392/')</script></body></html>"""
    browser, client = make_browser(tmp_path, monkeypatch, {start: body})
    client.safe_policy = SafeHTTPPolicy({"vacancies.osce.org"}, resolver=lambda host: ["8.8.8.8"])
    browser.patterns = [__import__("re").compile(r"^https://vacancies\.osce\.org/jobs/search/$")]
    browser.contract["inventory"] = "osce_full_search_v1"
    browser.contract["load_stylesheets"] = False
    from jobagg.http import HttpResponse
    dispatched = []
    def response_for(url, **kwargs):
        dispatched.append(url)
        assert url == start
        return HttpResponse("https://vacancies.osce.org/jobs/search/847392/", 200,
                            {"Content-Type": "text/html"}, body, body.encode())
    browser.capture.original = response_for
    response = browser.render(start)
    source = OrganizationSource("osce_custom_html", "OSCE", "static_html", start)
    jobs, total, count = parse_bundle(source, response.text)
    assert dispatched == [start]
    assert total == 3 and count == 2 and [j.external_id for j in jobs] == ["1", "2", "3"]
    receipt = json.loads(browser.last_receipt.read_text())
    assert receipt["omitted_resources"] == [{
        "url": "https://vacancies.osce.org/styles/core.css",
        "type": "stylesheet", "reason": "contract_omits_stylesheets",
    }]


@pytest.mark.parametrize("pages", [
    [page(1, 2, 1, 2)],  # All IDs but provider advertises another page.
    [page(1, 3, 1, 2), page(2, 3, 3, total_pages=3)],
    [{**page(1, 1, 1, total_pages=1), "number": 1,
      "html": page(2, 1, 1, total_pages=2)["html"]}],
])
def test_page_counters_are_independent_census_evidence(pages):
    with pytest.raises(ValueError, match="page|pagination"):
        reconcile(pages)


@pytest.mark.parametrize("total_pages", [0, 1])
def test_explicit_empty_board(total_pages):
    assert reconcile([page(1, 0, total_pages=total_pages)]) == ([], 0)
