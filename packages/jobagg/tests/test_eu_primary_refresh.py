from copy import deepcopy
from dataclasses import asdict
from datetime import datetime
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from jobagg.adapters.eu_primary_public import render_public_notice
from jobagg.adapters.eu_primary_refresh import (
    PrimaryPDFReviewRequired, current_primary_url, refresh_reviewed_notice, reviewed_previous_item,
)
from jobagg.models import OrganizationSource

SOURCE = OrganizationSource('eu_careers_static', 'EU Careers', 'static_html', 'https://eu-careers.europa.eu')
FIXTURES = Path(__file__).parent / 'fixtures/eu_primary_public'


def sample(identity='ca202604'):
    data = json.loads((FIXTURES / (identity + '.json')).read_text())
    data.pop('provenance')
    binary = b'%PDF-1.7 synthetic bytes for an exact-content admission test'
    data['document_proof']['content_sha256'] = hashlib.sha256(binary).hexdigest()
    data['document_proof'].update(
        reviewed_document={'path': '/synthetic-test/document.json', 'sha256': 'a' * 64,
                           'retrieval_metadata': '/synthetic-test/retrieval.json', 'retrieval_metadata_sha256': 'b' * 64},
        visual_review={'all_pages_reviewed': True, 'content_sha256': data['document_proof']['content_sha256'],
                       'text_sha256': data['document_proof']['extracted_text_sha256'],
                       'pages_actually_viewed': list(range(1, len(data['page_units']) + 1)),
                       'rendered_page_sha256': {str(n): 'c' * 64 for n in range(1, len(data['page_units']) + 1)}},
        original_retrieval_started_at='2026-09-13T10:00:00+00:00',
        original_retrieval_finished_at='2026-09-13T10:00:01+00:00')
    job = render_public_notice(SOURCE, **data)
    row = {k: v.isoformat() if isinstance(v, datetime) else v for k, v in asdict(job).items()}
    wrapper = ('https://www.euipo.europa.eu/en/about-us/the-office/who-we-are/employer-of-choice/vacancies'
               if identity.startswith('ext-') else 'https://www.euda.europa.eu/calls/2026/ca202604_en')
    return job, row, wrapper, f'<a href="{job.apply_url}">Full vacancy notice</a>', binary


class Adapter:
    source = SOURCE

    def __init__(self, url, binary):
        self.url, self.binary, self.calls = url, binary, []

    def _eu_get_official(self, url):
        self.calls.append(url)
        return SimpleNamespace(url=self.url, content=self.binary)


@pytest.mark.parametrize('identity', ['ext-26-40-ad-9-cpd', 'ext-26-41-ad-9-boa', 'ca202604'])
def test_exact_current_bytes_reuse_all_reviewed_pages_without_retiming(identity):
    old, row, wrapper, html, binary = sample(identity)
    adapter = Adapter(old.apply_url, binary)
    current = refresh_reviewed_notice(adapter, {'_reviewed_primary_previous_record': row}, identity=identity,
        summary_url=old.source_url, summary_html='<main>New summary observation</main>', wrapper_url=wrapper, wrapper_html=html)
    assert adapter.calls == [old.apply_url]
    assert current.description == old.description
    assert current.raw['public_primary_document_proof'] == old.raw['public_primary_document_proof']
    assert current.raw['public_primary_page_units'] == old.raw['public_primary_page_units']
    assert current.raw['_eu_primary_refresh_observation']['extraction_reused_only_after_current_byte_equality'] is True
    assert current.raw['_eu_primary_refresh_observation']['whole_job_complete'] is False
    assert current.raw['_eu_primary_refresh_observation']['wrapper_text_metadata_reconciled'] is False
    assert 'attachment_verification' not in current.raw


@pytest.mark.parametrize('change', ['new_bytes', 'wrong_url', 'no_prior', 'wrong_job', 'wrong_proof', 'html_response'])
def test_no_automatic_admission_of_new_unreviewed_or_other_vacancy_bytes(change):
    old, row, wrapper, html, binary = sample()
    adapter = Adapter(old.apply_url, binary)
    item = {'_reviewed_primary_previous_record': deepcopy(row)}
    if change == 'new_bytes':
        adapter.binary += b' new page'
    elif change == 'wrong_url':
        adapter.url = old.apply_url.replace('ca.2026.04', 'ca.2026.05')
    elif change == 'no_prior':
        item.clear()
    elif change == 'wrong_job':
        item['_reviewed_primary_previous_record']['external_id'] = 'ca202605'
    elif change == 'wrong_proof':
        item['_reviewed_primary_previous_record']['raw']['public_primary_document_proof']['content_sha256'] = 'a' * 64
    else:
        adapter.binary = b'<html>Access denied</html>'
    with pytest.raises(PrimaryPDFReviewRequired):
        refresh_reviewed_notice(adapter, item, identity=old.external_id, summary_url=old.source_url,
            summary_html='', wrapper_url=wrapper, wrapper_html=html)
    assert len(adapter.calls) == 1


