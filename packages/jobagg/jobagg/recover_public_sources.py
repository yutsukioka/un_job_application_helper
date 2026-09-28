"""Review and probe the two known public-source configuration failures.

Dry-run by default. Execution captures a full listing and independently verifies
it before releasing this specific old hold. It does not publish, reset quotas,
rewrite workspace bindings, or enable automatic retries of access denials.
"""

import argparse
from dataclasses import asdict, replace
import fcntl
import hashlib
import json
from pathlib import Path
import time
from uuid import uuid4

from jobagg.pipelines.host_recovery import authorize_configuration_probe, authorize_native_browser_probe, authorize_osce_data_probe, authorize_osce_csrf_probe
from jobagg.pipelines.http_checkpoint import safe_error
from jobagg.pipelines.inventory_checks import verify_listing
from jobagg.remediation_worker import Worker, register_builtin_adapters, shared_owner, write_json


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument("--robots", type=Path, required=True)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--shared-lock", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--source", choices=["worldbank_csod", "osce_custom_html"], required=True)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--recovery-mode", choices=["configuration", "native-browser", "osce-data", "osce-csrf"], default="configuration")
    parser.add_argument("--access-evidence", type=Path,
                        help="Recent evidence review for native-browser, osce-data or osce-csrf recovery")
    args = parser.parse_args(argv)
    if (args.recovery_mode != "configuration") != bool(args.access_evidence):
        parser.error("Browser/data recovery requires --access-evidence; configuration recovery does not accept it")
    access_review = json.loads(args.access_evidence.read_text()) if args.access_evidence else None
    lease_key = {"configuration": "reviewed_configuration_probe", "native-browser": "reviewed_native_browser_probe",
                 "osce-data": "reviewed_osce_data_probe", "osce-csrf": "reviewed_osce_csrf_probe"}[args.recovery_mode]

    def authorize(state, evidence, source, **kwargs):
        if args.recovery_mode == "osce-csrf":
            return authorize_osce_csrf_probe(state, evidence, source, access_review=access_review, **kwargs)
        if args.recovery_mode == "osce-data":
            return authorize_osce_data_probe(state, evidence, source, access_review=access_review, **kwargs)
        if access_review is not None:
            return authorize_native_browser_probe(state, evidence, source, access_review=access_review, **kwargs)
        return authorize_configuration_probe(state, evidence, source, **kwargs)

    worker = Worker(registry=args.registry, robots=args.robots, workspace=args.workspace,
                    shared_lock=args.shared_lock, max_tasks=1, max_seconds=600,
                    max_requests_per_task=200)
    register_builtin_adapters()
    source = worker.by_id[args.source]
    host = "us.api.csod.com" if source.id == "worldbank_csod" else "vacancies.osce.org"
    hold = worker.shared_policy.root / "hosts" / (
        "host-" + hashlib.sha256(host.encode()).hexdigest()[:24] + ".json")
    with shared_owner(worker.shared_lock):
        original = json.loads(hold.read_text())
        evidence = json.loads(Path(original["evidence"]).read_text())
        now = time.time()
        authorize(original, evidence, source, owner="dry-run",
                                      now=now, expires_at=now + 540)
        if not args.execute:
            print(json.dumps({"source": source.id, "eligible_for_reviewed_probe": True,
                              "evidence": original["evidence"], "network_requests": 0}))
            return 0
        if worker.shared_policy.source_hold(source.id):
            raise ValueError("Separate source hold requires review")
        if worker.policy_due(source, "listing", now) > now:
            raise ValueError("Listing quota/pacing is not yet due")
        attempt = "configuration-recovery-" + uuid4().hex
        target = args.output_dir / attempt
        target.mkdir(parents=True)
        write_json(target / "hold-before.json", original)
        write_json(target / "tested-binding.json", worker.binding)
        if access_review is not None:
            write_json(target / "access-review.json", access_review)
        worker.shared_policy.reserve(attempt, source.id, "listing", now, worker.workspace, attempt)
        outcome = "failed"
        capture = None
        try:
            listing_source = replace(source, extra={**source.extra, "fetch_details": False})
            adapter, _, capture = worker.context(listing_source, target,
                                                 {"kind": "listing", "external_id": ""}, now + 540)
            with hold.with_suffix(".lock").open("a+") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                if json.loads(hold.read_text()) != original:
                    raise ValueError("Hold changed during review")
                write_json(hold, authorize(
                    original, evidence, source, owner=capture.probe_owner,
                    now=now, expires_at=now + 540))
            adapter.listing_checkpoint_path = target / "listing-checkpoint.json"
            jobs = adapter.fetch_jobs()
            verification = verify_listing(source, jobs, sorted((target / "http").glob("*.json")))
            write_json(target / "verification.json", verification)
            if not adapter.run_diagnostics.pagination_complete or not verification["complete"]:
                raise ValueError("Independent complete-ID reconciliation failed")
            write_json(target / "listing.json", {"jobs": [asdict(job) for job in jobs],
                                                "diagnostics": asdict(adapter.run_diagnostics)})
            with hold.with_suffix(".lock").open("a+") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                latest = json.loads(hold.read_text())
                if (latest.get("evidence") != original["evidence"]
                        or latest.get(lease_key, {}).get("owner") != capture.probe_owner
                        or time.time() >= now + 540):
                    raise ValueError("Probe evidence changed or lease expired; hold retained")
                latest.pop(lease_key, None)
                latest.pop("reviewed_timeout_probe", None)
                latest.update(stopped=False, reason="Reviewed configuration repair: complete listing verified",
                              failure_category="configuration_repaired", review_evidence=str(target / "verification.json"))
                write_json(hold, latest)
            outcome = "complete_listing_verified"
            print(json.dumps({"status": outcome, "count": len(jobs), "target": str(target),
                              "details_fetched": False, "published": False}))
        except Exception as exc:
            write_json(target / "error.json", {"error": safe_error(exc), "complete": False})
            raise
        finally:
            try:
                if capture is not None:
                    with hold.with_suffix(".lock").open("a+") as lock:
                        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        latest = json.loads(hold.read_text())
                        if latest.get(lease_key, {}).get("owner") == capture.probe_owner:
                            latest.pop(lease_key, None)
                            write_json(hold, latest)
            finally:
                worker.shared_policy.finish(attempt, outcome)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
