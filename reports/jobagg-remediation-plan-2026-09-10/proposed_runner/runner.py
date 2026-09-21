#!/usr/bin/env python3
"""Bounded dispatcher for deterministic fetching and verified live publication.

Dry-run is the default. This file does not implement fetching or install a schedule.
Python 3.11+; macOS/POSIX; standard library only.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import tempfile
import time
import uuid

NORMALIZATION = "jobtext-v1-whitespace-known-boilerplate"
EXIT = {
    "complete": 0,
    "incomplete": 2,
    "process_failure": 3,
    "lock_busy": 75,
    "maintenance_paused": 75,
    "unchanged_failure_hold": 75,
}


def load_file_module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    # Configuration inspection is read-only, including helper bytecode caches.
    exec(compile(Path(path).read_bytes(), str(path), "exec"), module.__dict__)
    return module


OBSERVABILITY = load_file_module(
    Path(__file__).with_name("runner_observability.py"),
    "jobagg_dispatcher_observability",
)


def snapshot_helper(config):
    package = config["publication_cwd"] / "jobagg"
    path = package / "publication_snapshot.py"
    require(
        path.is_file() and path.resolve().is_relative_to(package.resolve()),
        "Reviewed publication snapshot helper missing or outside selected package",
    )
    return load_file_module(path, "jobagg_dispatcher_publication_snapshot")


def concurrency_helper(config):
    package = config["worker_cwd"] / "jobagg"
    path = package / "remediation_concurrency.py"
    require(
        path.is_file() and path.resolve().is_relative_to(package.resolve()),
        "Concurrency helper missing or outside selected worker package",
    )
    return load_file_module(path, "jobagg_dispatcher_concurrency")


def begin_concurrency(config, record):
    """Journal selected concurrency before spawning, under the shared owner."""
    if config.get("concurrency") is None:
        return None
    helper = concurrency_helper(config)
    policy = helper.ControllerPolicy(**config["concurrency"])
    path = config["state_dir"] / "concurrency.json"
    stamp = timestamp(record["started_at"]).timestamp()
    interrupted_run = None
    reconfigured = False
    if path.exists():
        envelope = read_json(path)
        require(
            set(envelope) == {"schema_version", "controller_state", "inflight"}
            and envelope["schema_version"] == 1,
            "Invalid persisted concurrency envelope; do not reset history",
        )
        state = envelope["controller_state"]
        updated = helper.reconfigure_state(state, policy, now=stamp)
        reconfigured = updated != state
        state = updated
        helper.validate_state(state, policy)
        if envelope["inflight"] is not None:
            require(isinstance(envelope["inflight"], dict), "Invalid concurrency crash marker")
            interrupted_run = envelope["inflight"].get("run_id")
            state = helper.interrupt(
                state, policy, now=stamp, reason="previous_dispatch_not_finalized"
            )["state"]
    else:
        candidates = [config["state_dir"] / "state.json"]
        candidates.extend((config["state_dir"] / "runs").glob("*/outcome.json"))
        candidates.extend((config["state_dir"] / "runs").glob("*/state.json"))
        require(
            not any(path.is_file() and "concurrency" in read_json(path) for path in candidates),
            "Persisted concurrency history is missing; reviewed recovery required, no reset",
        )
        state = helper.initial_state(policy, now=stamp)
    inflight = {"run_id": record["run_id"], "started_at": record["started_at"], "limit": state["limit"]}
    atomic_json(path, {"schema_version": 1, "controller_state": state, "inflight": inflight})
    record["concurrency"] = {
        "limit_used": state["limit"], "state_path": str(path),
        "previous_unfinished_run": interrupted_run,
        "policy_reconfigured": reconfigured,
    }
    return {"helper": helper, "policy": policy, "state": state, "path": path, "inflight": inflight}


def finish_concurrency(context, record, report_path):
    """Only finalized, validated fetching and publication can earn ramp credit."""
    if context is None:
        return
    helper, policy, state = context["helper"], context["policy"], context["state"]
    stamp = now().timestamp()
    current = read_json(context["path"])
    require(
        current["controller_state"] == state and current["inflight"] == context["inflight"],
        "Concurrency state changed during owned dispatch",
    )
    counters = (
        "eligible_distinct_sources", "eligible_distinct_hosts", "peak_active_tasks",
        "peak_active_sources", "peak_active_hosts", "peak_active_scheduling_hosts",
        "attempted_tasks", "accepted_progress",
        "eligible_backlog_remaining", "new_access_blocks", "transport_failures",
        "runtime_errors", "integrity_errors", "database_errors",
    )
    metrics = None
    if (
        record.get("worker_report_sha256") and report_path.exists()
        and digest(report_path) == record["worker_report_sha256"]
    ):
        metrics = read_json(report_path).get("concurrency")
    if not isinstance(metrics, dict) or any(key not in metrics for key in counters):
        decision = helper.interrupt(state, policy, now=stamp, reason="missing_validated_worker_metrics")
    else:
        killed = any(
            isinstance(record.get(key), dict) and record[key].get("kill_sent", False)
            for key in ("worker_timeout", "publication_timeout", "final_cleanup")
        )
        observation = {
            **{key: metrics[key] for key in counters},
            "batch_id": record["run_id"],
            "started_at": timestamp(record["started_at"]).timestamp(),
            "finished_at": stamp,
            "limit_used": metrics.get("limit_used"),
            "control_cycle_complete": (
                record["status"] in {"complete", "incomplete"}
                and record.get("worker_exit_code") == 0
                and "worker_timeout" not in record and "publication_timeout" not in record
                and not record.get("cleanup_failure")
                and metrics.get("control_cycle_complete") is True
                and metrics.get("limit_used") == context["inflight"]["limit"]
            ),
            "killed": killed,
            "publication_status": (
                "verified" if record.get("publication_status") in {"published", "noop"}
                and record.get("publication_exit_code") == 0
                and record.get("publication_result_sha256")
                else "unknown"
            ),
        }
        decision = helper.decide(state, observation, policy, now=stamp)
    helper.validate_state(decision["state"], policy)
    record["concurrency"].update(
        {key: value for key, value in decision.items() if key != "state"}
    )
    # The caller must first commit the terminal outcome journal. Otherwise a
    # crash could award health credit to a control cycle with no final receipt.
    return {"schema_version": 1, "controller_state": decision["state"], "inflight": None}


class InvalidReport(ValueError):
    pass


def now():
    return datetime.now(timezone.utc)


def timestamp(value):
    if not isinstance(value, str):
        raise InvalidReport("timestamp must be an ISO-8601 string")
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise InvalidReport("invalid timestamp") from exc
    if result.tzinfo is None:
        raise InvalidReport("timestamp must include a timezone")
    return result


def read_json(path):
    if path.stat().st_size > 16 * 1024 * 1024:
        raise InvalidReport("JSON artifact exceeds 16 MiB")
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise InvalidReport("JSON root must be an object")
    return value


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=".state-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w") as stream:
            json.dump(value, stream, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def require(condition, message):
    if not condition:
        raise InvalidReport(message)


def identifiers(values, name):
    require(isinstance(values, list), f"{name} must be a list")
    require(
        all(isinstance(v, str) and v.strip() == v and v for v in values),
        f"{name} must contain nonempty string IDs",
    )
    require(len(values) == len(set(values)), f"{name} contains duplicate IDs")
    return set(values)


def integer(value, name):
    require(type(value) is int and value >= 0, f"{name} must be a nonnegative integer")
    return value


def evidence_path(reference, run_dir):
    require(isinstance(reference, dict), "evidence reference must be an object")
    name = reference.get("path")
    require(isinstance(name, str) and name, "evidence path missing")
    path = (run_dir / name).resolve()
    require(
        path.is_relative_to(run_dir.resolve()), "evidence escapes this run directory"
    )
    require(path.is_file(), "evidence file missing")
    require(reference.get("sha256") == digest(path), "evidence SHA-256 mismatch")
    return path


def fresh(value, earliest, latest, label):
    moment = timestamp(value)
    require(earliest <= moment <= latest, f"{label} is stale or future-dated")


def common_artifact(value, request, latest):
    require(
        type(value.get("schema_version")) is int and value["schema_version"] == 1,
        "unsupported report/evidence schema",
    )
    require(value.get("run_id") == request["run_id"], "run ID mismatch")
    require(
        value.get("source_manifest_sha256") == request["source_manifest_sha256"],
        "source manifest digest mismatch",
    )
    fresh(
        value.get("generated_at"), timestamp(request["started_at"]), latest, "artifact"
    )


def verify_text(item, earliest, latest, name, problems):
    require(isinstance(item, dict), f"{name} result must be an object")
    for field in ("source_text_sha256", "database_text_sha256"):
        require(
            isinstance(item.get(field), str)
            and re.fullmatch(r"[0-9a-f]{64}", item[field]),
            f"{name} has invalid {field}",
        )
    source_chars = integer(item.get("source_chars"), name + ".source_chars")
    database_chars = integer(item.get("database_chars"), name + ".database_chars")
    fresh(item.get("source_fetched_at"), earliest, latest, name + ".source_fetched_at")
    if (
        item["source_text_sha256"] != item["database_text_sha256"]
        or source_chars == 0
        or source_chars != database_chars
        or item.get("source_content_valid") is not True
        or item.get("required_fields_complete") is not True
    ):
        problems.append(f"{name}: public text/content checks failed")


def verify_source(source, request, run_dir, latest):
    sid = source["source_id"]
    require(
        source.get("status") in {"complete", "incomplete", "blocked"},
        "invalid source status",
    )
    if source["status"] != "complete":
        require(
            isinstance(source.get("reason"), str) and source["reason"].strip(),
            "incomplete/blocked source requires a reason",
        )
        return [f"{sid}: {source['status']}: {source['reason']}"]
    evidence = read_json(evidence_path(source.get("evidence"), run_dir))
    common_artifact(evidence, request, latest)
    require(evidence.get("source_id") == sid, "evidence source ID mismatch")
    require(
        evidence.get("normalization") == NORMALIZATION, "unknown content normalization"
    )
    limits = request["freshness_limits"]
    fresh(
        evidence.get("listing_observed_at"),
        latest - timedelta(seconds=limits["listing_max_age_seconds"]),
        latest,
        sid + ".listing_observed_at",
    )
    listed = identifiers(evidence.get("listing_ids"), "listing_ids")
    published = identifiers(evidence.get("published_ids"), "published_ids")
    detailed = identifiers(evidence.get("complete_detail_ids"), "complete_detail_ids")
    counts = source.get("counts")
    require(isinstance(counts, dict), "source counts missing")
    for key in (
        "listed",
        "published",
        "complete_details",
        "listing_only",
        "empty_details",
        "attachments_pending",
        "attachments_failed",
        "verified_attachments",
    ):
        integer(counts.get(key), key)
    require(
        counts["listed"] == len(listed)
        and counts["published"] == len(published)
        and counts["complete_details"] == len(detailed),
        "counts disagree with ID evidence",
    )
    problems = []
    if listed != published or listed != detailed:
        problems.append(f"{sid}: listing/publication/detail ID sets differ")
    if any(
        counts[k]
        for k in (
            "listing_only",
            "empty_details",
            "attachments_pending",
            "attachments_failed",
        )
    ):
        problems.append(f"{sid}: pending/failed/stub details or attachments")
    enumeration = evidence.get("enumeration")
    require(isinstance(enumeration, dict), "enumeration evidence missing")
    if (
        evidence.get("scope_verified") is not True
        or enumeration.get("pagination_complete") is not True
    ):
        problems.append(f"{sid}: scope/pagination completeness unproven")
    method = enumeration.get("method")
    if method == "reported_total":
        total = integer(
            enumeration.get("reported_unique_total"), "reported_unique_total"
        )
        if total != len(listed):
            problems.append(
                f"{sid}: authoritative unique total differs from collected IDs"
            )
    elif method == "independent_terminal":
        independent = identifiers(
            enumeration.get("independent_listing_ids"), "independent_listing_ids"
        )
        evidence_path(enumeration.get("terminal_evidence"), run_dir)
        if independent != listed:
            problems.append(f"{sid}: independent listing IDs disagree")
    else:
        raise InvalidReport(
            "enumeration method must be reported_total or independent_terminal"
        )
    if not listed and enumeration.get("verified_zero") is not True:
        problems.append(f"{sid}: zero listings lack explicit verification")
    results = evidence.get("content_results")
    require(
        isinstance(results, list) and all(isinstance(x, dict) for x in results),
        "content results missing",
    )
    checked = identifiers([x.get("job_id") for x in results], "checked job IDs")
    if checked != detailed:
        problems.append(
            f"{sid}: content verification does not cover every complete detail"
        )
    attachment_total = 0
    earliest = latest - timedelta(seconds=limits["detail_max_age_seconds"])
    for item in results:
        label = sid + "/" + item["job_id"]
        if (
            item.get("source_job_id") != item["job_id"]
            or item.get("database_job_id") != item["job_id"]
        ):
            problems.append(label + ": source/database job identity mismatch")
        verify_text(item, earliest, latest, label, problems)
        required = identifiers(
            item.get("required_attachment_ids"), "required_attachment_ids"
        )
        attachments = item.get("attachments")
        require(
            isinstance(attachments, list)
            and all(isinstance(x, dict) for x in attachments),
            "attachment results missing",
        )
        actual = identifiers(
            [a.get("attachment_id") for a in attachments], "attachment IDs"
        )
        if item.get("attachment_discovery_validated") is not True or required != actual:
            problems.append(
                label + ": required JD/ToR attachment discovery/coverage unproven"
            )
        for attachment in attachments:
            verify_text(attachment, earliest, latest, label + "/attachment", problems)
        attachment_total += len(attachments)
    require(
        counts["verified_attachments"] == attachment_total,
        "verified attachment count mismatch",
    )
    return problems


def validate_report(report_path, request, latest):
    report = read_json(report_path)
    common_artifact(report, request, latest)
    require(
        report.get("status") in {"complete", "incomplete"},
        "invalid global report status",
    )
    sources = report.get("sources")
    require(
        isinstance(sources, list) and all(isinstance(x, dict) for x in sources),
        "source results missing",
    )
    observed = identifiers([s.get("source_id") for s in sources], "report source IDs")
    require(
        observed == set(request["expected_source_ids"]),
        "report source set differs from frozen enabled manifest",
    )
    problems = []
    for source in sources:
        problems.extend(verify_source(source, request, report_path.parent, latest))
    if report["status"] != "complete":
        problems.append("worker declared the global run incomplete")
    return ("incomplete", problems) if problems else ("complete", [])


def resolve(base, value):
    require(isinstance(value, str) and value, "configuration path missing")
    return Path(os.path.abspath(base / value))


def load_config(path, *, record_storage_health=False):
    config = read_json(path)
    config["_record_storage_health"] = record_storage_health
    config["_config_sha256"] = digest(path)
    config["_config_path"] = path
    require(config.get("schema_version") == 1, "unsupported configuration schema")
    for key in (
        "timeout_seconds",
        "terminate_grace_seconds",
        "listing_max_age_seconds",
        "detail_max_age_seconds",
    ):
        value = config.get(key)
        require(type(value) in (int, float) and 0 < value <= 86400, f"invalid {key}")
    command = config.get("worker_argv")
    require(
        isinstance(command, list) and all(isinstance(x, str) and x for x in command),
        "worker_argv must be a string array",
    )
    if command:
        require(
            any("{request_path}" in x for x in command)
            and any("{report_path}" in x for x in command),
            "worker argv requires {request_path} and {report_path}",
        )
    publication = config.get("publication_argv", [])
    require(
        isinstance(publication, list)
        and all(isinstance(x, str) and x for x in publication),
        "publication_argv must be a string array",
    )
    config["publication_argv"] = publication
    publication_timeout = config.get("publication_timeout_seconds", 0)
    require(
        type(publication_timeout) in (int, float)
        and (
            0 < publication_timeout <= 86400
            if publication
            else publication_timeout == 0
        ),
        "configured publication requires publication_timeout_seconds; otherwise omit it",
    )
    config["publication_timeout_seconds"] = publication_timeout
    config["total_timeout_seconds"] = config.get(
        "total_timeout_seconds",
        config["timeout_seconds"]
        + publication_timeout
        + config["terminate_grace_seconds"] * (2 if publication else 1),
    )
    require(
        type(config["total_timeout_seconds"]) in (int, float)
        and 0 < config["total_timeout_seconds"] <= 86400,
        "invalid total_timeout_seconds",
    )
    require(
        config["total_timeout_seconds"]
        >= config["timeout_seconds"] + config["terminate_grace_seconds"],
        "total budget must include worker cleanup",
    )
    if publication:
        require(
            any("{publication_request_path}" in x for x in publication),
            "publication argv requires {publication_request_path}",
        )
    base = path.parent
    for key in ("source_manifest_path", "state_dir", "shared_lock_path", "worker_cwd"):
        config[key] = resolve(base, config.get(key))
    config["maintenance_file"] = (
        resolve(base, config["maintenance_file"])
        if config.get("maintenance_file")
        else None
    )
    if publication:
        config["publication_worker_database"] = resolve(
            base, config.get("publication_worker_database")
        )
    config["publication_cwd"] = resolve(
        base, config.get("publication_cwd", str(config["worker_cwd"]))
    )
    if config.get("concurrency") is not None:
        require(isinstance(config["concurrency"], dict), "Concurrency policy must be an object")
        helper = concurrency_helper(config)
        helper.ControllerPolicy(**config["concurrency"])
        require(bool(publication), "Concurrency control requires configured publication")
        require(
            config["worker_argv"].count("{parallel_sources}") == 1
            and "--parallel-sources" in config["worker_argv"]
            and config["worker_argv"][config["worker_argv"].index("--parallel-sources") + 1] == "{parallel_sources}",
            "Concurrency requires exactly one --parallel-sources {parallel_sources} argument",
        )
    config["attempt_state_dir"] = resolve(
        base, config.get("attempt_state_dir", str(config["state_dir"]))
    )
    config["sealed_publication_snapshots"] = config.get(
        "sealed_publication_snapshots", False
    )
    require(
        type(config["sealed_publication_snapshots"]) is bool,
        "Invalid sealed_publication_snapshots",
    )
    config["publication_snapshot_mode"] = config.get("publication_snapshot_mode", "full")
    require(
        config["publication_snapshot_mode"] in {"full", "projection"},
        "Unknown publication_snapshot_mode",
    )
    require(
        config["publication_snapshot_mode"] != "projection"
        or bool(publication) and config["sealed_publication_snapshots"],
        "Publication projection requires sealed snapshots and a publisher",
    )
    guard = config.get("storage_guard")
    if guard:
        require(isinstance(guard, dict), "Invalid storage_guard")
        config["storage_guard"] = {
            **guard,
            "mount_root": str(resolve(base, guard.get("mount_root"))),
            "sentinel_path": str(resolve(base, guard.get("sentinel_path"))),
        }
        require(
            isinstance(guard.get("sentinel_sha256"), str)
            and re.fullmatch(r"[0-9a-f]{64}", guard["sentinel_sha256"]),
            "Invalid storage sentinel SHA",
        )
        require(
            type(guard.get("min_free_bytes", 0)) is int
            and guard.get("min_free_bytes", 0) >= 0,
            "Invalid storage min_free_bytes",
        )
    OBSERVABILITY.storage_check(config)
    manifest_bytes = config["source_manifest_path"].read_bytes()
    manifest = json.loads(manifest_bytes)
    require(isinstance(manifest, dict), "manifest root must be an object")
    config["_manifest_sha256"] = hashlib.sha256(manifest_bytes).hexdigest()
    require(manifest.get("schema_version") == 1, "unsupported source manifest schema")
    registry = manifest.get("registry")
    require(
        isinstance(registry, dict),
        "manifest must bind the source registry path and SHA-256",
    )
    config["_registry_path"] = resolve(
        config["source_manifest_path"].parent, registry.get("path")
    )
    config["_registry_sha256"] = registry.get("sha256")
    require(
        config["_registry_sha256"] == digest(config["_registry_path"]),
        "registry differs from frozen manifest",
    )
    sources = manifest.get("sources")
    require(
        isinstance(sources, list)
        and sources
        and all(isinstance(x, dict) for x in sources),
        "manifest sources missing",
    )
    identifiers([s.get("source_id") for s in sources], "manifest source IDs")
    require(
        all(type(s.get("enabled")) is bool for s in sources),
        "manifest enabled flags must be booleans",
    )
    expected = sorted(s["source_id"] for s in sources if s["enabled"])
    require(bool(expected), "manifest has no enabled sources")
    return config, expected


def group_alive(pid):
    try:
        os.killpg(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        # A restricted process census cannot prove absence. Still attempt the
        # explicitly owned child's TERM/KILL; never report a hidden group gone.
        return True


def terminate_group(process, grace):
    """Bounded TERM -> KILL for the entire child session, even after parent exit."""
    if not group_alive(process.pid):
        process.poll()
        return {"term_sent": False, "kill_sent": False}
    outcome = {"term_sent": True, "kill_sent": False}
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        process.poll()
        return outcome
    end = time.monotonic() + max(0, grace)
    while group_alive(process.pid) and time.monotonic() < end:
        process.poll()
        time.sleep(min(0.02, max(0, end - time.monotonic())))
    if group_alive(process.pid):
        try:
            os.killpg(process.pid, signal.SIGKILL)
            outcome["kill_sent"] = True
        except ProcessLookupError:
            pass
    process.wait(timeout=1)
    return outcome


def owner_evidence(config, descriptor):
    """Do not repair stale state unless this exact owner inode is exclusively held."""
    actual, opened = config["shared_lock_path"].stat(), os.fstat(descriptor)
    require(
        (actual.st_dev, actual.st_ino) == (opened.st_dev, opened.st_ino),
        "owner file changed",
    )
    with config["shared_lock_path"].open("r") as other:
        try:
            fcntl.flock(other, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise InvalidReport(
                    "supplied owner descriptor is not the exclusive holder"
                ) from exc
        else:
            fcntl.flock(other, fcntl.LOCK_UN)
            raise InvalidReport("dispatcher does not hold the actual exclusive owner")
    latest = config["shared_lock_path"].stat()
    require(
        (latest.st_dev, latest.st_ino) == (opened.st_dev, opened.st_ino),
        "owner file changed during validation",
    )
    return {
        "path": str(config["shared_lock_path"]),
        "device": actual.st_dev,
        "inode": actual.st_ino,
        "exclusive_owner_held": True,
    }


def former_processes_absent(record):
    """PID/process-group checks plus actual command census; elapsed age is no proof."""
    for key in ("wrapper_pid", "worker_pid", "publication_pid"):
        pid = record.get(key)
        if pid is not None:
            require(type(pid) is int and pid > 0, "invalid former PID")
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                pass
            else:
                raise InvalidReport(
                    f"former {key} {pid} is still present; no stale reconciliation"
                )
    for pid in record.get("process_groups", []):
        require(type(pid) is int and pid > 0, "invalid former process group")
        require(not group_alive(pid), "former process group is still present")
    result = subprocess.run(
        ["ps", "-axo", "pid=,args="],
        capture_output=True,
        text=True,
        check=True,
        timeout=5,
    )
    for line in result.stdout.splitlines():
        parts = line.split(None, 1)
        if len(parts) == 2 and parts[0].isdigit() and int(parts[0]) != os.getpid():
            require(
                record["run_id"] not in parts[1] and record["run_dir"] not in parts[1],
                "former run still appears in current processes",
            )
    return {
        "checked_at": now().isoformat(),
        "ps_sha256": hashlib.sha256(result.stdout.encode()).hexdigest(),
        "former_pids_groups_and_commands_absent": True,
    }


def immutable_json(path, value):
    """Idempotent exact durable evidence; never overwrite a different intent/outcome."""
    if path.exists():
        require(read_json(path) == value, "conflicting durable " + path.name)
        return
    atomic_json(path, value)


def recover_stale_runs(config, descriptor, allow_publication_recovery=False):
    owner = owner_evidence(config, descriptor)
    recovered = []
    root = config["state_dir"] / "runs"
    if not root.exists():
        return recovered
    for state_path in sorted(root.glob("*/state.json")):
        record = read_json(state_path)
        if record.get("status") != "running":
            continue
        run_dir = state_path.parent
        require(
            run_dir.parent.resolve() == root.resolve()
            and Path(record.get("run_dir", "")).resolve() == run_dir.resolve()
            and record.get("run_id") == run_dir.name,
            "former run state identity mismatch",
        )
        request = read_json(run_dir / "request.json")
        require(
            request.get("run_id") == record["run_id"],
            "former request identity mismatch",
        )
        proof = former_processes_absent(record)
        old_bytes = state_path.read_bytes()
        old_sha = hashlib.sha256(old_bytes).hexdigest()
        archive = run_dir / ("state.before-recovery-" + old_sha + ".json")
        if not archive.exists():
            with archive.open("xb") as stream:
                stream.write(old_bytes)
                stream.flush()
                os.fsync(stream.fileno())
        require(digest(archive) == old_sha, "stale state archive mismatch")
        final_path = run_dir / "outcome.json"
        if final_path.exists():
            final = read_json(final_path)
            require(
                final.get("run_id") == record["run_id"]
                and final.get("status")
                in {"complete", "incomplete", "process_failure"},
                "invalid durable terminal outcome",
            )
            reason = "durable_terminal_outcome_reconciled"
        else:
            final = dict(record)
            final.update(
                status="process_failure",
                finished_at=now().isoformat(),
                reasons=[
                    "Interrupted former dispatcher; owner and process absence verified"
                ],
                recovered_from_running=True,
            )
            acceptance = run_dir / "acceptance.json"
            if acceptance.exists():
                final["preserved_acceptance_sha256"] = digest(acceptance)
            if (run_dir / "publication_intent.json").exists():
                final["publication_status"] = "unknown_requires_review_no_replay"
                try:
                    pub_request = read_json(run_dir / "publication_request.json")
                    pub_result = read_json(run_dir / "publication_result.json")
                    worker_result = read_json(run_dir / "worker_result.json")
                    require(
                        worker_result.get("run_id") == record["run_id"]
                        and worker_result.get("worker_exit_code") == 0
                        and worker_result.get("report_sha256")
                        == digest(acceptance)
                        == pub_request["worker_acceptance_sha256"],
                        "former accepted worker proof differs",
                    )
                    require(
                        read_json(run_dir / "publication_intent.json")["request_sha256"]
                        == digest(run_dir / "publication_request.json"),
                        "former publication intent differs",
                    )
                    validate_publication(pub_result, pub_request)
                    status = (
                        "incomplete"
                        if "incomplete"
                        in (worker_result["status"], pub_result["status"])
                        else "complete"
                    )
                    final.update(
                        status=status,
                        reasons=worker_result["reasons"],
                        publication_status=pub_result["status"],
                        publication_result_sha256=digest(
                            run_dir / "publication_result.json"
                        ),
                        recovered_publication_receipt_without_replay=True,
                    )
                except (OSError, ValueError, KeyError, TypeError) as exc:
                    final["publication_recovery_limitation"] = str(exc)
            reason = "abandoned_execution_not_replayed"
            immutable_json(final_path, final)
        recovery_path = run_dir / "recovery.json"
        if recovery_path.exists():
            prior = read_json(recovery_path)
            require(
                prior.get("former_state_sha256") == old_sha
                and prior.get("outcome_sha256") == digest(final_path),
                "conflicting recovery evidence",
            )
        else:
            immutable_json(
                recovery_path,
                {
                    "run_id": record["run_id"],
                    "former_state_sha256": old_sha,
                    "archive": str(archive),
                    "owner": owner,
                    "process_evidence": proof,
                    "reason": reason,
                    "outcome_sha256": digest(final_path),
                },
            )
        atomic_json(state_path, final)
        latest_path = config["state_dir"] / "state.json"
        if (
            latest_path.exists()
            and read_json(latest_path).get("run_id") == record["run_id"]
        ):
            atomic_json(latest_path, final)
        recovered.append(
            {
                "run_id": record["run_id"],
                "reason": reason,
                "publication_status": final.get("publication_status"),
            }
        )
    # An interrupted publication may already have committed. Never issue another
    # publication automatically until that ambiguity has been explicitly resolved.
    if not allow_publication_recovery:
        require(
            not unresolved_publications(root),
            "Unresolved prior publication intent; use explicit --recover-publication before new work",
        )
    return recovered


def tick(config, expected, recover_publication=False):
    # Guard before creating a lock/runtime directory, including after config load.
    OBSERVABILITY.storage_check(config)
    lock_path = config["shared_lock_path"]
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {
                "status": "lock_busy",
                "reason": "another cooperating jobagg entry point holds the shared lock",
            }
        if config.get("maintenance_file") and config["maintenance_file"].exists():
            return {
                "status": "maintenance_paused",
                "maintenance_file": str(config["maintenance_file"]),
                "reason": "operator maintenance file is present; no work dispatched",
            }
        condition = OBSERVABILITY.condition_fingerprint(config, Path(__file__))
        held = OBSERVABILITY.failure_hold(config, condition)
        if held:
            return held
        try:
            recovered = recover_stale_runs(
                config,
                descriptor,
                allow_publication_recovery=recover_publication
                or bool(config["publication_argv"]),
            )
            if recover_publication or unresolved_publications(
                config["state_dir"] / "runs"
            ):
                result = resume_publication(config, expected, descriptor)
            else:
                result = locked_tick(config, expected, descriptor, recovered)
        except Exception as exc:
            result = {
                "status": "process_failure",
                "reasons": [type(exc).__name__ + ": " + str(exc)],
                "unresolved_publication": bool(
                    unresolved_publications(config["state_dir"] / "runs")
                ),
            }
        OBSERVABILITY.record_failure(config, condition, result)
        return result
    finally:
        os.close(descriptor)


def wait_with_deadlines(process, timeout, absolute_deadline=None):
    """Bound child wait across suspend and wall-clock adjustments."""
    elapsed_end = time.monotonic() + timeout
    wall_end = min(time.time() + timeout, absolute_deadline) if absolute_deadline is not None else time.time() + timeout
    while process.poll() is None:
        remaining = min(elapsed_end - time.monotonic(), wall_end - time.time())
        if remaining <= 0:
            raise subprocess.TimeoutExpired(process.args, timeout)
        try:
            process.wait(timeout=min(0.25, remaining))
        except subprocess.TimeoutExpired:
            continue


def replace_tokens(argv, replacements):
    result = list(argv)
    for token, value in replacements.items():
        result = [part.replace(token, str(value)) for part in result]
    require(
        all(not re.search(r"\{[a-z_]+\}", part) for part in result),
        "unknown command placeholder",
    )
    return result


def snapshot_database(path):
    require(
        path.is_file() and not path.is_symlink(),
        "publication database missing or aliased",
    )
    snapshot = {"path": str(path), "sha256": digest(path), "size": path.stat().st_size}
    wal = Path(str(path) + "-wal")
    snapshot["wal"] = (
        {"path": str(wal), "sha256": digest(wal), "size": wal.stat().st_size}
        if wal.exists()
        else None
    )
    return snapshot


PUBLICATION_BINDINGS = (
    "run_id",
    "source_manifest_sha256",
    "registry_sha256",
    "expected_source_ids",
    "worker_acceptance_path",
    "worker_acceptance_sha256",
    "worker_database",
    "worker_database_files",
)


def publication_request(
    config, request, report_path, worker_status, deadline, owner_fd
):
    report = read_json(report_path)
    require(
        Path(report.get("database", "")).resolve()
        == config["publication_worker_database"].resolve(),
        "worker report database differs from configured publication source",
    )
    OBSERVABILITY.storage_check(config)
    snapshot_fields = {}
    if config["sealed_publication_snapshots"]:
        snapshot = snapshot_helper(config).create_publication_snapshot(
            config["publication_worker_database"],
            report_path.parent / "publication_snapshot.sqlite3",
            digest(report_path),
            owner_fd,
            config["shared_lock_path"],
            deadline_at=time.monotonic() + max(0, deadline.timestamp() - time.time()),
            projection=config["publication_snapshot_mode"] == "projection",
        )
        files = [{"path": snapshot["path"], "sha256": snapshot["sha256"]}]
        snapshot_fields["publication_snapshot"] = snapshot
    else:
        snapshot = snapshot_database(config["publication_worker_database"])
        files = [{"path": snapshot["path"], "sha256": snapshot["sha256"]}]
        if snapshot["wal"]:
            files.append(
                {"path": snapshot["wal"]["path"], "sha256": snapshot["wal"]["sha256"]}
            )
    return {
        "schema_version": 1,
        "run_id": request["run_id"],
        "generated_at": now().isoformat(),
        "deadline_at": deadline.isoformat(),
        "deadline_epoch": deadline.timestamp(),
        "expected_source_ids": request["expected_source_ids"],
        "source_manifest_sha256": request["source_manifest_sha256"],
        "registry_sha256": config["_registry_sha256"],
        "worker_acceptance_path": str(report_path),
        "worker_acceptance_sha256": digest(report_path),
        "worker_status": worker_status,
        "worker_database": str(config["publication_worker_database"]),
        "worker_database_files": files,
        "shared_lock_path": str(config["shared_lock_path"]),
        **snapshot_fields,
    }


def validate_publication(result, request):
    require(
        isinstance(result, dict) and result.get("schema_version") == 1,
        "invalid publication receipt",
    )
    for key in ("publication_snapshot", "recovery_generation_binding"):
        require(
            (key in result) == (key in request),
            "publication " + key + " presence differs",
        )
    for key in PUBLICATION_BINDINGS + tuple(
        key
        for key in ("publication_snapshot", "recovery_generation_binding")
        if key in request
    ):
        require(
            result.get(key) == request[key],
            "publication receipt binding differs: " + key,
        )
    require(
        result.get("status") in {"published", "incomplete", "noop", "deferred"},
        "publication did not succeed",
    )
    if result.get("status") == "deferred":
        publication = result.get("publication", {})
        require(
            publication.get("status") == "deferred"
            and publication.get("generation_not_started") is True
            and not publication.get("generation_id")
            and not request.get("recover_publication")
            and "recovery_generation_binding" not in request,
            "Deferred publication lacks proof that no generation started",
        )
    fresh(
        result.get("generated_at"),
        timestamp(request["generated_at"]),
        now(),
        "publication receipt",
    )
    require(
        isinstance(result.get("observation_set_sha256"), str)
        and re.fullmatch(r"[0-9a-f]{64}", result["observation_set_sha256"]),
        "publication observation-set proof missing",
    )
    if "recovery_generation_binding" in request:
        binding = request["recovery_generation_binding"]
        require(
            result.get("observation_set_sha256") == binding["observation_set_sha256"]
            and result.get("publication", {}).get("generation_id")
            == binding["generation_id"],
            "publication receipt returned another recovery generation",
        )
    require(
        result.get("whole_job_completeness_certified") is False,
        "publication cannot manufacture whole-job certification",
    )


def unresolved_publications(root):
    unknown = []
    for outcome in root.glob("*/outcome.json"):
        value = read_json(outcome)
        if value.get("publication_status") not in {
            "unknown_requires_review_no_replay",
            "incomplete",
        }:
            continue
        no_write = outcome.parent / "publication_no_write_resolution.json"
        if no_write.exists():
            resolved = read_json(no_write)
            original = outcome.parent / "publication_request.json"
            proof = resolved.get("proof", {})
            gate = proof.get("prior_gate", {})
            require(
                resolved.get("run_id") == value["run_id"]
                and resolved.get("previous_outcome_sha256") == digest(outcome)
                and resolved.get("original_request_sha256") == digest(original)
                and resolved.get("original_intent_sha256") == digest(outcome.parent / "publication_intent.json")
                and proof.get("kind") == "verified_unstarted_publication"
                and proof.get("generation_not_started") is True
                and gate.get("state") == "complete"
                and timestamp(gate["completed_at"]) < timestamp(read_json(original)["generated_at"]),
                "invalid unstarted publication resolution",
            )
            for key in ("prior_plan", "prior_result"):
                require(digest(Path(proof[key]["path"])) == proof[key]["sha256"],
                        "unstarted publication journal evidence changed")
            require(read_json(Path(proof["prior_result"]["path"])) == gate,
                    "unstarted publication result differs")
            continue
        resolution = outcome.parent / "publication_resolution.json"
        if resolution.exists():
            resolved = read_json(resolution)
            require(
                resolved.get("run_id") == value["run_id"]
                and resolved.get("previous_outcome_sha256") == digest(outcome),
                "invalid publication resolution",
            )
            request_path = evidence_path(resolved["request"], outcome.parent)
            result_path = evidence_path(resolved["result"], outcome.parent)
            recovery_request = read_json(request_path)
            original_path = outcome.parent / "publication_request.json"
            original = read_json(original_path)
            require(
                recovery_request.get("run_id") == value["run_id"]
                and recovery_request.get("recover_publication") is True
                and isinstance(
                    recovery_request.get("original_publication_request"), dict
                )
                and Path(
                    recovery_request["original_publication_request"].get("path", "")
                ).resolve()
                == original_path.resolve()
                and recovery_request["original_publication_request"].get("sha256")
                == digest(original_path)
                and recovery_request.get("original_publication_intent_sha256")
                == digest(outcome.parent / "publication_intent.json")
                and ("publication_snapshot" in recovery_request)
                == ("publication_snapshot" in original)
                and all(
                    recovery_request.get(k) == original.get(k)
                    for k in PUBLICATION_BINDINGS
                    + (
                        ("publication_snapshot",)
                        if "publication_snapshot" in original
                        else ()
                    )
                ),
                "publication resolution borrowed a different intent or scope",
            )
            require(
                ("recovery_generation_binding" in recovery_request)
                == (outcome.parent / "publication_generation_binding.json").exists(),
                "recovery generation binding presence differs",
            )
            if "recovery_generation_binding" in recovery_request:
                require(
                    recovery_request["recovery_generation_binding"]
                    == read_json(
                        outcome.parent / "publication_generation_binding.json"
                    ),
                    "recovery borrowed a different publication generation",
                )
            validate_publication(read_json(result_path), recovery_request)
        else:
            unknown.append(outcome)
    return unknown


def recovery_generation_fields(
    config, original, validated_snapshot, run_dir, deadline_at
):
    argv = config["publication_argv"]
    standard = any(
        argv[i : i + 2] == ["-m", "jobagg.publish_worker"] for i in range(len(argv) - 1)
    )
    if not standard:
        return {}  # A custom publisher owns its own idempotent journal contract.
    paths = {}
    for flag in ("--output-dir", "--state-dir"):
        require(
            argv.count(flag) == 1, "Standard publisher recovery requires unique " + flag
        )
        index = argv.index(flag)
        require(
            index + 1 < len(argv) and Path(argv[index + 1]).is_absolute(),
            "Invalid recovery " + flag,
        )
        paths[flag] = Path(argv[index + 1])
    path = run_dir / "publication_generation_binding.json"
    prior = read_json(path) if path.exists() else None
    binding = snapshot_helper(config).capture_recovery_generation(
        original,
        validated_snapshot["effective_database"],
        paths["--output-dir"],
        paths["--state-dir"],
        prior_binding=prior,
        owner_held=True,
        deadline_at=deadline_at,
        expired_authorization=(read_json(run_dir / "reviewed_expired_publication.json")
                               if (run_dir / "reviewed_expired_publication.json").exists() else None),
    )
    immutable_json(path, binding)
    return {"recovery_generation_binding": binding}


def resume_publication(config, expected, descriptor):
    """Explicitly resume the publisher's same journal; never rerun the worker."""
    OBSERVABILITY.storage_check(config)
    unknown = unresolved_publications(config["state_dir"] / "runs")
    require(
        len(unknown) == 1,
        "explicit recovery requires exactly one unresolved publication",
    )
    outcome_path = unknown[0]
    run_dir = outcome_path.parent
    previous_outcome = read_json(outcome_path)
    require(
        previous_outcome.get("publication_recovery_allowed") is True,
        "prior publication receipt/evidence is invalid; automatic recovery is held",
    )
    intent = read_json(run_dir / "publication_intent.json")
    original_path = run_dir / "publication_request.json"
    original = read_json(original_path)
    require(
        intent.get("config_sha256") == config["_config_sha256"]
        and intent.get("command_template") == config["publication_argv"]
        and intent.get("cwd") == str(config["publication_cwd"]),
        "recovery configuration differs from immutable intent",
    )
    require(
        intent.get("request_sha256") == digest(original_path),
        "original publication request changed",
    )
    require(
        original["expected_source_ids"] == expected
        and original["source_manifest_sha256"] == config["_manifest_sha256"]
        and original["registry_sha256"] == config["_registry_sha256"]
        and original["worker_database"] == str(config["publication_worker_database"]),
        "recovery source binding differs",
    )
    require(
        digest(Path(original["worker_acceptance_path"]))
        == original["worker_acceptance_sha256"],
        "recovery acceptance changed",
    )
    validation_deadline = time.monotonic() + min(
        config["publication_timeout_seconds"],
        config["total_timeout_seconds"] - config["terminate_grace_seconds"],
    )
    owner_evidence(config, descriptor)
    validated_snapshot = snapshot_helper(config).validate_publication_snapshot(
        original,
        config["publication_worker_database"],
        owner_held=True,
        deadline_at=validation_deadline,
    )
    OBSERVABILITY.storage_check(config)
    # A completed preceding gate proves this intent never began live writes.
    # Release only that intent; accepted worker data remains for the next tick.
    argv = config["publication_argv"]
    standard = any(argv[i:i+2] == ["-m", "jobagg.publish_worker"] for i in range(len(argv)-1))
    if standard and not (run_dir / "publication_generation_binding.json").exists():
        output = Path(argv[argv.index("--output-dir") + 1])
        journal = Path(argv[argv.index("--state-dir") + 1])
        gate_path = output / ".jobagg-publication-state.json"
        gate = read_json(gate_path) if gate_path.exists() else {}
        if gate.get("state") == "complete" and timestamp(gate["completed_at"]) < timestamp(original["generated_at"]):
            require(not any(group_alive(pid) for pid in previous_outcome.get("process_groups", [])),
                    "original publication still has live processes")
            proof = snapshot_helper(config).capture_unstarted_publication(
                original, output, journal, owner_held=True, deadline_at=validation_deadline)
            immutable_json(run_dir / "publication_no_write_resolution.json", {
                "run_id": original["run_id"], "previous_outcome_sha256": digest(outcome_path),
                "original_request_sha256": digest(original_path),
                "original_intent_sha256": digest(run_dir / "publication_intent.json"),
                "resolved_at": now().isoformat(), "proof": proof,
            })
            result = {**previous_outcome, "status": "incomplete", "phase": "finished",
                      "publication_status": "deferred", "publication_recovery_allowed": False,
                      "generation_not_started": True, "worker_reexecuted": False,
                      "reasons": ["Unstarted publication safely deferred; accepted data retained for next tick"],
                      "finished_at": now().isoformat()}
            atomic_json(run_dir / "state.json", result)
            atomic_json(config["state_dir"] / "state.json", result)
            return result
    generation_fields = recovery_generation_fields(
        config, original, validated_snapshot, run_dir, validation_deadline
    )
    attempt = run_dir / "publication_recovery" / str(uuid.uuid4())
    attempt.mkdir(parents=True)
    immutable_json(
        attempt / "snapshot_validation.json", validated_snapshot["validation_receipt"]
    )
    timeout = validation_deadline - time.monotonic()
    require(timeout > 0, "no recovery budget")
    deadline = now() + timedelta(seconds=timeout)
    request = {
        **original,
        "generated_at": now().isoformat(),
        "deadline_at": deadline.isoformat(),
        "deadline_epoch": deadline.timestamp(),
        "recover_publication": True,
        **generation_fields,
        "original_publication_request": {
            "path": str(original_path),
            "sha256": digest(original_path),
        },
        "original_publication_intent_sha256": digest(
            run_dir / "publication_intent.json"
        ),
    }
    request_path, report_path = attempt / "request.json", attempt / "result.json"
    immutable_json(request_path, request)
    tokens = {
        "{request_path}": str(run_dir / "request.json"),
        "{report_path}": str(run_dir / "acceptance.json"),
        "{run_id}": original["run_id"],
        "{publication_request_path}": str(request_path),
        "{publication_report_path}": str(report_path),
        "{publication_deadline_at}": deadline.isoformat(),
        "{publication_max_seconds}": timeout,
        "{shared_lock_fd}": descriptor,
    }
    result_state = {
        "schema_version": 1,
        "run_id": original["run_id"],
        "status": "running",
        "recovery_attempt": str(attempt),
        "snapshot_validation": {
            "path": str(attempt / "snapshot_validation.json"),
            "sha256": digest(attempt / "snapshot_validation.json"),
        },
        "started_at": now().isoformat(),
    }
    process, handlers = None, {}

    def interrupted(signum, _frame):
        raise InterruptedError(f"publication recovery received signal {signum}")

    try:
        for sig in (signal.SIGTERM, signal.SIGINT):
            handlers[sig] = signal.signal(sig, interrupted)
        atomic_json(attempt / "state.json", result_state)
        environment = dict(
            os.environ,
            JOBAGG_SHARED_LOCK_FD=str(descriptor),
            PYTHONUNBUFFERED="1",
            PYTHONHASHSEED="0",
            PYTHONPATH=str(config["publication_cwd"]),
        )
        with (
            (attempt / "stdout.log").open("wb") as stdout,
            (attempt / "stderr.log").open("wb") as stderr,
        ):
            OBSERVABILITY.storage_check(config)
            process = subprocess.Popen(
                replace_tokens(config["publication_argv"], tokens),
                cwd=config["publication_cwd"],
                env=environment,
                stdout=stdout,
                stderr=stderr,
                start_new_session=True,
                pass_fds=(descriptor,),
                shell=False,
            )
            result_state["publication_pid"] = process.pid
            atomic_json(attempt / "state.json", result_state)
            wait_with_deadlines(process, timeout, deadline.timestamp())
        require(
            process.returncode == 0 and not group_alive(process.pid),
            "recovery process failed or leaked descendants",
        )
        result = read_json(
            report_path if report_path.exists() else attempt / "stdout.log"
        )
        validate_publication(result, request)
        immutable_json(report_path, result)
        if result["status"] != "incomplete":
            immutable_json(
                run_dir / "publication_resolution.json",
                {
                    "run_id": original["run_id"],
                    "previous_outcome_sha256": digest(outcome_path),
                    "recovered_at": now().isoformat(),
                    "request": {
                        "path": str(request_path.relative_to(run_dir)),
                        "sha256": digest(request_path),
                    },
                    "result": {
                        "path": str(report_path.relative_to(run_dir)),
                        "sha256": digest(report_path),
                    },
                },
            )
        status = (
            "incomplete"
            if "incomplete" in (original["worker_status"], result["status"])
            else "complete"
        )
        result_state.update(
            status=status,
            publication_status=result["status"],
            reasons=[],
            worker_reexecuted=False,
        )
    except Exception as exc:
        result_state.update(
            status="process_failure",
            publication_status="unknown_requires_review_no_replay",
            reasons=[type(exc).__name__ + ": " + str(exc)],
            worker_reexecuted=False,
        )
    finally:
        for sig in handlers:
            signal.signal(sig, signal.SIG_IGN)
        try:
            if process is not None:
                terminate_group(process, config["terminate_grace_seconds"])
            result_state["finished_at"] = now().isoformat()
            atomic_json(attempt / "state.json", result_state)
            if (
                result_state["status"] in {"complete", "incomplete"}
                and (run_dir / "publication_resolution.json").exists()
            ):
                resolved_state = {
                    **previous_outcome,
                    "original_finished_at": previous_outcome.get("finished_at"),
                    "finished_at": result_state["finished_at"],
                    "status": result_state["status"],
                    "publication_status": result_state["publication_status"],
                    "publication_resolution_sha256": digest(
                        run_dir / "publication_resolution.json"
                    ),
                    "publication_recovery_allowed": False,
                    "reasons": previous_outcome.get("worker_reasons", []),
                }
                atomic_json(run_dir / "state.json", resolved_state)
                latest = config["state_dir"] / "state.json"
                if (
                    latest.exists()
                    and read_json(latest).get("run_id") == original["run_id"]
                ):
                    atomic_json(latest, resolved_state)
                if resolved_state["status"] == "complete":
                    atomic_json(
                        config["state_dir"] / "last_complete.json", resolved_state
                    )
        finally:
            for sig, handler in handlers.items():
                signal.signal(sig, handler)
    return result_state