def test_wrapper_reference_selection_rejects_other_job_and_competing_versions():
    old, _, wrapper, html, _ = sample()
    other = html.replace('ca.2026.04', 'ca.2026.05')
    assert current_primary_url(other + html, wrapper, old.external_id) == old.apply_url
    with pytest.raises(PrimaryPDFReviewRequired):
        current_primary_url(other, wrapper, old.external_id)
    with pytest.raises(PrimaryPDFReviewRequired):
        current_primary_url(html + html.replace('.pdf', '-revised.pdf'), wrapper, old.external_id)
    with pytest.raises(ValueError):
        current_primary_url(html, wrapper.replace('https://', 'http://'), old.external_id)


def test_marker_only_body_without_full_page_review_cannot_be_admitted():
    old, _, wrapper, html, binary = sample()
    proof = deepcopy(old.raw['public_primary_document_proof'])
    proof.pop('visual_review')
    # Even re-rendering an otherwise coherent source marker cannot replace
    # the retained full-page review required for future automatic admission.
    job = render_public_notice(SOURCE, external_id=old.external_id, summary_url=old.source_url,
        primary_url=old.apply_url, page_units=old.raw['public_primary_page_units'],
        document_proof=proof, required_attachment_urls=old.raw['required_attachment_urls'])
    row = {k: v.isoformat() if isinstance(v, datetime) else v for k, v in asdict(job).items()}
    with pytest.raises(PrimaryPDFReviewRequired, match='full-page visual review'):
        refresh_reviewed_notice(Adapter(old.apply_url, binary), {'_reviewed_primary_previous_record': row},
            identity=old.external_id, summary_url=old.source_url, summary_html='', wrapper_url=wrapper, wrapper_html=html)


def test_pipeline_context_copies_only_a_valid_bound_old_record():
    old, row, _, _, _ = sample()
    class Database:
        def get_job(self, key):
            assert key == old.identity_key()
            return row
    item = reviewed_previous_item(Database(), old)
    assert item['_reviewed_primary_previous_record'] == row
    assert '_reviewed_primary_previous_record' not in old.raw
    item['_reviewed_primary_previous_record']['raw']['grade'] = 'tampered'
    assert row['raw']['grade'] != 'tampered'
    row['source_url'] = old.source_url + '?another'
    with pytest.raises(PrimaryPDFReviewRequired):
        reviewed_previous_item(Database(), old)


def test_static_adapter_routes_current_wrapper_and_pdf_through_review_guard(monkeypatch):
    import jobagg.adapters.static_html as module
    from jobagg.adapters.base import AdapterContext
    old, row, wrapper, html, binary = sample()
    monkeypatch.setattr(module, 'parse_detail_page', lambda *args: old)
    monkeypatch.setattr(module, '_eu_summary_metadata', lambda *args: {})
    monkeypatch.setattr(module, '_eu_vacancy_url', lambda *args: wrapper)
    adapter = module.StaticHTMLAdapter(AdapterContext(SOURCE, None))
    adapter.fetch_text = lambda url: '<main>Fresh summary</main>'
    calls = []
    def get(url):
        calls.append(url)
        return SimpleNamespace(url=url, text=html if url == wrapper else '', content=binary if url == old.apply_url else html.encode())
    adapter._eu_get_official = get
    result = adapter._fetch_eu_detail({'external_id': old.external_id, '_reviewed_primary_previous_record': row}, old.source_url)
    assert result.description == old.description and calls == [wrapper, old.apply_url]


def test_bare_host_redirect_cannot_fall_through_to_generic_success(monkeypatch):
    import jobagg.adapters.static_html as module
    from jobagg.adapters.base import AdapterContext
    old, row, wrapper, html, _ = sample()
    monkeypatch.setattr(module, 'parse_detail_page', lambda *args: old)
    monkeypatch.setattr(module, '_eu_summary_metadata', lambda *args: {})
    monkeypatch.setattr(module, '_eu_vacancy_url', lambda *args: wrapper)
    adapter = module.StaticHTMLAdapter(AdapterContext(SOURCE, None))
    adapter.fetch_text = lambda url: '<main>Fresh summary</main>'
    adapter._eu_get_official = lambda url: SimpleNamespace(url=url.replace('www.euda', 'euda'), text=html, content=html.encode())
    with pytest.raises(ValueError, match='wrapper changes'):
        adapter._fetch_eu_detail({'external_id': old.external_id, '_reviewed_primary_previous_record': row}, old.source_url)
