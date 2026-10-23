"""PR #142 reproductions for partial publication and listing occurrence identity."""
from datetime import datetime
import gzip
import hashlib
import json
import sqlite3
import time

import pytest

from jobagg import accepted_evidence, publication_work as work
from jobagg.db import JobDatabase
from jobagg.models import JobRecord
from jobagg.pipelines.live_publication import publish_incremental
from jobagg.publication_projection import build_projection
from test_live_publication import setup, add_detail, write  # noqa: F401
from test_publication_work import prepare, select

# ruff: noqa: F811


def _publish_selected(setup, tmp_path, selection, *, max_jobs=20):
    projection = tmp_path / f'projection-{time.time_ns()}.sqlite3'
    with sqlite3.connect(setup['worker'].path) as origin, sqlite3.connect(projection) as dest:
        build_projection(origin, dest, selection=selection, deadline_at=time.monotonic() + 10)
    return publish_incremental(projection, setup['registry'], setup['output'], setup['state'],
                               publication_selection=selection, max_jobs=max_jobs, execute=True)


def test_capped_details_remain_pending_until_next_generation(setup, tmp_path):
    add_detail(setup)
    second, _ = add_detail(setup, external_id='002')
    prepare(setup)
    with setup['worker'].connect() as conn:
        selection = select(setup, conn)
    first = _publish_selected(setup, tmp_path, selection, max_jobs=1)
    assert first['status'] == 'published' and first['changed_jobs'] == 1
    with setup['worker'].connect() as conn:
        work.acknowledge(conn, selection, result_sha256='a' * 64,
                         processed_units=first['processed_units'], rejected=first['rejected'])
        assert work.vector(conn, contract_sha256='contract', now=time.time())['pending']
        remaining = select(setup, conn)
        assert len(remaining['units']) == 1
        assert remaining['units'][0]['job_key'] == second.identity_key()
    with JobDatabase(setup['output'] / 'all_jobs.sqlite3').connect() as conn:
        assert conn.execute('SELECT 1 FROM jobs WHERE job_key=?', (second.identity_key(),)).fetchone() is None
    final = _publish_selected(setup, tmp_path, remaining, max_jobs=1)
    with setup['worker'].connect() as conn:
        work.acknowledge(conn, remaining, result_sha256='b' * 64,
                         processed_units=final['processed_units'], rejected=final['rejected'])
        assert work.vector(conn, contract_sha256='contract', now=time.time())['empty']
    with JobDatabase(setup['output'] / 'all_jobs.sqlite3').connect() as conn:
        assert conn.execute('SELECT description FROM jobs WHERE job_key=?', (second.identity_key(),)).fetchone()[0] == second.description


def test_listing_rejection_is_scoped_separately_from_details_and_health(setup):
    prepare(setup)
    with setup['worker'].connect() as conn:
        units = [('listing:a', 'a', None, 'listing'), ('listing:b', 'b', None, 'listing'),
                 ('health:a', 'a', None, 'health'), ('health:b', 'b', None, 'health'),
                 ('job:a:1', 'a', 'a:1', 'job'), ('job:b:1', 'b', 'b:1', 'job')]
        for unit, source, job, kind in units:
            work._mark(conn, unit, source, job, kind, 'fixture')
        rows = [dict(row) for row in conn.execute('SELECT * FROM jobagg_dirty_current')]
        work.acknowledge(conn, {'units': rows, 'held': []}, result_sha256='c' * 64,
                         processed_units=rows, rejected=[{'source_id': 'a'}, {'job_key': 'b:1'}])
        states = dict(conn.execute('SELECT unit,disposition FROM jobagg_dirty_current'))
        assert states == {unit: ('held' if unit in {'listing:a', 'job:b:1'} else 'acknowledged')
                          for unit, *_ in units}


