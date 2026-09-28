"""Bounded CLI for source/consolidated database and export publication."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import time

from jobagg.pipelines.live_publication import _save, _sha, publish_incremental
from jobagg.publication_snapshot import validate_publication_snapshot, validate_recovery_generation, _recovery_identity
from jobagg.publication_deadline import wall_deadline


@contextmanager
def _owner(path, inherited):
    path = Path(path).resolve()
    if inherited is not None:
        stat = os.fstat(inherited)
        actual = path.stat()
        if (stat.st_dev, stat.st_ino) != (actual.st_dev, actual.st_ino):
            raise ValueError("Inherited owner FD does not match shared lock")
        # A distinct descriptor must be unable to take the existing lock.
        with path.open("a+") as probe:
            try:
                fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                fcntl.flock(inherited, fcntl.LOCK_EX | fcntl.LOCK_NB)
                yield
            else:
                fcntl.flock(probe, fcntl.LOCK_UN)
                raise ValueError("Inherited shared owner is not locked")
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a+") as owner:
            fcntl.flock(owner, fcntl.LOCK_EX | fcntl.LOCK_NB)
            yield


def _request(args, *, owner_held=False, deadline_at=None, validation=None):
    if not args.request:
        return {}
    request = json.loads(Path(args.request).read_text())
    if request.get("schema_version") != 1 or not request.get("run_id"):
        raise ValueError("Invalid publication request schema")
    for key, value in (
        ("worker_database", args.worker_database),
        ("shared_lock_path", args.shared_lock),
    ):
        if Path(request[key]).resolve() != Path(value).resolve():
            raise ValueError("Publication request path differs: " + key)
    if request.get("registry_sha256") != _sha(args.registry):
        raise ValueError("Publication request registry differs")
    if request.get("deadline_epoch"):
        requested_deadline = time.monotonic() + max(
            0, float(request["deadline_epoch"]) - time.time()
        )
        deadline_at = min(deadline_at, requested_deadline) if deadline_at is not None else requested_deadline
    checked = validate_publication_snapshot(
        request, args.worker_database, owner_held=owner_held, deadline_at=deadline_at
    )
    if validation is not None:
        validation.update(checked)
    from jobagg.pipelines.sync_source import load_sources

    enabled = sorted(source.id for source in load_sources(args.registry) if source.enabled)
    if sorted(request.get("expected_source_ids", [])) != enabled:
        raise ValueError("Expected enabled source population differs")
    return request


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("worker-database", "registry", "output-dir", "state-dir", "shared-lock"):
        parser.add_argument("--" + name, required=True)
    parser.add_argument("--max-jobs", type=int, default=20)
    parser.add_argument("--max-seconds", type=float, default=180)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--request")
    parser.add_argument("--report")
    parser.add_argument("--owner-fd", type=int)
    args = parser.parse_args(argv)
    if args.max_jobs < 1 or args.max_seconds <= 0:
        parser.error("max-jobs and max-seconds must be positive")
    started = time.monotonic()
    inherited = args.owner_fd
    if inherited is None and os.environ.get("JOBAGG_SHARED_LOCK_FD"):
        inherited = int(os.environ["JOBAGG_SHARED_LOCK_FD"])

    def invoke(*, owner_held=False):
        deadline = started + args.max_seconds
        checked = {}
        request = _request(
            args, owner_held=owner_held, deadline_at=deadline, validation=checked
        )
        if request.get("deadline_epoch"):
            deadline = min(
                deadline, time.monotonic() + max(0, float(request["deadline_epoch"]) - time.time())
            )
        recovery = None
        if request.get("recover_publication") is True:
            recovery = validate_recovery_generation(
                request, checked["effective_database"], args.output_dir, args.state_dir,
                deadline_at=deadline,
            )
        if recovery and recovery["completed_result"] is not None:
            result = {**recovery["completed_result"], "reconciled_completed_generation": True}
        else:
            with wall_deadline(request.get("deadline_epoch")):
                result = publish_incremental(
                    checked.get("effective_database", args.worker_database),
                    args.registry,
                    args.output_dir,
                    args.state_dir,
                    max_jobs=args.max_jobs,
                    deadline_at=deadline,
                    execute=args.execute,
                    request_identity=_recovery_identity(request) if request else None,
                )
        if recovery and (
            result.get("generation_id") != recovery["binding"]["generation_id"]
            or result.get("observation_set_sha256") != recovery["binding"]["observation_set_sha256"]
        ):
            raise ValueError("Publisher returned a different recovery generation")
        if request:
            status = {"published": "published", "no_changes": "noop", "deferred": "deferred"}.get(
                result["status"], "incomplete"
            )
            result = {
                **request,
                "schema_version": 1,
                "publication_request_sha256": _sha(args.request),
                "publication_snapshot_validation": checked["validation_receipt"],
                "status": status,
                "publication": result,
                "observation_set_sha256": result.get("observation_set_sha256"),
                "whole_job_completeness_certified": False,
            }
        if args.report:
            _save(args.report, result)
        return result

    if args.execute:
        try:
            with _owner(args.shared_lock, inherited):
                result = invoke(owner_held=True)
        except BlockingIOError:
            result = {"status": "owner_busy", "completeness_certified": False}
            print(json.dumps(result))
            return 2
    else:
        result = invoke()
    print(json.dumps(result, sort_keys=True, default=str))
    return (
        0 if result["status"] in {"published", "no_changes", "preview", "noop", "incomplete", "deferred"} else 2
    )


if __name__ == "__main__":
    raise SystemExit(main())
