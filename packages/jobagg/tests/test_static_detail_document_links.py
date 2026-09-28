"""A successful HTML parse must retain source hrefs for document discovery."""
from types import SimpleNamespace

import pytest

from jobagg.adapters.base import AdapterContext
from jobagg.adapters.static_html import StaticHTMLAdapter
from jobagg.http import HttpResponse
from jobagg.models import OrganizationSource
from jobagg.pipelines.document_tasks import discover_documents


@pytest.mark.parametrize('explicit_identity', [True, False])
@pytest.mark.parametrize('structured', [True, False])
def test_detail_html_and_document_href_survive_each_return(explicit_identity, structured):
    import json
    url = 'https://jobs.example.org/roles/123'
    html = '<html><body><main><h1>Research Analyst</h1><p>Full duties and requirements.</p><a href="/files/123.pdf">Terms of Reference</a></main>'
    if structured:
        html += '<script type="application/ld+json">' + json.dumps({
            '@type': 'JobPosting', 'identifier': '123', 'title': 'Research Analyst',
            'url': url, 'description': 'Full duties and requirements.'}) + '</script>'
    html += '</body></html>'
    source = OrganizationSource('example', 'Example', 'static_html', 'https://jobs.example.org')
    adapter = StaticHTMLAdapter(AdapterContext(source, SimpleNamespace(get=lambda _: HttpResponse(
        url, 200, {'Content-Type': 'text/html'}, html, html.encode()))))
    item = {'href': url, **({'external_id': '123'} if explicit_identity else {})}
    job = adapter.fetch_detail_for_listing_item(item)
    assert job.raw['detail_html'] == html
    candidates = discover_documents(job)
    assert any(c['url'] == 'https://jobs.example.org/files/123.pdf' for c in candidates)
    assert not job.raw.get('attachment_verification')


def test_pdf_extracted_text_is_not_labelled_html(monkeypatch):
    monkeypatch.setattr('jobagg.adapters.static_html._extract_pdf_text', lambda _: 'Research Analyst\nFull public terms.')
    url = 'https://jobs.example.org/files/123.pdf'
    source = OrganizationSource('example', 'Example', 'static_html', 'https://jobs.example.org')
    adapter = StaticHTMLAdapter(AdapterContext(source, SimpleNamespace(get=lambda _: HttpResponse(
        url, 200, {'Content-Type': 'application/pdf'}, '', b'%PDF-test'))))
    job = adapter.fetch_detail_for_listing_item({'href': url, 'external_id': '123'})
    assert 'detail_html' not in job.raw
    assert 'Full public terms.' in job.description
