"""ECHA public PDF fields retain their source precision through list refreshes."""
from copy import deepcopy
from datetime import datetime, UTC
import hashlib
import json
from pathlib import Path

import pytest

from jobagg.db import JobDatabase
from jobagg.models import OrganizationSource
from jobagg.normalize import build_job

FIXTURE = Path(__file__).parent / 'fixtures/eu_careers/echa_2026_04_public_text_20260913.txt'
ENTRY = 'https://jobs.echa.europa.eu/psp/pshrrcr/EMPLOYEE/HRMS/c/HRS_HRAM.HRS_APP_SCHJOB.GBL?FOCUS=Applicant'
ACTION = 'https://jobs.echa.europa.eu/psc/pshrrcr/EMPLOYEE/HRMS/c/HRS_HRAM.HRS_APP_SCHJOB.GBL'
# The opaque response path is intentionally replaced in this offline fixture.
PDF = 'https://jobs.echa.europa.eu/psc/pshrrcr/view/fixture/ECHA-TA-2026-04_-_Scientific_Officer.pdf'
ID = 'echa-ta-2026-04'
SOURCE = OrganizationSource('eu_careers_static', 'EU', 'static_html', 'https://eu-careers.europa.eu')


def official():
    text = FIXTURE.read_text()
    provenance = json.loads(FIXTURE.with_suffix('.provenance.json').read_text())
    title = 'Scientific Officer – Ecotoxicologist and Toxicologist (2 profiles)'
    proof = {'record_kind': 'detail', 'provider': 'echa_public_notice_pdf',
             'public_reference': 'ECHA/TA/2026/04', 'public_title': title,
             'public_contract_type': 'Temporary Agent', 'official_grade': 'AD5',
             'public_location': 'Helsinki, Finland', 'public_publication_date': '10 September 2026',
             'public_deadline': '15 October 2026, at noon, 12:00 Helsinki time (11:00 CET)',
             'utc_resolved': False, 'posting_time_resolved': False,
             'source_conflicts': ['Helsinki noon versus literal fixed-offset CET during October DST']}
    job = build_job(SOURCE, external_id=ID, title=title, location='Helsinki, Finland',
        employment_type='Temporary Agent', apply_url=ENTRY, description=text,
        raw={'parser': 'eu_official_detail', 'external_id': ID,
             'official_notice_text': text, 'official_vacancy_url': PDF, 'detail_fetch_url': PDF,
             'official_wrapper_url': ENTRY, 'detail_html': f'<a href="{PDF}">Official vacancy PDF</a>',
             'required_attachment_urls': [PDF, 'https://echa.europa.eu/guide.pdf'],
             '_eu_official_field_resolution': proof,
             '_eu_reviewed_primary_extraction': {'text_sha256': provenance['text_sha256'],
                 'content_sha256': provenance['content_sha256'], 'page_count': 8},
             '_echa_public_download_response': {**provenance['source_times'], 'url': ACTION}})
    # The offline PDF importer intentionally preserves exact extracted text.
    job.description = text
    return job


def listing():
    return build_job(SOURCE, external_id=ID, title='Scientific Officer', employment_type='AD5',
        description='Board summary', posted_at='2026-09-10T00:00:00Z', closes_at='2026-10-15T00:00:00Z',
        apply_url='https://eu-careers.europa.eu/jobs/' + ID,
        raw={'parser': 'eu_careers_open_vacancies', 'external_id': ID})


@pytest.fixture
def db(tmp_path):
    database = JobDatabase(tmp_path / 'jobs.sqlite3')
    database.initialize()
    return database


def test_fresh_pdf_clears_invented_instants_and_two_lists_keep_full_public_observation(db):
    db.upsert_job(listing())
    full = official()
    full.raw['attachments'] = [{'sha256': 'existing-document-bytes', 'text': 'Previously captured required guide'}]
    full.raw['attachment_verification'] = {'complete': True, 'discovery_complete': True}
    original = deepcopy(full.raw)
    db.upsert_job(full)
    for _ in range(2):
        db.upsert_job(listing())
        row = db.get_job(full.identity_key())
        assert row['title'] == full.title
        assert row['employment_type'] == 'Temporary Agent'
        assert row['location'] == 'Helsinki, Finland'
        assert row['description'] == full.description
        assert row['apply_url'] == ENTRY
        assert all(row[key] is None for key in ('posted_at', 'closes_at', 'closes_at_local', 'closes_tz'))
        for key, value in original.items():
            assert row['raw'][key] == value
        assert row['raw']['_eu_listing_observation']['closes_at'] == '2026-10-15T00:00:00+00:00'


@pytest.mark.parametrize('defect', ['reference', 'body_hash', 'title', 'contract', 'date', 'source', 'download'])
def test_mismatched_public_identity_body_metadata_or_download_proof_cannot_be_retained(db, defect):
    job = official()
    if defect == 'reference':
        job.raw['_eu_official_field_resolution']['public_reference'] = 'ECHA/TA/2026/99'
    elif defect == 'body_hash':
        job.raw['_eu_reviewed_primary_extraction']['text_sha256'] = '0' * 64
    elif defect == 'title':
        job.title = 'Unrelated role'
    elif defect == 'contract':
        job.employment_type = 'AD5'
    elif defect == 'date':
        job.closes_at = datetime(2026, 10, 15, tzinfo=UTC)
    elif defect == 'source':
        job.raw['official_vacancy_url'] = PDF.replace('jobs.echa.europa.eu', 'unrelated.example')
    else:
        job.raw['_echa_public_download_response']['url'] = 'https://jobs.echa.europa.eu/signin'
    db.upsert_job(job)
    with pytest.raises(ValueError, match='EU official'):
        db.upsert_job(listing())


def test_new_public_pdf_body_replaces_old_group_and_invalidates_document_discovery(db):
    old = official()
    old.raw['obsolete_detail_claim'] = 'old'
    old.raw['attachments'] = [{'text': 'Captured guide', 'sha256': 'document-bytes'}]
    old.raw['attachment_verification'] = {'complete': True, 'discovery_complete': True}
    db.upsert_job(old)
    incoming = official()
    incoming.description += '\nNewly published public notice clarification.'
    incoming.raw['official_notice_text'] = incoming.description
    incoming.raw['_eu_reviewed_primary_extraction']['text_sha256'] = hashlib.sha256(incoming.description.encode()).hexdigest()
    db.upsert_job(incoming)
    row = db.get_job(incoming.identity_key())
    assert row['description'] == incoming.description
    assert 'obsolete_detail_claim' not in row['raw']
    assert row['raw']['attachments'] == old.raw['attachments']
    assert row['raw']['attachment_verification']['complete'] is False
