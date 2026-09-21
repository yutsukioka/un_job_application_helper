"""Pure controller evidence tests; no worker, policy-store or network mutations."""
from dataclasses import asdict
from copy import deepcopy
import json

import pytest

from jobagg.remediation_concurrency import (
    BatchObservation, ControllerPolicy, HISTORY_LIMIT, decide, initial_state,
    interrupt, reconfigure_state, validate_state,
)

POLICY = ControllerPolicy()


def observed(state, index, **changes):
    start = state['updated_at'] + 1
    limit = state['limit']
    values = dict(
        batch_id=f'batch-{index}', started_at=start, finished_at=start + 5,
        limit_used=limit, eligible_distinct_sources=20, eligible_distinct_hosts=20,
        peak_active_tasks=limit, peak_active_sources=limit, peak_active_hosts=limit,
        peak_active_scheduling_hosts=limit,
        attempted_tasks=limit, accepted_progress=limit, eligible_backlog_remaining=50,
        new_access_blocks=0, transport_failures=0, runtime_errors=0,
        integrity_errors=0, database_errors=0, control_cycle_complete=True, killed=False,
        publication_status='verified', global_completeness_certified=False,
    )
    values.update(changes)
    return BatchObservation(**values)


def step(state, index, policy=POLICY, **changes):
    batch = observed(state, index, **changes)
    decision = decide(state, batch, policy, now=batch.finished_at)
    validate_state(decision['state'], policy)
    return decision


def credit_two(policy=POLICY):
    state = initial_state(policy, now=100)
    for index in range(2):
        state = step(state, index, policy)['state']
    assert state['healthy_streak'] == 2
    return state


def test_actual_repeated_growth_reaches_five_six_seven_eight():
    state = initial_state(POLICY, now=100)
    limits = []
    for index in range(16):
        previous = deepcopy(state)
        result = step(state, index)
        assert state == previous  # pure, detached output
        state = result['state']
        limits.append(state['limit'])
        assert result['global_completeness_certified'] is False
    assert limits == [4, 4, 5, 5, 5, 6, 6, 6, 7, 7, 7, 8, 8, 8, 8, 8]
    assert state['healthy_streak'] == 0
    assert json.loads(json.dumps(state)) == state


@pytest.mark.parametrize('changes,reason', [
    ({'peak_active_tasks': 3, 'peak_active_sources': 3, 'peak_active_scheduling_hosts': 3}, 'not_saturated'),
    ({'peak_active_sources': 3}, 'not_saturated'),
    ({'peak_active_scheduling_hosts': 2}, 'not_saturated'),
    ({'eligible_distinct_sources': 4}, 'backlog_to_grow'),
    ({'eligible_distinct_hosts': 4}, 'backlog_to_grow'),
    ({'eligible_backlog_remaining': 0}, 'backlog_to_grow'),
    ({'accepted_progress': 0}, 'accepted_task_progress'),
    ({'accepted_progress': 3}, 'accepted_task_progress'),
    ({'transport_failures': 1}, 'transport_failure'),
    ({'control_cycle_complete': False}, 'control_cycle_incomplete'),
    ({'publication_status': 'failed'}, 'publication_not_verified'),
    ({'publication_status': 'pending'}, 'publication_not_verified'),
    ({'publication_status': 'not_run'}, 'publication_not_verified'),
    ({'publication_status': 'unknown'}, 'publication_not_verified'),
    ({'limit_used': 5}, 'observed_limit_differs'),
])
def test_unqualified_cycle_clears_prior_credit(changes, reason):
    state = credit_two()
    decision = step(state, 'bad', **changes)
    assert decision['limit'] == 4 and decision['state']['healthy_streak'] == 0
    assert not decision['qualifying']
    assert any(reason in text for text in decision['reasons'])
    later = step(decision['state'], 'later')
    assert later['limit'] == 4 and later['state']['healthy_streak'] == 1


def test_held_sites_and_content_incompleteness_are_not_operational_failures():
    state = initial_state(POLICY, now=0)
    for index in range(3):
        result = step(state, index, existing_held_hosts=999, global_completeness_certified=False)
        state = result['state']
    assert result['limit'] == 5


@pytest.mark.parametrize('changes', [
    {'new_access_blocks': 1}, {'transport_failures': 2}, {'runtime_errors': 1},
    {'integrity_errors': 1}, {'database_errors': 1}, {'killed': True},
    {'peak_active_tasks': 5, 'attempted_tasks': 5},
])
def test_new_pressure_conservatively_backs_off_even_when_publication_failed(changes):
    state = credit_two()
    decision = step(state, 'pressure', publication_status='failed', **changes)
    assert decision['limit'] == 3 and decision['state']['healthy_streak'] == 0
    assert decision['action'] == 'decrease' and not decision['qualifying']
    assert decision['consumed']


def test_lower_bound_and_old_pressure_is_not_applied_again():
    state = initial_state(POLICY, now=0)
    for i in range(10):
        decision = step(state, i, new_access_blocks=1)
        state = decision['state']
    assert state['limit'] == 1
    duplicate = decide(state, BatchObservation.from_mapping(state['last_observation']), POLICY, now=state['updated_at']+1)
    assert duplicate['state'] == state and not duplicate['consumed']


