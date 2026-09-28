"""Synthetic idempotent publication fixture; no real outputs/databases/network."""

import argparse
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import sys
import time

p = argparse.ArgumentParser()
p.add_argument("--request", type=Path)
p.add_argument("--report", type=Path)
p.add_argument("--mode", default="published")
p.add_argument("--max-seconds", type=float)
a = p.parse_args()
r = json.loads(a.request.read_text())
fd = int(os.environ["JOBAGG_SHARED_LOCK_FD"])
os.fstat(fd)
with Path(r["shared_lock_path"]).open("r") as probe:
    try:
        fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        pass
    else:
        raise RuntimeError("Owner was not inherited")
assert a.max_seconds > 0 and time.time() <= r["deadline_epoch"]
root = Path(r["worker_database"]).parent
journal = root / "publisher_journal.json"
count = root / "publication_commits.txt"
if a.mode == "timeout":
    time.sleep(30)
if a.mode in ("commit_then_fail", "commit_then_crash") and not r.get(
    "recover_publication"
):
    journal.write_text(json.dumps({"run_id": r["run_id"]}))
    with count.open("a") as f:
        f.write(r["run_id"] + "\n")
    if a.mode == "commit_then_crash":
        os.kill(os.getpid(), 9)
    sys.exit(9)
if a.mode == "defer_once" and not r.get("recover_publication"):
    journal.write_text(json.dumps({"run_id": r["run_id"]}))
if r.get("recover_publication"):
    assert json.loads(journal.read_text())["run_id"] == r["run_id"]
elif a.mode != "deferred":
    with count.open("a") as f:
        f.write(r["run_id"] + "\n")
keys = (
    "run_id",
    "source_manifest_sha256",
    "registry_sha256",
    "expected_source_ids",
    "worker_acceptance_path",
    "worker_acceptance_sha256",
    "worker_database",
    "worker_database_files",
)
result = {k: r[k] for k in keys}
if "publication_snapshot" in r:
    from jobagg.publication_snapshot import validate_publication_snapshot

    validated = validate_publication_snapshot(r, r["worker_database"], owner_held=True)
    assert validated["effective_database"] == r["publication_snapshot"]["path"]
    result["publication_snapshot"] = r["publication_snapshot"]
result.update(
    schema_version=1,
    generated_at=datetime.now(timezone.utc).isoformat(),
    status=a.mode if a.mode in ("published", "noop", "incomplete", "deferred") else "published",
    observation_set_sha256="a" * 64,
    whole_job_completeness_certified=False,
)
if a.mode == "defer_once" and not r.get("recover_publication"):
    result["status"] = "incomplete"
if a.mode == "deferred":
    result["publication"] = {"status": "deferred", "generation_not_started": True}
if a.mode == "false_deferral":
    result["status"] = "deferred"
    result["publication"] = {"status": "deferred", "generation_not_started": False}
if a.mode == "wrong_binding":
    result["worker_acceptance_sha256"] = "f" * 64
if a.mode == "wrong_scope":
    result["expected_source_ids"] = ["unrelated"]
if a.mode == "certify":
    result["whole_job_completeness_certified"] = True
a.report.write_text(json.dumps(result))
print(json.dumps(result))
