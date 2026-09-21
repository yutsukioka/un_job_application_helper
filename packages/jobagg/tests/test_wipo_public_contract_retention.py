"""A public WIPO detail cannot inherit an unobserved listing contract class."""
import pytest

from jobagg.db import JobDatabase
from jobagg.models import OrganizationSource
from jobagg.normalize import build_job
from test_taleo_public_bindings import adapter, fixture


def detail():
    body, url = fixture('wipo_taleo')
    job = adapter('wipo_taleo').parse_detail_html(body, url)
    job.raw['detail_html'] = body
    return job


def listing(type='Fixed Term Appointment'):
    full = detail()
    return build_job(OrganizationSource('wipo_taleo', 'WIPO', 'taleo', 'https://wipo.taleo.net'),
        external_id=full.external_id, title=full.title, apply_url=full.apply_url,
        employment_type=type, description='WIPO listing summary',
        raw={'_taleo_record_kind': 'listing', '_taleo_flat': {'Job Number': full.external_id, 'Job Type': type}})


@pytest.fixture
def db(tmp_path):
    result = JobDatabase(tmp_path / 'jobs.sqlite3')
    result.initialize()
    return result


def test_public_contract_absence_clears_old_classification_and_two_lists_keep_unknown(db):
    db.upsert_job(listing())
    full = detail()
    assert full.employment_type is None
    db.upsert_job(full)
    for _ in range(2):
        db.upsert_job(listing('Temporary Appointment'))
        row = db.get_job(full.identity_key())
        assert row['employment_type'] is None
        assert row['raw']['_wipo_previous_employment_observation']['employment_type'] == 'Fixed Term Appointment'
        assert row['raw']['_wipo_listing_employment_observation']['employment_type'] == 'Temporary Appointment'
        assert row['raw']['_taleo_flat']['Contract Duration'] == '2 years (maximum cumulative length of 5 years) *'
        assert row['description'] == full.description


@pytest.mark.parametrize('defect', ['identity', 'type', 'field', 'html'])
def test_malformed_or_stale_public_contract_proof_fails_closed(db, defect):
    db.upsert_job(listing())
    incoming = detail()
    if defect == 'identity':
        incoming.raw['_taleo_detail_url'] = incoming.raw['_taleo_detail_url'].replace(incoming.external_id, 'WRONG')
    elif defect == 'type':
        incoming.employment_type = 'Unobserved classification'
    elif defect == 'field':
        incoming.raw['_taleo_flat']['JOB_TYPE'] = 'Invented public field'
    else:
        incoming.raw['detail_html'] = '<h1>Unrelated public notice</h1>'
    with pytest.raises(ValueError, match='WIPO public'):
        db.upsert_job(incoming)
    assert db.get_job(incoming.identity_key())['employment_type'] == 'Fixed Term Appointment'
