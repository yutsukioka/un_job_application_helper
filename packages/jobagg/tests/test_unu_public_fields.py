from copy import deepcopy
from dataclasses import asdict
from datetime import UTC, datetime
import gzip
import json
from pathlib import Path

import pytest

from jobagg.adapters.base import AdapterContext
from jobagg.adapters.static_html import StaticHTMLAdapter, parse_detail_page
from jobagg.adapters.unu_public import MARKER, public_fields
from jobagg.db import JobDatabase
from jobagg.models import JobRecord, OrganizationSource

F = Path(__file__).parent / 'fixtures/unu'
SAMPLES = json.loads((F/'capture_provenance_20260913.json').read_text())
SOURCE = OrganizationSource('unu_recruitee','UNU','unu_recruitee','https://careers.unu.edu/')
CLOSES = {
    'human-resources-associate-gs6-1':'2026-09-17T15:59:00+00:00',
    'administrative-officer-noa':'2026-09-20T14:59:00+00:00',
    'geoc2026-consultant-ctc5':'2026-09-16T14:59:00+00:00',
    'executive-development-coordinator-psa':'2026-09-16T20:59:00+00:00',
    'remote-intern-neuroscience-learning-and-ai-in-higher-edu':'2026-09-18T15:59:00+00:00',
    'adjunct-professorresearcher-on-the-resource-nexus-roster':None,
}


def html(s):
    return gzip.decompress((F/s['filename']).read_bytes()).decode()


def row(job):
    result = asdict(job)
    result['raw_json'] = json.dumps(result.pop('raw'),ensure_ascii=False)
    for key in ('posted_at','closes_at','first_seen_at','last_seen_at'):
        if result[key] is not None:
            result[key] = result[key].isoformat()
    return result


def listing(s):
    values = deepcopy(s['listing'])
    for key in ('posted_at','closes_at','first_seen_at','last_seen_at'):
        if values.get(key):
            values[key] = datetime.fromisoformat(values[key])
    return JobRecord(**values)


@pytest.mark.parametrize('s',SAMPLES,ids=lambda s:s['external_id'])
def test_actual_six_public_fields_keep_full_body_and_two_list_refreshes(s):
    j = parse_detail_page(SOURCE,html(s),s['url'])
    assert j.external_id == s['external_id']
    assert j.posted_at is None
    assert (j.closes_at.isoformat() if j.closes_at else None) == CLOSES[j.external_id]
    assert j.department != 'United Nations University'
    assert JobDatabase._labelled_notice_bound_public_detail(j.source_id,j.raw,row(j))
    old = deepcopy(j)
    old.raw.pop(MARKER)
    old.department = 'United Nations University'
    old.posted_at = datetime(2026,9,4,tzinfo=UTC)
    old.raw['attachments'] = [{'url':'https://example.org/p11.docx','text':'unchanged'}]
    db = object.__new__(JobDatabase)
    db._merge_existing_detail_fields(j,row(old))
    assert j.posted_at is None and j.description == old.description
    assert j.raw['attachments'] == old.raw['attachments']
    for _ in range(2):
        newer = listing(s)
        db._merge_existing_detail_fields(newer,row(j))
        for key in ('title','description','department','location','employment_type','posted_at','closes_at','closes_at_local','closes_tz'):
            assert getattr(newer,key) == getattr(j,key)
        assert newer.raw[MARKER] == j.raw[MARKER]
        assert newer.raw['attachments'] == j.raw['attachments']
        j = newer


def test_actual_hr_public_literal_location_and_contract():
    s = next(s for s in SAMPLES if s['external_id']=='human-resources-associate-gs6-1')
    j = parse_detail_page(SOURCE,html(s),s['url'])
    assert j.location == 'Putrajaya, Wilayah Persekutuan Putrajaya, Malaysia'
    assert j.department == 'Human Resources'
    assert j.employment_type == 'full-time, fixed-term appointment'
    assert j.raw[MARKER]['publisher_date_posted_claim'] == '2026-09-04'
    assert j.raw[MARKER]['publisher_valid_through_claim'] == '2026-09-17'
    assert j.closes_tz == 'Asia/Kuala_Lumpur'


