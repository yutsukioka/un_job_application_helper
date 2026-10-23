"""Publication budgets enforce elapsed AND absolute time across system suspend."""
from contextlib import contextmanager
from contextvars import ContextVar
import time
import math
from jobagg import runtime_clock

_wall_deadline = ContextVar('publication_wall_deadline', default=None)
_elapsed_deadline = ContextVar('publication_elapsed_deadline', default=None)
_continuous_budget = ContextVar('publication_continuous_budget', default=None)

@contextmanager
def wall_deadline(epoch):
    previous = _wall_deadline.get()
    effective = min(previous, epoch) if previous is not None and epoch is not None else (epoch if epoch is not None else previous)
    token = _wall_deadline.set(effective)
    try:
        yield
    finally:
        _wall_deadline.reset(token)

def wall_now():
    return time.time()

def expired(monotonic_deadline):
    continuous = _continuous_budget.get() or runtime_clock.inherited()
    if continuous is not None:
        try:
            if runtime_clock.remaining(continuous) <= 0:
                return True
        except ValueError:
            return True
    wall = _wall_deadline.get()
    elapsed = _elapsed_deadline.get()
    if elapsed is not None:
        monotonic_deadline = min(elapsed, monotonic_deadline) if monotonic_deadline is not None else elapsed
    return ((wall is not None and wall_now() >= wall) or
            (monotonic_deadline is not None and time.monotonic() >= monotonic_deadline))


@contextmanager
def entry_deadlines(monotonic_epoch, wall_epoch):
    parent = _continuous_budget.get() or runtime_clock.inherited()
    allowance = max(0, min(monotonic_epoch-time.monotonic(), wall_epoch-wall_now()))
    # An unbounded legacy verifier adds no clock; it must retain any finite
    # admitted task/entry budget rather than reject it or create a fresh one.
    continuous = parent if math.isinf(allowance) else runtime_clock.budget(allowance, parent=parent)
    continuous_token = _continuous_budget.set(continuous)
    previous = _elapsed_deadline.get()
    token = _elapsed_deadline.set(min(previous, monotonic_epoch) if previous is not None else monotonic_epoch)
    try:
        with wall_deadline(wall_epoch):
            yield
    finally:
        _elapsed_deadline.reset(token)
        _continuous_budget.reset(continuous_token)


def current_budget():
    return _continuous_budget.get() or runtime_clock.inherited()


@contextmanager
def continuous_budget(value):
    parent = current_budget()
    if value is None:
        value = parent
    elif parent is not None:
        # Both tokens are boot-validated; preserve the earlier absolute bound.
        runtime_clock.remaining(value)
        runtime_clock.remaining(parent)
        value = value if value['deadline'] <= parent['deadline'] else parent
    token = _continuous_budget.set(value)
    try:
        yield
    finally:
        _continuous_budget.reset(token)
