import gzip
from pathlib import Path
from jobagg.adapters.base import AdapterContext
from jobagg.adapters.static_html import StaticHTMLAdapter, parse_unssc_jobs
from jobagg.http import HttpResponse
from jobagg.models import OrganizationSource

FIXTURES = Path(__file__).parent / 'fixtures/unssc'


class CapturedHTTP:
    def get(self, url):
        body = gzip.decompress((FIXTURES / 'int_002_2026.pdf.gz').read_bytes())
        return HttpResponse(url, 200, {'Content-Type': 'application/pdf'}, body.decode('utf-8', 'replace'), body)


def test_unssc_pdf_detail_preserves_official_listing_title_and_identity():
    source = OrganizationSource('unssc_drupal_custom', 'UNSSC', 'static_html',
                                'https://www.unssc.org/about/employment-opportunities',
                                extra={'parser': 'unssc_drupal'})
    html = gzip.decompress((FIXTURES / 'listing_20260910.html.gz').read_bytes()).decode()
    listings = parse_unssc_jobs(source, html, source.base_url)
    assert len(listings) == 1
    listing = listings[0]
    assert listing.title == 'Intern, Peace and Security Hub'
    assert listing.raw['title'] == listing.title
    detail = StaticHTMLAdapter(AdapterContext(source, CapturedHTTP())).fetch_detail_for_listing_item(listing.raw)
    assert detail.identity_key() == listing.identity_key()
    assert detail.external_id == 'INT_002_2026'
    assert detail.title == listing.title
    assert 'Intern, Peace and Security' in detail.description
    assert 'expiration date thereof' in detail.description
    assert len(detail.description) > 6000
