"""Pure, conservative feedback for distinct-source/host worker concurrency.

The coordinator owns persistence and invokes this only after final publication
verification (or explicit failure). No files, clocks, requests, locks, or tasks
are touched here. Operational success is separate from content completeness.
Policy/schema bindings intentionally exclude unrelated implementation hashes.
"""
from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import asdict, dataclass, fields
import hashlib
import json
from math import isfinite

VERSION = "distinct-host-concurrency-v1"
HARD_CEILING = 32
HISTORY_LIMIT = 128


def _integer(value, name, minimum=0, maximum=10**9):
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError(f"{name} must be an integer in {minimum}..{maximum}")


def _time(value, name):
    if type(value) not in (int, float) or not isfinite(value) or value < 0:
        raise ValueError(f"{name} must be a finite nonnegative epoch")


def _identifier(value, name):
    if not isinstance(value, str) or not value.strip() or len(value) > 256:
        raise ValueError(f"{name} must be a nonempty bounded string")


@dataclass(frozen=True)
class ControllerPolicy:
    initial_limit: int = 4
    minimum_limit: int = 1
    maximum_limit: int = 8
    healthy_batches_to_increase: int = 3
    max_batch_age_seconds: int = 1800
    mode: str = "adaptive"
    fixed_limit: int | None = None
    transport_pressure_min_failures: int = 2

    def __post_init__(self):
        for name in ("initial_limit", "minimum_limit", "maximum_limit"):
            _integer(getattr(self, name), name, 1, HARD_CEILING)
        if not self.minimum_limit <= self.initial_limit <= self.maximum_limit:
            raise ValueError("Initial limit must lie within policy bounds")
        _integer(self.healthy_batches_to_increase, "healthy_batches_to_increase", 1, 100)
        _integer(self.max_batch_age_seconds, "max_batch_age_seconds", 1, 86400)
        _integer(self.transport_pressure_min_failures, "transport_pressure_min_failures", 1, 1000)
        if self.mode not in {"adaptive", "fixed"}:
            raise ValueError("Mode must be adaptive or fixed")
        if self.mode == "fixed":
            _integer(self.fixed_limit, "fixed_limit", self.minimum_limit, self.maximum_limit)
        elif self.fixed_limit is not None:
            raise ValueError("fixed_limit is only valid in fixed mode")

    @property
    def signature(self):
        return hashlib.sha256(json.dumps(asdict(self), sort_keys=True).encode()).hexdigest()


@dataclass(frozen=True)
class BatchObservation:
    batch_id: str
    started_at: float
    finished_at: float
    limit_used: int
    eligible_distinct_sources: int
    eligible_distinct_hosts: int
    peak_active_tasks: int
    peak_active_sources: int
    peak_active_hosts: int
    peak_active_scheduling_hosts: int
    attempted_tasks: int
    accepted_progress: int
    eligible_backlog_remaining: int
    new_access_blocks: int
    transport_failures: int
    runtime_errors: int
    integrity_errors: int
    database_errors: int
    control_cycle_complete: bool
    killed: bool
    publication_status: str
    existing_held_hosts: int = 0
    global_completeness_certified: bool = False

    def __post_init__(self):
        _identifier(self.batch_id, "batch_id")
        _time(self.started_at, "started_at")
        _time(self.finished_at, "finished_at")
        if self.finished_at < self.started_at:
            raise ValueError("Observation ends before it starts")
        _integer(self.limit_used, "limit_used", 1, HARD_CEILING)
        for name in (
            "eligible_distinct_sources", "eligible_distinct_hosts", "peak_active_tasks",
            "peak_active_sources", "peak_active_hosts", "peak_active_scheduling_hosts", "attempted_tasks", "accepted_progress",
            "eligible_backlog_remaining", "new_access_blocks", "transport_failures",
            "runtime_errors", "integrity_errors", "database_errors", "existing_held_hosts",
        ):
            _integer(getattr(self, name), name)
        for name in ("control_cycle_complete", "killed", "global_completeness_certified"):
            if type(getattr(self, name)) is not bool:
                raise ValueError(f"{name} must be a boolean")
        if self.publication_status not in {"verified", "failed", "pending", "not_run", "unknown"}:
            raise ValueError("Unknown publication_status")
        if not (self.accepted_progress <= self.attempted_tasks
                and self.peak_active_tasks <= self.attempted_tasks
                and self.peak_active_sources <= self.peak_active_tasks
                and self.peak_active_scheduling_hosts <= self.peak_active_tasks):
            raise ValueError("Inconsistent task progress or observed concurrency counts")

    @classmethod
    def from_mapping(cls, value):
        """Require real counters/flags; never manufacture absent healthy evidence."""
        if not isinstance(value, Mapping):
            raise ValueError("Observation must be an object")
        allowed = {field.name for field in fields(cls)}
        optional = {"existing_held_hosts", "global_completeness_certified"}
        missing, extra = allowed - optional - set(value), set(value) - allowed
        if missing or extra:
            raise ValueError(f"Observation fields differ: missing={sorted(missing)}, extra={sorted(extra)}")
        return cls(**dict(value))


