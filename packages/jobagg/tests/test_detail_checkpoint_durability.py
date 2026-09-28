import pytest

from jobagg.adapters.base import JobAdapter, register_adapter
from jobagg.db import JobDatabase
from jobagg.models import OrganizationSource
from jobagg.normalize import build_job
from jobagg.pipelines.sync_source import _persist_completed_detail, sync_source_with_selective_details
from jobagg.robots import RobotsPolicy


BODY = ('Responsibilities include delivery planning, stakeholder coordination and programme '
        'monitoring. Qualifications include relevant education and five years of professional experience.')


@register_adapter
class InterruptedBatchAdapter(JobAdapter):
    family = 'checkpoint_interruption_test'

    def fetch_jobs(self):
        return [build_job(self.source, title='Officer', external_id=key,
                          apply_url=f'https://example.org/{key}', raw={'id': key})
                for key in ('one', 'two')]

    def fetch_detail_for_listing_item(self, item):
        if item['id'] == 'two':
            raise KeyboardInterrupt('simulated process interruption')
        return build_job(self.source, title='Officer', external_id=item['id'],
                         apply_url=f"https://example.org/{item['id']}", description=BODY,
                         closes_at='2099-12-31', raw={'id': item['id'], 'full': BODY})


def test_interrupted_later_request_keeps_completed_body_and_flag_durable(tmp_path):
    db = JobDatabase(tmp_path / 'jobs.sqlite3')
    db.initialize()
    source = OrganizationSource('checkpoint', 'Checkpoint', InterruptedBatchAdapter.family,
                                'https://example.org')
    with pytest.raises(KeyboardInterrupt):
        sync_source_with_selective_details(
            source, db=db, policy=RobotsPolicy(honor_robots_txt=False, min_delay_seconds=0),
            close_missing=False,
        )
    reopened = JobDatabase(db.path)
    assert reopened.get_job('checkpoint:one')['description'] == BODY
    assert reopened.get_detail_backlog('checkpoint:one')['detail_status'] == 'complete'
    assert reopened.get_detail_backlog('checkpoint:two')['detail_status'] != 'complete'


def test_failed_completion_write_rolls_back_body_and_rejects_wrong_identity(tmp_path, monkeypatch):
    db = JobDatabase(tmp_path / 'jobs.sqlite3')
    db.initialize()
    source = OrganizationSource('checkpoint', 'Checkpoint', 'test', 'https://example.org')
    job = build_job(source, title='Officer', external_id='one',
                    apply_url='https://example.org/one', description=BODY)
    def fail(**kwargs):
        raise RuntimeError('simulated completion write failure')
    monkeypatch.setattr(db, 'record_detail_backlog_attempt', fail)
    with pytest.raises(RuntimeError, match='completion write'):
        _persist_completed_detail(db, job, job, 'listing-hash')
    assert db.get_job(job.identity_key()) is None
    other = build_job(source, title='Officer', external_id='wrong',
                      apply_url='https://example.org/wrong', description=BODY)
    with pytest.raises(ValueError, match='identity'):
        _persist_completed_detail(db, job, other, 'listing-hash')
    assert db.get_job(other.identity_key()) is None
