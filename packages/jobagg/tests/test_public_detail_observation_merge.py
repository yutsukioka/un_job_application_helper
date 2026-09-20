"""Real fresh EU notices and IOM public-contract persistence regressions."""
# ruff: noqa: E402
from datetime import datetime
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
from unittest.mock import patch
from jobagg import db as m
from jobagg.models import OrganizationSource
from jobagg.normalize import build_job
from jobagg.adapters.base import AdapterContext
from jobagg.adapters.oracle_hcm import OracleHCMAdapter
from test_eda_public_notice import fixture as eda_fixture, render as eda_render


@pytest.fixture
def database(tmp_path):
    db = m.JobDatabase(tmp_path / 'jobs.sqlite3')
    db.initialize()
    return db


def eu_job(provider):
    if provider == 'eda':
        return eda_render(eda_fixture())
    from jobagg.adapters.static_html import StaticHTMLAdapter, _extract_pdf_text, _sesar_notice_section
    from jobagg.http import HttpResponse
    if provider == 'enisa':
        from test_enisa_vacancy_notice import adapter, FIX, ID
        full = _extract_pdf_text(FIX.with_name('enisa_14_public_notice_20260913.pdf').read_bytes())
        with patch('jobagg.adapters.static_html._extract_pdf_text', return_value=full):
            obj, _ = adapter(FIX.read_text())
            return obj._fetch_eu_detail({'external_id': ID, 'title': 'Cybersecurity Expert'}, 'https://eu-careers.europa.eu/en/jobs/' + ID)
    from test_sesar_vacancy_directory import FIXTURE, URL
    notice = _sesar_notice_section(FIXTURE.read_text(), URL, 'vn226')['notice_url']
    class HTTP:
        def get(self, url):
            if url == URL:
                return HttpResponse(url, 200, {}, FIXTURE.read_text())
            if url == notice:
                return HttpResponse(url, 200, {'Content-Type': 'application/pdf'}, '', FIXTURE.with_name('sesar_vn226_public_notice_20260913.pdf').read_bytes())
            return HttpResponse(url, 200, {}, '<h1>Budget Officer</h1><div class="field--name-field-epso-link"><a href="https://www.sesarju.eu/careers">Link</a></div>')
    obj = StaticHTMLAdapter(AdapterContext(OrganizationSource('eu_careers_static', 'EU', 'static_html', 'https://eu-careers.europa.eu/en/jobs'), HTTP()))
    return obj._fetch_eu_detail({'external_id': 'vn226', 'title': 'Budget Officer'}, 'https://eu-careers.europa.eu/en/jobs/vn226')


def board(first):
    source = OrganizationSource('eu_careers_static', 'EU', 'static_html', 'https://eu-careers.europa.eu')
    return build_job(source, external_id=first.external_id, title='Short board title', description='Brief listing',
        employment_type='AD99', department='Board department', location='Board location',
        posted_at='2026-09-01T00:00:00Z', closes_at='2026-10-01T00:00:00Z',
        apply_url='https://eu-careers.europa.eu/job/' + first.external_id,
        raw={'parser': 'eu_careers_open_vacancies', 'external_id': first.external_id,
             '_jobagg_listing_verification': {'observed_at': '2026-09-13T15:00:00Z'}})


def add_documents(job):
    job.raw['attachments'] = [{'url': 'https://example.org/old.pdf', 'text': 'Stored public ToR', 'sha256': 'stored'}]
    job.raw['attachment_verification'] = {'complete': True, 'discovery_complete': True, 'observed_at': '2026-09-13T11:00:00Z'}
    return job


@pytest.mark.parametrize('provider', ['sesar', 'enisa', 'eda'])
def test_two_summaries_preserve_complete_official_fields_body_dates_raw_and_documents(database, provider):
    original = add_documents(eu_job(provider))
    database.upsert_job(original)
    for _ in range(2):
        database.upsert_job(board(original))
        row = database.get_job(original.identity_key())
        for key in ('title', 'description', 'department', 'location', 'employment_type', 'closes_at_local', 'closes_tz', 'apply_url'):
            assert row[key] == getattr(original, key), key
        for key in ('posted_at', 'closes_at'):
            assert row[key] == (getattr(original, key).isoformat() if getattr(original, key) else None)
        for key, value in original.raw.items():
            if key != '_jobagg_listing_verification':
                assert row['raw'][key] == value, key
        assert row['raw']['_eu_listing_observation']['raw']['parser'] == 'eu_careers_open_vacancies'
        assert row['raw']['_eu_listing_observation']['observed_at'] == '2026-09-13T15:00:00Z'