def locked_tick(config, expected, lock_descriptor, recovered=None):
    OBSERVABILITY.storage_check(config)
    run_id, started = str(uuid.uuid4()), now()
    hard_end = time.monotonic() + config["total_timeout_seconds"]
    run_dir = config["state_dir"] / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    request_path, report_path = run_dir / "request.json", run_dir / "acceptance.json"
    manifest_path = run_dir / "source_manifest.json"
    manifest_path.write_bytes(config["source_manifest_path"].read_bytes())
    request = {
        "schema_version": 1,
        "run_id": run_id,
        "started_at": started.isoformat(),
        "deadline_at": (
            started + timedelta(seconds=config["timeout_seconds"])
        ).isoformat(),
        "tick_deadline_at": (
            started + timedelta(seconds=config["total_timeout_seconds"])
        ).isoformat(),
        "source_manifest_path": str(manifest_path),
        "source_manifest_sha256": digest(manifest_path),
        "source_registry": {
            "path": str(config["_registry_path"]),
            "sha256": config["_registry_sha256"],
        },
        "expected_source_ids": expected,
        "report_path": str(report_path),
        "freshness_limits": {
            k: config[k] for k in ("listing_max_age_seconds", "detail_max_age_seconds")
        },
    }
    record = {
        "schema_version": 1,
        "run_id": run_id,
        "status": "running",
        "phase": "preparing",
        "started_at": started.isoformat(),
        "wrapper_pid": os.getpid(),
        "process_groups": [],
        "run_dir": str(run_dir),
        "request_path": str(request_path),
        "report_path": str(report_path),
        "publication_status": "not_configured"
        if not config["publication_argv"]
        else "not_started",
        "prior_reconciliations": recovered or [],
    }

    def save():
        atomic_json(run_dir / "state.json", record)
        atomic_json(config["state_dir"] / "state.json", record)

    previous, process = {}, None
    concurrency_context = None

    def interrupted(signum, _frame):
        raise InterruptedError(f"wrapper received signal {signum}")

    environment = dict(
        os.environ,
        JOBAGG_SHARED_LOCK_FD=str(lock_descriptor),
        PYTHONUNBUFFERED="1",
        PYTHONHASHSEED="0",
    )

    def execute(argv, cwd, phase, timeout, absolute_deadline=None):
        nonlocal process
        OBSERVABILITY.storage_check(config)
        require(
            not (
                config.get("maintenance_file") and config["maintenance_file"].exists()
            ),
            "operator maintenance requested before " + phase + " dispatch",
        )
        remaining = hard_end - time.monotonic()
        require(remaining > 0, "tick total execution budget exhausted")
        phase_timeout = min(
            timeout, max(0, remaining - config["terminate_grace_seconds"])
        )
        require(phase_timeout > 0, "no command budget remains after cleanup reserve")
        record["phase"] = phase + "_starting"
        save()
        stdout_path = run_dir / (
            "stdout.log" if phase == "worker" else "publication_stdout.log"
        )
        stderr_path = run_dir / (
            "stderr.log" if phase == "worker" else "publication_stderr.log"
        )
        with stdout_path.open("wb") as stdout, stderr_path.open("wb") as stderr:
            OBSERVABILITY.storage_check(config)
            process = subprocess.Popen(
                argv,
                cwd=cwd,
                env={**environment, "PYTHONPATH": str(cwd)},
                shell=False,
                stdout=stdout,
                stderr=stderr,
                start_new_session=True,
                pass_fds=(lock_descriptor,),
            )
            record[phase + "_pid"] = process.pid
            record["process_groups"].append(process.pid)
            record["phase"] = phase + "_running"
            save()
            timed_out = False
            try:
                wait_with_deadlines(process, phase_timeout, absolute_deadline)
            except subprocess.TimeoutExpired:
                timed_out = True
                record[phase + "_timeout"] = {
                    "deadline_reached_at": now().isoformat(),
                    "timeout_seconds": phase_timeout,
                }
                record["phase"] = phase + "_stopping"
                save()
                cleanup = terminate_group(
                    process,
                    min(
                        config["terminate_grace_seconds"],
                        max(0, hard_end - time.monotonic()),
                    ),
                )
                record[phase + "_timeout"].update(cleanup)
            record[phase + "_exit_code"] = process.returncode
        require(
            process.returncode == 0,
            f"{phase} exited {process.returncode}; acceptance cannot override process failure",
        )
        require(
            not group_alive(process.pid),
            f"{phase} exited with surviving processes in its process group",
        )
        return timed_out, stdout_path

    try:
        for signum in (signal.SIGTERM, signal.SIGINT):
            previous[signum] = signal.signal(signum, interrupted)
        concurrency_context = begin_concurrency(config, record)
        if concurrency_context:
            request["parallel_sources"] = concurrency_context["state"]["limit"]
        atomic_json(request_path, request)
        save()
        require(
            request["source_manifest_sha256"] == config["_manifest_sha256"],
            "source manifest changed before dispatch",
        )
        require(
            digest(config["_registry_path"]) == config["_registry_sha256"],
            "registry changed before dispatch",
        )
        require(
            bool(config["worker_argv"]),
            "worker_argv is empty: worker is not configured",
        )
        replacements = {
            "{request_path}": str(request_path),
            "{report_path}": str(report_path),
            "{run_id}": run_id,
            "{parallel_sources}": str(request.get("parallel_sources", 1)),
        }
        timed_out, _ = execute(
            replace_tokens(config["worker_argv"], replacements),
            config["worker_cwd"],
            "worker",
            config["timeout_seconds"],
        )
        require(
            digest(config["source_manifest_path"]) == request["source_manifest_sha256"],
            "source manifest changed during tick",
        )
        require(
            digest(manifest_path) == request["source_manifest_sha256"],
            "worker modified frozen manifest",
        )
        require(
            digest(config["_registry_path"]) == config["_registry_sha256"],
            "registry changed during tick",
        )
        status, reasons = validate_report(report_path, request, now())
        if timed_out:
            status = "incomplete"
            reasons.append(
                "Worker reached its deadline and returned valid acceptance during graceful shutdown"
            )
        record.update(
            worker_status=status,
            worker_reasons=reasons,
            worker_report_sha256=digest(report_path),
            phase="worker_validated",
        )
        immutable_json(
            run_dir / "worker_result.json",
            {
                "run_id": run_id,
                "status": status,
                "reasons": reasons,
                "report_sha256": digest(report_path),
                "worker_exit_code": 0,
                "graceful_timeout": timed_out,
            },
        )
        save()
        if config["publication_argv"]:
            remaining = min(
                config["publication_timeout_seconds"],
                hard_end - time.monotonic() - config["terminate_grace_seconds"],
            )
            require(remaining > 0, "no publication budget remains")
            publication_deadline = now() + timedelta(seconds=remaining)
            pub_request = publication_request(
                config,
                request,
                report_path,
                status,
                publication_deadline,
                lock_descriptor,
            )
            remaining = min(config["publication_timeout_seconds"],
                            hard_end - time.monotonic() - config["terminate_grace_seconds"])
            require(remaining > 0, "snapshot preparation exhausted total publication budget")
            publication_deadline = now() + timedelta(seconds=remaining)
            pub_request.update(generated_at=now().isoformat(),
                               deadline_at=publication_deadline.isoformat(),
                               deadline_epoch=publication_deadline.timestamp())
            pub_path = run_dir / "publication_request.json"
            immutable_json(pub_path, pub_request)
            pub_result_path = run_dir / "publication_result.json"
            # The intent precedes spawn. A crash in the spawn/commit gap is unknown,
            # never permission to publish the same run again.
            immutable_json(
                run_dir / "publication_intent.json",
                {
                    "run_id": run_id,
                    "request_sha256": digest(pub_path),
                    "created_at": now().isoformat(),
                    "config_sha256": config["_config_sha256"],
                    "command_template": config["publication_argv"],
                    "cwd": str(config["publication_cwd"]),
                },
            )
            record["publication_status"] = "intent_recorded"
            record["publication_recovery_allowed"] = True
            save()
            remaining = min(
                remaining,
                publication_deadline.timestamp() - time.time(),
                hard_end - time.monotonic() - config["terminate_grace_seconds"],
            )
            require(remaining > 0, "snapshot preparation consumed publication budget")
            publication_tokens = {
                **replacements,
                "{publication_request_path}": str(pub_path),
                "{publication_report_path}": str(pub_result_path),
                "{publication_deadline_at}": publication_deadline.isoformat(),
                "{publication_max_seconds}": max(0.001, remaining),
                "{shared_lock_fd}": lock_descriptor,
            }
            _, stdout_path = execute(
                replace_tokens(config["publication_argv"], publication_tokens),
                config["publication_cwd"],
                "publication",
                remaining,
                absolute_deadline=publication_deadline.timestamp(),
            )
            record["publication_recovery_allowed"] = False
            save()
            result = read_json(
                pub_result_path if pub_result_path.exists() else stdout_path
            )
            validate_publication(result, pub_request)
            require(
                digest(report_path) == pub_request["worker_acceptance_sha256"],
                "publication modified acceptance",
            )
            immutable_json(pub_result_path, result)
            record.update(
                publication_status=result["status"],
                publication_result_sha256=digest(pub_result_path),
                publication_recovery_allowed=result["status"] == "incomplete",
            )
            if result["status"] in {"incomplete", "deferred"}:
                status = "incomplete"
                reasons.append("Publisher reported bounded incomplete work")
        record.update(status=status, reasons=reasons)
    except Exception as exc:
        record.update(
            status="process_failure", reasons=[f"{type(exc).__name__}: {exc}"]
        )
        if record["publication_status"] == "intent_recorded":
            record["publication_status"] = "unknown_requires_review_no_replay"
    finally:
        for signum in previous:
            signal.signal(signum, signal.SIG_IGN)
        try:
            if process is not None:
                cleanup = terminate_group(
                    process,
                    min(
                        config["terminate_grace_seconds"],
                        max(0, hard_end - time.monotonic()),
                    ),
                )
                record["final_cleanup"] = cleanup
        except Exception as exc:
            record.update(
                status="process_failure",
                cleanup_failure=type(exc).__name__ + ": " + str(exc),
            )
        # Journal terminal outcome before replacing either summary. If any latter
        # replace is interrupted, the next owner repairs summaries without replay.
        record.update(finished_at=now().isoformat(), phase="finished")
        concurrency_terminal = None
        try:
            concurrency_terminal = finish_concurrency(concurrency_context, record, report_path)
        except Exception as exc:
            # Leave an unfinished marker for the next owner. A failed controller
            # update cannot earn health credit or cause a silent reset.
            record.update(
                status="process_failure",
                concurrency_failure=type(exc).__name__ + ": " + str(exc),
            )
        try:
            record["finished_at"] = now().isoformat()
            immutable_json(run_dir / "outcome.json", record)
            if concurrency_terminal is not None:
                atomic_json(concurrency_context["path"], concurrency_terminal)
            save()
            if record["status"] == "complete":
                atomic_json(config["state_dir"] / "last_complete.json", record)
        finally:
            for signum, handler in previous.items():
                signal.signal(signum, handler)
    return record


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--execute",
        action="store_true",
        help="explicitly run one configured worker tick",
    )
    mode.add_argument(
        "--dry-run",
        action="store_true",
        help="default; inspect configuration without writes or dispatch",
    )
    parser.add_argument(
        "--recover-publication",
        action="store_true",
        help="with --execute, resume only the exact unresolved publisher journal before any new fetch",
    )
    mode.add_argument(
        "--health",
        action="store_true",
        help="Read internal invocation/publication health; no writes or mount dependency",
    )
    args = parser.parse_args(argv)
    attempt = intent = None
    try:
        config_path = Path(os.path.abspath(args.config))
        if args.health:
            raw = read_json(config_path)
            directory = resolve(
                config_path.parent,
                raw.get(
                    "attempt_state_dir", raw.get("state_dir", "dispatcher-attempts")
                ),
            )
            print(json.dumps(OBSERVABILITY.health_view(directory), indent=2))
            return 0
        if args.execute:
            os.umask(0o077)
            raw = read_json(config_path)
            attempt, intent = OBSERVABILITY.start_attempt(config_path, raw)
        config, expected = load_config(config_path, record_storage_health=args.execute)
        if not args.execute:
            result = {
                "status": "dry_run",
                "would_execute": config["worker_argv"],
                "worker_configured": bool(config["worker_argv"]),
                "expected_sources": len(expected),
                "manifest_sha256": digest(config["source_manifest_path"]),
                "state_dir": str(config["state_dir"]),
                "shared_lock_path": str(config["shared_lock_path"]),
                "timeout_seconds": config["timeout_seconds"],
                "maintenance_file": str(config["maintenance_file"])
                if config.get("maintenance_file")
                else None,
                "maintenance_active": bool(
                    config.get("maintenance_file")
                    and config["maintenance_file"].exists()
                ),
                "publication_configured": bool(config["publication_argv"]),
                "would_publish": config["publication_argv"],
                "total_timeout_seconds": config["total_timeout_seconds"],
                "storage": OBSERVABILITY.storage_check(config),
                "attempt_state_dir": str(config["attempt_state_dir"]),
                "sealed_publication_snapshots": config["sealed_publication_snapshots"],
                "publication_snapshot_mode": config["publication_snapshot_mode"],
                "concurrency_policy": config.get("concurrency"),
                "writes_performed": False,
            }
            print(json.dumps(result, indent=2))
            return 0
        os.umask(0o077)
        result = tick(config, expected, recover_publication=args.recover_publication)
    except Exception as exc:
        result = {
            "status": "process_failure",
            "reasons": [f"{type(exc).__name__}: {exc}"],
        }
    if attempt is not None:
        try:
            OBSERVABILITY.finish_attempt(attempt, intent, result)
        except Exception as exc:
            result = {
                **result,
                "status": "process_failure",
                "attempt_receipt_error": type(exc).__name__ + ": " + str(exc),
            }
    print(json.dumps(result, indent=2))
    return EXIT[result["status"]]


if __name__ == "__main__":
    sys.exit(main())
