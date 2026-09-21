from datetime import UTC, datetime

import pytest

from jobagg.db import JobDatabase
from jobagg.models import OrganizationSource
from jobagg.normalize import build_job


def test_partial_listing_preserves_history_but_replaces_observed_membership(tmp_path):
    source = OrganizationSource('source', 'Source', 'test', 'https://example.org')
    db = JobDatabase(tmp_path / 'jobs.sqlite3')
    db.initialize()
    def job(key):
        return build_job(source, title='Officer', external_id=key,
                         apply_url=f'https://example.org/jobs/{key}',
                         description='Full job description and eligibility requirements.')
    one, two = job('1'), job('2')
    db.upsert_jobs([one, two])
    before = db.get_job(two.identity_key())
    observed = datetime(2026, 9, 10, tzinfo=UTC)
    db.record_listing_observation(source.id, {one.identity_key()},
                                  observed_at=observed, inventory_complete=False)
    after = db.get_job(two.identity_key())
    for field in ('status', 'description', 'first_seen_at', 'last_seen_at', 'normalized_hash'):
        assert after[field] == before[field]
    assert after['raw']['_jobagg_listing_verification']['observed_in_latest_listing'] is False
    assert db.get_job(one.identity_key())['raw']['_jobagg_listing_verification']['observed_in_latest_listing'] is True
    db.upsert_job(job('1'))
    assert db.get_job(one.identity_key())['raw']['_jobagg_listing_verification']['observed_in_latest_listing'] is True
    db.record_listing_observation(source.id, {two.identity_key()}, observed_at=observed)
    assert db.get_job(one.identity_key())['raw']['_jobagg_listing_verification']['observed_in_latest_listing'] is False
    assert db.get_job(two.identity_key())['raw']['_jobagg_listing_verification']['observed_in_latest_listing'] is True
    db.record_listing_observation(source.id, {two.identity_key()}, observed_at=observed, enabled=False)
    assert db.get_job(two.identity_key())['raw']['_jobagg_listing_verification']['reason'] == 'disabled_source'
    with pytest.raises(ValueError, match='timezone-aware'):
        db.record_listing_observation(source.id, set(), observed_at=datetime(2026, 9, 10))
