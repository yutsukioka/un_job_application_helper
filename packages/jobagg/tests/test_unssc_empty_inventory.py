from pathlib import Path
import pytest
from jobagg.adapters.base import AdapterContext
from jobagg.adapters.static_html import StaticHTMLAdapter
from jobagg.http import HttpResponse
from jobagg.models import OrganizationSource

BODY = (Path(__file__).parent / "fixtures/unssc/empty_listing_20260913.html").read_text()
MARKER = "There are no vacancies at present, please visit this page regularly for updates."


def parse(body):
    class HTTP:
        def get(self, url):
            return HttpResponse(url, 200, {}, body, body.encode())
    source = OrganizationSource("unssc_drupal_custom", "UNSSC", "static_html",
                               "https://www.unssc.org/about/employment-opportunities",
                               extra={"parser": "unssc_drupal"})
    adapter = StaticHTMLAdapter(AdapterContext(source, HTTP()))
    return adapter.fetch_jobs(), adapter.run_diagnostics


def test_actual_public_employment_view_is_verified_empty():
    jobs, diagnostics = parse(BODY)
    assert jobs == []
    assert diagnostics.pagination_complete is True
    assert diagnostics.total_reported_by_source == 0
    assert diagnostics.zero_fetched_evidence["matched_text"] == MARKER


@pytest.mark.parametrize("body", [
    BODY.replace(MARKER, "Loading vacancies..."),
    BODY.replace("view-employment", "view-news"),
    BODY.replace(MARKER, "") + "<footer>" + MARKER + "</footer>",
    BODY.replace("</main>", '<table><tr><td class="views-field-field-vacancy-code-1">XYZ</td></tr></table></main>'),
    "<h1>Access denied</h1>",
])
def test_unknown_or_partial_view_is_not_certified_empty(body):
    jobs, diagnostics = parse(body)
    assert jobs == []
    assert diagnostics.empty_reason != "verified_structural_empty"
    assert diagnostics.total_reported_by_source != 0
