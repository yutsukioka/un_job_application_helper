"""Synthetic subprocess fixture: no network or real database operations."""

import argparse
import json
from pathlib import Path
import runpy
import signal
import sys
import time

p = argparse.ArgumentParser()
p.add_argument("--request", type=Path)
p.add_argument("--report", type=Path)
p.add_argument("--database", type=Path)
p.add_argument("--mode", default="complete")
a = p.parse_args()
count = a.database.parent / "worker_starts.txt"
with count.open("a") as f:
    f.write("start\n")
stop = False


def onterm(signum, frame):
    global stop
    stop = True


if a.mode == "graceful":
    signal.signal(signal.SIGTERM, onterm)
    while not stop:
        time.sleep(0.01)
sys.argv = [
    "fake_worker",
    "--request",
    str(a.request),
    "--report",
    str(a.report),
    "--mode",
    "declared_incomplete" if a.mode in ("graceful", "incomplete") else "happy",
]
runpy.run_path(str(Path(__file__).with_name("fake_worker.py")), run_name="__main__")
if not a.database.exists():
    import sqlite3

    with sqlite3.connect(a.database) as db:
        db.execute("CREATE TABLE fixture_jobs(id TEXT PRIMARY KEY, body TEXT)")
        db.execute("INSERT INTO fixture_jobs VALUES('one', 'Synthetic public body')")
report = json.loads(a.report.read_text())
report["database"] = str(a.database)
a.report.write_text(json.dumps(report))
if a.mode == "acceptance_then_sleep":
    time.sleep(30)
