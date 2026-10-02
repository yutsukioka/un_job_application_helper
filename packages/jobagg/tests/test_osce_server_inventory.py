"""Full listing proofs are independent of server-page collector assertions."""

import copy
from datetime import datetime, timedelta, timezone
import gzip
import hashlib
import json
import re
from pathlib import Path

import pytest

from jobagg.adapters.osce_inventory import captured_scope, parse_bundle
from jobagg.osce_fragments import SERVER_NAVIGATION, page_url
from jobagg.pipelines.inventory_checks import verify_listing
from jobagg.pipelines.inventory_osce_server import SESSION_SCOPE, TRANSPORT
from jobagg.pipelines.sync_source import load_sources

FIXTURE = json.loads((Path(__file__).parent / "fixtures/osce/fragments/pages.json").read_text())
ENTRY = "https://vacancies.osce.org/jobs/search/"


def sha(data):
    return hashlib.sha256(data).hexdigest()


def encoded_bundle(pages):
    return '<script type="application/json" id="jobagg-osce-inventory">' + json.dumps(pages) + '</script>'


def minimal_html(total, pages=1):
    cards = ''.join(f'<a class="job_link" href="https://vacancies.osce.org/jobs/job-{i}">Job {i}</a>'
                    for i in range(1, total + 1))
    return (f'<html><body><div id="jPaginateCurrPage">1</div><div id="jPaginateNumPages">{pages}</div>'
            '<div class="jResultsContent" data-keyword-string="All jobs" data-location-string="All locations" '
            'data-keywords="" data-location-ids=""></div><input type="checkbox" aria-label="New Jobs">'
            f'<strong>{total}</strong> results{cards}</body></html>')


class ProofFixture:
    def __init__(self, root, html_pages=None):
        self.source = next(s for s in load_sources(Path(__file__).parents[1] / 'config/organizations.yaml')
                           if s.id == 'osce_custom_html')
        self.source.extra['browser_render']['navigation'] = SERVER_NAVIGATION
        self.root, self.pages, self.paths = root, [], []
        (root / 'http').mkdir()
        browser = root / 'browser-fixture'
        browser.mkdir()
        self.receipt_path = browser / 'receipt.json'
        self.rendered = browser / 'rendered.html'
        self.receipt = {'url': ENTRY, 'contract': copy.deepcopy(self.source.extra['browser_render']),
                        'transport': TRANSPORT, 'session_scope': SESSION_SCOPE,
                        'html_path': str(self.rendered)}
        self.session = FIXTURE['session']
        self.capture(ENTRY, b'', 307, redirect_url=page_url(self.session, 1))
        html_pages = FIXTURE['pages'] if html_pages is None else html_pages
        for number, html in enumerate(html_pages, 1):
            url = page_url(self.session, number)
            path, meta = self.capture(url, html.encode(), 200)
            from jobagg.adapters.osce_inventory import pagination_state
            import re
            total = int(re.search(r'>(\d+)</(?:strong|span)>\s*results', html)[1])
            self.pages.append({'number': number, 'url': url, 'request_url': url, 'capture_path': str(path),
                               'capture_sha256': sha(path.read_bytes()), 'response_sha256': meta['body_sha256'],
                               'html': html, 'scope': captured_scope(html), 'navigation': SERVER_NAVIGATION,
                               'advertised_pages': pagination_state(html)[1], 'reported_total': total})
        self.receipt['final_url'] = self.pages[-1]['request_url']
        self.jobs, _, _ = parse_bundle(self.source, encoded_bundle(self.pages))

    def capture(self, url, raw, status, **extra):
        number = len(self.paths) + 1
        path = self.root / 'http' / f'{number:05d}.json'
        artifact = path.with_suffix('.body.gz')
        artifact.write_bytes(gzip.compress(raw))
        started = datetime(2026, 10, 1, tzinfo=timezone.utc) + timedelta(seconds=number * 2)
        meta = {'number': number, 'url': url, 'response_url': url, 'request_url_sha256': sha(url.encode()),
                'status_code': status, 'state': 'response_captured', 'phase': {'kind': 'listing', 'job_id': None},
                'external_id': None, 'method': 'GET', 'transport': TRANSPORT,
                'source_binding': {'source_id': self.source.id, 'ats_family': self.source.ats_family},
                'body_captured': True, 'body_bytes': len(raw), 'body_sha256': sha(raw), 'artifact': str(artifact),
                'started_at': started.isoformat(), 'finished_at': (started + timedelta(seconds=1)).isoformat(),
                'transport_diagnostics': {'resource_type': 'Document', 'request_body_bytes': 0, **extra}}
        path.write_text(json.dumps(meta))
        self.paths.append(path)
        return path, meta

    def meta(self, index, patch):
        path = self.paths[index]
        meta = json.loads(path.read_bytes())
        patch(meta)
        path.write_text(json.dumps(meta))
        if index:
            self.pages[index - 1]['capture_sha256'] = sha(path.read_bytes())

    def raw(self, index, transform):
        page = self.pages[index - 1]
        raw = transform(page['html']).encode()
        self.paths[index].with_suffix('.body.gz').write_bytes(gzip.compress(raw))
        self.meta(index, lambda meta: meta.update(body_bytes=len(raw), body_sha256=sha(raw)))
        page.update(html=raw.decode(), response_sha256=sha(raw))

    def proof(self):
        bundle = encoded_bundle(self.pages).encode()
        self.rendered.write_bytes(bundle)
        self.receipt['html_sha256'] = sha(bundle)
        self.receipt_path.write_text(json.dumps(self.receipt))
        return verify_listing(self.source, self.jobs, self.paths)


