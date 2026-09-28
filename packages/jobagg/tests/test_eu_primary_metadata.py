"""Actual full primary notices distinguish contracts, units and public clocks."""
from copy import deepcopy
from dataclasses import fields
from datetime import datetime
import json
from pathlib import Path

import pytest

from jobagg.adapters.eu_primary_metadata import FIELDS, MARKER, PREVIOUS, apply_public_fields
from jobagg.models import JobRecord

FIXTURE = Path(__file__).parent / 'fixtures/eu_primary_metadata/current_eight_20260913.json'


def sample(index=0):
    value = deepcopy(json.loads(FIXTURE.read_text())['jobs'][index])
    values = {field.name: value[field.name] for field in fields(JobRecord) if field.name in value}
    for field in ('posted_at', 'closes_at', 'first_seen_at', 'last_seen_at'):
        if isinstance(values.get(field), str):
            values[field] = datetime.fromisoformat(values[field])
    job = JobRecord(**values)
    job.raw[PREVIOUS] = job.raw.pop('_eu_primary_text_spacing_resolution')
    return job


@pytest.mark.parametrize('index,contract,department', [
    (0, 'Temporary Staff', 'Corporate Services Department'),
    (1, 'Temporary Agent', 'Resource and Service Centre Unit (RSC)'),
    (2, 'Temporary Agent', 'Corporate Services Unit'),
    (3, 'Temporary Agent', 'Internal Control and Compliance Unit'),
    (4, 'Temporary Agent', 'Corporate Security Unit'),
    (5, 'Temporary Staff', 'Legal Unit'),
    (6, 'Temporary Agent', 'Administration Department (ADMIN)'),
    (7, 'Contract Agent', 'Operational and Technical Assistance Unit (OTAU)'),
])
def test_eight_full_notices_contract_unit_and_body(index, contract, department):
    job = sample(index)
    before = deepcopy(job)
    apply_public_fields(job, job.raw['official_notice_text'])
    assert job.employment_type == contract and job.department == department
    assert job.description == before.description and job.raw['official_notice_text'] == before.raw['official_notice_text']
    assert job.raw['attachments'] == before.raw['attachments']
    assert job.raw[PREVIOUS] == before.raw[PREVIOUS]
    assert job.posted_at is None
    assert job.raw[MARKER]['whole_job_complete'] is False
    assert job.raw[MARKER]['wrapper_text_metadata_reconciled'] is False
    assert job.raw[MARKER]['public_grade'] != job.employment_type
    assert set(job.raw[MARKER]['canonical_fields']) == set(FIELDS)


def test_actual_primary_title_overrides_reversed_board_title_and_two_clocks_agree():
    job = sample()
    apply_public_fields(job, job.raw['official_notice_text'])
    assert job.title == 'Head of Budget and Finance Unit'
    assert job.raw['title'] == 'Head of Finance and Budget Unit'
    assert job.closes_at.isoformat() == '2026-09-21T09:59:00+00:00'
    assert job.closes_at_local == '2026-09-21T12:59:00+03:00'
    assert job.raw[MARKER]['public_posting_calendar_date'] == '2026-08-07'


def test_f4e_actual_cet_place_conflict_is_unknown_and_publication_date_kept():
    job = sample(6)
    apply_public_fields(job, job.raw['official_notice_text'])
    assert job.closes_at is None
    assert job.closes_at_local == '2026-09-15T11:59:00'
    assert job.closes_tz == 'Europe/Madrid'
    assert job.raw[MARKER]['public_posting_calendar_date'] == '2026-07-27'
    assert job.raw[MARKER]['source_conflicts'][0]['field'] == 'closing_time'


def test_euaa_country_not_letterhead_address_and_qualified_location_preserved():
    job = sample(7)
    apply_public_fields(job, job.raw['official_notice_text'])
    assert job.location == 'Malta'
    assert 'different location in which the Agency is operationally present' in job.raw[MARKER]['public_claims']['location']
    assert job.closes_at_local == '2026-09-23T12:00:00+02:00'


def test_euosha_alternative_offer_does_not_replace_advertised_contract():
    job = sample(1)
    apply_public_fields(job, job.raw['official_notice_text'])
    assert job.employment_type == 'Temporary Agent'
    assert job.raw[MARKER]['public_claims']['alternate_contract_offer'] == 'Contract Agent, Function Group III'


@pytest.mark.parametrize('field,value', [('source_id', 'wrong'), ('external_id', 'eu-lisa-26-ta-ad10-999'),
    ('source_url', 'https://eu-careers.europa.eu/en/job-opportunities/other/wrong'),
    ('apply_url', 'http://erecruitment.eulisa.europa.eu/notice.pdf')])
def test_source_identity_scope_mutations_rejected(field, value):
    job = sample()
    setattr(job, field, value)
    with pytest.raises(ValueError):
        apply_public_fields(job, job.raw['official_notice_text'])


@pytest.mark.parametrize('old,new', [('Function Group/Grade AD10 (Temporary Staff)', 'Function Group/Grade AD10'),
    ('Contract Duration', 'Other Duration'), ('Ref. eu-LISA/26/TA/AD10/15.1', 'Ref. eu-LISA/26/TA/AD10/99.1'),
    ('Unit and Department Budget and Finance Unit / Corporate Services Department', 'Unit and Department An agency')])
def test_missing_or_ambiguous_public_labels_fail_closed(old, new):
    job = sample()
    job.description = job.description.replace(old, new)
    job.raw['official_notice_text'] = job.raw['official_notice_text'].replace(old, new)
    with pytest.raises(ValueError):
        apply_public_fields(job, job.raw['official_notice_text'])


def test_dual_place_clock_disagreement_not_guessed():
    job = sample()
    old, new = '11:59 am Strasbourg time', '10:59 am Strasbourg time'
    job.description = job.description.replace(old, new)
    job.raw['official_notice_text'] = job.raw['official_notice_text'].replace(old, new)
    apply_public_fields(job, job.raw['official_notice_text'])
    assert job.closes_at is None and job.raw[MARKER]['utc_resolved'] is False


def test_fresh_primary_projection_does_not_fabricate_visual_review():
    job = sample()
    job.raw.pop(PREVIOUS)
    apply_public_fields(job, job.raw['official_notice_text'])
    assert job.raw[MARKER]['previous_text_proof_sha256'] is None
    assert job.raw[MARKER]['whole_job_complete'] is False


@pytest.mark.parametrize('field,value', [('external_id', 'another-reference'),
    ('detail_fetch_url', 'https://erecruitment.eulisa.europa.eu/another.pdf'),
    ('official_vacancy_url', 'https://erecruitment.eulisa.europa.eu/another.pdf')])
def test_raw_reference_and_actual_notice_route_cannot_drift(field, value):
    job = sample()
    job.raw[field] = value
    with pytest.raises(ValueError):
        apply_public_fields(job, job.raw['official_notice_text'])


def test_f4e_winter_cet_and_named_place_are_coherent_when_both_labels_change():
    job = sample(6)
    job.description = job.description.replace('15/09/2026', '15/12/2026')
    job.raw['official_notice_text'] = job.raw['official_notice_text'].replace('15/09/2026', '15/12/2026')
    apply_public_fields(job, job.raw['official_notice_text'])
    assert job.closes_at.isoformat() == '2026-12-15T10:59:00+00:00'
    assert job.closes_at_local == '2026-12-15T11:59:00+01:00'
    assert job.raw[MARKER]['utc_resolved'] is True
