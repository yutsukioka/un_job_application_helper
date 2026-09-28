"""Corrected primary metadata and old full-PDF evidence retain separate scopes."""
from copy import deepcopy
from dataclasses import asdict
from datetime import datetime
import json
from pathlib import Path

import pytest

from jobagg.adapters.eu_primary_metadata import FIELDS, MARKER, PREVIOUS, apply_public_fields
from jobagg.db import JobDatabase
from jobagg.eu_primary_observation import MARKER as TEXT_MARKER
from jobagg.models import JobRecord

F = Path(__file__).parent/'fixtures/eu_primary_metadata'
SAMPLES = json.loads((F/'current_eight_20260913.json').read_text())['jobs']
LISTINGS = {j['external_id']:j for j in json.loads((F/'original_listing_eight_20260913.json').read_text())['jobs']}


def record(data):
    values = deepcopy(data)
    for key in ('posted_at','closes_at','first_seen_at','last_seen_at'):
        if isinstance(values.get(key),str):
            values[key] = datetime.fromisoformat(values[key])
    return JobRecord(**values)


def row(job):
    result = asdict(job)
    result['raw_json'] = json.dumps(result.pop('raw'),ensure_ascii=False)
    for key in ('posted_at','closes_at','first_seen_at','last_seen_at'):
        if result[key] is not None:
            result[key] = result[key].isoformat()
    return result


def candidate(index=0):
    job = record(SAMPLES[index])
    job.raw[PREVIOUS] = job.raw.pop(TEXT_MARKER)
    return apply_public_fields(job,job.raw['official_notice_text'])


@pytest.mark.parametrize('index',range(8))
def test_actual_eight_keep_new_metadata_old_body_proof_documents_and_two_real_lists(index):
    job = candidate(index)
    original = record(SAMPLES[index])
    assert JobDatabase._eu_primary_metadata_bound_public_detail(job.raw,row(job))
    before_raw = deepcopy(job.raw)
    db = object.__new__(JobDatabase)
    db._merge_existing_detail_fields(job,row(original))
    assert job.posted_at is None
    assert job.description == original.description
    assert job.employment_type != original.employment_type
    assert job.raw[PREVIOUS] == original.raw[TEXT_MARKER]
    assert job.raw['attachments'] == original.raw['attachments']
    assert not job.raw['attachment_verification']['complete']
    assert job.raw[MARKER]['whole_job_complete'] is False
    assert job.raw[MARKER]['wrapper_text_metadata_reconciled'] is False
    for _ in range(2):
        listing = record(LISTINGS[job.external_id])
        db._merge_existing_detail_fields(listing,row(job))
        for key in (*FIELDS,'description','source_url','apply_url'):
            assert getattr(listing,key) == getattr(job,key)
        for key in (MARKER,PREVIOUS,'official_notice_text','attachments','required_attachment_urls'):
            assert listing.raw[key] == job.raw[key]
        observer = listing.raw['_eu_primary_metadata_listing_observation']
        assert '_eu_primary_metadata_listing_observation' not in observer['raw']
        assert observer['wrapper_text_metadata_reconciled'] is False
        assert listing.raw[MARKER] == before_raw[MARKER]
        assert JobDatabase._eu_primary_metadata_bound_public_detail(listing.raw,row(listing))
        job = listing


