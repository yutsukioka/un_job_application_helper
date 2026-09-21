from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

import pytest

from jobagg.adapters.base import AdapterContext
from jobagg.adapters.ilo_metadata import apply_public_metadata, public_metadata
from jobagg.adapters.successfactors_rmk import SuccessFactorsRMKAdapter, _apply_ilo_public_deadline
from jobagg.db import JobDatabase
from jobagg.models import JobRecord, OrganizationSource

FIXTURES = Path(__file__).parent/'fixtures/ilo'
SOURCE = OrganizationSource('ilo_successfactors', 'ILO', 'successfactors_rmk', 'https://jobs.ilo.org')
EXPECTED = [
    ('13786', '1431497133', 'Abidjan', 'BR-Afrique', 'Durée déterminée', 'NOA', '2026-08-28'),
    ('13792', '1433645433', 'Ciudad de México', 'OR–América Latina y el Caribe', 'Duración determinada', 'NOA', '2026-09-03'),
    ('13794', '1431494733', 'Damascus', 'RO-Arab States/DWT-Beirut', 'Fixed Term', 'NOA', '2026-08-28'),
    ('13806', '1435999233', 'Vientiane', 'RO-Asia and the Pacific', 'Fixed Term', 'NOB', '2026-09-11'),
]


def body(identity):
    return (FIXTURES/f'{identity}_full_metadata_20260913.html').read_text()


def job(legacy='1435999233'):
    return JobRecord(SOURCE.id, SOURCE.id, SOURCE.ats_family, 'Current public job',
                     'https://jobs.ilo.org/job/Role/'+legacy+'/', external_id=legacy,
                     employment_type='NOA', description='The complete public job text stays unchanged.')


@pytest.mark.parametrize('identity,legacy,location,department,contract,grade,posted', EXPECTED)
def test_exact_fresh_multilingual_labels_keep_contract_separate_from_grade(identity, legacy, location, department, contract, grade, posted):
    original = _apply_ilo_public_deadline(job(legacy), body(identity), detail=True)
    original.raw['attachments'] = [{'url': 'https://example.invalid/jd.pdf', 'text': 'Document body'}]
    before = deepcopy(original)
    parsed = apply_public_metadata(original, body(identity), detail=True)
    assert (parsed.location, parsed.department, parsed.employment_type) == (location, department, contract)
    assert parsed.raw['grade'] == grade
    assert parsed.raw['contract_type'] == contract
    assert parsed.raw['_ilo_field_resolution']['publication_date_local'] == posted
    assert parsed.posted_at is None
    assert parsed.description == before.description
    assert parsed.raw['detail_html'] == before.raw['detail_html']
    assert parsed.raw['attachments'] == before.raw['attachments']
    assert parsed.raw['_ilo_deadline_resolution'] == before.raw['_ilo_deadline_resolution']
    assert parsed.identity_key() == before.identity_key()
    assert parsed.raw['_ilo_field_resolution']['values']['public_job_id'] == identity


def test_metadata_uses_only_bounded_header_and_rejects_conflicting_labels():
    minimal = '<p><strong>Grade:</strong> NOB<br>Job ID: 13806<br>Location: Vientiane</p><hr><p>Location: Rome. Grade: P5. Narrative example.</p>'
    assert public_metadata(minimal)['values']['location'] == 'Vientiane'
    with pytest.raises(ValueError, match='Conflicting ILO public metadata'):
        public_metadata(minimal.replace('</p><hr>', '<br>Location: Rome</p><hr>'))
    assert public_metadata('<p>Location: Rome. Narrative example.</p>') is None


