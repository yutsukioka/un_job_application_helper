"""Synthetic concurrency receipts for dispatcher tests; never contacts a site."""
import argparse
import json
from pathlib import Path
import runpy
import sys

parser = argparse.ArgumentParser()
parser.add_argument("--request", type=Path, required=True)
parser.add_argument("--report", type=Path, required=True)
parser.add_argument("--database", type=Path, required=True)
parser.add_argument("--parallel-sources", type=int, required=True)
parser.add_argument("--metrics", default="healthy")
args = parser.parse_args()
request = json.loads(args.request.read_text())
assert request["parallel_sources"] == args.parallel_sources
sys.argv = ["phase_worker", "--request", str(args.request), "--report", str(args.report),
            "--database", str(args.database), "--mode", "incomplete"]
runpy.run_path(str(Path(__file__).with_name("phase_worker.py")), run_name="__main__")
report = json.loads(args.report.read_text())
limit = args.parallel_sources
metrics = {
    "limit_used": limit, "eligible_distinct_sources": 12, "eligible_distinct_hosts": 12,
    "peak_active_tasks": limit, "peak_active_sources": limit, "peak_active_hosts": 1,
    "peak_active_scheduling_hosts": limit,
    "attempted_tasks": 12, "accepted_progress": 10, "eligible_backlog_remaining": 30,
    "new_access_blocks": int(args.metrics == "access_block"), "transport_failures": 0,
    "runtime_errors": 0, "integrity_errors": 0, "database_errors": 0,
    "control_cycle_complete": True,
}
if args.metrics == "missing":
    metrics.pop("database_errors")
report["concurrency"] = metrics
args.report.write_text(json.dumps(report))