def _listing(setup, source, attempt, observed, *, external_id='001'):
    root = setup['root'] / 'frames' / attempt
    frame = root / 'listing.json'
    job = {'source_id': source, 'org_id': source, 'ats_family': 'custom_html',
           'external_id': external_id, 'title': 'Listing ' + external_id,
           'apply_url': 'https://example.org/jobs/' + external_id}
    write(frame, {'source_id': source, 'observed_at': observed, 'jobs': [job]})
    http = root / 'http' / '1.json'
    body = http.with_suffix('.body.gz')
    body.parent.mkdir(exist_ok=True)
    payload = b'<html>Captured listing</html>'
    body.write_bytes(gzip.compress(payload))
    write(http, {'status_code': 200, 'body_captured': True, 'artifact': str(body),
                 'body_sha256': hashlib.sha256(payload).hexdigest(), 'body_bytes': len(payload),
                 'phase': {'kind': 'listing'}})
    proof = {'complete': False, 'method': 'unsupported', 'capture_paths': [], 'observed_count': 1}
    receipt = {'frame_path': str(frame), 'frame_sha256': hashlib.sha256(frame.read_bytes()).hexdigest(),
               'enumeration': proof}
    with setup['worker'].connect() as conn:
        conn.execute('CREATE TABLE IF NOT EXISTS remediation_sources(source_id TEXT PRIMARY KEY,last_list_at REAL,listing_ids TEXT,listing_proof TEXT)')
        conn.execute('INSERT INTO remediation_sources(source_id,last_list_at,listing_ids,listing_proof) VALUES(?,?,?,?) ON CONFLICT(source_id) DO UPDATE SET last_list_at=excluded.last_list_at,listing_ids=excluded.listing_ids,listing_proof=excluded.listing_proof',
                     (source, datetime.fromisoformat(observed).timestamp(), json.dumps([JobRecord(**job).identity_key()]), json.dumps(proof)))
        conn.execute('INSERT OR REPLACE INTO remediation_tasks VALUES(?,?,?,?,?,?,?)',
                     ('listing:' + source, source, 'listing', 'listing', 'done', json.dumps(receipt), '{}'))
        at = datetime.fromisoformat(observed).timestamp()
        conn.execute('INSERT INTO remediation_attempts VALUES(?,?,?,?,?,?,?,?)',
                     (attempt, 'listing:' + source, source, 'listing', at, at, 'done', json.dumps(receipt)))
    return receipt


@pytest.mark.parametrize('same_timestamp', [False, True])
def test_repeated_listing_resolves_exact_current_frame_through_projection(setup, tmp_path, same_timestamp):
    _listing(setup, 'test_custom_html', 'first', '2025-09-14T12:00:00+00:00')
    latest = _listing(setup, 'test_custom_html', 'second',
                      '2025-09-14T12:00:00+00:00' if same_timestamp else '2025-09-14T13:00:00+00:00', external_id='002')
    prepare(setup)
    with setup['worker'].connect() as conn:
        assert conn.execute('SELECT count(*) FROM jobagg_accepted_events').fetchone()[0] == 2
        selection = select(setup, conn)
        assert not selection['held']
        assert any(unit['kind'] == 'listing' for unit in selection['units'])
        reference = accepted_evidence.resolve_listing_event(conn, source_id='test_custom_html')
        assert reference['attempt_id'] == 'second' and reference['receipt'] == latest
    result = _publish_selected(setup, tmp_path, selection)
    assert result.get('published_listing_frames') == 1 and not result['rejected'], result
    with JobDatabase(setup['output'] / 'all_jobs.sqlite3').connect() as conn:
        assert conn.execute('SELECT frame_sha256 FROM live_listing_frames').fetchone()[0] == latest['frame_sha256']


