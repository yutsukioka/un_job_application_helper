#!/usr/bin/env python3
"""Entry watchdog covering runner imports/configuration and blocking preparation.

No schedule installation. Use only as a separately reviewed deployment entry.
A timeout is unknown/failed work; retained owner locks and journals govern repair.
"""
import argparse
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time


def main(argv=None):
    entered_mono, entered_wall = time.monotonic(), time.time()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--entry-timeout-seconds', type=float, default=840)
    parser.add_argument('runner_args', nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    if not 0 < args.entry_timeout_seconds <= 840:
        parser.error('entry timeout must be within the 840-second objective')
    forwarded = args.runner_args[1:] if args.runner_args[:1] == ['--'] else args.runner_args
    process = subprocess.Popen([sys.executable, str(Path(__file__).with_name('runner.py')), *forwarded],
        env={**os.environ, 'JOBAGG_ENTRY_MONOTONIC_DEADLINE': str(entered_mono+args.entry_timeout_seconds),
             'JOBAGG_ENTRY_WALL_DEADLINE': str(entered_wall+args.entry_timeout_seconds)}, start_new_session=True)
    while process.poll() is None:
        remaining = min(entered_mono+args.entry_timeout_seconds-time.monotonic(),
                        entered_wall+args.entry_timeout_seconds-time.time())
        if remaining <= 0:
            try:
                os.killpg(process.pid, signal.SIGTERM)
                process.wait(timeout=1)
            except (ProcessLookupError, subprocess.TimeoutExpired):
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait(timeout=1)
            print(json.dumps({'status': 'process_failure', 'entry_watchdog_expired': True,
                              'review_required': True, 'accepted_work_unknown': True}), flush=True)
            return 3
        try:
            process.wait(timeout=min(.25, remaining))
        except subprocess.TimeoutExpired:
            pass
    return process.returncode


if __name__ == '__main__':
    raise SystemExit(main())
