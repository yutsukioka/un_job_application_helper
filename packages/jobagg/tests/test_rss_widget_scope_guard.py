from jobagg.adapters.base import AdapterContext
from jobagg.adapters.successfactors_rmk import SuccessFactorsRMKAdapter
from jobagg.http import HttpResponse
from jobagg.models import OrganizationSource

EMPTY='<rss><channel><item><title>No jobs currently available - Check out our other opportunities.</title><link>https://jobs.example/</link></item></channel></rss>'
JOB='<rss><channel><item><title>Programme Officer</title><link>https://jobs.example/job/123/</link><description>Role description</description></item></channel></rss>'


class HTTP:
    def __init__(self, body):
        self.body = body
    def get(self, url):
        return HttpResponse(url, 200, {}, self.body)


def adapter(body, widget):
    extra={'rss_url': 'https://jobs.example/feed'}
    if widget:
        extra.update(public_widget_root_url='https://official.example/widget', public_all_jobs_url='https://jobs.example/all')
    source=OrganizationSource('sample', 'Sample', 'successfactors_rmk', 'https://jobs.example', extra=extra)
    return SuccessFactorsRMKAdapter(AdapterContext(source, HTTP(body)))


def test_empty_legacy_rss_cannot_certify_observed_widget_scope():
    a=adapter(EMPTY, True)
    assert a.fetch_jobs() == []
    assert a.run_diagnostics.pagination_complete is False
    assert a.run_diagnostics.health_status == 'issue'
    assert a.run_diagnostics.zero_fetched_evidence['public_widget_scope_verified'] is False


def test_nonempty_legacy_rss_is_preserved_but_scope_stays_incomplete():
    a=adapter(JOB, True)
    assert len(a.fetch_jobs()) == 1
    assert a.run_diagnostics.pagination_complete is False
    assert a.run_diagnostics.empty_reason == 'rss_scope_unverified_public_widget'


def test_unrelated_verified_empty_rss_keeps_original_behavior():
    a=adapter(EMPTY, False)
    assert a.fetch_jobs() == []
    assert a.run_diagnostics.health_status == 'ok_empty'