_STATE_FIELDS = {
    "version", "policy", "policy_signature", "created_at", "updated_at", "limit",
    "healthy_streak", "processed_batches", "recent_batch_ids", "last_finished_at",
    "last_observation", "last_reasons",
}


def initial_state(policy: ControllerPolicy, *, now):
    _time(now, "now")
    return {
        "version": VERSION, "policy": asdict(policy), "policy_signature": policy.signature,
        "created_at": now, "updated_at": now,
        "limit": policy.fixed_limit if policy.mode == "fixed" else policy.initial_limit,
        "healthy_streak": 0, "processed_batches": 0, "recent_batch_ids": [],
        "last_finished_at": None, "last_observation": None, "last_reasons": ["initialized_without_health_credit"],
    }


def validate_state(state, policy: ControllerPolicy):
    if not isinstance(state, Mapping) or set(state) != _STATE_FIELDS:
        raise ValueError("Unknown or incomplete concurrency state schema")
    try:
        stored_policy = ControllerPolicy(**state["policy"])
    except (TypeError, ValueError) as exc:
        raise ValueError("Malformed stored controller policy") from exc
    if stored_policy != policy:
        raise ValueError("Controller policy differs; explicit reconfiguration required")
    if (state["version"] != VERSION or state["policy"] != asdict(policy)
            or state["policy_signature"] != policy.signature):
        raise ValueError("Controller version/policy differs; explicit reconfiguration required")
    _time(state["created_at"], "created_at")
    _time(state["updated_at"], "updated_at")
    if state["updated_at"] < state["created_at"]:
        raise ValueError("State clock moved backwards")
    _integer(state["limit"], "limit", policy.minimum_limit, policy.maximum_limit)
    _integer(state["healthy_streak"], "healthy_streak", 0, policy.healthy_batches_to_increase - 1)
    _integer(state["processed_batches"], "processed_batches")
    if policy.mode == "fixed" and (state["limit"] != policy.fixed_limit or state["healthy_streak"]):
        raise ValueError("Fixed-mode state differs from fixed setting")
    seen = state["recent_batch_ids"]
    if not isinstance(seen, list) or len(seen) > HISTORY_LIMIT:
        raise ValueError("Invalid bounded batch history")
    for identifier in seen:
        _identifier(identifier, "historical batch ID")
    if len(set(seen)) != len(seen) or len(seen) != min(state["processed_batches"], HISTORY_LIMIT):
        raise ValueError("Duplicate/inconsistent batch history")
    if state["processed_batches"] == 0:
        if state["last_finished_at"] is not None or state["last_observation"] is not None:
            raise ValueError("Unprocessed state has observation history")
    else:
        _time(state["last_finished_at"], "last_finished_at")
        observation = BatchObservation.from_mapping(state["last_observation"])
        if (observation.batch_id != seen[-1] or observation.finished_at != state["last_finished_at"]
                or not state["created_at"] <= observation.started_at <= observation.finished_at <= state["updated_at"]):
            raise ValueError("Last observation/history/time binding differs")
    if state["healthy_streak"] > state["processed_batches"]:
        raise ValueError("Health credit has no processed batch history")
    if state["healthy_streak"]:
        last = BatchObservation.from_mapping(state["last_observation"])
        if (last.publication_status != "verified" or not last.control_cycle_complete
                or last.killed or last.new_access_blocks or last.transport_failures
                or last.runtime_errors or last.integrity_errors or last.database_errors
                or last.limit_used != state["limit"]
                or min(last.peak_active_tasks, last.peak_active_sources, last.peak_active_scheduling_hosts) < state["limit"]
                or last.peak_active_tasks > last.limit_used
                or last.accepted_progress < state["limit"] or not last.eligible_backlog_remaining
                or min(last.eligible_distinct_sources, last.eligible_distinct_hosts) <= state["limit"]
                or state["updated_at"] - last.finished_at > policy.max_batch_age_seconds):
            raise ValueError("Health credit contradicts last observed operational evidence")
    if not isinstance(state["last_reasons"], list) or not 1 <= len(state["last_reasons"]) <= 30:
        raise ValueError("Invalid decision reasons")
    for reason in state["last_reasons"]:
        _identifier(reason, "reason")