@pytest.mark.parametrize('field,value',[('source_url','https://example.org/job'),('apply_url','https://example.org/job'),
    ('external_id','other'),('department','United Nations University'),('location','16, MY'),
    ('posted_at','2026-09-04T00:00:00+00:00'),('closes_at','2026-09-17T00:00:00+00:00')])
def test_public_metadata_proof_rejects_canonical_tampering(field,value):
    s = SAMPLES[0]
    j = parse_detail_page(SOURCE,html(s),s['url'])
    r = row(j)
    r[field] = value
    assert not JobDatabase._labelled_notice_bound_public_detail(j.source_id,j.raw,r)


@pytest.mark.parametrize('mutation',['body','marker','canonical','numeric_identifier','raw_identifier'])
def test_source_binding_cannot_follow_different_identity(mutation):
    s = SAMPLES[0]
    j = parse_detail_page(SOURCE,html(s),s['url'])
    if mutation=='body':
        j.description += 'Inserted text'
    elif mutation=='marker':
        j.raw[MARKER]['public_department'] = 'Different department'
    elif mutation=='canonical':
        j.raw['detail_html'] = j.raw['detail_html'].replace('rel="canonical" href="'+s['url']+'"','rel="canonical" href="https://careers.unu.edu/o/other"')
    elif mutation=='numeric_identifier':
        j.raw[MARKER]['publisher_identifier']['value'] = 99999
    else:
        j.raw['identifier']['value'] = 99999
    try:
        bound = JobDatabase._labelled_notice_bound_public_detail(j.source_id,j.raw,row(j))
    except ValueError:
        bound = False
    assert not bound


def test_open_roster_and_unknown_fields_do_not_revive_from_listing_in_fetch():
    s = next(s for s in SAMPLES if s['external_id'].startswith('adjunct-'))
    class Response:
        text = html(s)
        headers = {'Content-Type':'text/html'}
        content = text.encode()
    class HTTP:
        def get(self,url):
            assert url == s['url']
            return Response()
    a = StaticHTMLAdapter(AdapterContext(SOURCE,HTTP()))
    item = {**s['listing']['raw'],'posted_at':'2030-01-01','closes_at':'2030-12-31','location':'Wrong','employment_type':'Wrong'}
    j = a.fetch_detail_for_listing_item(item)
    assert j.posted_at is None and j.closes_at is None and j.closes_at_local is None
    assert j.location == 'Dresden, Sachsen, Germany'
    assert j.employment_type == 'Academic Affiliation Agreement and/or Consultant Contract (CTC)'


def test_unknown_clock_is_retained_as_unresolved_and_cannot_assume_jst():
    s = next(s for s in SAMPLES if s['external_id']=='geoc2026-consultant-ctc5')
    changed = html(s).replace('(23:59 JST)','(23:59 unknown zone)')
    r = public_fields(changed,s['url'])
    assert r['deadline']['closes_at'] is None
    assert r['deadline']['closes_at_local'] == '2026-09-16'
    assert r['deadline']['kind'] == 'unparsed_public_clock_or_timezone'


def test_duplicate_department_fails_instead_of_guessing():
    s = SAMPLES[0]
    changed = html(s).replace('</body>','<li data-cy="department-name">Other</li></body>')
    with pytest.raises(ValueError,match='department'):
        public_fields(changed,s['url'])


def test_detail_unknown_metadata_cannot_revive_from_listing_values():
    import re
    s = next(s for s in SAMPLES if s['external_id'].startswith('adjunct-'))
    text = html(s)
    text,count = re.subn(r'<li data-testid="styled-location-list-item"[^>]*>.*?</li>','',text,flags=re.S)
    assert count == 1
    text = text.replace('The successful candidate will be retained under an Academic Affiliation Agreement and/or&nbsp;Consultant Contract (CTC).','No current contract is specified.')
    class Response:
        headers = {'Content-Type':'text/html'}
        content = text.encode()
    response = Response()
    response.text = text
    class HTTP:
        def get(self,url):
            assert url == s['url']
            return response
    a = StaticHTMLAdapter(AdapterContext(SOURCE,HTTP()))
    item = {**s['listing']['raw'],'posted_at':'2030-01-01','closes_at':'2030-12-31','location':'Wrong','employment_type':'Wrong'}
    job = a.fetch_detail_for_listing_item(item)
    assert job.location is None and job.employment_type is None
    assert job.posted_at is None and job.closes_at is None
