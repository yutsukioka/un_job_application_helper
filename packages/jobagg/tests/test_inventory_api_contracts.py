"""Public API paging evidence, independent of adapter success diagnostics."""
from copy import deepcopy
import gzip
import hashlib
import json

import pytest

from jobagg.adapters.base import AdapterContext
from jobagg.adapters.oracle_hcm import OracleHCMAdapter
from jobagg.adapters.smartrecruiters import SmartRecruitersAdapter
from jobagg.models import OrganizationSource
from jobagg.pipelines.inventory_checks import verify_listing


def fixture(tmp_path, family, *, total=3, page_size=2, rows=None):
    oracle = family == 'oracle_hcm'
    source = OrganizationSource('test', 'Test', family, 'https://example.org', extra={
        'site_number': 'CX_1', 'company': 'OECD', 'page_size': page_size, 'max_pages': 3,
    })
    adapter = (OracleHCMAdapter if oracle else SmartRecruitersAdapter)(AdapterContext(source, None))
    if rows is None:
        rows = ([{'Id': str(i), 'Title': 'Public role ' + str(i)} for i in range(total)] if oracle else
                [{'id': str(i), 'refNumber': 'REF-' + str(i), 'name': 'Public role ' + str(i)} for i in range(total)])
    endpoint = ('https://example.org/hcmRestApi/resources/latest/recruitingCEJobRequisitions' if oracle
                else 'https://api.smartrecruiters.com/v1/companies/OECD/postings')
    paths, jobs = [], []
    for offset in range(0, max(len(rows), 1), page_size):
        page_rows = rows[offset:offset + page_size]
        # Oracle outer count=1/hasMore=false measures the envelope, NOT vacancies.
        payload = ({'items': [{'SiteNumber': 'CX_1', 'Offset': offset, 'Limit': page_size,
                              'TotalJobsCount': total, 'requisitionList': page_rows}],
                    'count': 1, 'hasMore': False} if oracle else
                   {'offset': offset, 'limit': page_size, 'totalFound': total, 'content': page_rows})
        jobs.extend(adapter.parse_jobs(payload))
        path = tmp_path / f'{offset}.json'
        body = json.dumps(payload).encode()
        artifact = path.with_suffix('.gz')
        artifact.write_bytes(gzip.compress(body))
        url = adapter._page_url(endpoint, limit=page_size, offset=offset)
        path.write_text(json.dumps({'phase': {'kind': 'listing'}, 'method': 'GET', 'url': url,
                                   'response_url': url, 'status_code': 200, 'body_captured': True,
                                   'artifact': str(artifact), 'body_sha256': hashlib.sha256(body).hexdigest(),
                                   'body_bytes': len(body)}))
        paths.append(path)
    if not oracle:
        grouped = {}
        for job in jobs:
            if job.external_id not in grouped:
                grouped[job.external_id] = job
            else:
                first = grouped[job.external_id]
                first.raw.setdefault('_smartrecruiters_listing_variants', [deepcopy(first.raw)]).append(deepcopy(job.raw))
        jobs = list(grouped.values())
    return source, jobs, paths


def mutate_page(path, function):
    meta = json.loads(path.read_text())
    from pathlib import Path
    artifact = Path(meta['artifact'])
    body = json.loads(gzip.decompress(artifact.read_bytes()))
    function(body)
    encoded = json.dumps(body).encode()
    artifact.write_bytes(gzip.compress(encoded))
    meta.update(body_sha256=hashlib.sha256(encoded).hexdigest(), body_bytes=len(encoded))
    path.write_text(json.dumps(meta))


@pytest.mark.parametrize('family', ['oracle_hcm', 'smartrecruiters'])
def test_exact_pages_and_provider_totals_reconcile(family, tmp_path):
    result = verify_listing(*fixture(tmp_path, family))
    assert result['complete'] is True and result['reported_total'] == 3
    assert result['page_count'] == 2 and len(result['capture_paths']) == 2
    assert 'whole_job_complete' not in result


@pytest.mark.parametrize('family', ['oracle_hcm', 'smartrecruiters'])
def test_zero_requires_actual_empty_public_response(family, tmp_path):
    source, jobs, paths = fixture(tmp_path, family, total=0)
    assert verify_listing(source, jobs, paths)['verified_zero'] is True
    assert verify_listing(source, jobs, [])['complete'] is False