def test_duplicate_terminal_feedback_is_exactly_idempotent():
    state = credit_two()
    batch = observed(state, 'once')
    result = decide(state, batch, POLICY, now=batch.finished_at)
    assert result['limit'] == 5
    state = result['state']
    repeated = decide(state, asdict(batch), POLICY, now=batch.finished_at + 10)
    assert repeated['state'] == state and repeated['limit'] == 5
    assert not repeated['qualifying'] and not repeated['consumed']


def test_stale_event_resets_credit_without_replaying_old_site_pressure():
    state = credit_two()
    batch = observed(state, 'stale', new_access_blocks=1)
    result = decide(state, batch, POLICY, now=batch.finished_at + 1801)
    assert result['limit'] == 4 and result['state']['healthy_streak'] == 0
    assert result['reasons'] == ['stale_batch_no_health_or_pressure_credit']
    validate_state(result['state'], POLICY)


def test_bounded_history_and_old_receipts_cannot_reenter_after_id_eviction():
    policy = ControllerPolicy(mode='fixed', fixed_limit=4)
    state = initial_state(policy, now=0)
    first = observed(state, 0)
    for i in range(HISTORY_LIMIT + 2):
        state = step(state, i, policy)['state']
    assert len(state['recent_batch_ids']) == HISTORY_LIMIT and first.batch_id not in state['recent_batch_ids']
    result = decide(state, first, policy, now=state['updated_at'] + 1)
    assert not result['consumed'] and result['limit'] == 4
    assert 'future_overlapping_or_out_of_order_batch' in result['reasons']


def test_crash_marker_interrupt_resets_credit_and_late_old_callback_cannot_ramp():
    state = credit_two()
    old = observed(state, 'old-unfinished')
    result = interrupt(state, POLICY, now=old.finished_at + 1, reason='previous_inflight_marker')
    interrupted = result['state']
    assert interrupted['healthy_streak'] == 0 and interrupted['processed_batches'] == 2
    delayed = decide(interrupted, old, POLICY, now=old.finished_at + 2)
    assert delayed['state']['healthy_streak'] == 0 and not delayed['qualifying']
    assert 'batch_started_before_latest_control_update' in delayed['reasons']
    validate_state(delayed['state'], POLICY)
    next_state = interrupted
    for i in range(3):
        next_state = step(next_state, f'new-{i}')['state']
    assert next_state['limit'] == 5


@pytest.mark.parametrize('kind', ['missing', 'bool_count', 'string_flag', 'future', 'overlap'])
def test_missing_invalid_or_bad_chronology_never_manufactures_metrics(kind):
    state = credit_two()
    batch = asdict(observed(state, 'bad'))
    if kind == 'missing':
        del batch['peak_active_hosts']
    elif kind == 'bool_count':
        batch['accepted_progress'] = True
    elif kind == 'string_flag':
        batch['control_cycle_complete'] = 'true'
    elif kind == 'future':
        batch['finished_at'] += 50
    else:
        batch['started_at'] = state['last_finished_at'] - 1
    result = decide(state, batch, POLICY, now=state['updated_at'] + 10)
    assert result['limit'] == 4 and result['state']['healthy_streak'] == 0
    assert not result['qualifying'] and not result['consumed']
    validate_state(result['state'], POLICY)


def test_manual_fixed_mode_never_adapts_or_bypasses_site_guards():
    policy = ControllerPolicy(mode='fixed', fixed_limit=6)
    state = initial_state(policy, now=0)
    for i in range(5):
        result = step(state, i, policy, new_access_blocks=2)
        state = result['state']
    assert state['limit'] == 6 and state['healthy_streak'] == 0
    assert 'manual_fixed_limit' in result['reasons'] and 'new_access_block' in result['reasons']


def test_policy_binding_and_explicit_reconfiguration_preserve_replay_history():
    state = credit_two()
    assert reconfigure_state(state, POLICY, now=state['updated_at']+1) == state
    changed = ControllerPolicy(maximum_limit=6)
    with pytest.raises(ValueError, match='policy'):
        validate_state(state, changed)
    current = reconfigure_state(state, changed, now=state['updated_at']+1)
    assert current['limit'] == 4 and current['healthy_streak'] == 0
    assert current['recent_batch_ids'] == state['recent_batch_ids']
    fixed = ControllerPolicy(mode='fixed', fixed_limit=2)
    current = reconfigure_state(current, fixed, now=current['updated_at']+1)
    assert current['limit'] == 2 and current['processed_batches'] == 2
    validate_state(current, fixed)


