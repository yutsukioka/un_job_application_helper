"""Pure single-task selection; callers must own the shared writer lock.

All times are UTC epoch seconds. The dispatcher must persist service times and
eligibility after each attempt and reselect, never pre-dispatch a batch. Hosts
must be normalized actual request hosts, not organization or source aliases.
This module neither grants access permission nor certifies completed content.
"""

from dataclasses import dataclass
from collections.abc import Iterable, Mapping
from math import isfinite


def _validate_time(value: float) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("Time must be a finite nonnegative epoch value")
    if not isfinite(value) or value < 0:
        raise ValueError("Time must be a finite nonnegative epoch value")


@dataclass(frozen=True)
class Task:
    task_id: str
    source: str
    host: str
    discovered_at: float
    eligible_at: float = 0
    unchanged_failures: int = 0
    kind: str = ""
    last_attempt_at: float = 0


def select_due(
    tasks: Iterable[Task],
    *,
    now: float,
    host_eligible_at: Mapping[str, float] | None = None,
    host_last_served: Mapping[str, float] | None = None,
    source_last_served: Mapping[str, float] | None = None,
    kind_last_served: Mapping[str, float] | None = None,
) -> Task | None:
    """Choose one due task, alternating service before considering task age.

    Optional kind service times give listing, detail and document work equal
    opportunities before source fairness, even with many undiscovered sources.
    Host floors must include reviewed pacing, Retry-After and cooldown state.
    Two unchanged eligible failures require diagnosis before the caller may
    reset the counter. Skips must not advance counters or service timestamps.
    """
    floors = host_eligible_at or {}
    hosts = host_last_served or {}
    sources = source_last_served or {}
    kinds = kind_last_served or {}
    _validate_time(now)
    for mapping in (floors, hosts, sources, kinds):
        for value in mapping.values():
            _validate_time(value)
    tasks = tuple(tasks)
    for task in tasks:
        for value in (task.discovered_at, task.eligible_at, task.last_attempt_at):
            _validate_time(value)
        if type(task.unchanged_failures) is not int or task.unchanged_failures < 0:
            raise ValueError("Failure count must be a nonnegative integer")
    eligible = (
        task
        for task in tasks
        if task.unchanged_failures < 2
        and task.eligible_at <= now
        and floors.get(task.host, 0) <= now
    )
    return min(
        eligible,
        key=lambda task: (
            kinds.get(task.kind, float("-inf")),
            hosts.get(task.host, float("-inf")),
            sources.get(task.source, float("-inf")),
            task.last_attempt_at,
            task.discovered_at,
            task.task_id,
        ),
        default=None,
    )
