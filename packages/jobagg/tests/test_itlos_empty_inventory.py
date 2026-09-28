from pathlib import Path

import pytest

from jobagg.adapters.base import AdapterContext
from jobagg.adapters.static_html import StaticHTMLAdapter
from jobagg.http import JobAggHTTPClient
from jobagg.pipelines.sync_source import load_sources


EMPTY_HTML = """
<h1>Employment Opportunities</h1>
<h2>Professional Level</h2><p>There are currently no vacancies.</p>
<h2>General Service Level</h2><p>There are currently no vacancies.</p>
<h2>How to apply</h2>
<a href="/fileadmin/itlos/documents/registry/P.11_Personal_History_Form_ITLOS_EN.docx">Personal History Form</a>
<a href="/fileadmin/itlos/documents/registry/JPO/Guidelines_JPO_Eng.pdf">JPO Guidelines</a>
"""


def adapter():
    registry = Path(__file__).resolve().parents[1] / "config/organizations.yaml"
    source = next(s for s in load_sources(registry) if s.id == "itlos_static_html")
    return StaticHTMLAdapter(AdapterContext(source=source, http=JobAggHTTPClient()))


def test_itlos_accepts_both_explicit_empty_categories_and_general_forms():
    subject = adapter()
    assert subject._parse_generic_links(EMPTY_HTML, subject.source.base_url) == []
    assert subject.run_diagnostics.pagination_complete is True
    assert subject.run_diagnostics.zero_fetched_evidence["observed_text_counts"] == {
        "There are currently no vacancies.": 2
    }


@pytest.mark.parametrize("body", [
    EMPTY_HTML.replace("There are currently no vacancies.", "Loading vacancies...", 1),
    EMPTY_HTML.replace("General Service Level", "Temporary Staff"),
    "<h1>Access denied</h1>",
])
def test_itlos_partial_or_changed_page_is_never_certified_empty(body):
    subject = adapter()
    with pytest.raises(RuntimeError, match="structural empty markers"):
        subject._parse_generic_links(body, subject.source.base_url)


def test_actual_vacancy_link_wins_over_empty_markers():
    subject = adapter()
    body = EMPTY_HTML + '<a href="/fileadmin/itlos/documents/registry/VA/VA_2026_001.pdf">Associate Press Officer</a>'
    jobs = subject._parse_generic_links(body, subject.source.base_url)
    assert len(jobs) == 1
    assert jobs[0].title == "Associate Press Officer"
    assert jobs[0].apply_url.endswith("/registry/VA/VA_2026_001.pdf")
    assert subject.run_diagnostics.empty_reason != "verified_structural_empty"