@pytest.mark.parametrize('family', ['oracle_hcm', 'smartrecruiters'])
@pytest.mark.parametrize('failure', ['cap', 'missing_page', 'duplicate_page', 'raw_drift', 'job_source',
                                    'missing_job', 'body_hash', 'method', 'http_status', 'wrong_site',
                                    'offset', 'missing_capture', 'duplicate_query', 'metadata_shape'])
def test_scope_integrity_and_truncated_censuses_fail(family, failure, tmp_path):
    source, jobs, paths = fixture(tmp_path, family)
    if failure == 'cap':
        source.extra['max_pages'] = 1
    elif failure == 'missing_page':
        paths.pop()
    elif failure == 'duplicate_page':
        paths[1] = paths[0]
    elif failure == 'raw_drift':
        jobs[0].raw['Title' if family == 'oracle_hcm' else 'name'] = 'Other role'
    elif failure == 'job_source':
        jobs[0].source_id = 'other'
    elif failure == 'missing_job':
        jobs.pop()
    else:
        meta = json.loads(paths[0].read_text())
        if failure == 'body_hash':
            meta['body_sha256'] = '0' * 64
        elif failure == 'method':
            meta['method'] = 'POST'
        elif failure == 'http_status':
            meta['status_code'] = 403
        elif failure == 'wrong_site':
            meta['response_url'] = meta['url'].replace('example.org', 'different.org').replace('/OECD/', '/Other/')
        elif failure == 'offset':
            meta['url'] += '&offset=99'
        elif failure == 'missing_capture':
            del meta['body_captured']
        elif failure == 'duplicate_query':
            meta['url'] += '&limit=2&limit=2'
        elif failure == 'metadata_shape':
            meta = []
        paths[0].write_text(json.dumps(meta))
    result = verify_listing(source, jobs, paths)
    assert result['complete'] is False and result['reasons']


@pytest.mark.parametrize('family', ['oracle_hcm', 'smartrecruiters'])
@pytest.mark.parametrize('failure', ['total_drift', 'offset_drift', 'missing_total', 'bool_total', 'duplicate_id'])
def test_rehashed_public_response_contract_drift_fails(family, failure, tmp_path):
    args = fixture(tmp_path, family)
    def change(body):
        search = body['items'][0] if family == 'oracle_hcm' else body
        total_key = 'TotalJobsCount' if family == 'oracle_hcm' else 'totalFound'
        if failure == 'total_drift':
            search[total_key] = 4
        elif failure == 'offset_drift':
            search['Offset' if family == 'oracle_hcm' else 'offset'] = 20
        elif failure == 'missing_total':
            del search[total_key]
        elif failure == 'bool_total':
            search[total_key] = True
        else:
            row = search['requisitionList' if family == 'oracle_hcm' else 'content'][0]
            row['Id' if family == 'oracle_hcm' else 'id'] = '0'
    mutate_page(args[2][-1], change)
    assert verify_listing(*args)['complete'] is False


def test_smartrecruiters_counts_each_public_language_posting(tmp_path):
    args = fixture(tmp_path, 'smartrecruiters', rows=[
        {'id': 'en-1', 'refNumber': 'R1', 'name': 'Officer', 'language': {'code': 'en'}},
        {'id': 'fr-1', 'refNumber': 'R1', 'name': 'Administrateur', 'language': {'code': 'fr'}},
        {'id': 'en-2', 'refNumber': 'R2', 'name': 'Director', 'language': {'code': 'en'}},
    ])
    result = verify_listing(*args)
    assert result['complete'] and result['reported_total'] == 3
    assert result['canonical_vacancy_count'] == 2 and result['observed_posting_count'] == 3
    args[1][0].raw['_smartrecruiters_listing_variants'].pop()
    assert not verify_listing(*args)['complete']


def test_oracle_custom_template_is_explicitly_unsupported(tmp_path):
    args = fixture(tmp_path, 'oracle_hcm')
    args[0].extra['list_url_template'] = 'https://example.org/other'
    result = verify_listing(*args)
    assert not result['complete'] and 'Custom Oracle' in result['reasons'][0]
