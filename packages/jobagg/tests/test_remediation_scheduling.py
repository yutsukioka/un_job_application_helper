from jobagg.remediation_scheduling import Task, select_due
import pytest


@pytest.mark.parametrize('value', [float('nan'), float('inf'), -1])
def test_invalid_clock_is_rejected(value):
    with pytest.raises(ValueError):
        select_due([Task('a', 's', 'h', 0)], now=value)


def test_negative_failure_counter_is_rejected():
    with pytest.raises(ValueError):
        select_due([Task('a', 's', 'h', 0, unchanged_failures=-1)], now=10)


def test_invalid_host_floor_is_rejected_even_without_tasks():
    with pytest.raises(ValueError):
        select_due([], now=10, host_eligible_at={'h': float('nan')})


def test_host_floor_cannot_be_evaded_by_another_source():
    tasks = [Task('a', 'one', 'shared', 0), Task('b', 'two', 'shared', 0)]
    assert select_due(tasks, now=10, host_eligible_at={'shared': 11}) is None
    assert select_due(tasks, now=11, host_eligible_at={'shared': 11}).task_id == 'a'


def test_blocked_host_does_not_block_independent_work():
    tasks = [Task('a', 'one', 'blocked', 0), Task('b', 'two', 'free', 2)]
    assert select_due(tasks, now=10, host_eligible_at={'blocked': 20}).task_id == 'b'


def test_least_recently_served_host_then_source_wins():
    tasks = [Task('a', 'one', 'h1', 0), Task('b', 'two', 'h2', 2),
             Task('c', 'three', 'h2', 3)]
    result = select_due(tasks, now=10, host_last_served={'h1': 9, 'h2': 5},
                        source_last_served={'two': 8, 'three': 4})
    assert result.task_id == 'c'


def test_repeated_failure_requires_diagnosis_and_future_work_waits():
    tasks = [Task('a', 'one', 'h', 0, unchanged_failures=2),
             Task('b', 'two', 'h', 0, eligible_at=11)]
    assert select_due(tasks, now=10) is None


def test_oldest_task_wins_within_service_share():
    assert select_due([Task('new', 's', 'h', 3), Task('old', 's', 'h', 1)],
                      now=10).task_id == 'old'