def _current(state, policy, now):
    validate_state(state, policy)
    _time(now, "now")
    if now < state["updated_at"]:
        raise ValueError("Decision clock precedes persisted state")
    return deepcopy(dict(state))


def _decision(state, old_limit, reasons, *, qualifying=False, consumed=False, observation=None):
    state["last_reasons"] = reasons
    return {
        "state": state, "limit": state["limit"], "previous_limit": old_limit,
        "action": "increase" if state["limit"] > old_limit else "decrease" if state["limit"] < old_limit else "hold",
        "reasons": reasons, "qualifying": qualifying, "consumed": consumed,
        "observation": asdict(observation) if observation is not None else None,
        "global_completeness_certified": False,
    }


def interrupt(state, policy: ControllerPolicy, *, now, reason):
    """Invalidate the streak after an unfinished dispatch/publish/crash marker.

    Unknown results are not invented as host pressure. Existing access/quota
    guards still apply; the limit is held and three new healthy batches needed.
    """
    result = _current(state, policy, now)
    _identifier(reason, "interrupt reason")
    result["healthy_streak"] = 0
    result["updated_at"] = now
    return _decision(result, state["limit"], ["control_cycle_interrupted", reason])


def reconfigure_state(state, policy: ControllerPolicy, *, now, requested_limit=None):
    """Explicit policy change: preserve replay history, clamp limits, reset credit.

    requested_limit is an explicit operator action, never an automatic reset;
    all processed-batch history survives and subsequent backoff remains active.
    Unrelated deploy/code hashes are not policy inputs and require no migration.
    A new algorithm version needs its own reviewed migration, never a reset.
    """
    if not isinstance(state, Mapping) or not isinstance(state.get("policy"), dict):
        raise ValueError("Cannot reconfigure invalid state")
    try:
        old_policy = ControllerPolicy(**state["policy"])
    except TypeError as exc:
        raise ValueError("Invalid stored policy") from exc
    result = _current(state, old_policy, now)
    if requested_limit is not None:
        _integer(requested_limit, "requested_limit", policy.minimum_limit, policy.maximum_limit)
        if policy.mode != "adaptive":
            raise ValueError("Operator limit requires adaptive mode")
    if old_policy == policy and requested_limit is None:
        return result
    result.update(policy=asdict(policy), policy_signature=policy.signature, updated_at=now,
                  healthy_streak=0, last_reasons=["explicit_policy_change_health_credit_reset"])
    result["limit"] = (policy.fixed_limit if policy.mode == "fixed" else
                       min(policy.maximum_limit, max(policy.minimum_limit, result["limit"])))
    if requested_limit is not None:
        result["limit"] = requested_limit
        result["last_reasons"].append("explicit_operator_limit_change")
    validate_state(result, policy)
    return result


