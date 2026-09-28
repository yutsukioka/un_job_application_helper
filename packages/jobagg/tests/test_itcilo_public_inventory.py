from pathlib import Path

import pytest

from jobagg.adapters.base import AdapterContext
from jobagg.adapters.itcilo_public import parse_itcilo_board
from jobagg.adapters.static_html import StaticHTMLAdapter
from jobagg.http import JobAggHTTPClient
from jobagg.models import OrganizationSource

SOURCE = OrganizationSource("itcilo_custom_html", "ITCILO", "itcilo_custom_html",
                            "https://jobs.itcilo.org/", adapter="static_html",
                            extra={"parser": "public_links", "job_link_hints": ["/jobs/", "/job/"]})
FIXTURE = Path(__file__).parent / "fixtures/itcilo/current_listing_20260913.html"
EMPTY = """<h2>Vacancy Available</h2><p>There are no vacancies available at the moment.</p>
<h2>Internship available</h2><p>There are no internships available at the moment.</p>"""


def test_actual_public_vacancies_survive_empty_internships_and_stale_generic_hints():
    adapter = StaticHTMLAdapter(AdapterContext(source=SOURCE, http=JobAggHTTPClient()))
    adapter.fetch_text = lambda _: FIXTURE.read_text()
    jobs = adapter.fetch_jobs()
    assert [job.external_id for job in jobs] == ["207", "208", "209"]
    assert jobs[2].title == "Programme Assistant (Internal Vacancy)"
    assert jobs[0].raw["vacancy_number"] == "04/2026"
    assert jobs[0].raw["grade"] == "P 2"
    assert all(job.employment_type is None for job in jobs)
    assert jobs[2].raw["job_family"] == "Learning Innovation Programme (LIP)"
    assert jobs[2].apply_url == "https://jobs.itcilo.org/view_vacancy/209"
    assert jobs[2].closes_at_local == "Sept. 24, 2026"
    assert all(job.closes_at is None for job in jobs)
    assert adapter.run_diagnostics.empty_reason is None
    assert adapter.run_diagnostics.pagination_complete is True


def test_true_zero_requires_both_categories_explicitly_empty():
    jobs, evidence = parse_itcilo_board(SOURCE, EMPTY, SOURCE.base_url)
    assert jobs == []
    assert evidence["verified_empty"] is True
    assert evidence["explicit_empty_categories"] == {"vacancies": True, "internships": True}


@pytest.mark.parametrize("body", [
    EMPTY.replace("There are no vacancies available at the moment.", "Loading vacancies..."),
    EMPTY.replace("Vacancy Available", "A changed category"),
    '<h2>Internship available</h2><p>There are no internships available at the moment.</p>',
    EMPTY + '<a href="?page=2">Next</a>',
])
def test_incomplete_or_changed_sections_do_not_certify_zero(body):
    with pytest.raises(ValueError):
        parse_itcilo_board(SOURCE, body, SOURCE.base_url)


def test_unidentified_table_row_is_not_silently_dropped():
    body = FIXTURE.read_text().replace('href="view_vacancy/207"', 'href="changed-route/207"')
    with pytest.raises(ValueError, match="unidentified"):
        parse_itcilo_board(SOURCE, body, SOURCE.base_url)
