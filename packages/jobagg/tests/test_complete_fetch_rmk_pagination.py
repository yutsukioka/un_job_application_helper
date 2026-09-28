from types import SimpleNamespace

from jobagg.adapters.base import AdapterContext
from jobagg.adapters.successfactors_rmk import SuccessFactorsRMKAdapter
from jobagg.models import OrganizationSource


def tile(number):
    return f'<li class="job-tile"><a class="jobTitle-link" href="/job/Role/{number}/">Role {number}</a></li>'


def make_adapter(max_pages=5):
    source = OrganizationSource(
        id="icrc_successfactors",
        name="ICRC",
        ats_family="successfactors_rmk",
        base_url="https://careers.icrc.org",
        extra={"max_pages": max_pages},
    )
    return SuccessFactorsRMKAdapter(AdapterContext(source=source, http=SimpleNamespace()))


def first_page():
    return (
        "<span>Showing 1 to 2 of 3 Jobs</span>"
        + tile(1)
        + tile(2)
        + """<script>j2w.SearchResults.init({
    apiEndpoint: "tile-search-results/category/3807301", searchQuery: "",
    jobRecordsPerPage: parseInt("2"), jobRecordsFound: parseInt("3")});</script>"""
    )


def test_modern_tile_pagination_follows_official_continuation_shape():
    a = make_adapter()
    urls = []

    def fetch(url):
        urls.append(url)
        return first_page() if len(urls) == 1 else tile(3)

    a.fetch_text = fetch
    jobs = a._fetch_html_jobs("https://careers.icrc.org/go/All-Jobs/3807301/")
    assert {j.external_id for j in jobs} == {"1", "2", "3"}
    assert urls[-1] == "https://careers.icrc.org/tile-search-results/category/3807301/&startrow=2"
    assert a.run_diagnostics.pagination_complete is True
    assert a.run_diagnostics.total_reported_by_source == 3
    assert a.run_diagnostics.pages_fetched == 2


def test_modern_tile_page_cap_is_incomplete():
    a = make_adapter(max_pages=1)
    a.fetch_text = lambda _: first_page()
    assert len(a._fetch_html_jobs("https://careers.icrc.org/go/All-Jobs/3807301/")) == 2
    assert a.run_diagnostics.pagination_complete is False


def test_modern_tile_repeated_page_is_incomplete_not_success():
    a = make_adapter()
    a.fetch_text = lambda _: first_page()
    assert len(a._fetch_html_jobs("https://careers.icrc.org/go/All-Jobs/3807301/")) == 2
    assert a.run_diagnostics.pagination_complete is False