def test_full_raw_server_walk_is_complete_and_records_interval(tmp_path):
    fixture = ProofFixture(tmp_path)
    proof = fixture.proof()
    assert proof['complete'], proof
    assert proof['reported_total'] == 45 and proof['page_count'] == 5
    assert len(proof['capture_paths']) == 7  # Receipt, fresh redirect, five raw pages.
    assert proof['started_at'] == '2026-10-01T00:00:02+00:00'
    assert proof['finished_at'] == '2026-10-01T00:00:13+00:00'


@pytest.mark.parametrize('total,pages', [(0, 0), (0, 1), (1, 1)])
def test_zero_and_single_page_boards(tmp_path, total, pages):
    fixture = ProofFixture(tmp_path, [minimal_html(total, pages)])
    proof = fixture.proof()
    assert proof['complete'], proof
    assert proof['reported_total'] == total and proof['verified_zero'] == (total == 0)


@pytest.mark.parametrize('change', [
    'missing_page', 'reordered_page', 'reused_capture', 'outside_capture', 'wrong_session', 'query',
    'wrong_navigation', 'html', 'response_hash', 'metadata_hash', 'advertised_pages', 'reported_total',
    'duplicate_id', 'filtered', 'changed_total', 'truncated', 'page_counter', 'wrong_job_title',
    'missing_redirect', 'extra_document', 'receipt_final', 'receipt_navigation', 'receipt_session',
    'receipt_transport',
])
def test_incomplete_or_unbound_inventory_never_claims_complete(tmp_path, change):
    f = ProofFixture(tmp_path)
    page = f.pages[1]
    if change == 'missing_page': f.pages.pop()
    elif change == 'reordered_page': f.pages[0], f.pages[1] = f.pages[1], f.pages[0]
    elif change == 'reused_capture': page['capture_path'] = f.pages[0]['capture_path']
    elif change == 'outside_capture': page['capture_path'] = str(tmp_path / 'other.json')
    elif change == 'wrong_session': page['url'] = page_url('999', 2)
    elif change == 'query': page['request_url'] += '?q=filter'
    elif change == 'wrong_navigation': page['navigation'] = 'osce_ui_navigation_v1'
    elif change == 'html': page['html'] += 'modified'
    elif change == 'response_hash': page['response_sha256'] = '0' * 64
    elif change == 'metadata_hash': page['capture_sha256'] = '0' * 64
    elif change == 'advertised_pages': page['advertised_pages'] += 1
    elif change == 'reported_total': page['reported_total'] += 1
    elif change == 'duplicate_id': f.raw(2, lambda _: f.pages[0]['html'].replace('jPaginateCurrPage">1', 'jPaginateCurrPage">2'))
    elif change == 'filtered': f.raw(2, lambda html: html.replace('All jobs', 'Some jobs'))
    elif change == 'changed_total': f.raw(2, lambda html: html.replace('>45</span>', '>46</span>').replace('>45</strong>', '>46</strong>'))
    elif change == 'truncated': f.raw(2, lambda html: html[:len(html) // 2])
    elif change == 'page_counter': f.raw(2, lambda html: re.sub(r'(id="jPaginateCurrPage"[^>]*>)2', r'\g<1>3', html))
    elif change == 'wrong_job_title': f.jobs[0].title += ' changed'
    elif change == 'missing_redirect': f.paths.pop(0)
    elif change == 'extra_document': f.capture(page['request_url'], page['html'].encode(), 200)
    elif change == 'receipt_final': f.receipt['final_url'] = f.pages[0]['url']
    elif change == 'receipt_navigation': f.receipt['contract']['navigation'] = 'osce_ui_navigation_v1'
    elif change == 'receipt_session': f.receipt['session_scope'] = 'reused browser'
    elif change == 'receipt_transport': f.receipt['transport'] = 'urllib'
    proof = f.proof()
    assert not proof['complete'], (change, proof)
    assert proof['reasons']


@pytest.mark.parametrize('patch', [
    {'status_code': 403}, {'method': 'POST'}, {'transport': 'urllib'}, {'source_binding': {}},
    {'source_binding': {'source_id': 'other', 'ats_family': 'osce_custom_html'}},
    {'phase': {'kind': 'detail'}}, {'phase': {'kind': 'listing', 'job_id': '123'}}, {'external_id': '123'},
    {'response_url': 'https://other.example'}, {'request_url_sha256': '0' * 64},
    {'body_captured': False}, {'body_bytes': 0}, {'body_sha256': '0' * 64},
    {'transport_diagnostics': {'resource_type': 'XHR', 'request_body_bytes': 0}},
    {'transport_diagnostics': {'resource_type': 'Document', 'request_body_bytes': 1}},
    {'request_body_sha256': '0' * 64}, {'state': 'failed'}, {'error_type': 'HTTPError'},
    {'failure_category': 'access_denied'}, {'started_at': 'not-a-time'},
    {'started_at': '2026-10-01T00:00:01'}, {'started_at': '2026-10-01T00:01:00+00:00'},
    {'started_at': '2026-10-01T00:00:00+00:00'},
])
def test_raw_capture_context_and_hashes_are_required(tmp_path, patch):
    fixture = ProofFixture(tmp_path)
    fixture.meta(2, lambda meta: meta.update(patch))
    proof = fixture.proof()
    assert not proof['complete'], (patch, proof)


def test_generated_session_without_observed_redirect_is_rejected(tmp_path):
    fixture = ProofFixture(tmp_path)
    fixture.meta(0, lambda meta: meta['transport_diagnostics'].update(redirect_url=page_url('999', 1)))
    assert not fixture.proof()['complete']


def test_raw_body_cannot_change_behind_saved_metadata(tmp_path):
    fixture = ProofFixture(tmp_path)
    fixture.paths[2].with_suffix('.body.gz').write_bytes(gzip.compress(b'changed'))
    assert not fixture.proof()['complete']
