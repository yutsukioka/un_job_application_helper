from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest

from jobagg.adapters.eu_primary_public import render_public_notice
from jobagg.models import OrganizationSource

FIXTURES = Path(__file__).parent / 'fixtures/eu_primary_public'
SOURCE = OrganizationSource('eu_careers_static', 'EU Careers', 'static_html', 'https://eu-careers.europa.eu')


def inputs(identity):
    data = json.loads((FIXTURES / (identity + '.json')).read_text())
    data.pop('provenance')
    return data


@pytest.mark.parametrize('identity,title,department', [
    ('ext-26-40-ad-9-cpd', 'Head of Communities and Consistent Practices Service', 'Cooperation and Partnerships Department'),
    ('ext-26-41-ad-9-boa', 'Head of Litigation Service', 'Boards of Appeal Operations Area'),
])
def test_actual_euipo_notice_keeps_full7_pages_and_conflicting_clock(identity, title, department):
    data = inputs(identity)
    job = render_public_notice(SOURCE, **data)
    assert job.title == title and job.department == department
    assert job.employment_type == 'Temporary Agent' and job.raw['grade'] == 'AD 9'
    assert job.location == 'Alicante, SPAIN' and job.posted_at is None
    assert job.closes_at is None and job.closes_at_local == '2026-09-22T23:59:00' and job.closes_tz == 'Europe/Madrid'
    assert job.raw['_eu_official_field_resolution']['utc_resolved'] is False
    assert len(job.raw['source_content_conflicts']) == 1
    original = ''.join(''.join(unit['text'].split()) for unit in data['page_units'])
    assert ''.join(job.description.split()) == original
    assert len(job.raw['public_primary_page_units']) == 7


def test_actual_euda_notice_keeps11_pages_precise_deadline_and_dateonly_publication():
    data = inputs('ca202604')
    conflict = {'field': 'footnote_2', 'source_claim': 'Marker2 occurs, no footnote2 body appears in11 source pages.'}
    job = render_public_notice(SOURCE, **data, source_conflicts=[conflict])
    assert job.title == 'Scientific development officer'
    assert job.employment_type == 'Contract agent' and job.raw['grade'] == 'FG III'
    assert job.department == 'Substance use, harms and responses (SHR) unit'
    assert job.location == 'Lisbon, Portugal' and job.posted_at is None
    assert job.closes_at.isoformat() == '2026-09-18T22:59:00+00:00'
    assert job.closes_at_local == '2026-09-18T23:59:00+01:00' and job.closes_tz == 'Europe/Lisbon'
    proof = job.raw['_eu_official_field_resolution']
    assert proof['public_posting_calendar_date'] == '2026-08-17'
    assert proof['public_fields']['Contract duration'] == '5-year contract'
    assert job.raw['source_content_conflicts'] == [conflict]
    assert ''.join(job.description.split()) == ''.join(''.join(unit['text'].split()) for unit in data['page_units'])


@pytest.mark.parametrize('mutation', [
    lambda data: data.update(external_id='ext-26-99-ad-9-cpd'),
    lambda data: data.update(summary_url=data['summary_url'] + '?another=1'),
    lambda data: data.update(primary_url=data['primary_url'].replace('euipo.europa.eu', 'example.org')),
    lambda data: data.update(primary_url=data['primary_url'].replace('https://', 'http://')),
    lambda data: data.update(primary_url=data['primary_url'].replace('euipo.europa.eu', 'user@euipo.europa.eu')),
    lambda data: data.update(required_attachment_urls=[]),
    lambda data: data['document_proof'].update(job_key='eu_careers_static:another'),
    lambda data: data['document_proof'].update(content_sha256=''),
    lambda data: data['page_units'].pop(),
    lambda data: data['page_units'][0].update(page=2),
    lambda data: data['page_units'][0].update(text='Listing only'),
])
def test_source_identity_page_and_document_mutations_fail(mutation):
    data = inputs('ext-26-40-ad-9-cpd')
    mutation(data)
    with pytest.raises(ValueError):
        render_public_notice(SOURCE, **data)


def test_public_reference_tamper_is_rejected_even_if_text_hash_rebound():
    data = deepcopy(inputs('ext-26-40-ad-9-cpd'))
    data['page_units'][0]['text'] = data['page_units'][0]['text'].replace('EXT/26/40/AD 9/CPD', 'EXT/26/41/AD 9/BoA')
    data['document_proof']['ordered_page_text_sha256'] = hashlib.sha256('\n'.join(u['text'] for u in data['page_units']).encode()).hexdigest()
    with pytest.raises(ValueError, match='reference differs'):
        render_public_notice(SOURCE, **data)
