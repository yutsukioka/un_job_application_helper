import gzip
import hashlib
import json
from pathlib import Path
from urllib.parse import urlencode
import pytest
from jobagg.models import OrganizationSource, JobRecord
from jobagg.pipelines.inventory_checks import verify_listing


def case(tmp_path, family):
    unv = family == 'unv'
    sid = 'unv_uvp' if unv else 'unops_avature'
    endpoint = 'https://example.org/Search'
    source = OrganizationSource(sid, 'Test', family, 'https://example.org', extra={
        'api_url' if unv else 'listing_url': endpoint, 'page_size': 2, 'max_pages': 4})
    jobs = [];paths=[]
    for page, ids in enumerate(([1,2],[3])):
        rows = [{'id': i, 'name': 'Job '+str(i)} for i in ids]
        if unv:
            body=json.dumps({'value':{'total':3,'result':rows}}).encode();url=endpoint
        else:
            body=('<div class="list-controls__text__legend" aria-label="3 results"></div>'+''.join(f'<article class="article--result"><a href="https://example.org/JobDetail/Job/{i}">Job</a></article>' for i in ids)).encode();url=endpoint+'?'+urlencode({'jobRecordsPerPage':2,'jobOffset':page*2})
        path=tmp_path/f'{page}.json';artifact=path.with_suffix('.gz');artifact.write_bytes(gzip.compress(body))
        meta={'phase':{'kind':'listing'},'state':'response_captured','method':'POST' if unv else 'GET','status_code':200,'url':url,'response_url':url,'body_captured':True,'artifact':str(artifact),'body_sha256':hashlib.sha256(body).hexdigest(),'started_at':'2026-09-18T00:00:00+00:00','finished_at':'2026-09-18T00:00:01+00:00'}
        if unv:meta['request_body_sha256']=hashlib.sha256(json.dumps({'take':2,'skip':page*2},separators=(',',':')).encode()).hexdigest()
        path.write_text(json.dumps(meta));paths.append(path)
        for row in rows:
            jobs.append(JobRecord(source_id=sid,org_id='Test',ats_family=family,external_id=str(row['id']),title=row['name'],apply_url=endpoint,raw=row if unv else {'_detail_url':f"https://example.org/JobDetail/Job/{row['id']}"}))
    return source,jobs,paths


@pytest.mark.parametrize('family',['unv','avature'])
def test_complete_paginated_capture_matches_parsed_population(tmp_path,family):
    source,jobs,paths=case(tmp_path,family)
    result=verify_listing(source,jobs,paths)
    assert result['complete'],result
    assert result['reported_total']==3 and result['pages']==2


@pytest.mark.parametrize('family',['unv','avature'])
@pytest.mark.parametrize('damage',['missing_page','wrong_offset','duplicate_page','changed_body','missing_job'])
def test_incomplete_or_tampered_inventory_is_not_certified(tmp_path,family,damage):
    source,jobs,paths=case(tmp_path,family)
    if damage=='missing_page':paths.pop()
    elif damage=='duplicate_page':paths[1]=paths[0]
    elif damage=='missing_job':jobs.pop()
    else:
        meta=json.loads(paths[1].read_text())
        if damage=='wrong_offset':
            if family=='unv':meta['request_body_sha256']='wrong'
            else:meta['url']=meta['url'].replace('jobOffset=2','jobOffset=4')
        else:meta['body_sha256']='wrong'
        paths[1].write_text(json.dumps(meta))
    assert verify_listing(source,jobs,paths)['complete'] is False
