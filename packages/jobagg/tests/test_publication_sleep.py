from jobagg.publication_deadline import expired, wall_deadline
from jobagg.pipelines import live_publication as publication
from test_live_publication import setup, add_detail


def test_sleep_after_preparation_cannot_open_gate(setup, monkeypatch):
    import jobagg.publication_deadline as clocks
    wall = [100.0]
    monkeypatch.setattr(clocks, 'wall_now', lambda: wall[0])
    add_detail(setup)
    original = publication._prepare
    def suspended(*args, **kwargs):
        result = original(*args, **kwargs)
        wall[0] = 400.0  # wall time advances; elapsed budget remains available
        return result
    monkeypatch.setattr(publication, '_prepare', suspended)
    with wall_deadline(200.0):
        result = publication.publish_incremental(setup['worker'].path, setup['registry'], setup['output'], setup['state'], execute=True, deadline_at=clocks.time.monotonic()+1000)
    assert result['reason'] == 'deadline_after_preparation_before_gate'
    assert result['generation_not_started'] is True
    assert not (setup['output']/publication.GATE_NAME).exists()


def test_absolute_and_elapsed_budget_are_both_enforced(monkeypatch):
    import jobagg.publication_deadline as clocks
    monkeypatch.setattr(clocks, 'wall_now', lambda: 500)
    monkeypatch.setattr(clocks.time, 'monotonic', lambda: 10)
    with wall_deadline(400):
        assert expired(100)
    with wall_deadline(600):
        assert not expired(100)
        assert expired(5)
    assert not expired(100)


import json
import pytest

@pytest.mark.parametrize('stage', ['after_database_commit', 'after_export_verified_checkpoint'])
def test_sleep_during_commit_or_export_resumes_same_generation(setup, monkeypatch, stage):
    import jobagg.publication_deadline as clocks
    add_detail(setup)
    wall = [100.0]
    monkeypatch.setattr(clocks, 'wall_now', lambda: wall[0])
    def suspend(at, key):
        if at == stage:
            wall[0] = 400.0
    args = (setup['worker'].path, setup['registry'], setup['output'], setup['state'])
    with wall_deadline(200):
        first = publication.publish_incremental(*args, execute=True, fault=suspend, request_identity='a'*64)
    assert first['status'] == 'publication_pending'
    gate = json.loads((setup['output']/publication.GATE_NAME).read_text())
    assert gate['request_identity_sha256'] == 'a'*64
    with wall_deadline(500):
        result = publication.publish_incremental(*args, execute=True)
    assert result['status'] == 'published'
    assert result['generation_id'] == gate['generation_id']


def test_selected_publication_does_not_touch_other_jobs_or_inventory(setup):
    add_detail(setup)
    result = publication.publish_incremental(setup['worker'].path,setup['registry'],setup['output'],setup['state'],job_keys={'unknown:missing'})
    assert result['plan']['changes']==[] and result['plan']['listing_frames']==[]
    assert result['plan']['selected_job_keys']==['unknown:missing']