def test_listing_cap_reports_only_processed_sources(setup):
    from jobagg.models import OrganizationSource
    from jobagg.pipelines.live_inventory import plan_frames
    sources, targets = {}, {}
    for index in range(6):
        source = f'source_{index}'
        _listing(setup, source, f'frame-{index}', f'2025-09-14T12:00:0{index}+00:00')
        sources[source] = OrganizationSource(id=source, name=source, ats_family='custom_html', base_url='https://example.org/jobs')
        target = JobDatabase(setup['output'] / f'{source}_jobs.sqlite3')
        target.initialize()
        targets[source] = target.path
    diagnostics = {}
    with setup['worker'].connect() as worker, JobDatabase(setup['output'] / 'all_jobs.sqlite3').connect() as dest:
        frames, rejected = plan_frames(worker, dest, sources, targets, limit=5, diagnostics=diagnostics)
    assert len(frames) == 5 and not rejected, rejected
    assert diagnostics['complete'] is False
    assert diagnostics['processed_source_ids'] == [f'source_{index}' for index in range(5)]


def test_missing_or_foreign_processing_evidence_cannot_clear_pending_versions(setup):
    add_detail(setup)
    prepare(setup)
    with setup['worker'].connect() as conn:
        selection = select(setup, conn)
        work.acknowledge(conn, selection, result_sha256='a' * 64)
        assert work.vector(conn, contract_sha256='contract', now=time.time())['pending']
        with pytest.raises(ValueError, match='processed_versions_not_selected'):
            work.acknowledge(conn, selection, result_sha256='b' * 64,
                             processed_units=[{'unit': selection['units'][0]['unit'], 'version': -1}])
        assert work.vector(conn, contract_sha256='contract', now=time.time())['pending']


def test_six_selected_listings_drain_across_two_real_generations(setup, tmp_path):
    from jobagg.models import OrganizationSource
    from jobagg.pipelines.bundles import source_output_slug

    sources = [f'source{index}_custom_html' for index in range(6)]
    registry = {'sources': []}
    paths = {}
    for index, source_id in enumerate(sources):
        _listing(setup, source_id, f'accepted-{index}', f'2025-09-14T12:00:0{index}+00:00')
        source = OrganizationSource(id=source_id, name=f'Source{index}', ats_family='custom_html',
                                    base_url='https://example.org/jobs', enabled=True)
        registry['sources'].append({'id': source.id, 'name': source.name, 'ats_family': source.ats_family,
                                    'base_url': source.base_url, 'enabled': True})
        path = setup['output'] / f'{source_output_slug(source)}_jobs.sqlite3'
        JobDatabase(path).initialize()
        paths[source_id] = path
    import yaml
    setup['registry'].write_text(yaml.safe_dump(registry))
    prepare(setup, expected_source_ids=sources)
    policy = dict(max_candidates=10, max_units=6, max_sources=6, max_targets=7,
                  max_bytes=1_000_000_000, minimum_seconds=0.01, measurement_sha256='a' * 64)
    def selection(conn):
        return work.select(conn, policy=policy, contract_sha256='contract', output_root=setup['output'],
                           source_paths=paths, deadline_at=time.monotonic() + 10)
    with setup['worker'].connect() as conn:
        admitted = selection(conn)
        assert len(admitted['units']) == 6 and not admitted['held'], json.dumps(admitted)
    first = _publish_selected(setup, tmp_path, admitted)
    assert first['published_listing_frames'] == 5
    assert len(first['processed_units']) == 5
    with setup['worker'].connect() as conn:
        work.acknowledge(conn, admitted, result_sha256='a' * 64,
                         processed_units=first['processed_units'], rejected=first['rejected'])
        remaining = selection(conn)
        assert len(remaining['units']) == 1
        assert remaining['units'][0]['source_id'] == sources[-1]
    with JobDatabase(setup['output'] / 'all_jobs.sqlite3').connect() as conn:
        assert conn.execute('SELECT count(*) FROM live_listing_frames').fetchone()[0] == 5
    final = _publish_selected(setup, tmp_path, remaining)
    assert final['published_listing_frames'] == 1
    with setup['worker'].connect() as conn:
        work.acknowledge(conn, remaining, result_sha256='b' * 64,
                         processed_units=final['processed_units'], rejected=final['rejected'])
        assert work.vector(conn, contract_sha256='contract', now=time.time())['empty']
