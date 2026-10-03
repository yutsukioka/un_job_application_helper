"""Preserve OSCE legacy database identity while verifying numeric census cards."""

from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timezone
import gzip
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from jobagg.adapters.base import AdapterContext
from jobagg.adapters.static_html import StaticHTMLAdapter, _external_id_from_url, parse_detail_page
from jobagg.db import JobDatabase
from test_osce_server_inventory import ProofFixture

FIXTURE = Path(__file__).parent / 'fixtures/osce/detail_4972_20260919.html.gz'
URL = 'https://vacancies.osce.org/jobs/senior-interpreter-political-assistant-g6-4972'
SLUG = URL.rsplit('/', 1)[-1]


def numeric(job):
    key = job.raw['provider_id']
    raw = {k: v for k, v in job.raw.items() if k not in {'provider_id', 'parser'}}
    return replace(job, external_id=key, raw={**raw, 'external_id': key})


def test_fullboard_uses_legacy_slug_keys_and_separate_provider_ids(tmp_path):
    fixture = ProofFixture(tmp_path)
    assert fixture.proof()['complete']
    for job in fixture.jobs:
        assert job.external_id == _external_id_from_url(job.source_url)
        assert job.raw['external_id'] == job.external_id
        assert job.raw['provider_id'] == job.source_url.rsplit('-', 1)[-1]
        assert job.raw['parser'] == 'browser_inventory'
    assert next(j for j in fixture.jobs if j.raw['provider_id'] == '4972').external_id == SLUG


def test_historical_numeric_frame_still_verifies_against_captured_cards(tmp_path):
    fixture = ProofFixture(tmp_path)
    fixture.jobs = [numeric(job) for job in fixture.jobs]
    proof = fixture.proof()
    assert proof['complete'] and proof['reported_total'] == 45
    assert all(job.external_id.isdigit() for job in fixture.jobs)


@pytest.mark.parametrize('change', ['wrong_slug', 'raw_identity', 'raw_href', 'raw_title', 'raw_provider',
                                    'source_url', 'apply_url', 'duplicate_alias'])
def test_compatibility_alias_requires_exact_card_identity_and_content(tmp_path, change):
    fixture = ProofFixture(tmp_path)
    job = fixture.jobs[0]
    if change == 'wrong_slug': job.external_id = 'not-the-listed-slug-' + job.raw['provider_id']
    elif change == 'raw_identity': job.raw['external_id'] = job.raw['provider_id']
    elif change == 'raw_href': job.raw['href'] = URL
    elif change == 'raw_title': job.raw['title'] += ' changed'
    elif change == 'raw_provider': job.raw['provider_id'] = '999999'
    elif change == 'source_url': job.source_url += '?unlisted=1'
    elif change == 'apply_url': job.apply_url += '?unlisted=1'
    elif change == 'duplicate_alias': fixture.jobs.append(numeric(deepcopy(job)))
    proof = fixture.proof()
    assert not proof['complete'], (change, proof)


def test_captured_4972_listing_refresh_preserves_legacy_detail_and_accepts_refetch(tmp_path):
    fixture = ProofFixture(tmp_path)
    listing = next(j for j in fixture.jobs if j.raw['provider_id'] == '4972')
    raw = gzip.decompress(FIXTURE.read_bytes())
    provenance = json.loads(FIXTURE.with_suffix('.provenance.json').read_text())
    assert hashlib.sha256(raw).hexdigest() == provenance['body_sha256']
    assert len(raw) == provenance['body_bytes'] == 93426
    historical = parse_detail_page(fixture.source, raw.decode(), URL)
    historical.first_seen_at = historical.last_seen_at = datetime(2026, 9, 19, tzinfo=timezone.utc)
    historical.raw['attachments'] = [{'url': 'https://jobs.osce.org/retained.pdf', 'sha256': 'retained-content'}]
    historical.raw['attachment_verification'] = {'complete': True, 'discovery_complete': True}
    database = JobDatabase(tmp_path / 'jobs.sqlite3')
    database.initialize()
    database.upsert_job(historical)
    before = database.get_job(historical.identity_key())
    assert historical.identity_key() == listing.identity_key()
    listing.raw['_jobagg_listing_verification'] = {'observed_at': '2026-10-01T06:00:00+00:00',
                                                 'observed_in_latest_listing': True}
    database.upsert_job(deepcopy(listing))
    retained = database.get_job(historical.identity_key())
    assert retained['description'] == before['description'] and len(retained['description']) == 7194
    assert retained['first_seen_at'] == before['first_seen_at']
    assert retained['raw']['detail_html'] == before['raw']['detail_html']
    assert retained['raw']['attachments'] == before['raw']['attachments']
    assert retained['raw']['_osce_public_field_resolution'] == before['raw']['_osce_public_field_resolution']
    assert retained['raw']['_osce_listing_observation']['observed_at'] == '2026-10-01T06:00:00+00:00'
    response = SimpleNamespace(text=raw.decode(), content=raw, headers={'Content-Type': 'text/html;charset=UTF-8'})
    adapter = StaticHTMLAdapter(AdapterContext(source=fixture.source,
                                http=SimpleNamespace(get=lambda url: response)))
    adapter.ensure_allowed = lambda url: None
    refreshed = adapter.fetch_detail_for_listing_item(deepcopy(listing.raw))
    assert refreshed.external_id == SLUG
    database.upsert_job(refreshed)  # Exercises independently reparsed OSCE public-body binding.
    accepted = database.get_job(refreshed.identity_key())
    assert accepted['description'] == before['description']
    assert accepted['first_seen_at'] == before['first_seen_at']
    assert JobDatabase._osce_bound_public_detail(accepted['raw'], accepted)
    with database.connect() as conn:
        assert conn.execute('SELECT count(*) FROM jobs').fetchone()[0] == 1
    assert not database.get_job(replace(listing, external_id='4972').identity_key())