@pytest.mark.parametrize('mutation',[
    lambda j:setattr(j,'source_id','other'),
    lambda j:setattr(j,'external_id','eu-lisa-26-ta-ad10-999'),
    lambda j:setattr(j,'source_url',j.source_url+'-other'),
    lambda j:setattr(j,'apply_url','https://example.org/other.pdf'),
    lambda j:setattr(j,'title','Head of Finance and Budget Unit'),
    lambda j:setattr(j,'employment_type','AD10'),
    lambda j:setattr(j,'department','Other unit'),
    lambda j:setattr(j,'closes_at',datetime.fromisoformat('2026-09-21T00:00:00+00:00')),
    lambda j:setattr(j,'posted_at',datetime.fromisoformat('2026-08-07T00:00:00+00:00')),
    lambda j:setattr(j,'description',j.description+'Added words'),
    lambda j:j.raw.update(official_notice_text=j.raw['official_notice_text']+'Added words'),
    lambda j:j.raw[MARKER].update(public_grade='AD9'),
    lambda j:j.raw[MARKER].update(whole_job_complete=True),
    lambda j:j.raw[MARKER].update(wrapper_text_metadata_reconciled=True),
    lambda j:j.raw[MARKER].update(previous_text_proof_sha256='a'*64),
    lambda j:j.raw[PREVIOUS]['primary_document_snapshot']['units'][0].update(text='Missing page'),
    lambda j:j.raw[PREVIOUS]['primary_document_snapshot']['units'].reverse(),
    lambda j:j.raw[PREVIOUS]['primary_document_snapshot']['retrieval']['phase'].update(job_id='other'),
    lambda j:j.raw[PREVIOUS]['primary_document_snapshot']['retrieval'].update(method='POST'),
    lambda j:j.raw[PREVIOUS]['visual_scope'].update(pages_actually_viewed=[999]),
    lambda j:j.raw.update({TEXT_MARKER:deepcopy(j.raw[PREVIOUS])}),
    lambda j:j.raw.update({PREVIOUS:None}),
])
def test_metadata_body_identity_and_historical_proof_mutations_fail_closed(mutation):
    job = candidate()
    mutation(job)
    assert not JobDatabase._eu_primary_metadata_bound_public_detail(job.raw,row(job))


def test_recomputed_new_marker_cannot_launder_invalid_old_page_proof():
    job = candidate()
    job.raw[PREVIOUS]['primary_document_snapshot']['units'][0]['text'] += ' Missing source words'
    apply_public_fields(job,job.raw['official_notice_text'])
    assert not JobDatabase._eu_primary_metadata_bound_public_detail(job.raw,row(job))


def test_invalid_actual_listing_source_is_rejected():
    job = candidate()
    listing = record(LISTINGS[job.external_id])
    listing.raw['href'] = 'https://example.org/other'
    with pytest.raises(ValueError,match='source/identity'):
        object.__new__(JobDatabase)._merge_existing_detail_fields(listing,row(job))


def test_fresh_metadata_without_prior_visual_proof_cannot_claim_completeness():
    old = candidate()
    new = deepcopy(old)
    new.raw.pop(PREVIOUS)
    new.raw['attachment_verification'] = {'complete':True,'discovery_complete':True}
    # A newly captured notice can change a secondary public clock. Do not
    # inherit either an old exact UTC instant or an old page certificate.
    before,after = '11:59 am Strasbourg time','10:59 am Strasbourg time'
    new.description = new.description.replace(before,after)
    new.raw['official_notice_text'] = new.raw['official_notice_text'].replace(before,after)
    apply_public_fields(new,new.raw['official_notice_text'])
    assert JobDatabase._eu_primary_metadata_bound_public_detail(new.raw,row(new))
    assert new.closes_at is None and new.posted_at is None
    object.__new__(JobDatabase)._merge_existing_detail_fields(new,row(old))
    assert PREVIOUS not in new.raw and TEXT_MARKER not in new.raw
    assert new.raw[MARKER]['previous_text_proof_sha256'] is None
    assert not new.raw[MARKER]['whole_job_complete']
    assert not new.raw['attachment_verification']['complete']
    listing = record(LISTINGS[new.external_id])
    object.__new__(JobDatabase)._merge_existing_detail_fields(listing,row(new))
    assert listing.closes_at is None and listing.posted_at is None
    assert PREVIOUS not in listing.raw


def test_fresh_independent_detail_replaces_metadata_and_does_not_inherit_old_marker():
    old = candidate()
    new = deepcopy(old)
    new.raw.pop(PREVIOUS)
    new.raw.pop(MARKER)
    new.description += ' New independently published content.'
    new.raw['official_notice_text'] += ' New independently published content.'
    new.raw['detail_html'] = '<article>New independent detail.</article>'
    object.__new__(JobDatabase)._merge_existing_detail_fields(new,row(old))
    assert MARKER not in new.raw and PREVIOUS not in new.raw
    assert new.description != old.description
