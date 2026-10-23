"""Bounded controller writes and child logs for an admitted dispatcher cycle.

This is cooperative admission, not a filesystem quota. Every byte written by
these helpers is charged before allocation; consumed grants survive failures.
"""

import json
import os
import selectors
import subprocess
import time
from contextlib import contextmanager
from pathlib import Path

from .storage_budget import StorageBudgetDeferred


@contextmanager
def charged_write(ledger, path, size):
    # Round allocation and include a conservative directory/temporary allowance.
    maximum = ((size + 4095) // 4096 + 4) * 4096
    operation = ledger.reserve(ledger.category_for(path), maximum, path=path)
    operation.consume(maximum)
    try:
        yield
    finally:
        # No refund for deleted or partly written temporaries.
        operation.settle()


def write_json(ledger, path, value):
    from .atomic_files import atomic_write_text
    atomic_write_text(path, json.dumps(value, indent=2) + "\n")


@contextmanager
def controller_scope(ledger):
    from .atomic_files import admitted_file_writes
    with admitted_file_writes(lambda path, size: charged_write(ledger, path, size)):
        yield


def wait_logged(process, paths, ledger, timeout, absolute_deadline=None, *, continuous_budget=None):
    """Drain both pipes without unbounded memory or unchecked child file writes."""
    end = time.monotonic() + timeout
    wall_end = min(time.time() + timeout, absolute_deadline or float("inf"))
    from jobagg import runtime_clock
    from jobagg.publication_deadline import current_budget
    continuous = runtime_clock.budget(timeout, parent=continuous_budget or current_budget())
    selector = selectors.DefaultSelector()
    streams = []
    try:
        for pipe, path in zip((process.stdout, process.stderr), paths):
            path = Path(path)
            with charged_write(ledger, path, 0):
                stream = path.open("wb")
            streams.append(stream)
            os.set_blocking(pipe.fileno(), False)
            selector.register(pipe, selectors.EVENT_READ, (stream, path))
        while selector.get_map():
            remaining = min(end - time.monotonic(), wall_end - time.time(), runtime_clock.remaining(continuous))
            if remaining <= 0:
                raise subprocess.TimeoutExpired(process.args, timeout)
            for key, _ in selector.select(min(0.25, remaining)):
                chunk = os.read(key.fileobj.fileno(), 65536)
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                stream, path = key.data
                with charged_write(ledger, path, len(chunk)):
                    stream.write(chunk)
                    stream.flush()
        process.wait(timeout=max(0.001, min(end-time.monotonic(), wall_end-time.time(), runtime_clock.remaining(continuous))))
    finally:
        selector.close()
        for stream in streams:
            stream.close()
        for pipe in (process.stdout, process.stderr):
            if pipe:
                pipe.close()


def require_admission(decision):
    if not decision.ready:
        raise StorageBudgetDeferred(";".join(decision.reasons))


def summary(context):
    decision = context["admission"]
    observed = decision.inventory
    return {"mode": decision.config.mode, "total_limit_bytes": 100_000_000_000,
            "usage_bytes": dict(observed.charged_bytes),
            "projected_growth_bytes": dict(decision.projected_growth),
            "offline_backup_paths": [str(path) for path in decision.config.offline_backup_paths],
            "offline_backups_in_active_total": False,
            "physical_reserve_bytes": decision.config.physical_reserve_bytes,
            "cycle_id": context["ledger"].cycle_id,
            "phase": context["ledger"].snapshot()["phase"]}
