from datetime import UTC, datetime
import json
import time

import pytest
import yaml

from jobagg.http import HTTPError
from jobagg.remediation_worker import Worker
from test_remediation_worker import FixtureClient
from test_vacancy_http_denials import BASE, URL, S22, error_response


@pytest.fixture
def case(tmp_path, monkeypatch):
    monkeypatch.setattr('jobagg.pipelines.http_checkpoint.time.sleep', lambda _: None)
    registry = tmp_path / 'sources.yaml'
    registry.write_text(yaml.safe_dump({'sources': [{'id': 'unhcr_workday', 'name': 'UNHCR',
        'ats_family': 'workday', 'base_url': 'https://unhcr.wd3.myworkdayjobs.com/External',
        'extra': {'cxs_base_url': BASE, 'page_size': 20, 'max_pages': 2}}]}))
    robots = tmp_path / 'robots.yaml'
    robots.write_text('default:\n  honor_robots_txt: false\n  min_delay_seconds: 0\n')
    lock = tmp_path / 'shared.lock'
    bootstrap = tmp_path / 'bootstrap.json'
    bootstrap.write_text(json.dumps({'schema_version': 1, 'shared_lock': str(lock),
        'reviewed_at': datetime.now(UTC).isoformat(), 'prior_writers_reviewed': True,
        'no_unmigrated_policy_state': True, 'scope_source_ids': ['unhcr_workday'],
        'evidence': [], 'detail_attempts': [], 'host_states': {}, 'source_holds': {},
        'review_note': 'Isolated test'}))
    listing = {'total': 1, 'jobPostings': [{'title': 'Role', 'externalPath': '/job/City/Role_JR123',
                                         'locationsText': 'City', 'bulletFields': ['JR123']}]}
    detail = {'jobPostingInfo': {'jobReqId': 'JR123', 'jobPostingId': 'Role_JR123', 'title': 'Role', 'location': 'City',
        'externalUrl': 'https://unhcr.wd3.myworkdayjobs.com/External/job/City/Role_JR123',
        'jobDescription': '<h2>Responsibilities</h2><p>' + 'Prepare reports and conduct analysis. ' * 30 +
                          '</p><h2>Qualifications</h2><p>Advanced degree and relevant experience.</p>'}}
    replies, calls = {BASE + '/jobs': listing, URL: detail}, []
    worker = Worker(registry=registry, robots=robots, workspace=tmp_path / 'worker', shared_lock=lock,
        max_tasks=3, client_factory=lambda source, policy: FixtureClient(replies, calls), policy_bootstrap=bootstrap)
    return worker, replies, calls


def task(worker):
    with worker.db.connect() as conn:
        return dict(conn.execute("SELECT * FROM remediation_tasks WHERE kind='detail'").fetchone())


def force_listing(worker):
    with worker.db.connect() as conn:
        conn.execute('UPDATE remediation_sources SET next_list_at=0')


def test_s22_preserves_last_good_detail_and_does_not_stop_listing_or_reduce_concurrency(case):
    worker, replies, calls = case
    initial = worker.tick(execute=True)
    assert task(worker)['status'] == 'done', task(worker)['last_error']
    prior = worker.db.get_job('unhcr_workday:JR123')['description']
    replies[URL] = HTTPError('provider refusal', response=error_response(URL))
    with worker.db.connect() as conn:
        conn.execute("UPDATE remediation_tasks SET status='pending',eligible_at=0 WHERE kind='detail'")
    report = worker.tick(execute=True)
    assert task(worker)['status'] == 'unavailable_pending_inventory', report
    assert worker.db.get_job('unhcr_workday:JR123')['description'] == prior
    assert report['concurrency']['new_access_blocks'] == 0
    assert report['concurrency']['transport_failures'] == 0
    assert report['concurrency']['runtime_errors'] == report['concurrency']['integrity_errors'] == 0
    assert report['concurrency']['vacancies_unavailable'] == 1
    assert not any(json.loads(path.read_text()).get('stopped') for path in
                   (worker.shared_policy.root / 'hosts').glob('*.json'))
    force_listing(worker)
    report = worker.tick(execute=True)
    row = task(worker)
    assert report['concurrency']['accepted_progress'] == 1
    assert row['status'] == 'pending' and row['eligible_at'] >= time.time() + 86300, report['sources'][0]['enumeration']
    receipt = json.loads(row['receipt'])
    assert receipt['listing_reconciliation']['closure_inferred'] is False
    assert receipt['detail_denied_rechecks'] == 1
    # Repeated inventories must not bypass the daily delay.
    due = row['eligible_at']
    force_listing(worker); worker.tick(execute=True)
    assert task(worker)['eligible_at'] >= due
    assert sum(url == URL for url, _ in calls) == 2
    # After the next charged denial a later fresh inventory permits another
    # daily retry, rather than making a temporary permission error permanent.
    with worker.db.connect() as conn:
        conn.execute("UPDATE remediation_tasks SET eligible_at=0 WHERE kind='detail'")
    worker.tick(execute=True)
    assert task(worker)['status'] == 'unavailable_pending_inventory'
    force_listing(worker); worker.tick(execute=True)
    row = task(worker)
    assert row['status'] == 'pending' and row['eligible_at'] >= time.time() + 86300
    assert json.loads(row['receipt'])['detail_denied_rechecks'] == 2


def test_fresh_complete_absence_retires_only_task_and_keeps_saved_description(case):
    worker, replies, calls = case
    worker.tick(execute=True)
    prior = worker.db.get_job('unhcr_workday:JR123')['description']
    replies[URL] = HTTPError('provider refusal', response=error_response(URL))
    with worker.db.connect() as conn:
        conn.execute("UPDATE remediation_tasks SET status='pending',eligible_at=0 WHERE kind='detail'")
    worker.tick(execute=True)
    replies[BASE + '/jobs'] = {'total': 0, 'jobPostings': []}
    force_listing(worker); worker.tick(execute=True)
    row = task(worker)
    assert row['status'] == 'not_observed', row['last_error']
    assert json.loads(row['receipt'])['listing_reconciliation']['closure_inferred'] is False
    assert worker.db.get_job('unhcr_workday:JR123')['description'] == prior


def test_listing_403_still_stops_host_and_counts_access_pressure(case):
    worker, replies, calls = case
    replies[BASE + '/jobs'] = HTTPError('listing refusal', response=error_response(BASE + '/jobs'))
    report = worker.tick(execute=True)
    assert report['concurrency']['new_access_blocks'] == 1
    assert report['concurrency']['vacancies_unavailable'] == 0
    assert any(json.loads(path.read_text()).get('stopped') for path in
               (worker.shared_policy.root / 'hosts').glob('*.json'))
