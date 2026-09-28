"""Synthetic fixture only. Never fetches jobs or accesses production data."""
import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

NORMALIZATION = "jobtext-v1-whitespace-known-boilerplate"


def stamp():
    return datetime.now(timezone.utc).isoformat()


def write(path, value):
    path.write_text(json.dumps(value))


parser = argparse.ArgumentParser()
parser.add_argument("--request", type=Path, required=True)
parser.add_argument("--report", type=Path, required=True)
parser.add_argument("--mode", default="happy")
args = parser.parse_args()
request = json.loads(args.request.read_text())
# Detect accidental loss of the inherited lock descriptor.
os.fstat(int(os.environ["JOBAGG_SHARED_LOCK_FD"]))
if args.mode == "timeout":
    child_code = "import pathlib,signal,sys,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); p=pathlib.Path(sys.argv[1]); [(p.write_text(str(n)),time.sleep(0.02)) for n in range(1000)]"
    child = subprocess.Popen([sys.executable, "-c", child_code, str(args.report.parent / "child-heartbeat.txt")],
                             pass_fds=(int(os.environ["JOBAGG_SHARED_LOCK_FD"]),))
    (args.report.parent / "grandchild.pid").write_text(str(child.pid))
    time.sleep(30)
if args.mode == "nonzero":
    sys.exit(7)
if args.mode == "missing_report":
    sys.exit(0)
base = {"schema_version": 1, "run_id": request["run_id"],
        "source_manifest_sha256": request["source_manifest_sha256"], "generated_at": stamp()}
source_results = []
for sid in request["expected_source_ids"]:
    text_hash = hashlib.sha256(b"Synthetic full public job description.").hexdigest()
    detail = {"job_id": "job-1", "source_job_id": "job-1", "database_job_id": "job-1",
              "source_text_sha256": text_hash, "database_text_sha256": text_hash,
              "source_chars": 38, "database_chars": 38, "source_content_valid": True,
              "required_fields_complete": True, "source_fetched_at": stamp(),
              "required_attachment_ids": [], "attachment_discovery_validated": True, "attachments": []}
    enumeration = {"method": "reported_total", "reported_unique_total": 1, "pagination_complete": True}
    if args.mode == "independent":
        terminal = args.report.parent / (sid + "-terminal.txt")
        terminal.write_text("Synthetic terminal-page evidence: one listing, no next page.")
        enumeration = {"method": "independent_terminal", "reported_unique_total": None,
                       "pagination_complete": True, "independent_listing_ids": ["job-1"],
                       "terminal_evidence": {"path": terminal.name, "sha256": hashlib.sha256(terminal.read_bytes()).hexdigest()}}
    if args.mode == "attachment":
        attachment = {k: v for k, v in detail.items() if k in ("source_text_sha256", "database_text_sha256", "source_chars", "database_chars", "source_content_valid", "required_fields_complete", "source_fetched_at")}
        attachment["attachment_id"] = "tor.pdf"
        detail["required_attachment_ids"] = ["tor.pdf"]
        detail["attachments"] = [attachment]
    if args.mode == "attachment_missing":
        detail["required_attachment_ids"] = ["tor.pdf"]
    if args.mode == "content_mismatch":
        detail["database_text_sha256"] = "0" * 64
    if args.mode == "stale_detail":
        detail["source_fetched_at"] = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
    evidence = {**base, "source_id": sid, "listing_observed_at": stamp(), "scope_verified": True,
                "normalization": NORMALIZATION, "enumeration": enumeration, "listing_ids": ["job-1"],
                "published_ids": ["job-1"], "complete_detail_ids": ["job-1"], "content_results": [detail]}
    if args.mode == "duplicate_id":
        evidence["listing_ids"].append("job-1")
    if args.mode == "incomplete":
        evidence["enumeration"]["pagination_complete"] = False
    path = args.report.parent / (sid + "-evidence.json")
    write(path, evidence)
    counts = {"listed": 1, "published": 1, "complete_details": 1, "listing_only": 0,
              "empty_details": 0, "attachments_pending": 0, "attachments_failed": 0,
              "verified_attachments": len(detail["attachments"])}
    if args.mode == "bad_count":
        counts["published"] = 9
    source_results.append({"source_id": sid, "status": "complete", "counts": counts,
                           "evidence": {"path": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}})
report = {**base, "generated_at": stamp(), "status": "complete", "sources": source_results}
if args.mode == "bad_run_id":
    report["run_id"] = "old-run"
if args.mode == "bad_manifest":
    report["source_manifest_sha256"] = "0" * 64
if args.mode == "missing_source":
    report["sources"] = []
if args.mode == "declared_incomplete":
    report["status"] = "incomplete"
if args.mode == "blocked":
    report["sources"][0] = {"source_id": request["expected_source_ids"][0], "status": "blocked", "reason": "Synthetic public-source outage"}
if args.mode == "stale_report":
    report["generated_at"] = "2000-01-01T00:00:00+00:00"
if args.mode == "tampered_evidence":
    path.write_text("{}")
if args.mode == "registry_drift":
    Path(request["source_registry"]["path"]).write_text("synthetic registry changed during this tick")
write(args.report, report)