@pytest.mark.parametrize('timestamp,expected', [
    ('2026-09-11', None),
    ('Fri, 11 Sep 2026 14:30:00', None),
    ('Fri, 11 Sep 2026 14:30:00 +0700', datetime(2026, 9, 11, 7, 30, tzinfo=UTC)),
    ('Fri, 11 Sep 2026 0:00:00 GMT', datetime(2026, 9, 11, tzinfo=UTC)),
    ('Sat, 12 Sep 2026 0:00:00 GMT', None),
])
def test_publication_calendar_date_never_becomes_utc_midnight(timestamp, expected):
    current = job()
    current.posted_at = datetime(2026, 9, 11, tzinfo=UTC)
    current.raw['pubDate'] = timestamp
    parsed = apply_public_metadata(current, body('13806'), detail=True)
    assert parsed.posted_at == expected
    assert parsed.raw['_ilo_field_resolution']['publication_date_local'] == '2026-09-11'
    assert parsed.raw['pubDate'] == timestamp
    if timestamp.endswith('+0700'):
        assert parsed.raw['_ilo_field_resolution']['rss_claimed_publication_utc'] == '2026-09-11T07:30:00+00:00'


def test_detail_fetch_wires_metadata_without_changing_public_body():
    adapter = SuccessFactorsRMKAdapter(AdapterContext(SOURCE, None))
    text = body('13806')
    with patch.object(adapter, 'fetch_text', return_value=text), patch.object(adapter, 'ensure_allowed'):
        parsed = adapter.fetch_detail_for_listing_item({'title': 'National Coordinator', 'detail_url': job().apply_url})
    assert parsed.location == 'Vientiane'
    assert parsed.employment_type == 'Fixed Term'
    assert parsed.raw['grade'] == 'NOB'
    assert parsed.closes_at is None and parsed.closes_tz == 'Asia/Bangkok'


def test_actual_fresh_rss_maps_all_four_fields_without_reintroducing_grade_as_contract():
    adapter = SuccessFactorsRMKAdapter(AdapterContext(SOURCE, None))
    jobs = {job.external_id: job for job in adapter.parse_jobs_from_rss((FIXTURES/'rss_metadata_20260913.xml').read_text())}
    assert set(jobs) == {row[1] for row in EXPECTED}
    for identity, legacy, location, department, contract, grade, posted in EXPECTED:
        parsed = jobs[legacy]
        assert (parsed.location, parsed.department, parsed.employment_type) == (location, department, contract)
        assert parsed.raw['grade'] == grade
        assert parsed.raw['_ilo_field_resolution']['publication_date_local'] == posted
        assert parsed.raw['_ilo_field_resolution']['values']['public_job_id'] == identity
        assert parsed.posted_at.isoformat().startswith(posted)
        assert parsed.raw['_ilo_field_resolution']['posted_at_precision'] == 'explicit_rss_timestamp'


@pytest.mark.parametrize('identity,legacy,location,department,contract,grade,posted', EXPECTED)
def test_two_fresh_rss_updates_retain_detail_metadata_and_body(tmp_path, identity, legacy, location, department, contract, grade, posted):
    adapter = SuccessFactorsRMKAdapter(AdapterContext(SOURCE, None))
    rss = (FIXTURES/'rss_metadata_20260913.xml').read_text()
    initial = next(job for job in adapter.parse_jobs_from_rss(rss) if job.external_id == legacy)
    detailed = apply_public_metadata(_apply_ilo_public_deadline(initial, body(identity), detail=True), body(identity), detail=True)
    before_text = detailed.description
    database = JobDatabase(tmp_path/'jobs.sqlite3')
    database.initialize()
    database.upsert_job(detailed)
    for _ in range(2):
        listing = next(job for job in adapter.parse_jobs_from_rss(rss) if job.external_id == legacy)
        database.upsert_job(listing)
        stored = database.get_job(detailed.identity_key())
        assert (stored['location'], stored['department'], stored['employment_type']) == (location, department, contract)
        assert stored['raw']['grade'] == grade
        assert stored['raw']['contract_type'] == contract
        assert stored['posted_at'].startswith(posted)
        assert stored['description'] == before_text
        assert stored['raw']['detail_html'] == body(identity)
        assert stored['raw']['_ilo_deadline_resolution']['record_kind'] == 'detail'


def test_other_successfactors_source_is_unchanged():
    current = job()
    current.source_id = 'other_successfactors'
    before = deepcopy(current)
    assert apply_public_metadata(current, body('13806'), detail=True) == before