@pytest.mark.parametrize('mutation', [
    {'limit': 9}, {'limit': True}, {'healthy_streak': 3}, {'version': 'unknown'},
    {'policy_signature': 'wrong'}, {'processed_batches': 0}, {'recent_batch_ids': ['x', 'x']},
    {'last_finished_at': -1}, {'updated_at': float('nan')}, {'last_observation': {}},
])
def test_corrupt_state_cannot_dispatch_or_silently_reset(mutation):
    state = credit_two()
    state.update(mutation)
    with pytest.raises(ValueError):
        validate_state(state, POLICY)
    with pytest.raises(ValueError):
        interrupt(state, POLICY, now=1000, reason='do not reset corrupted state')


@pytest.mark.parametrize('kwargs', [
    {'maximum_limit': 33}, {'maximum_limit': 3}, {'minimum_limit': 0},
    {'healthy_batches_to_increase': 0}, {'max_batch_age_seconds': 0},
    {'mode': 'fixed', 'fixed_limit': 9}, {'mode': 'fixed'}, {'fixed_limit': 4},
    {'initial_limit': True}, {'transport_pressure_min_failures': 0},
])
def test_invalid_bounds_and_manual_configuration_fail(kwargs):
    with pytest.raises(ValueError):
        ControllerPolicy(**kwargs)


def test_observation_strict_parsing_and_counter_consistency():
    state = initial_state(POLICY, now=0)
    batch = asdict(observed(state, 0))
    assert BatchObservation.from_mapping(batch) == observed(state, 0)
    for updates in ({'accepted_progress': 100}, {'peak_active_scheduling_hosts': 5},
                    {'started_at': float('inf')}, {'unexpected': 'not trusted'}):
        with pytest.raises(ValueError):
            BatchObservation.from_mapping({**batch, **updates})
    with pytest.raises(ValueError):
        decide(state, batch, POLICY, now=-1)


def test_no_health_credit_can_be_inserted_without_observed_history():
    state = initial_state(POLICY, now=0)
    state['healthy_streak'] = 2
    with pytest.raises(ValueError, match='processed batch history'):
        validate_state(state, POLICY)
    state = credit_two()
    state['last_observation']['publication_status'] = 'failed'
    with pytest.raises(ValueError, match='contradicts'):
        validate_state(state, POLICY)


def test_policy_boolean_does_not_alias_integer_through_python_equality():
    state = initial_state(POLICY, now=0)
    state['policy']['minimum_limit'] = True
    with pytest.raises(ValueError, match='Malformed'):
        validate_state(state, POLICY)


def test_health_streak_expires_across_long_idle_gap():
    state = credit_two()
    batch = observed(state, 'after-idle', started_at=state['updated_at']+2000,
                     finished_at=state['updated_at']+2005)
    result = decide(state, batch, POLICY, now=batch.finished_at)
    assert result['limit'] == 4 and result['state']['healthy_streak'] == 1
    assert 'previous_health_credit_expired' in result['reasons']
    validate_state(result['state'], POLICY)


def test_genuine_parallel_org_tasks_ramp_even_when_http_requests_finish_quickly():
    state = initial_state(POLICY, now=0)
    for i in range(6):
        result = step(state, i, peak_active_hosts=1)
        state = result['state']
    assert state['limit'] == 6
    assert state['last_observation']['peak_active_hosts'] == 1
    assert state['last_observation']['peak_active_scheduling_hosts'] == 5


def test_distinct_source_names_never_substitute_for_observed_scheduling_hosts():
    state = credit_two()
    result = step(state, 'shared-hosts', peak_active_hosts=1, peak_active_scheduling_hosts=2)
    assert result['limit'] == 4 and result['state']['healthy_streak'] == 0
    assert 'observed_distinct_source_host_concurrency_not_saturated' in result['reasons']


def test_operator_increase_preserves_history_and_adaptive_backoff():
    state = credit_two()
    policy = ControllerPolicy(initial_limit=16, maximum_limit=32, healthy_batches_to_increase=2)
    migrated = reconfigure_state(state, policy, now=state['updated_at']+1, requested_limit=16)
    assert migrated['limit'] == 16 and migrated['healthy_streak'] == 0
    for field in ('created_at', 'processed_batches', 'recent_batch_ids', 'last_observation'):
        assert migrated[field] == state[field]
    pressured = step(migrated, 3, policy, new_access_blocks=1)['state']
    assert pressured['limit'] == 15
    growing = step(pressured, 4, policy)['state']
    growing = step(growing, 5, policy)['state']
    assert growing['limit'] == 16
    with pytest.raises(ValueError):
        reconfigure_state(growing, policy, now=growing['updated_at']+1, requested_limit=33)


def test_growth_stops_at_32_and_requires_distinct_hosts():
    policy = ControllerPolicy(initial_limit=31, maximum_limit=32, healthy_batches_to_increase=1)
    state = initial_state(policy, now=100)
    no_growth = step(state, 1, policy, eligible_distinct_sources=49, eligible_distinct_hosts=31)['state']
    assert no_growth['limit'] == 31
    at_limit = step(no_growth, 2, policy, eligible_distinct_sources=49, eligible_distinct_hosts=40)['state']
    assert at_limit['limit'] == 32
    assert step(at_limit, 3, policy, eligible_distinct_sources=49, eligible_distinct_hosts=40)['state']['limit'] == 32
