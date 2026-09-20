import gzip
from pathlib import Path
from jobagg.adapters.base import AdapterContext
from jobagg.adapters.static_html import StaticHTMLAdapter
from jobagg.http import HttpResponse
from jobagg.models import OrganizationSource


class HTTP:
    def __init__(self, body):
        self.body = body
        self.urls = []

    def get(self, url):
        self.urls.append(url)
        return HttpResponse(url, 200, {}, self.body)


def test_osce_latest_ten_cannot_claim_nineteen_or_full_search():
    body = gzip.decompress((Path(__file__).parent / 'fixtures/osce/latest_20260910.html.gz').read_bytes()).decode()
    http = HTTP(body)
    source = OrganizationSource('osce_custom_html', 'OSCE', 'static_html', 'https://vacancies.osce.org/latest-jobs',
                                extra={'parser': 'public_links', 'job_link_selector_hint': '/jobs/',
                                       'exclude_link_selector_hint': '/other-jobs-matching/', 'fetch_details': False})
    adapter = StaticHTMLAdapter(AdapterContext(source, http))
    jobs = adapter.fetch_jobs()
    assert len(jobs) == 10
    assert len({job.external_id for job in jobs}) == 10
    assert adapter.run_diagnostics.total_reported_by_source == 19
    assert adapter.run_diagnostics.pagination_complete is False
    assert adapter.run_diagnostics.health_status == 'issue'
    assert 'scope unverified' in adapter.run_diagnostics.empty_reason
    assert len(http.urls) == 1


def test_osce_preserves_hyphen_inside_the_official_role_title():
    from jobagg.adapters.static_html import parse_detail_page
    source = OrganizationSource('osce_custom_html', 'OSCE', 'static_html', 'https://vacancies.osce.org/latest-jobs')
    title = "International Consultant - Audit Experts – Roster Call (SSA's)"
    detail = parse_detail_page(source, f'<title>{title} - OSCE Careers</title><h1>{title}</h1><main>Full vacancy text</main>',
                               'https://vacancies.osce.org/jobs/international-consultant-audit-experts-%E2%80%93-roster-call-ssas-4941')
    assert detail.title == title
