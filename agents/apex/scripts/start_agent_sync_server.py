#!/usr/bin/env python3
"""Start one agent_sync server as a detached background process."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--server-py", required=True)
    parser.add_argument("--port", required=True)
    parser.add_argument("--agents", required=True)
    parser.add_argument("--rundir", required=True)
    parser.add_argument("--logfile", required=True)
    parser.add_argument("--pidfile", required=True)
    args = parser.parse_args()

    rundir = Path(args.rundir)
    rundir.mkdir(parents=True, exist_ok=True)
    Path(args.logfile).parent.mkdir(parents=True, exist_ok=True)

    env = os.environ.copy()
    env["AGENTS_LIST"] = args.agents

    log = open(args.logfile, "ab", buffering=0)
    proc = subprocess.Popen(
        [sys.executable, args.server_py, "--port", args.port],
        cwd=str(rundir),
        stdout=log,
        stderr=log,
        env=env,
        start_new_session=True,
    )
    Path(args.pidfile).write_text(str(proc.pid), encoding="utf-8")
    time.sleep(0.4)

    if proc.poll() is None:
        return 0
    return proc.returncode or 1


if __name__ == "__main__":
    raise SystemExit(main())