@pytest.mark.parametrize('provider', ['sesar', 'enisa', 'eda'])
def test_fresh_official_record_replaces_board_metadata_including_unknown_dates(database, provider):
    official = eu_job(provider)
    database.upsert_job(board(official))
    database.upsert_job(official)
    row = database.get_job(official.identity_key())
    assert row['description'] == official.description
    assert row['employment_type'] == official.employment_type
    assert row['posted_at'] == (official.posted_at.isoformat() if official.posted_at else None)
    assert row['closes_at'] == (official.closes_at.isoformat() if official.closes_at else None)


def test_fresh_eda_changed_public_body_replaces_raw_and_invalidates_certificate_but_keeps_bytes(database):
    first = add_documents(eu_job('eda'))
    first.raw['obsolete_metadata'] = 'old observation'
    database.upsert_job(first)
    payload = eda_fixture()
    payload['Notices'][0]['Description'] += '<p>A newly published condition.</p>'
    changed = eda_render(payload)
    database.upsert_job(changed)
    row = database.get_job(first.identity_key())
    assert row['description'] == changed.description
    assert row['raw']['eda_public_notice'] == payload
    assert 'obsolete_metadata' not in row['raw']
    assert row['raw']['attachments'] == first.raw['attachments']
    assert row['raw']['attachment_verification']['complete'] is False


def test_eda_identical_new_official_observation_preserves_document_certificate(database):
    first = add_documents(eu_job('eda'))
    database.upsert_job(first)
    database.upsert_job(eu_job('eda'))
    assert database.get_job(first.identity_key())['raw']['attachment_verification'] == first.raw['attachment_verification']


@pytest.mark.parametrize('provider', ['sesar', 'enisa', 'eda'])
@pytest.mark.parametrize('defect', ['identity', 'contract', 'body'])
def test_corrupt_retained_identity_public_field_or_body_fails_closed(database, provider, defect):
    first = eu_job(provider)
    if defect == 'identity':
        if provider == 'eda':
            first.raw['eda_public_notice']['Reference'] = 'EDA/2026/999'
        else:
            first.raw['external_id'] = 'unrelated-reference'
    elif defect == 'contract':
        first.employment_type = 'Unsubstantiated contract'
    else:
        first.description = 'Unrelated description'
    database.upsert_job(first)
    with pytest.raises(ValueError, match='EU official'):
        database.upsert_job(board(first))


def iom(contract=None, *, observed=True, external_id='22911', notice='Vacancy Notice'):
    source = OrganizationSource('iom_oracle_hcm', 'IOM', 'oracle_hcm', 'https://example.org')
    item = {'Id': external_id, 'Title': 'Senior Project Associate', 'ExternalResponsibilitiesStr': '',
            'ExternalQualificationsStr': '', 'requisitionFlexFields': [{'Prompt': 'Vacancy Type', 'Value': notice}]}
    if observed:
        item['ExternalDescriptionStr'] = '<p>Actual public duties and eligibility requirements.</p>'
    if contract:
        item['requisitionFlexFields'].append({'Prompt': 'Contract Type', 'Value': contract})
    return OracleHCMAdapter(AdapterContext(source, None)).parse_jobs({'items': [item]}, public_detail_observed=observed)[0]


def test_unknown_public_iom_contract_clears_stale_category_and_two_lists_keep_unknown(database):
    first = iom('Fixed-term')
    database.upsert_job(first)
    unknown = iom()
    database.upsert_job(unknown)
    for _ in range(2):
        listing = iom(observed=False, contract='Listing claim')
        database.upsert_job(listing)
        row = database.get_job(first.identity_key())
        assert row['employment_type'] is None
        assert row['raw']['_oracle_contract_resolution']['resolved'] is False
        assert all(f['Prompt'] != 'Contract Type' for f in row['raw']['requisitionFlexFields'])
        assert row['raw']['_oracle_listing_contract_observation']['requisitionFlexFields'][-1]['Value'] == 'Listing claim'


def test_iom_listing_keeps_genuine_prior_contract_then_fresh_detail_replaces_it(database):
    first = iom('Fixed-term')
    database.upsert_job(first)
    database.upsert_job(iom(observed=False))
    row = database.get_job(first.identity_key())
    assert row['employment_type'] == 'Fixed-term'
    assert row['raw']['_oracle_contract_resolution'] == first.raw['_oracle_contract_resolution']
    database.upsert_job(iom('Special short-term'))
    assert database.get_job(first.identity_key())['employment_type'] == 'Special short-term'


