"""Excluding supplementary documents preserves posting text and stored history."""
import json
import sqlite3
import yaml
from test_remediation_worker import setup as worker_setup
from test_live_publication import setup as publication_setup, add_detail, add_document, add_frame, run, body
from jobagg.fetch_coverage import census


def exclude(registry):
    data = yaml.safe_load(registry.read_text())
    for source in data['sources']:
        source.setdefault('extra', {})['fetch_attachments'] = False
    registry.write_text(yaml.safe_dump(data))


def test_worker_skips_discovery_and_pending_documents(worker_setup, monkeypatch):
    worker, replies, calls, _ = worker_setup
    worker.by_id['demo_workday'].extra['fetch_attachments'] = False
    def unexpected(*args, **kwargs):
        raise AssertionError('Attachment discovery must not run')
    monkeypatch.setattr('jobagg.pipelines.document_tasks.discover_document_inventory', unexpected)
    worker.tick(execute=True)
    assert len(calls) == 2
    row = worker.db.get_job('demo_workday:R1')
    assert 'Qualifications' in row['description']
    assert row['raw']['attachment_verification']['excluded_by_scope']
    with worker.db.connect() as conn:
        assert worker.enqueue_document(conn, 'demo_workday', {}) is None
        worker.enqueue(conn, 'demo_workday', 'document', 'old', {'url': 'https://demo.example/old.pdf'})
    assert worker.choose() is None


def test_publication_retains_stored_documents_but_skips_reconciliation(publication_setup, monkeypatch):
    f = publication_setup
    record, proof = add_detail(f)
    add_document(f, record, proof)
    add_frame(f, external_ids=('001',))
    assert run(f, execute=True)['status'] == 'published'
    paths = [f['output'] / name for name in ('all_jobs.sqlite3', 'test_jobs.sqlite3')]
    def documents(path):
        with sqlite3.connect(path) as c:
            return [c.execute('SELECT * FROM '+table).fetchall() for table in ('attachment_blobs','job_attachments')]
    before = [documents(p) for p in paths]
    exclude(f['registry'])
    add_detail(f, description=body('Updated full posting'), observed='2025-09-15T12:00:00+00:00')
    def unexpected(*args, **kwargs):
        raise AssertionError('Attachment reconciliation must not run')
    monkeypatch.setattr('jobagg.pipelines.live_publication._documents', unexpected)
    assert run(f, execute=True)['status'] == 'published'
    assert [documents(p) for p in paths] == before
    for path in paths:
        with sqlite3.connect(path) as c:
            description, raw = c.execute('SELECT description,raw_json FROM jobs').fetchone()
            assert description == body('Updated full posting')
            assert json.loads(raw)['attachment_verification']['excluded_by_scope']
    result = census(f['registry'], f['worker'].path, paths[0])
    source = result['enabled_sources'][0]
    assert source['attachments_in_scope'] is False
    assert source['retained_document_task_counts']
    assert result['totals']['current_document_done_tasks'] == 0
    assert source['document_reconciliation'] == []
    assert not any('document' in gap['gap'] for gap in source['gaps'])
    assert result['completeness_certified'] is False
