"""Publication budgets enforce elapsed AND absolute time across system suspend."""
from contextlib import contextmanager
from contextvars import ContextVar
import time

_wall_deadline = ContextVar('publication_wall_deadline', default=None)

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
    wall = _wall_deadline.get()
    return ((wall is not None and wall_now() >= wall) or
            (monotonic_deadline is not None and time.monotonic() >= monotonic_deadline))