@pytest.mark.parametrize('defect', ['identity', 'claim'])
def test_iom_invalid_incoming_contract_proof_rejected(database, defect):
    database.upsert_job(iom('Fixed-term'))
    incoming = iom()
    if defect == 'identity':
        incoming.raw['Id'] = '999'
    else:
        incoming.raw['_oracle_contract_resolution']['public_contract_type'] = 'Wrong'
    with pytest.raises(ValueError, match='IOM contract'):
        database.upsert_job(incoming)


def test_fresh_sesar_unresolved_contract_clears_prior_value_and_stays_unknown(database):
    first = eu_job('sesar')
    database.upsert_job(first)
    unknown = eu_job('sesar')
    phrase = unknown.raw['_eu_official_field_resolution']['public_contract_phrase']
    changed = 'Administrator - Undetermined contract – AD6'
    unknown.raw['official_notice_text'] = unknown.raw['official_notice_text'].replace(phrase, changed)
    unknown.description = unknown.description.replace(phrase, changed)
    unknown.raw['_eu_official_field_resolution'].update(employment_type=None, official_grade=None,
        public_contract_phrase=None, contract_type_resolved=False)
    unknown.employment_type = None
    database.upsert_job(unknown)
    database.upsert_job(board(unknown))
    assert database.get_job(first.identity_key())['employment_type'] is None


def test_fresh_enisa_unresolved_deadline_clears_prior_instant_and_stays_unknown(database, monkeypatch):
    from test_enisa_vacancy_notice import adapter, FIX, ID
    first = eu_job('enisa')
    database.upsert_job(first)
    monkeypatch.setattr('jobagg.adapters.static_html._extract_pdf_text', lambda _: first.raw['official_notice_text'])
    obj, _ = adapter(FIX.read_text().replace('12/10/2026 at 23:59:59 Greek time', '12/10/2026 at 23:59:59'))
    unknown = obj._fetch_eu_detail({'external_id': ID, 'title': 'Cybersecurity Expert'}, 'https://eu-careers.europa.eu/en/jobs/' + ID)
    database.upsert_job(unknown)
    database.upsert_job(board(unknown))
    row = database.get_job(first.identity_key())
    assert row['closes_at'] is row['closes_at_local'] is row['closes_tz'] is None


def osce():
    from jobagg.adapters.static_html import parse_detail_page
    source = OrganizationSource('osce_custom_html', 'OSCE', 'static_html', 'https://vacancies.osce.org')
    html = (REPO / 'packages/jobagg/tests/fixtures/eu_careers/osce_4936_public_20260913.html').read_text()
    return parse_detail_page(source, html, 'https://vacancies.osce.org/jobs/adviser-on-gender-issues-s-4936')


def osce_listing(first, parser='public_links'):
    source = OrganizationSource('osce_custom_html', 'OSCE', 'static_html', 'https://vacancies.osce.org')
    return build_job(source, external_id=first.external_id, title='Summary title with location',
        description='Brief summary', employment_type='S / International Secondment',
        posted_at='2026-06-30T00:00:00Z', closes_at='2026-09-21T00:00:00Z', apply_url=first.apply_url,
        raw={'parser': parser, 'external_id': first.external_id, 'href': first.apply_url})


@pytest.mark.parametrize('parser', ['public_links', 'browser_inventory'])
def test_osce_fresh_detail_clears_midnights_and_both_listing_routes_retain_public_fields(database, parser):
    original = add_documents(osce())
    database.upsert_job(osce_listing(original, parser))
    database.upsert_job(original)
    for _ in range(2):
        database.upsert_job(osce_listing(original, parser))
        row = database.get_job(original.identity_key())
        assert row['title'] == 'Adviser on Gender Issues (S)'
        assert row['employment_type'] == 'International Secondment'
        assert row['posted_at'] is row['closes_at'] is row['closes_at_local'] is row['closes_tz'] is None
        assert row['description'] == original.description
        assert row['raw']['_osce_public_field_resolution'] == original.raw['_osce_public_field_resolution']
        assert row['raw']['grade'] == 'S'
        assert row['raw']['attachments'] == original.raw['attachments']


@pytest.mark.parametrize('defect', ['date', 'body', 'identity'])
def test_osce_invalid_retained_proof_rejected(database, defect):
    first = osce()
    if defect == 'date':
        first.closes_at = datetime.fromisoformat('2026-09-21T00:00:00+00:00')
    elif defect == 'body':
        first.description = 'Unrelated duties'
    else:
        first.raw['href'] = first.apply_url.replace('4936', '9999')
    database.upsert_job(first)
    with pytest.raises(ValueError, match='EU official'):
        database.upsert_job(osce_listing(first))
