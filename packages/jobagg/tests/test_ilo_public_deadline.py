from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from jobagg.adapters.base import AdapterContext
from jobagg.adapters.successfactors_rmk import SuccessFactorsRMKAdapter, _ilo_public_deadline, _apply_ilo_public_deadline
from jobagg.db import JobDatabase
from jobagg.models import JobRecord, OrganizationSource


FIXTURES = Path(__file__).parent / 'fixtures/ilo'


@pytest.mark.parametrize('identity,date,zone', [
    ('13806', '2026-10-11', 'Asia/Bangkok'),
    ('13792', '2026-09-17', None),
    ('13786', '2026-09-25', 'Africa/Abidjan'),
    ('13794', '2026-09-14', None),
])
def test_actual_four_public_labels_keep_date_without_invented_utc(identity, date, zone):
    text = (FIXTURES / f'{identity}_deadline_20260913.html').read_text()
    result = _ilo_public_deadline(text)
    assert result['closes_at_local'] == date
    assert result['closes_tz'] == zone
    assert result['closes_at'] is None and result['utc_resolved'] is False
    assert result['public_date'] and result['unknown_utc_reason']


def test_numeric_clock_and_named_zone_are_resolvable():
    result = _ilo_public_deadline('Application deadline (23:59 Bangkok time): 11 October 2026')
    assert result['closes_at'] == '2026-10-11T16:59:00+00:00'
    assert result['closes_at_local'] == '2026-10-11T23:59:00'
    assert result['utc_resolved'] is True


def test_no_timezone_inferred_from_duty_station_or_office():
    result = _ilo_public_deadline('Application deadline (midnight local time): 14 September 2026 Location: Damascus Office: Bangkok')
    assert result['closes_tz'] is None and result['closes_at'] is None


def test_conflicting_public_dates_fail_closed():
    with pytest.raises(ValueError, match='disagree'):
        _ilo_public_deadline('Application deadline: 11 October 2026 Application deadline: 12 October 2026')


def detail_job():
    return JobRecord(source_id='ilo_successfactors', org_id='ilo_successfactors', ats_family='successfactors_rmk',
                     external_id='13806', title='National Coordinator', description='Full public responsibilities and qualifications. '*30,
                     apply_url='https://jobs.ilo.org/job/National-Coordinator/13806-en_GB/',
                     closes_at=datetime(2026, 10, 11, tzinfo=timezone.utc),
                     raw={'parser': 'successfactors_detail', 'detail_html': '<p>Older public job body</p>'})


def test_new_unknown_utc_clears_cached_utc_and_listing_cannot_restore_it(tmp_path):
    db = JobDatabase(tmp_path / 'jobs.sqlite3')
    db.initialize()
    old = detail_job()
    db.upsert_job(old)
    text = (FIXTURES / '13806_deadline_20260913.html').read_text()
    fresh = _apply_ilo_public_deadline(deepcopy(old), text, detail=True)
    db.upsert_job(fresh)
    row = db.get_job(old.identity_key())
    assert row['closes_at'] is None and row['closes_at_local'] == '2026-10-11'
    assert row['closes_tz'] == 'Asia/Bangkok'
    listing = deepcopy(old)
    listing.raw = {'link': old.apply_url, 'description': 'Listing summary'}
    listing.closes_at = datetime(2026, 10, 12, tzinfo=timezone.utc)
    db.upsert_job(listing)
    retained = db.get_job(old.identity_key())
    assert retained['closes_at'] is None and retained['closes_at_local'] == '2026-10-11'
    assert retained['raw']['_ilo_deadline_resolution']['public_date'] == '11 October 2026'


def test_new_detail_without_deadline_drops_old_clock_and_zone(tmp_path):
    db = JobDatabase(tmp_path / 'jobs.sqlite3')
    db.initialize()
    old = _apply_ilo_public_deadline(detail_job(), 'Application deadline (23:59 Bangkok time): 11 October 2026', detail=True)
    db.upsert_job(old)
    fresh = _apply_ilo_public_deadline(deepcopy(old), '<p>Public body without a deadline</p>', detail=True)
    db.upsert_job(fresh)
    row = db.get_job(old.identity_key())
    assert row['closes_at'] is None and row['closes_tz'] is None and row['closes_at_local'] is None


def test_detail_fetch_applies_source_deadline_without_changing_full_body():
    source = OrganizationSource(id='ilo_successfactors', name='ILO', ats_family='successfactors_rmk', base_url='https://jobs.ilo.org')
    adapter = SuccessFactorsRMKAdapter(AdapterContext(source, None))
    text = (FIXTURES / '13806_deadline_20260913.html').read_text()
    body = 'Complete duties and requirements remain unchanged. '*20
    with patch.object(adapter, 'fetch_text', return_value=text), patch.object(adapter, 'ensure_allowed'), patch.object(adapter, 'parse_jobs_from_html', return_value=[]), patch('jobagg.adapters.successfactors_rmk._detail_description', return_value=body):
        job = adapter.fetch_detail_for_listing_item({'title': 'National Coordinator', 'detail_url': 'https://jobs.ilo.org/job/National-Coordinator/13806-en_GB/'})
    assert job.description == body.strip()
    assert job.closes_at is None and job.closes_tz == 'Asia/Bangkok'
    assert job.raw['detail_html'] == text