def decide(state, observation, policy: ControllerPolicy, *, now):
    """Consume one terminal observation, returning state for atomic persistence.

    Actual HTTP-host overlap is reported but not required: short requests may
    finish between admissions while distinct organization tasks truly overlap.
    Fresh errors can lower concurrency even when publication failed. Only three
    consecutive verified, error-free, saturated, backlogged batches can raise it.
    Existing held hosts and content-completeness flags are never failure signals.
    """
    result = _current(state, policy, now)
    old_limit = state["limit"]
    try:
        observed = BatchObservation.from_mapping(
            asdict(observation) if isinstance(observation, BatchObservation) else observation
        )
    except (ValueError, TypeError):
        return interrupt(state, policy, now=now, reason="invalid_or_missing_batch_metrics")
    if observed.batch_id in state["recent_batch_ids"]:
        # Return the exact persisted state: repeating a terminal callback is idempotent.
        return {"state": deepcopy(dict(state)), "limit": old_limit, "previous_limit": old_limit,
                "action": "hold", "reasons": ["duplicate_batch_ignored"], "qualifying": False,
                "consumed": False, "observation": asdict(observed), "global_completeness_certified": False}
    if (observed.finished_at > now or observed.started_at < state["created_at"]
            or (state["last_finished_at"] is not None
                and (observed.finished_at <= state["last_finished_at"]
                     or observed.started_at < state["last_finished_at"]))):
        return interrupt(state, policy, now=now, reason="future_overlapping_or_out_of_order_batch")
    result.update(updated_at=now, processed_batches=state["processed_batches"] + 1,
                  recent_batch_ids=(state["recent_batch_ids"] + [observed.batch_id])[-HISTORY_LIMIT:],
                  last_finished_at=observed.finished_at, last_observation=asdict(observed), healthy_streak=0)
    if now - observed.finished_at > policy.max_batch_age_seconds:
        return _decision(result, old_limit, ["stale_batch_no_health_or_pressure_credit"], consumed=True, observation=observed)
    reasons = []
    pressure = []
    if observed.new_access_blocks:
        pressure.append("new_access_block")
    if observed.transport_failures >= policy.transport_pressure_min_failures:
        pressure.append("transport_pressure")
    if observed.runtime_errors or observed.integrity_errors or observed.database_errors:
        pressure.append("runtime_integrity_or_database_error")
    if observed.killed:
        pressure.append("killed_control_cycle")
    if observed.peak_active_tasks > observed.limit_used:
        pressure.append("observed_concurrency_exceeded_selected_limit")
    if policy.mode == "fixed":
        return _decision(result, old_limit, ["manual_fixed_limit"] + pressure,
                         consumed=True, observation=observed)
    if pressure:
        result["limit"] = max(policy.minimum_limit, old_limit - 1)
        return _decision(result, old_limit, pressure + ["conservative_one_step_backoff"],
                         consumed=True, observation=observed)
    if observed.publication_status != "verified":
        reasons.append("publication_not_verified")
    if not observed.control_cycle_complete:
        reasons.append("control_cycle_incomplete")
    if observed.transport_failures:
        reasons.append("transport_failure_health_credit_reset")
    if observed.limit_used != old_limit:
        reasons.append("observed_limit_differs_from_selected_limit")
    if observed.started_at < state["updated_at"]:
        reasons.append("batch_started_before_latest_control_update")
    if min(observed.peak_active_tasks, observed.peak_active_sources, observed.peak_active_scheduling_hosts) < old_limit:
        reasons.append("observed_distinct_source_host_concurrency_not_saturated")
    if observed.accepted_progress < old_limit:
        reasons.append("insufficient_accepted_task_progress")
    if (not observed.eligible_backlog_remaining or
            min(observed.eligible_distinct_sources, observed.eligible_distinct_hosts) <= old_limit):
        reasons.append("insufficient_distinct_source_host_backlog_to_grow")
    if reasons:
        return _decision(result, old_limit, reasons, consumed=True, observation=observed)
    if old_limit >= policy.maximum_limit:
        return _decision(result, old_limit, ["configured_ceiling_reached"], consumed=True, observation=observed)
    previous_credit = state["healthy_streak"]
    expired_credit = bool(previous_credit and state["last_finished_at"] is not None
                          and now - state["last_finished_at"] > policy.max_batch_age_seconds)
    result["healthy_streak"] = (0 if expired_credit else previous_credit) + 1
    if result["healthy_streak"] == policy.healthy_batches_to_increase:
        result["limit"] = old_limit + 1
        result["healthy_streak"] = 0
        reasons = ["required_healthy_batches_reached", "increase_one"]
    else:
        reasons = ["healthy_backlogged_parallel_batch", "await_additional_consecutive_health"]
    if expired_credit:
        reasons.append("previous_health_credit_expired")
    return _decision(result, old_limit, reasons, qualifying=True, consumed=True, observation=observed)
