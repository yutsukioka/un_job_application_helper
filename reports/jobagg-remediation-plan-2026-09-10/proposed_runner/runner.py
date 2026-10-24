#!/usr/bin/env python3
"""Bounded dispatcher for deterministic fetching and verified live publication.

Dry-run is the default. This file does not implement fetching or install a schedule.
Python 3.11+; macOS/POSIX; standard library only.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager, ExitStack, nullcontext
from datetime import datetime, timedelta, timezone
import fcntl
import hashlib
import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import re
import signal
import stat
import subprocess
import sys
import tempfile
import time
import uuid

_CONTROL_WRITER = None
_ENTRY_DEADLINES = None
_CLOCK_SPEC = importlib.util.spec_from_file_location('_jobagg_entry_clock', Path(__file__).with_name('runtime_clock.py'))
RUNTIME_CLOCK = importlib.util.module_from_spec(_CLOCK_SPEC)
_CLOCK_SPEC.loader.exec_module(RUNTIME_CLOCK)
_ENTRY_CLOCK = None


def check_entry_budget():
    if _ENTRY_CLOCK is not None and RUNTIME_CLOCK.remaining(_ENTRY_CLOCK) <= 0:
        raise TimeoutError("Suspend-aware entry budget exhausted")
    if _ENTRY_DEADLINES is not None:
        mono, wall = _ENTRY_DEADLINES
        if time.monotonic() >= mono or time.time() >= wall:
            raise TimeoutError("Entry-anchored invocation budget exhausted")


NORMALIZATION = "jobtext-v1-whitespace-known-boilerplate"
EXIT = {
    "complete": 0,
    "incomplete": 2,
    "process_failure": 3,
    "lock_busy": 75,
    "maintenance_paused": 75,
    "unchanged_failure_hold": 75,
    "publication_held": 75,
    "storage_deferred": 75,
    "accepted_work_pending": 75,
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


def verify_package_modules(package):
    """Reject an interpreter already holding any other JobAgg implementation."""
    for name, module in tuple(sys.modules.items()):
        if name != "jobagg" and not name.startswith("jobagg."):
            continue
        relative = Path(*name.split(".")[1:])
        expected = {package / relative / "__init__.py"}
        if name != "jobagg":
            expected.add((package / relative).with_suffix(".py"))
        origin = getattr(module, "__file__", None)
        spec = getattr(module, "__spec__", None)
        spec_origin = getattr(spec, "origin", None)
        paths = getattr(module, "__path__", None)
        if name != "jobagg" and origin is None and spec_origin is None:
            # Subpackages such as jobagg.pipelines are implicit namespaces.
            # They have no file origin, so bind both search paths instead.
            namespace = package / relative
            initializer = namespace / "__init__.py"
            spec_paths = getattr(spec, "submodule_search_locations", None)
            require(
                isinstance(getattr(spec, "loader", None), importlib.machinery.NamespaceLoader)
                and getattr(spec, "name", None) == name
                and getattr(spec, "has_location", True) is False
                and namespace.is_dir() and namespace.resolve().is_relative_to(package)
                and not initializer.exists() and not initializer.is_symlink()
                and paths is not None and spec_paths is not None
                and [Path(path).resolve() for path in paths] == [namespace]
                and [Path(path).resolve() for path in spec_paths] == [namespace],
                "Loaded JobAgg module differs from selected implementation: " + name,
            )
            continue
        require(
            bool(origin) and bool(spec_origin)
            and Path(origin).resolve().is_relative_to(package)
            and Path(origin).resolve() in {path.resolve() for path in expected}
            and Path(spec_origin).resolve() == Path(origin).resolve(),
            "Loaded JobAgg module differs from selected implementation: " + name,
        )
        if paths is not None:
            require(
                [Path(path).resolve() for path in paths] == [package / relative],
                "Loaded JobAgg package search path differs from selected implementation: " + name,
            )


def bind_package(cwd):
    """Bind nested helper imports to the same cwd used for its child process.

    A shared interpreter may have an editable install of a different checkout.
    Binding is independent of optional storage-budget activation. Never evict
    already imported modules: mixing releases requires a fresh interpreter.
    """
    selected = str(Path(cwd).resolve())
    package = (Path(selected) / "jobagg").resolve()
    require(
        (package / "__init__.py").is_file()
        and (package / "__init__.py").resolve().is_relative_to(package),
        "Reviewed JobAgg package missing or outside selected package",
    )
    verify_package_modules(package)
    # Move an existing entry too; a stale checkout may precede it on sys.path.
    sys.path[:] = [selected] + [entry for entry in sys.path if entry != selected]
    sys.dont_write_bytecode = True
    importlib.import_module("jobagg")
    verify_package_modules(package)
    return package


def snapshot_helper(config):
    package = bind_package(config["publication_cwd"])
    path = package / "publication_snapshot.py"
    require(
        path.is_file() and path.resolve().is_relative_to(package.resolve()),
        "Reviewed publication snapshot helper missing or outside selected package",
    )
    module = load_file_module(path, "jobagg_dispatcher_publication_snapshot")
    verify_package_modules(package)
    return module


def retention_helper(config):
    package = (config["worker_cwd"] / "jobagg").resolve()
    initializer = package / "__init__.py"
    # This stdlib-only helper also supports standalone worker installations.
    packaged = initializer.exists() or initializer.is_symlink()
    if packaged:
        bind_package(config["worker_cwd"])
    path = package / "storage_retention.py"
    require(
        path.is_file() and path.resolve().is_relative_to(package.resolve()),
        "Reviewed storage retention helper missing or outside selected package",
    )
    module = load_file_module(path, "jobagg_dispatcher_storage_retention")
    if packaged:
        verify_package_modules(package)
    return module


def package_helper(config, name):
    """Import new shared contracts from the selected, reviewed worker package."""
    package = bind_package(config["worker_cwd"])
    require(isinstance(name, str) and re.fullmatch(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*", name),
            "Invalid reviewed package helper name")
    path = package.joinpath(*name.split(".")).with_suffix(".py")
    require(path.is_file() and path.resolve().is_relative_to(package),
            "Reviewed package helper is missing: " + name)
    module = importlib.import_module("jobagg." + name)
    verify_package_modules(package)
    require(Path(module.__file__).resolve() == path,
            "Loaded package helper differs from selected implementation: " + name)
    return module


def concurrency_helper(config):
    package = (config["worker_cwd"] / "jobagg").resolve()
    initializer = package / "__init__.py"
    # This stdlib-only helper also supports standalone worker installations.
    packaged = initializer.exists() or initializer.is_symlink()
    if packaged:
        bind_package(config["worker_cwd"])
    path = package / "remediation_concurrency.py"
    require(
        path.is_file() and path.resolve().is_relative_to(package.resolve()),
        "Concurrency helper missing or outside selected worker package",
    )
    module = load_file_module(path, "jobagg_dispatcher_concurrency")
    if packaged:
        verify_package_modules(package)
    return module


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
                and (not record.get('runtime_control') or (record.get('readback_verified') is True
                     and record.get('pending_backlog_empty') is True and not record.get('publication_held')))
                else "unknown"
            ),
        }
        decision = helper.decide(state, observation, policy, now=stamp)
    helper.validate_state(decision["state"], policy)
    record["concurrency"].update(
        {key: value for key, value in decision.items() if key != "state"}
    )
    if isinstance(metrics, dict):
        record["concurrency"].update(
            peak_active_sources=metrics.get("peak_active_sources"),
            controller_approved=metrics.get("limit_used") == context["inflight"]["limit"],
            safe_downshift_verified=(decision.get("action") == "decrease" and
                                     decision.get("limit", 0) < context["inflight"]["limit"]),
            failure_classes=metrics.get("failure_classes"))
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
    if _CONTROL_WRITER is not None:
        return _CONTROL_WRITER(path, value)
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


def load_config(path, *, record_storage_health=False, allow_storage_maintenance=False):
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
    retention = config.get("storage_retention", {})
    require(
        isinstance(retention, dict)
        and set(retention) <= {"enabled", "max_seconds", "max_groups", "keep_completed",
                              "selected_runs", "selected_generations", "selected_evidence_bytes_limit"},
        "Invalid storage_retention configuration",
    )
    selection = {key: retention[key] for key in
                 ("selected_runs", "selected_generations", "selected_evidence_bytes_limit")
                 if key in retention}
    for key in ("selected_runs", "selected_generations"):
        if key in selection:
            require(isinstance(selection[key], list) and all(
                isinstance(item, str) and item and Path(item).name == item
                for item in selection[key]), "Invalid storage_retention." + key)
    if "selected_evidence_bytes_limit" in selection:
        require(type(selection["selected_evidence_bytes_limit"]) is int
                and 0 <= selection["selected_evidence_bytes_limit"] <= 10_000_000_000,
                "Invalid storage_retention.selected_evidence_bytes_limit")
    retention = {
        "enabled": retention.get("enabled", False),
        "max_seconds": retention.get("max_seconds", 20),
        "max_groups": retention.get("max_groups", 2),
        "keep_completed": retention.get("keep_completed", 2),
        **selection,
    }
    require(type(retention["enabled"]) is bool, "Invalid storage_retention.enabled")
    require(
        type(retention["max_seconds"]) in (int, float)
        and 0 < retention["max_seconds"] <= 120,
        "Invalid storage_retention.max_seconds",
    )
    require(
        type(retention["max_groups"]) is int and 1 <= retention["max_groups"] <= 8,
        "Invalid storage_retention.max_groups",
    )
    require(
        type(retention["keep_completed"]) is int and retention["keep_completed"] >= 2,
        "Invalid storage_retention.keep_completed",
    )
    config["storage_retention"] = retention
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
        for key in ("warning_free_bytes", "publication_headroom_bytes", "warning_runway_seconds"):
            require(
                type(guard.get(key, 0)) is int and guard.get(key, 0) >= 0,
                "Invalid storage " + key,
            )
    OBSERVABILITY.storage_check(
        config, require_reserve=not (allow_storage_maintenance and retention["enabled"])
    )
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
    if config.get("storage_budget", {}).get("enabled"):
        config.update(package_helper(config, "storage_cycle").validate_config(
            config["storage_budget"], config.get("storage_cycle")))
        policy = config.get("publication_policy")
        require(isinstance(policy, dict) and policy.get("export_storage") == "record_chunks_v1"
                and isinstance(policy.get("policy_epoch"), str) and policy["policy_epoch"],
                "Storage budget requires an explicit semantic/CAS publication policy")
        require(isinstance(policy.get("write_limits"), dict)
                and policy["write_limits"] == config["storage_cycle"].get("publisher_write_limits"),
                "Publisher write limits differ from completion admission")
        require(type(policy.get("max_history_bytes")) is int and 0 < policy["max_history_bytes"] <= 40_000_000_000,
                "Semantic history requires an explicit finite essential-data allowance")
        require(config["sealed_publication_snapshots"] and config["publication_snapshot_mode"] == "projection",
                "Storage budget requires sealed incremental publication projections")
        history = config.get("history_policy", {})
        require(history.get("enabled") is True and history.get("policy_epoch") == policy["policy_epoch"],
                "Storage budget requires matching activated history retirement policy")
        require(retention["enabled"] and retention["keep_completed"] == 2,
                "Activated retention must keep current and one usable previous publication")
        require(retention["max_groups"] >= 3,
                "Activated retention requires at least three groups for projections, history and copies")
        config["history_policy"] = {**history, "scratch_dir": config["storage_cycle"]["scratch_dir"]}
        config["_record_storage_health"] = False
    elif config.get("history_policy", {}).get("enabled"):
        require(False, "History policy requires an admitted storage budget")
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


def recover_stale_runs(config, descriptor, allow_publication_recovery=False, *, deadline_at=None):
    if deadline_at is None:
        deadline_at = time.monotonic() + config["total_timeout_seconds"]
    owner = owner_evidence(config, descriptor)
    recovered = []
    root = config["state_dir"] / "runs"
    if not root.exists():
        return recovered
    state_paths = ([Path(config["_runtime_active_control"]) / "state.json"]
                   if runtime_enabled(config) and config.get("_runtime_active_run") else
                   ([] if runtime_enabled(config) else sorted(root.glob("*/state.json"))))
    for state_path in state_paths:
        if runtime_enabled(config) and not state_path.exists():
            continue  # Newly admitted continuation has not spawned a child.
        record = read_json(state_path)
        if record.get("status") != "running":
            continue
        run_dir = state_path.parent
        require(
            ((runtime_enabled(config) and str(run_dir) == config.get('_runtime_active_control')
              and record.get('run_id') == config.get('_runtime_active_run'))
             or (run_dir.parent.resolve() == root.resolve() and record.get('run_id') == run_dir.name))
            and Path(record.get("run_dir", "")).resolve() == run_dir.resolve(),
            "former run state identity mismatch",
        )
        accepted_dir = root / record['run_id'] if runtime_enabled(config) else run_dir
        accepted_files = {'acceptance':accepted_dir/'acceptance.json','worker_result':accepted_dir/'worker_result.json'}
        native_control = {}
        if runtime_enabled(config):
            with runtime_connection(config) as conn:
                native_control = package_helper(config,'runtime_authority').publication_control(conn)
            accepted_files = package_helper(config,'pending_publication').proof_paths(accepted_dir,native_control.get('proof_files'))
        request = read_json(accepted_dir / "request.json")
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
            acceptance = accepted_files['acceptance']
            if acceptance.exists():
                final["preserved_acceptance_sha256"] = digest(acceptance)
            if (run_dir / "publication_intent.json").exists():
                final["publication_status"] = "unknown_requires_review_no_replay"
                try:
                    pub_request = read_json(run_dir / "publication_request.json")
                    pub_result = read_json(run_dir / "publication_result.json")
                    worker_result = read_json(accepted_files['worker_result'])
                    require(
                        worker_result.get("run_id") == record["run_id"]
                        and (worker_result.get('worker_exit_code')==0 or (native_control.get('publication_only') and worker_result.get('schema')=='native-accepted-queue-proof-1'))
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
            not unresolved_publications(root, config, deadline_at=deadline_at),
            "Unresolved prior publication intent; use explicit --recover-publication before new work",
        )
    return recovered


def maintain_storage(config, descriptor, budget=None, *, deadline_at=None):
    """One bounded retention pass; never release publication or maintenance gates."""
    try:
        if config.get("maintenance_file") and config["maintenance_file"].exists():
            return {"status": "skipped", "reason": "maintenance_paused"}
        retention = config["storage_retention"]
        deadline = time.monotonic() + retention["max_seconds"]
        if deadline_at is not None:
            deadline = min(deadline, deadline_at)
        # Validate unresolved receipts, but protect their exact dependency closure
        # in the shared classifier instead of pinning every unrelated old group.
        unresolved_publications(config["state_dir"] / "runs", config, deadline_at=deadline)
        history = projections = None
        remaining = retention["max_groups"]
        if config.get("history_policy", {}).get("enabled"):
            require(remaining >= 3, "Activated retention requires at least three maintenance groups")
            helper = package_helper(config, "terminal_history")
            policy = dict(config["history_policy"])
            for key in ("selected_runs", "selected_generations"):
                policy[key] = sorted(set(policy.get(key, [])) | set(retention.get(key, [])))
            projections = helper.retire_published_projections(
                config["state_dir"].parent, scratch_dir=config["storage_cycle"]["scratch_dir"],
                budget=budget, owner_fd=descriptor, shared_lock=config["shared_lock_path"],
                deadline_at=deadline, max_groups=max(1, remaining // 3),
                selected_runs=policy["selected_runs"], selected_generations=policy["selected_generations"],
            )
            remaining -= len(projections["selected"])
            history = helper.maintain_history(
                config["state_dir"].parent, policy=policy,
                execute=True, owner_fd=descriptor, shared_lock=config["shared_lock_path"],
                deadline_at=deadline, max_groups=remaining - 1, budget=budget,
            )
            remaining -= len(history["selected"])
        selection = {key: retention[key] for key in
                     ("selected_runs", "selected_generations", "selected_evidence_bytes_limit")
                     if key in retention}
        if history is not None:
            selection.update({key: policy[key] for key in ("selected_runs", "selected_generations")})
        summary = retention_helper(config).prune_completed(
            config["state_dir"].parent,
            keep_completed=retention["keep_completed"],
            max_groups=remaining,
            execute=True,
            owner_fd=descriptor,
            shared_lock=config["shared_lock_path"],
            deadline_at=deadline,
            fixed_budget=budget is not None,
            **selection,
            **({"budget": budget, "protection": history["protected"]} if history else {}),
        )
        return {
            "status": "deferred" if summary["deadline_deferred"] else "complete",
            "summary": summary,
            **({"history": history} if history is not None else {}),
            **({"projections": projections} if projections is not None else {}),
        }
    except TimeoutError as exc:
        return {"status": "deferred", "reason": str(exc)}
    except Exception as exc:
        return {"status": "error", "reason": type(exc).__name__ + ": " + str(exc)}


@contextmanager
def controller_scope(config, context):
    """Route dispatcher and observability controls through the same escrow."""
    global _CONTROL_WRITER
    helper = package_helper(config, "dispatcher_storage")
    previous, previous_observer = _CONTROL_WRITER, OBSERVABILITY.write
    def writer(path, value):
        helper.write_json(context["ledger"], path, value)
    _CONTROL_WRITER = OBSERVABILITY.write = writer
    config["_storage_context"] = context
    try:
        with helper.controller_scope(context["ledger"]):
            yield
    finally:
        _CONTROL_WRITER, OBSERVABILITY.write = previous, previous_observer
        config.pop("_storage_context", None)


def budgeted_tick(config, expected, recover_publication=False, *, deadline_at=None):
    """Admit the complete bounded cycle before creating a run or fetching."""
    if deadline_at is None:
        deadline_at = time.monotonic() + config["total_timeout_seconds"]
    bridge = package_helper(config, "storage_cycle")
    controls = package_helper(config, "dispatcher_storage")
    pending = package_helper(config, "pending_publication")
    errors = package_helper(config, "storage_budget")
    execution_attempted, worker_attempt_possible, result = False, False, None
    # Activation must prepare this existing lock; admission cannot create an
    # unregistered root or silently create a replacement ownership domain.
    descriptor = os.open(config["shared_lock_path"], os.O_RDWR)
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {"status": "lock_busy", "worker_dispatched": False}
        config['_runtime_controller_fd'] = descriptor
        if config.get("maintenance_file") and config["maintenance_file"].exists():
            return {"status": "maintenance_paused", "worker_dispatched": False}
        OBSERVABILITY.storage_check(config, require_reserve=False)
        context = bridge.existing_context(config)
        native_resume = None
        if runtime_enabled(config):
            native_state = runtime_active(config)
            require(config.get('native_publication_escrow') == 'per_bounded_batch',
                    'reviewed native per-batch escrow policy required')
            if context and native_state['phase'] in {'accepted','idle'} and context['ledger'].snapshot()['phase'] == 'publication_admitted':
                with runtime_connection(config) as conn:
                    control = package_helper(config, 'runtime_authority').publication_control(conn)
                if native_state['phase'] == 'idle' or control.get('publication_sequence', 0) > 0:
                    previous = Path(control['publication_control_dir'] if native_state['phase'] == 'idle' else control['previous_publication_control_dir'])
                    receipt = read_json(previous / 'outcome.json')
                    if (previous/'publication_no_write_resolution.json').exists():
                        proof_sha=runtime_no_write_evidence(config,previous)
                        require(proof_sha==control.get('previous_no_write_resolution_sha256'),'native no-write escrow reference changed')
                    else:
                        require(receipt.get('publication_status') in {'published','noop','deferred'} and receipt.get('publication_result_sha256') == digest(previous / 'publication_result.json'),
                                'native batch escrow close lacks exact prior publication proof')
                        proof_sha=receipt['publication_result_sha256']
                        if receipt['publication_status'] == 'deferred':
                            validate_publication(read_json(previous / 'publication_result.json'), read_json(previous / 'publication_request.json'))
                            require(not (previous / 'publication_generation_binding.json').exists(), 'deferred batch has generation binding')
                    with controller_scope(config, context):
                        close_native_batch_escrow(config, context, previous, proof_sha)
                    return {**receipt, 'storage_cycle_reconciled':True, 'worker_reexecuted':False, 'publisher_reexecuted':False}
            if context is None:
                if native_state['phase'] == 'accepted':
                    context = bridge.prepare_cycle(config, native_state['run_id'])
                elif native_state['phase'] == 'idle':
                    with runtime_connection(config) as conn:
                        work = package_helper(config, 'publication_work')
                        vector = work.vector(conn, contract_sha256=runtime_publication_contract(config)['sha256'], now=time.time()) if work.exists(conn) else None
                    if vector and (vector['pending'] or vector['revalidation_reasons']):
                        context = bridge.prepare_cycle(config, str(uuid.uuid4()))
            if context:
                with controller_scope(config, context):
                    native_resume = runtime_resume(config, descriptor)
            else:
                native_resume = runtime_resume(config, descriptor)
            if native_resume and '_runtime_completed' in native_resume:
                if context and context['ledger'].snapshot()['phase'] == 'publication_admitted':
                    terminal = native_resume['_runtime_completed']
                    with controller_scope(config, context):
                        close_native_batch_escrow(config, context, Path(terminal['run_dir']), terminal['publication_result_sha256'])
                return {**native_resume['_runtime_completed'], 'runtime_completion_reconciled':True}
        accepted = pending.inspect(config["state_dir"])
        if context and not runtime_enabled(config):
            interrupted_run = config["state_dir"] / "runs" / context["ledger"].cycle_id
            terminal_path = interrupted_run / "outcome.json"
            if context["ledger"].snapshot()["phase"] == "publication_admitted" and terminal_path.exists():
                terminal = read_json(terminal_path)
                if (interrupted_run / "publication_no_write_resolution.json").exists():
                    settle_unstarted_publication(config, context, interrupted_run, deadline_at=deadline_at)
                    context["ledger"].transition("publication_admitted", "completed")
                    return {**read_json(interrupted_run / "state.json"), "storage_cycle_reconciled": True,
                            "worker_reexecuted": False, "publisher_reexecuted": False}
                if terminal.get("publication_status") in {"published", "noop", "deferred"}:
                    # A crash after the terminal receipt must not wedge a fully
                    # published cycle or authorize replay. Reconcile only exact
                    # immutable request/result evidence and absent old processes.
                    former_processes_absent(terminal)
                    publication_path = interrupted_run / "publication_request.json"
                    receipt_path = interrupted_run / "publication_result.json"
                    intent = read_json(interrupted_run / "publication_intent.json")
                    require(intent.get("request_sha256") == digest(publication_path)
                            and terminal.get("publication_result_sha256") == digest(receipt_path),
                            "Completed publication reconciliation bindings differ")
                    validate_publication(read_json(receipt_path), read_json(publication_path))
                    require(read_json(receipt_path).get("status") == terminal["publication_status"],
                            "Completed publication reconciliation status differs")
                    if terminal["publication_status"] == "deferred":
                        settle_unstarted_publication(config, context, interrupted_run, deadline_at=deadline_at)
                    context["ledger"].transition("publication_admitted", "completed")
                    return {**terminal, "storage_cycle_reconciled": True,
                            "worker_reexecuted": False, "publisher_reexecuted": False}
            if ((interrupted_run / "publication_intent.json").exists()
                    and not (interrupted_run / "outcome.json").exists()):
                state = read_json(interrupted_run / "state.json")
                proof = former_processes_absent(state)
                with controller_scope(config, context):
                    state.update(status="process_failure", phase="finished", finished_at=now().isoformat(),
                                 publication_status="unknown_requires_review_no_replay",
                                 publication_recovery_allowed=True,
                                 reasons=["Interrupted publication intent requires explicit recovery"])
                    immutable_json(interrupted_run / "outcome.json", state)
                    immutable_json(interrupted_run / "interrupted_processes.json", proof)
                    atomic_json(interrupted_run / "state.json", state)
            if accepted and (interrupted_run / "publication_intent.json").exists():
                with controller_scope(config, context):
                    pending.release_after_intent(config["state_dir"], run_id=accepted["run_id"])
                accepted = None
        unknown = unresolved_publications(config["state_dir"] / "runs", config, deadline_at=deadline_at)
        condition = OBSERVABILITY.condition_fingerprint(config, Path(__file__))
        held = OBSERVABILITY.failure_hold(config, condition,
            stage="publication" if unknown else "projection" if accepted else "worker")
        if held and not recover_publication:
            return held
        if context and accepted is None and not unknown:
            old_run = config["state_dir"] / "runs" / context["ledger"].cycle_id
            old_state = old_run / "state.json"
            worker_result = old_run / "worker_result.json"

            def hold_prior_work(reason):
                return {"status": "storage_deferred", "worker_dispatched": False,
                        "publisher_dispatched": False, "worker_reexecuted": False,
                        "review_required": True, "worker_outcome_unknown": True,
                        "reasons": [reason], "storage_budget": controls.summary(context)}

            if old_state.exists() and worker_result.exists() and not (old_run / "publication_intent.json").exists():
                # Reconcile the crash gap after immutable worker proof but before
                # the pending marker. An acceptance file alone never certifies
                # exit or authorizes another fetch after a logging interruption.
                try:
                    state = read_json(old_state)
                    require(state.get("run_id") == old_run.name
                            and state.get("run_dir") == str(old_run)
                            and type(state.get("worker_pid")) is int
                            and state["worker_pid"] in state.get("process_groups", []),
                            "Accepted worker crash lacks its process identity")
                    former_processes_absent(state)
                    proof = read_json(worker_result)
                    require(proof.get("request_sha256") == digest(old_run / "request.json")
                            and proof.get("config_sha256") == config["_config_sha256"],
                            "Accepted worker crash request/configuration binding differs")
                    binding = proof.get("database_binding", {})
                    require(binding.get("semantic", {}).get("writer_fenced")
                            and pending.same_database_binding(binding, pending.database_binding(config["publication_worker_database"])) ,
                            "Accepted worker crash lacks a database writer fence")
                    with controller_scope(config, context):
                        accepted = pending.seal(
                            config["state_dir"], old_run.name, config_sha256=config["_config_sha256"],
                            database=config["publication_worker_database"], lease=context["ledger"].wire(),
                            binding=binding, projection_target=Path(context["limits"]["scratch_dir"]) / old_run.name / "publication_snapshot.sqlite3")
                except (InvalidReport, pending.PendingPublicationError) as exc:
                    return hold_prior_work(str(exc))
            else:
                return hold_prior_work("Unfinished prior storage cycle lacks verified worker/process evidence; review required without replay")
        if accepted:
            require(context is not None and context["ledger"].cycle_id == accepted["run_id"],
                    "Accepted work and storage cycle identities differ")
            if context["ledger"].snapshot()["phase"] == "worker_admitted":
                context["ledger"].transition("worker_admitted", "accepted_pending")
        if unknown and not recover_publication:
            result = {"status": "publication_held", "worker_dispatched": False,
                      "publisher_dispatched": False, "unresolved_publication": True,
                      "reasons": ["Unresolved publication requires explicit recovery"]}
            try:
                maintenance = bridge.prepare_maintenance(config, str(uuid.uuid4()), pending_context=context)
                with controller_scope(config, maintenance):
                    result["storage_retention"] = maintain_storage(config, descriptor, maintenance["ledger"], deadline_at=deadline_at)
                if not maintenance.get("maintenance_reuses_pending"):
                    maintenance["ledger"].transition("worker_admitted", "completed")
            except (errors.StorageBudgetError, errors.StorageBudgetDeferred) as exc:
                result["storage_retention"] = {"status": "deferred", "reason": str(exc)}
            return result
        prior_retention = None
        if context is None:
            if recover_publication:
                return {"status": "publication_held", "worker_dispatched": False,
                        "reasons": ["Legacy publication needs a reviewed budget-bound recovery"]}
            if config["storage_retention"]["enabled"]:
                maintenance = bridge.prepare_maintenance(config, str(uuid.uuid4()))
                with controller_scope(config, maintenance):
                    prior_retention = maintain_storage(config, descriptor, maintenance["ledger"], deadline_at=deadline_at)
                maintenance["ledger"].transition("worker_admitted", "completed")
                require(prior_retention["status"] not in {"error", "skipped"},
                        "Storage maintenance could not validate eligible artifacts")
            context = bridge.prepare_cycle(config, str(uuid.uuid4()))
        phase = context["ledger"].snapshot()["phase"]
        controls.require_admission(bridge.recheck_cycle(config, context, phase))
        with controller_scope(config, context):
            attempt, intent = OBSERVABILITY.start_attempt(config["_config_path"], read_json(config["_config_path"]))
            if recover_publication:
                require(phase == "publication_admitted", "Recovery requires the original publication escrow")
                execution_attempted = True
                result = resume_publication(config, expected, descriptor, deadline_at=deadline_at)
            else:
                if accepted and not runtime_enabled(config):
                    pending.validate(config["state_dir"], config_sha256=config["_config_sha256"],
                                     database=config["publication_worker_database"],
                                     projection_target=Path(context["limits"]["scratch_dir"]) / accepted["run_id"] / "publication_snapshot.sqlite3")
                    run = config["state_dir"] / "runs" / accepted["run_id"]
                    state = read_json(run / "state.json")
                    proof = former_processes_absent(state)
                    pending.prepare_resume(config["state_dir"], process_evidence=proof)
                execution_attempted, worker_attempt_possible = True, accepted is None
                result = locked_tick(config, expected, descriptor, storage_context=context, resume=accepted,
                                     deadline_at=deadline_at)
            result["storage_budget"] = controls.summary(context)
            OBSERVABILITY.record_failure(config, condition, result)
            if result.get("publication_status") in {"published", "noop"} and config["storage_retention"]["enabled"]:
                result["storage_retention"] = maintain_storage(config, descriptor, context["ledger"], deadline_at=deadline_at)
            elif prior_retention is not None:
                result["storage_retention"] = prior_retention
            OBSERVABILITY.finish_attempt(attempt, intent, result)
            if config.get("_controller_stdout_regular"):
                payload = (json.dumps(result, indent=2) + "\n").encode("utf-8")
                target = Path(context["limits"]["controller_log_path"])
                with controls.charged_write(context["ledger"], target, len(payload)):
                    view = memoryview(payload)
                    while view:
                        written = os.write(sys.stdout.fileno(), view)
                        view = view[written:]
                    os.fsync(sys.stdout.fileno())
        if result.get("publication_status") == "deferred" and not runtime_enabled(config):
            settle_unstarted_publication(config, context,
                                         config["state_dir"] / "runs" / context["ledger"].cycle_id,
                                         deadline_at=deadline_at)
        if result.get('publication_status') == 'proven_empty':
            context['ledger'].transition('worker_admitted', 'completed')
        elif result.get("publication_status") in {"published", "noop", "deferred"}:
            if runtime_enabled(config) and result.get('publication_status') in {'published','noop'}:
                close_native_batch_escrow(config, context, Path(result['run_dir']), result['publication_result_sha256'])
            elif not runtime_enabled(config):
                context["ledger"].transition("publication_admitted", "completed")
        return result
    except (errors.StorageBudgetError, errors.StorageBudgetDeferred) as exc:
        held = {"status": "accepted_work_pending" if pending.inspect(config["state_dir"]) else "storage_deferred",
                "reasons": [str(exc)], "protected_data_deleted": False, "budget_increased": False}
        if execution_attempted and result is None:
            # Log exhaustion may also prevent terminal journaling. The child
            # could already have changed the DB; never claim it was not started.
            held.update(dispatch_attempted=True, review_required=True,
                        worker_outcome_unknown=worker_attempt_possible)
            if not worker_attempt_possible:
                held["worker_reexecuted"] = False
        else:
            held["worker_dispatched"] = bool(worker_attempt_possible and result and result.get("worker_pid"))
        return held
    finally:
        if config.get('_runtime_controller_fd') == descriptor:
            config.pop('_runtime_controller_fd', None)
        os.close(descriptor)


def settle_unstarted_publication(config, context, run, *, deadline_at=None):
    """Close only proven no-generation publisher grants; retain accepted data.

    The caller holds the shared owner. A terminal receipt is written before this
    settlement, so a restart can validate the same immutable evidence. Consumed
    reservations remain charged, including any abandoned preparation files.
    """
    require(run.name == context["ledger"].cycle_id,
            "Deferred publication storage cycle differs")
    outcome = read_json(run / "outcome.json")
    request_path, result_path = run / "publication_request.json", run / "publication_result.json"
    request = read_json(request_path)
    intent = read_json(run / "publication_intent.json")
    repair_state = False
    require(outcome.get("run_id") == run.name
            and not outcome.get("cleanup_failure")
            and request.get("run_id") == run.name
            and intent.get("run_id") == run.name
            and intent.get("config_sha256") == config["_config_sha256"]
            and intent.get("command_template") == config["publication_argv"]
            and intent.get("cwd") == str(config["publication_cwd"])
            and intent.get("request_sha256") == digest(request_path),
            "Deferred publication terminal bindings differ")
    if (run / "publication_no_write_resolution.json").exists():
        # Explicit recovery may have proven an old ambiguous intent never
        # started. Its immutable outcome remains unknown; validate the complete
        # canonical resolution instead of manufacturing a publisher receipt.
        classified = package_helper(config, "terminal_publication").classify_run(
            run, deadline_at=deadline_at, allow_unfinished_no_write_state=True)
        require(classified["disposition"] == "terminal"
                and classified["terminal_kind"] == "verified_unstarted",
                "Deferred publication no-write resolution is not verified")
        repair_state = classified.get("no_write_state_pending", False)
    else:
        result = read_json(result_path)
        require(outcome.get("publication_status") == "deferred"
                and outcome.get("publication_recovery_allowed") is False
                and outcome.get("publication_exit_code") == 0
                and result.get("publication_request_sha256") == digest(request_path)
                and outcome.get("publication_result_sha256") == digest(result_path)
                and result.get("status") == "deferred",
                "Deferred publication terminal bindings differ")
        validate_publication(result, request)
    acceptance = run / "acceptance.json"
    worker = read_json(run / "worker_result.json")
    require(request.get("worker_acceptance_path") == str(acceptance)
            and request.get("worker_acceptance_sha256") == digest(acceptance)
            and worker.get("run_id") == run.name
            and worker.get("worker_exit_code") == 0
            and worker.get("report_sha256") == digest(acceptance)
            and worker.get("request_sha256") == digest(run / "request.json")
            and worker.get("config_sha256") == config["_config_sha256"]
            and request.get("publication_policy") == config["publication_policy"],
            "Deferred publication accepted-work proof differs")
    former_processes_absent(outcome)
    if deadline_at is not None and time.monotonic() >= deadline_at:
        raise TimeoutError("Deferred publication settlement deadline exceeded")
    if repair_state:
        state = {**outcome, "status": "incomplete", "phase": "finished",
                 "publication_status": "deferred", "publication_recovery_allowed": False,
                 "generation_not_started": True, "worker_reexecuted": False,
                 "reasons": ["Unstarted publication safely deferred; accepted data retained for next tick"],
                 "finished_at": now().isoformat()}
        with controller_scope(config, context):
            atomic_json(run / "state.json", state)
            atomic_json(config["state_dir"] / "state.json", state)
    # Resume only the publisher's deterministic operation IDs and original
    # limits. Unknown worker/controller operations still prevent ledger close.
    publishers = [writer for writer, role in context["admission"].config.writer_roles.items()
                  if role == "publisher"]
    require(len(publishers) == 1, "Deferred publication writer identity differs")
    publisher = context["ledger"].for_writer(publishers[0])
    operations = publisher.snapshot()["operations"]
    selected = []
    for category, maximum in context["limits"]["publisher_write_limits"].items():
        # PublisherEscrow.operation_id's immutable cycle/writer/category key.
        operation_id = hashlib.sha256(
            f"publication\n{publisher.cycle_id}\n{publisher.writer_id}\n{category}".encode()
        ).hexdigest()
        operation = operations.get(operation_id)
        if operation is not None:
            require(operation.get("writer_id") == publisher.writer_id
                    and operation.get("category") == category
                    and operation.get("maximum") == maximum,
                    "Deferred publication reservation binding differs")
            selected.append(operation_id)
    proof = run / "publication_no_write_resolution.json"
    proof_sha = digest(proof if proof.exists() else result_path)
    for operation_id in selected:
        # Already settled categories are absent after a partial settlement;
        # never reserve them again or refund their consumed charges.
        publisher.resume(operation_id).settle(proof_sha256=proof_sha)


def runtime_publication_contract(config):
    value=config.get('_runtime_publication_contract')
    if value is None:
        base=Path(__file__).parent
        bound_package=Path(package_helper(config,'runtime_authority').__file__).parent.parent
        value=package_helper(config,'runtime_contract').build(package_root=bound_package,
            dispatcher_files=[base/'runner.py',base/'runner_observability.py',base/'supervisor.py',base/'runtime_clock.py'],
            config_sha256=config['_config_sha256'],registry_sha256=config['_registry_sha256'],
            manifest_sha256=config['_manifest_sha256'])
        config['_runtime_publication_contract']=value
    return value


def runtime_enabled(config):
    return bool(config.get("runtime_control", "jobagg.remediation_worker" in config.get("worker_argv", [])))


def runtime_publication_scope(config):
    argv = config.get("publication_argv", [])
    if "--output-dir" not in argv:
        return None
    require(argv.count("--output-dir") == 1, "publication output scope is ambiguous")
    return package_helper(config, "publication_fence").publication_scope(
        config["_registry_path"], Path(argv[argv.index("--output-dir")+1]))


@contextmanager
def runtime_connection(config):
    import sqlite3
    database = config['publication_worker_database']
    require(database.exists(), 'runtime authority missing; explicit bootstrap required')
    conn = sqlite3.connect(database, timeout=1)
    try:
        with ExitStack() as stack:
            context = config.get('_storage_context')
            work = package_helper(config, 'publication_work')
            native = work.exists(conn)
            legacy_tables = package_helper(config,'runtime_control_catalog').CONTROL_TABLES if native else set()
            fd = config.get('_runtime_controller_fd')
            charged = False
            maximum = context['limits'].get('controller_sql_cap_bytes') if context and native else None
            if context and native:
                require(type(maximum) is int and maximum >= 4096, 'measured controller SQL allocation bound required')
            def authorize(action, table, column, database_name, trigger):
                nonlocal charged
                if native and action in {sqlite3.SQLITE_INSERT, sqlite3.SQLITE_UPDATE, sqlite3.SQLITE_DELETE}:
                    if table != 'jobagg_runtime_state' and table not in work.CONTROL_TABLES | legacy_tables and table != 'sqlite_sequence':
                        return sqlite3.SQLITE_DENY
                    if context and not charged:
                        stack.enter_context(package_helper(config, 'dispatcher_storage').charged_write(context['ledger'], Path(database), maximum))
                        charged = True
                return sqlite3.SQLITE_OK
            if native and fd is not None:
                stack.enter_context(package_helper(config, 'runtime_source_head').grant(
                    conn, owner_fd=fd, control=True, authorizer=authorize))
            elif native:
                conn.set_authorizer(authorize)
            with conn:
                yield conn
    finally:
        conn.close()


def require_native_capabilities(config, conn):
    if 'jobagg.remediation_worker' in config.get('worker_argv', []):
        work = package_helper(config, 'publication_work')
        require(work.exists(conn), 'native publication-work migration required before fetching')
        work.validate_freshness(conn, config)
        catalogue = package_helper(config,'runtime_control_catalog').receipt(conn)
        require(catalogue['epoch']==package_helper(config,'runtime_authority').state(conn)['epoch'], 'runtime control catalogue epoch differs')
        require(package_helper(config, 'accepted_evidence').exists(conn)
                and package_helper(config, 'task_headers').exists(conn), 'native evidence/header migration required')
        require(isinstance(config.get('policy_audit'), dict) and isinstance(config.get('publication_admission'), dict),
                'native measured admission and evidence audit contracts required')
        policy_head = Path(str(config['shared_lock_path']) + '.worker-policy') / 'authority.json'
        if config.get('policy_authority_location') is not None:
            policy_type = package_helper(config, 'pipelines.worker_policy').SharedPolicy
            native_policy = policy_type(config['shared_lock_path'], [], native_location=config['policy_authority_location'])
            require(native_policy.inspect().get('authority') == 'indexed_sql', 'existing native policy authority required')
            policy_head = native_policy.root / 'authority.json'
        require(policy_head.is_file(), 'native all-writer policy migration required')


def runtime_active(config):
    helper = package_helper(config, "runtime_authority")
    with runtime_connection(config) as conn:
        value = helper.validate(conn)
        require_native_capabilities(config, conn)
    config["_runtime_active_run"] = value["run_id"] if value["phase"] != "idle" else None
    control = json.loads(value['control_json'])
    if value['phase'] != 'idle':
        default = config['state_dir'] / 'runs' / value['run_id']
        config['_runtime_active_control'] = str(package_helper(config, 'pending_publication').control_dir(
            config['state_dir'], {'run_id': value['run_id'], 'publication_control_dir': control.get('publication_control_dir') or str(default)}))
    else:
        config.pop('_runtime_active_control', None)
    return value


def close_native_batch_escrow(config, context, controls, proof_sha256):
    # A completed bounded batch gets an immutable final ledger before a new
    # token/escrow may be admitted for the same frozen accepted run. Actual file
    # usage is inventoried again; provider charges and prior ledgers are retained.
    settle_native_publication(config, context, proof_sha256)
    ledger = context['ledger']
    target = Path(controls) / 'storage_cycle_completed.json'
    budget = package_helper(config, 'storage_budget')
    if not target.exists():
        maximum = 2*budget.LEDGER_MAX_BYTES + 16384
        operation_id = hashlib.sha256(('native-batch-ledger:'+str(target)).encode()).hexdigest()
        funding_proof = hashlib.sha256(f'{target}\n{maximum}\n{ledger.cycle_id}\n{ledger.scope_hash}'.encode()).hexdigest()
        funded = ledger.snapshot().get('completions', {}).get(operation_id)
        if funded is None:
            outstanding = ledger.snapshot()['operations'].get(operation_id)
            operation = ledger.resume(operation_id) if outstanding else ledger.reserve(ledger.category_for(target), maximum, path=target, operation_id=operation_id)
            if outstanding is None or outstanding['consumed'] == 0:
                operation.consume(maximum)
            operation.settle(proof_sha256=funding_proof)
        else:
            require(funded['path'] == str(target) and funded['maximum'] == maximum
                    and funded['consumed'] == maximum and funded['proof_sha256'] == funding_proof,
                    'native batch ledger funding receipt differs')
        document = {**ledger.snapshot(), 'phase':'completed'}
        payload = json.dumps(document, sort_keys=True, separators=(',', ':')) + '\n'
        require(len(payload.encode()) <= budget.LEDGER_MAX_BYTES, 'native batch ledger exceeds its sealed bound')
        from contextlib import nullcontext as prepaid
        files = package_helper(config, 'atomic_files')
        def covered(path, size):
            require(path == target and size == len(payload.encode()), 'native escrow receipt escaped its prepaid bound')
            return prepaid()
        with files.admitted_file_writes(covered):
            files.atomic_write_text(target, payload)
    require(read_json(target) == {**ledger.snapshot(), 'phase':'completed'}, 'native batch final escrow differs')
    ledger.transition('publication_admitted', 'completed')


def settle_native_publication(config, context, proof_sha256):
    state = context['ledger'].snapshot()
    writers = [key for key, role in context['admission'].config.writer_roles.items() if role == 'publisher']
    require(len(writers) == 1, 'native publisher writer scope differs')
    publisher = context['ledger'].for_writer(writers[0])
    for category, maximum in context['limits']['publisher_write_limits'].items():
        operation_id = hashlib.sha256(f'publication\n{publisher.cycle_id}\n{publisher.writer_id}\n{category}'.encode()).hexdigest()
        operation = state['operations'].get(operation_id)
        if operation is not None:
            require(operation['writer_id'] == publisher.writer_id and operation['maximum'] == maximum
                    and operation['category'] == category, 'native publisher escrow binding differs')
            publisher.resume(operation_id).settle(proof_sha256=proof_sha256)


def runtime_projection_target(config, run_id, controls):
    with runtime_connection(config) as conn:
        reused = package_helper(config, 'runtime_authority').publication_control(conn).get('reusable_projection')
    if reused is not None:
        return Path(reused['path'])
    context = config.get('_storage_context')
    if context is None:
        return Path(controls) / 'publication_snapshot.sqlite3'
    root = config['state_dir'] / 'runs' / run_id
    return Path(context['limits']['scratch_dir']) / run_id / Path(controls).relative_to(root) / 'publication_snapshot.sqlite3'


def runtime_containment_args(config):
    return {
        "config_sha256": config["_config_sha256"],
        "implementation_sha256": runtime_publication_contract(config)[
            "jobagg_implementation_sha256"
        ],
        "policy_location": config.get("policy_authority_location"),
    }


def runtime_history_review(config, conn):
    helper = package_helper(config, "runtime_containment")
    approved = helper.approved(conn, **runtime_containment_args(config))
    return approved, helper.summary(conn)


def runtime_acknowledge(config, run_id, result, request, descriptor):
    work = package_helper(config, "publication_work")
    with runtime_connection(config) as conn:
        if not work.exists(conn):
            return {
                "drained": result.get("pending_backlog_empty") is True,
                "empty": result.get("pending_backlog_empty") is True,
                "held": None,
            }
        selection = request.get("publication_selection")
        require(
            isinstance(selection, dict)
            and result.get("publication", {}).get("publication_selection") == selection,
            "selected publication acknowledgement differs from the admitted dependency set",
        )
        require(
            result.get("readback_verified") is True,
            "selected publication lacks destination/export readback",
        )
        authority = package_helper(config, "runtime_authority")
        require(
            result.get("publication_boundary") is not None,
            "native publication requires a complete destination/export boundary",
        )
        authority.remember_publication_boundary(
            conn, run_id, result["publication_boundary"]
        )
        with authority.controller(conn, run_id=run_id, owner_fd=descriptor):
            work.acknowledge(
                conn,
                selection,
                result_sha256=hashlib.sha256(
                    json.dumps(result, sort_keys=True).encode()
                ).hexdigest(),
                processed_units=result.get("publication", {}).get(
                    "processed_units", []
                ),
                rejected=result.get("publication", {}).get("rejected", []),
            )
        vector = work.vector(
            conn,
            contract_sha256=runtime_publication_contract(config)["sha256"],
            now=time.time(),
        )
        # A finished bounded batch preserves both scoped current and historical
        # debt. Neither can earn authoritative empty-backlog credit.
        _, historical = runtime_history_review(config, conn)
        return {
            "drained": vector["pending"] is None
            and not vector["revalidation_reasons"]
            and not vector.get("unscoped_held"),
            "empty": vector["empty"]
            and not (historical or {}).get("historical_control_records_held"),
            "held": vector["held"] or historical,
            "historical_held": historical,
        }


def runtime_no_write_evidence(config, controls):
    resolution=read_json(controls/'publication_no_write_resolution.json')
    proof=resolution.get('proof',{})
    require(proof.get('kind')=='verified_unstarted_publication' and proof.get('generation_not_started') is True
            and not (controls/'publication_generation_binding.json').exists(), 'native no-write proof missing or contradictory')
    # Canonical validation is restricted to this active predecessor, never a
    # completed-history census. Once advanced, retain only its sealed reference.
    bounded={**config,'_runtime_active_control':str(controls),'_runtime_active_run':read_json(controls/'outcome.json')['run_id']}
    require(not unresolved_publications(config['state_dir']/'runs',bounded,deadline_at=config.get('_entry_monotonic_deadline',config.get('_tick_monotonic_deadline'))),
            'native no-write resolution did not close the exact original intent')
    request=read_json(controls/'publication_request.json')
    snapshot_helper(config).validate_publication_snapshot(request,config['publication_worker_database'],owner_held=True,
        deadline_at=config.get('_entry_monotonic_deadline',config.get('_tick_monotonic_deadline')))
    former_processes_absent(read_json(controls/'outcome.json'))
    return digest(controls/'publication_no_write_resolution.json')


def runtime_advance(config, run_id, controls, descriptor, *, reusable_request=None):
    with runtime_connection(config) as conn:
        authority = package_helper(config, 'runtime_authority')
        files = package_helper(config,'pending_publication').proof_paths(config['state_dir']/'runs'/run_id, authority.publication_control(conn).get('proof_files'))
        with authority.controller(conn, run_id=run_id, owner_fd=descriptor):
            authority.advance_publication(conn, run_id=run_id, controls=controls,
                outcome_sha256=digest(controls / 'outcome.json'),
                worker_result_sha256=digest(files['worker_result']),
                reusable_projection=(reusable_request or {}).get('publication_snapshot'),
                reusable_selection=(reusable_request or {}).get('publication_selection'),
                no_write_resolution_sha256=digest(controls/'publication_no_write_resolution.json') if (controls/'publication_no_write_resolution.json').exists() else None)
    runtime_active(config)


def runtime_queue_controls(config, run_id, descriptor):
    root = config['state_dir'] / 'runs' / run_id
    with runtime_connection(config) as conn:
        authority = package_helper(config, 'runtime_authority')
        value = authority.validate(conn)
        control = authority.publication_control(conn)
        require(value['run_id'] == run_id and value['phase'] == 'accepted' and control.get('publication_only'),
                'native queue proof requires its publication-only owner')
        require(value['revision'] == value['start_revision'], 'native queue source revision changed before sealing')
    root.mkdir(parents=True, exist_ok=True)
    files = package_helper(config,'pending_publication').proof_paths(root,control.get('proof_files'))
    stamp = control['queue_started_at']
    if not control.get('atomic_recovery'):
        immutable_json(root / 'request.json', {'schema_version':1, 'run_id':run_id, 'started_at':stamp,
            'deadline_at':stamp, 'kind':'native_publication_only',
            'source_manifest_path':str(root / 'source_manifest.json'),
            'source_manifest_sha256':config['_manifest_sha256'],
            'source_registry':{'path':str(config['_registry_path']), 'sha256':config['_registry_sha256']},
            'expected_source_ids':config['_runtime_expected_source_ids'], 'report_path':str(root / 'acceptance.json')})
        manifest = root / 'source_manifest.json'
        if not manifest.exists():
            payload = config['source_manifest_path'].read_bytes()
            require(hashlib.sha256(payload).hexdigest() == config['_manifest_sha256'], 'queue source manifest changed')
            if config.get('_storage_context'):
                with package_helper(config, 'dispatcher_storage').charged_write(config['_storage_context']['ledger'], manifest, len(payload)):
                    manifest.write_bytes(payload)
            else:
                manifest.write_bytes(payload)
        require(digest(manifest) == config['_manifest_sha256'], 'queue manifest differs')
    else:
        original = read_json(root / 'request.json')
        require(original['run_id']==run_id and original['source_manifest_sha256']==config['_manifest_sha256']
                and digest(root/'source_manifest.json')==config['_manifest_sha256'], 'atomic recovery original manifest changed')
        for name,sha in control['original_worker_controls'].items():
            require(digest(root/name)==sha, 'atomic recovery original process evidence changed')
    immutable_json(files['acceptance'], {'schema_version':1,'kind':'native_accepted_queue','run_id':run_id,
        'queue_epoch':value['epoch'], 'source_revision':value['revision'], 'database':str(config['publication_worker_database']),
        'bootstrap_sha256':hashlib.sha256(value['bootstrap_json'].encode()).hexdigest(),
        'scope':'Frozen committed acceptance references; exact selected artifacts and destinations are verified before acknowledgement.',
        **({'atomic_recovery':control['atomic_recovery'], 'original_worker_controls':control['original_worker_controls']} if control.get('atomic_recovery') else {})})
    current_binding = package_helper(config, 'pending_publication').database_binding(config['publication_worker_database'])
    if (files['worker_result']).exists():
        previous = read_json(files['worker_result'])['database_binding']
        require(package_helper(config, 'pending_publication').same_database_binding(previous, current_binding),
                'native queue source fence changed')
        current_binding = previous
    immutable_json(files['worker_result'], {'schema':'native-accepted-queue-proof-1','run_id':run_id,
        'status':'incomplete','reasons':['Continuing authoritative older publication work without fetching'],
        'worker_exit_code':None, 'report_sha256':digest(files['acceptance']),
        'request_sha256':digest(root / 'request.json'), 'config_sha256':config['_config_sha256'],
        'database_binding':current_binding})
    if not (root / 'state.json').exists():
        atomic_json(root / 'state.json', {'schema_version':1,'run_id':run_id,'run_dir':str(root),
            'status':'accepted_work_pending','phase':'accepted','process_groups':[], 'worker_dispatched':False})


def runtime_resume(config, descriptor):
    """Reconcile only the authoritative active run under the exact shared owner."""
    pending = package_helper(config, "pending_publication")
    value = runtime_active(config)
    if value["phase"] == "idle":
        require(pending.inspect(config["state_dir"]) is None, "idle authority has an unresolved accepted marker")
        work = package_helper(config, 'publication_work')
        with runtime_connection(config) as conn:
            vector = work.vector(conn, contract_sha256=runtime_publication_contract(config)['sha256'], now=time.time()) if work.exists(conn) else None
            legacy=package_helper(config,'runtime_control_catalog').oldest_unresolved(conn)
            contained,historical=runtime_history_review(config,conn)
            config['_runtime_historical_held']=historical
            require(legacy is None or contained or (legacy['disposition']=='accepted_pending' and vector and (vector['pending'] or vector['revalidation_reasons'])),
                    'legacy runtime control requires exact bounded reconciliation before fetching: '+str(legacy))
            require(not vector or not vector.get('unscoped_held'),'unknown current operation requires explicit reconciliation')
            if vector:
                control = package_helper(config, 'runtime_authority').publication_control(conn)
                require(package_helper(config, 'publication_fence').verify_boundary(control.get('publication_cursor'),
                        expected_scope=runtime_publication_scope(config)),
                        'publication/export cursor requires explicit reconstruction before fetching')
            if vector and (vector['pending'] or vector['revalidation_reasons']):
                require(not any(reason != 'time_transition_due' for reason in vector['revalidation_reasons']),
                        'publication contract requires explicit dirty-epoch migration before fetching')
                context = config.get('_storage_context')
                run_id = context['ledger'].cycle_id if context else str(uuid.uuid4())
                package_helper(config, 'runtime_authority').begin_publication_only(conn, run_id, config['_config_sha256'],
                    control_dir=config['state_dir'] / 'runs' / run_id,
                    publication_scope=runtime_publication_scope(config), started_at=now().isoformat())
            else:
                require(not vector or not vector['held'] or contained, 'accepted publication units require scoped repair before fetching: '+str(vector))
                return None
        runtime_queue_controls(config, run_id, descriptor)
        return runtime_resume(config, descriptor)
    accepted_dir = config["state_dir"] / "runs" / value["run_id"]
    if value['phase']=='worker':
        exit_path=accepted_dir/'worker_result.json'
        exit_proof=read_json(exit_path) if exit_path.exists() else {}
        if exit_proof.get('worker_exit_code')!=0:
            original_state=read_json(accepted_dir/'state.json')
            former_processes_absent(original_state)
            argv=config['worker_argv']
            bound=int(argv[argv.index('--max-tasks')+1]) if '--max-tasks' in argv else config.get('atomic_recovery_max_attempts')
            require(type(bound) is int and bound>0, 'reviewed atomic recovery attempt bound required')
            with runtime_connection(config) as conn:
                proof=package_helper(config,'accepted_evidence').atomic_run(conn,value['run_id'],limit=bound,
                        deadline_at=config.get('_entry_monotonic_deadline',time.monotonic()+config['total_timeout_seconds']))
                require(proof is not None,'failed worker has no independent committed acceptance; repair required')
                original_request=read_json(accepted_dir/'request.json')
                original_sha=digest(accepted_dir/'request.json') if proof.get('request_hash_kind')=='file' else hashlib.sha256(package_helper(config,'remediation_worker').dump(original_request).encode()).hexdigest()
                require(proof['request_sha256']==original_sha,'atomic acceptance original request changed')
                original={name:digest(accepted_dir/name) for name in ('request.json','acceptance.json','worker_result.json','outcome.json') if (accepted_dir/name).exists()}
                package_helper(config,'runtime_authority').promote_atomic_acceptance(conn,value['run_id'],proof=proof,
                    original_controls=original,started_at=now().isoformat())
            value=runtime_active(config)
    run = Path(config['_runtime_active_control'])
    with runtime_connection(config) as conn:
        control = package_helper(config, 'runtime_authority').publication_control(conn)
    proof_paths = pending.proof_paths(accepted_dir,control.get('proof_files'))
    if value['phase'] == 'accepted' and control.get('publication_only') and control.get('publication_sequence', 0) == 0:
        runtime_queue_controls(config, value['run_id'], descriptor)
    if control.get('previous_no_write_resolution_sha256'):
        require(digest(Path(control['previous_publication_control_dir'])/'publication_no_write_resolution.json')==control['previous_no_write_resolution_sha256'],
                'native predecessor no-write reference changed')
    if control.get('publication_sequence', 0):
        previous = Path(control['previous_publication_control_dir'])
        require(digest(previous / 'outcome.json') == control['previous_outcome_sha256']
                and digest(proof_paths['worker_result']) == control['accepted_worker_result_sha256'],
                'publication continuation predecessor/acceptance proof changed')
    require(value["config_sha256"] == config["_config_sha256"], "active runtime configuration differs; explicit repair required")
    if (run / "publication_intent.json").exists():
        if not (run/'outcome.json').exists() and (run/'publication_result.json').exists():
            former=read_json(run/'state.json')
            # A report cannot override an unknown/nonzero exit. This narrow
            # checkpoint is durable only after execute() observed actual exit 0.
            if type(former.get('publication_exit_code')) is int and former['publication_exit_code']==0:
                former_processes_absent(former)
                request=read_json(run/'publication_request.json')
                result=read_json(run/'publication_result.json')
                require(read_json(run/'publication_intent.json').get('request_sha256')==digest(run/'publication_request.json'),
                        'post-exit publication intent changed')
                validate_publication(result,request)
                if result['status'] in {'published','noop','deferred'}:
                    immutable_json(run/'outcome.json',{**former,'phase':'finished','status':'incomplete',
                        'finished_at':now().isoformat(),'publication_status':result['status'],
                        'publication_result_sha256':digest(run/'publication_result.json'),
                        'publication_recovery_allowed':False,'reconciled_post_exit_receipt':True})
                    return runtime_resume(config,descriptor)
        if (run / "publication_resolution.json").exists():
            resolution = read_json(run / "publication_resolution.json")
            require(resolution.get("previous_outcome_sha256") == digest(run / "outcome.json"),
                    "Completed publication resolution changed")
            resolved_request = read_json(evidence_path(resolution["request"], run))
            resolved = read_json(evidence_path(resolution["result"], run))
            validate_publication(resolved, resolved_request)
            drained = runtime_acknowledge(config, value['run_id'], resolved, resolved_request, descriptor)
            if not drained['drained']:
                runtime_advance(config, value['run_id'], run, descriptor)
                return runtime_resume(config, descriptor)
            with runtime_connection(config) as conn:
                package_helper(config, "runtime_authority").finish_publication(conn, value['run_id'])
            return {"_runtime_completed": {**read_json(run / "outcome.json"),
                "status": "incomplete", "publication_status": resolved["status"],
                "publication_exit_code": 0, "readback_verified": resolved.get("readback_verified") is True,
                "pending_backlog_empty": drained['empty'], 'publication_held': drained['held']}}
        if (run / "outcome.json").exists():
            terminal = read_json(run / "outcome.json")
            if (run/'publication_no_write_resolution.json').exists():
                proof_sha=runtime_no_write_evidence(config,run)
                request=read_json(run/'publication_request.json')
                context=config.get('_storage_context')
                runtime_advance(config,value['run_id'],run,descriptor,reusable_request=request)
                if context and context['ledger'].snapshot()['phase']=='publication_admitted':
                    close_native_batch_escrow(config,context,run,proof_sha)
                return {'_runtime_completed':{**terminal,'status':'incomplete','publication_status':'deferred',
                    'generation_not_started':True,'accepted_work_retained':True,'publication_continuation_prepared':True,
                    'worker_reexecuted':False,'publisher_reexecuted':False}}
            if terminal.get('publication_status') == 'deferred':
                request = read_json(run / 'publication_request.json')
                result = read_json(run / 'publication_result.json')
                require(read_json(run / 'publication_intent.json').get('request_sha256') == digest(run / 'publication_request.json')
                        and terminal.get('publication_result_sha256') == digest(run / 'publication_result.json')
                        and terminal.get('publication_exit_code') == 0
                        and result.get('status') == 'deferred'
                        and not (run / 'publication_generation_binding.json').exists(),
                        'deferred native publication lacks exact no-generation proof')
                validate_publication(result, request)
                former_processes_absent(terminal)
                snapshot_helper(config).validate_publication_snapshot(request, config['publication_worker_database'], owner_held=True,
                    deadline_at=config.get('_entry_monotonic_deadline',config.get('_tick_monotonic_deadline')))
                context = config.get('_storage_context')
                runtime_advance(config, value['run_id'], run, descriptor, reusable_request=request)
                if context and context['ledger'].snapshot()['phase'] == 'publication_admitted':
                    close_native_batch_escrow(config, context, run, terminal['publication_result_sha256'])
                return {'_runtime_completed':{**terminal, 'accepted_work_retained':True,
                    'publication_continuation_prepared':True, 'worker_reexecuted':False}}
            if terminal.get("publication_status") in {"published", "noop"}:
                request = read_json(run / "publication_request.json")
                require(read_json(run / "publication_intent.json").get("request_sha256") == digest(run / "publication_request.json")
                        and terminal.get("publication_result_sha256") == digest(run / "publication_result.json"),
                        "Completed runtime generation proof changed")
                result = read_json(run / 'publication_result.json')
                validate_publication(result, request)
                former_processes_absent(terminal)
                drained = runtime_acknowledge(config, value['run_id'], result, request, descriptor)
                if not drained['drained']:
                    runtime_advance(config, value['run_id'], run, descriptor)
                    return runtime_resume(config, descriptor)
                with runtime_connection(config) as conn:
                    helper = package_helper(config, 'runtime_authority')
                    if result.get('publication_boundary') is not None:
                        helper.remember_publication_boundary(conn, value['run_id'], result['publication_boundary'])
                    helper.finish_publication(conn, value['run_id'])
                return {"_runtime_completed": terminal}
        # Existing publication recovery owns this exact run, including uncertain commits.
        marker = pending.inspect(config["state_dir"])
        if marker:
            pending.release_after_intent(config["state_dir"], run_id=value['run_id'])
        return None
    state_path = run / 'state.json'
    if not state_path.exists():
        state_path = Path(control.get('previous_publication_control_dir',accepted_dir)) / 'state.json'
    state = read_json(state_path)
    former_processes_absent(state)
    proof = read_json(proof_paths["worker_result"])
    require((proof.get("worker_exit_code") == 0 or (control.get('publication_only') and proof.get('schema') == 'native-accepted-queue-proof-1'))
            and proof.get("request_sha256") == digest(accepted_dir / "request.json")
            and proof.get("config_sha256") == config["_config_sha256"]
            and (control.get('publication_sequence', 0) > 0 or pending.same_database_binding(proof.get("database_binding", {}), pending.database_binding(config["publication_worker_database"]))) ,
            "active worker lacks exact successful exit/configuration/database proof; repair required")
    marker = pending.inspect(config["state_dir"])
    if marker is None:
        marker = pending.seal(config["state_dir"], value['run_id'], config_sha256=config["_config_sha256"],
                              database=config["publication_worker_database"], lease={"runtime_epoch": value["epoch"]},
                              publication_control_dir=run, proof_files=control.get('proof_files'), projection_target=runtime_projection_target(config, value['run_id'], run))
    if control.get('reusable_projection') is not None and marker.get('projection') is None:
        require(control.get('reusable_selection') is not None, 'deferred projection selection missing')
        pending.record_selection(config['state_dir'], control['reusable_selection'])
        pending.record_projection(config['state_dir'], control['reusable_projection'])
    pending.validate(config["state_dir"], config_sha256=config["_config_sha256"], database=config["publication_worker_database"],
                     projection_target=runtime_projection_target(config, value['run_id'], run))
    pending.prepare_resume(config["state_dir"], process_evidence=former_processes_absent(state))
    return marker


def phase_remaining(config, hard_end, wall_end, phase):
    """Missing measured admission values defer safely, never invent a viable unit."""
    policy = config.get("phase_budgets")
    if not policy:
        require(not runtime_enabled(config), "phase budgets require reviewed viable-unit measurements")
        return min(config["publication_timeout_seconds"], hard_end-time.monotonic(), wall_end-time.time())
    require(set(policy) == {"projection_max_seconds", "projection_min_seconds", "publisher_min_seconds", "cleanup_report_seconds", "measurement_sha256"}, "phase budget fields differ")
    for key in ("projection_max_seconds", "projection_min_seconds", "publisher_min_seconds", "cleanup_report_seconds"):
        require(type(policy[key]) in (int, float) and 0 < policy[key] < config["total_timeout_seconds"], "invalid phase budget")
    require(isinstance(policy["measurement_sha256"], str) and re.fullmatch(r"[a-f0-9]{64}", policy["measurement_sha256"]), "phase budget lacks measurement seal")
    continuous = config.get('_publication_clock')
    continuous_remaining = RUNTIME_CLOCK.remaining(continuous) if continuous else float('inf')
    remaining = min(hard_end-time.monotonic(), wall_end-time.time(), continuous_remaining) - policy["cleanup_report_seconds"]
    if phase == "projection":
        available = min(policy["projection_max_seconds"], remaining-policy["publisher_min_seconds"])
        require(available >= policy["projection_min_seconds"], "insufficient viable projection/publisher budget; accepted work retained")
    else:
        available = min(config["publication_timeout_seconds"], remaining)
        require(available >= policy["publisher_min_seconds"], "insufficient viable publisher budget; accepted work retained")
    return available


def tick(config, expected, recover_publication=False):
    config.pop('_runtime_publication_contract',None)
    config.pop('_publication_clock',None)
    config['_runtime_expected_source_ids'] = expected
    deadline_at = min(time.monotonic() + config['total_timeout_seconds'], config.get('_entry_monotonic_deadline', float('inf')))
    wall_at = min(time.time() + config['total_timeout_seconds'], config.get('_entry_wall_deadline', float('inf')))
    config['_tick_monotonic_deadline']=deadline_at
    with (package_helper(config, 'publication_deadline').entry_deadlines(deadline_at, wall_at) if runtime_enabled(config) else nullcontext()):
        return _tick(config, expected, recover_publication=recover_publication)


def _tick(config, expected, recover_publication=False):
    deadline_at = min(time.monotonic() + config["total_timeout_seconds"],
                      config.get("_entry_monotonic_deadline", float("inf")))
    require(time.monotonic() < deadline_at and time.time() < config.get("_entry_wall_deadline", float("inf")),
            "entry budget expired before ownership/admission")
    if config.get("storage_budget", {}).get("enabled"):
        return budgeted_tick(config, expected, recover_publication=recover_publication, deadline_at=deadline_at)
    # Identity/mount/path checks still precede every guarded-volume write. Only
    # enabled retention may enter under the reserve, and it cannot launch work.
    retention = config.get("storage_retention", {})
    if retention.get("enabled"):
        OBSERVABILITY.storage_check(config, require_reserve=False)
    else:
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
        config['_runtime_controller_fd'] = descriptor
        if config.get("maintenance_file") and config["maintenance_file"].exists():
            return {
                "status": "maintenance_paused",
                "maintenance_file": str(config["maintenance_file"]),
                "reason": "operator maintenance file is present; no work dispatched",
            }
        prior_retention = None
        if (retention.get("enabled") and not recover_publication
                and unresolved_publications(config["state_dir"] / "runs", config, deadline_at=deadline_at)):
            # This is deliberately a maintenance-only invocation.  Reclaiming B
            # must never become an implicit replay/recovery of unresolved A.
            return {
                "status": "publication_held", "phase": "storage_maintenance",
                "unresolved_publication": True,
                "worker_dispatched": False, "publisher_dispatched": False,
                "reasons": ["Unresolved publication requires explicit recovery; independent cleanup only"],
                "storage_retention": maintain_storage(config, descriptor, deadline_at=deadline_at),
            }
        if retention.get("enabled"):
            # Recheck after acquiring the owner: the first free-space sample may
            # race a prior publisher. A failed reserve is maintenance-only until
            # a fresh, fully guarded check admits the ordinary worker path.
            storage = OBSERVABILITY.storage_check(config, require_reserve=False)
            if not storage["ready"]:
                prior_retention = maintain_storage(config, descriptor, deadline_at=deadline_at)
                try:
                    OBSERVABILITY.storage_check(config)
                except Exception as exc:
                    return {
                        "status": "process_failure",
                        "phase": "storage_maintenance",
                        "reasons": [type(exc).__name__ + ": " + str(exc)],
                        "storage_retention": prior_retention,
                        "unresolved_publication": bool(
                            unresolved_publications(config["state_dir"] / "runs", config, deadline_at=deadline_at)
                        ),
                    }
                if prior_retention["status"] in {"error", "skipped"}:
                    return {
                        "status": "process_failure",
                        "phase": "storage_maintenance",
                        "reasons": ["Storage maintenance did not safely complete"],
                        "storage_retention": prior_retention,
                    }
        try:
            resume = runtime_resume(config, descriptor) if runtime_enabled(config) else None
        except (ValueError, OSError) as exc:
            return {"status": "accepted_work_pending", "review_required": True,
                    "worker_dispatched": False, "publisher_dispatched": False,
                    "reasons": ["Runtime authority requires repair: " + str(exc)]}
        if resume and "_runtime_completed" in resume:
            return {**resume["_runtime_completed"], "runtime_completion_reconciled": True,
                    "worker_reexecuted": False, "publisher_reexecuted": False}
        condition = OBSERVABILITY.condition_fingerprint(config, Path(__file__))
        try:
            known_unknown = unresolved_publications(config["state_dir"] / "runs", config, deadline_at=deadline_at)
        except Exception as exc:
            result = {"status": "process_failure", "failure_stage": "publication",
                      "failure_type": type(exc).__name__, "unresolved_publication": True,
                      "reasons": [type(exc).__name__ + ": " + str(exc)]}
            OBSERVABILITY.record_failure(config, condition, result)
            if retention.get("enabled"):
                result["storage_retention"] = maintain_storage(config, descriptor, deadline_at=deadline_at)
            return result
        held = OBSERVABILITY.failure_hold(config, condition,
            stage="publication" if known_unknown else "projection" if resume else "worker")
        if held:
            if prior_retention is not None:
                held["storage_retention"] = prior_retention
            return held
        try:
            recovered = recover_stale_runs(
                config,
                descriptor,
                allow_publication_recovery=recover_publication
                or bool(config["publication_argv"]),
                deadline_at=deadline_at,
            )
            if recover_publication or unresolved_publications(
                config["state_dir"] / "runs", config, deadline_at=deadline_at
            ):
                result = resume_publication(config, expected, descriptor, deadline_at=deadline_at)
            else:
                result = locked_tick(config, expected, descriptor, recovered, resume=resume, deadline_at=deadline_at)
        except Exception as exc:
            try:
                unresolved = bool(unresolved_publications(config["state_dir"] / "runs", config, deadline_at=deadline_at))
            except Exception:
                # An unreadable recovery boundary is unknown, never evidence
                # that publication is clear. Preserve the original error below.
                unresolved = True
            result = {
                "status": "process_failure",
                "failure_stage": "publication" if known_unknown or recover_publication else "projection" if resume else "worker",
                "failure_type": type(exc).__name__,
                "reasons": [type(exc).__name__ + ": " + str(exc)],
                "unresolved_publication": unresolved,
            }
        OBSERVABILITY.record_failure(config, condition, result)
        if retention.get("enabled"):
            # Exactly one maintenance budget per invocation, before work only
            # when needed to regain the reserve, otherwise after terminal work.
            result["storage_retention"] = (
                prior_retention if prior_retention is not None
                else maintain_storage(config, descriptor, deadline_at=deadline_at)
            )
        return result
    finally:
        if config.get('_runtime_controller_fd') == descriptor:
            config.pop('_runtime_controller_fd', None)
        os.close(descriptor)


def wait_with_deadlines(process, timeout, absolute_deadline=None, *, continuous_budget=None):
    """Bound child wait across suspend and wall-clock adjustments."""
    continuous = RUNTIME_CLOCK.budget(timeout, parent=continuous_budget or _ENTRY_CLOCK or RUNTIME_CLOCK.inherited())
    elapsed_end = time.monotonic() + timeout
    wall_end = min(time.time() + timeout, absolute_deadline) if absolute_deadline is not None else time.time() + timeout
    while process.poll() is None:
        remaining = min(elapsed_end - time.monotonic(), wall_end - time.time(), RUNTIME_CLOCK.remaining(continuous))
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


def publication_request(config, request, report_path, worker_status, deadline, owner_fd):
    if not runtime_enabled(config):
        return _publication_request(config, request, report_path, worker_status, deadline, owner_fd)
    clocks = package_helper(config, 'publication_deadline')
    with clocks.continuous_budget(config.get('_publication_clock')):
        with clocks.entry_deadlines(time.monotonic()+max(0,deadline.timestamp()-time.time()),deadline.timestamp()):
            return _publication_request(config, request, report_path, worker_status, deadline, owner_fd)


def _publication_request(config, request, report_path, worker_status, deadline, owner_fd):
    report = read_json(report_path)
    require(
        Path(report.get("database", "")).resolve()
        == config["publication_worker_database"].resolve(),
        "worker report database differs from configured publication source",
    )
    OBSERVABILITY.storage_check(config)
    context = config.get("_storage_context")
    pending_value = None
    if context:
        phase = context["ledger"].snapshot()["phase"]
        package_helper(config, "dispatcher_storage").require_admission(
            package_helper(config, "storage_cycle").recheck_cycle(config, context, phase))
        pending_value = package_helper(config, "pending_publication").validate(
            config["state_dir"], config_sha256=config["_config_sha256"],
            database=config["publication_worker_database"],
            projection_target=runtime_projection_target(config, request['run_id'], config.get('_runtime_active_control', report_path.parent)) if runtime_enabled(config)
                              else Path(context["limits"]["scratch_dir"]) / request["run_id"] / "publication_snapshot.sqlite3")
    if runtime_enabled(config) and not context:
        pending_value = package_helper(config, "pending_publication").validate(
            config["state_dir"], config_sha256=config["_config_sha256"],
            database=config["publication_worker_database"],
            projection_target=runtime_projection_target(config, request['run_id'], config['_runtime_active_control']))
    if runtime_enabled(config):
        with runtime_connection(config) as conn:
            work=package_helper(config,'publication_work')
            if work.exists(conn):
                vector=work.vector(conn,contract_sha256=runtime_publication_contract(config)['sha256'],now=time.time())
                require(not any(reason!='time_transition_due' for reason in vector['revalidation_reasons']),
                        'active publication requires reviewed contract revalidation')
    snapshot_fields = {}
    selection = pending_value.get('selection') if pending_value else None
    if runtime_enabled(config) and selection is None:
        work = package_helper(config, 'publication_work')
        with runtime_connection(config) as conn:
            if work.exists(conn):
                scope = runtime_publication_scope(config)
                require(scope is not None, 'native publication output scope required')
                policy = config.get('publication_admission')
                require(isinstance(policy, dict), 'measured native publication admission required')
                with package_helper(config, 'runtime_authority').controller(conn, run_id=request['run_id'], owner_fd=owner_fd):
                    work.advance_time(conn, now=time.time(), limit=policy['max_candidates'])
                selection = work.select(conn, policy=policy, contract_sha256=runtime_publication_contract(config)['sha256'],
                    output_root=scope['output_root'], source_paths=package_helper(config, 'publication_fence').source_paths(config['_registry_path'], scope['output_root']),
                    deadline_at=time.monotonic() + max(0, deadline.timestamp()-time.time()))
                require(selection['units'] or selection['held'], 'no selected publication unit; work remains held')
        if selection is not None:
            pending_value = package_helper(config, 'pending_publication').record_selection(config['state_dir'], selection)
    if selection is not None:
        snapshot_fields['publication_selection'] = selection
    if config["sealed_publication_snapshots"]:
        snapshot = pending_value.get("projection") if pending_value else None
        if snapshot is None:
            if context and selection is not None:
                target_parent = Path(pending_value['projection_target']).parent
                if not target_parent.exists():
                    with package_helper(config, 'dispatcher_storage').charged_write(context['ledger'], target_parent, 0):
                        target_parent.mkdir(parents=True)
            snapshot = snapshot_helper(config).create_publication_snapshot(
                config["publication_worker_database"],
                Path(pending_value['projection_target']) if pending_value else report_path.parent / 'publication_snapshot.sqlite3',
                digest(report_path),
                owner_fd,
                config["shared_lock_path"],
                deadline_at=time.monotonic() + max(0, deadline.timestamp() - time.time()),
                projection=config["publication_snapshot_mode"] == "projection",
                **({'selection': selection, 'max_bytes': config['publication_admission']['max_projection_bytes']} if selection is not None and not context else {}),
                **({"budget": context["ledger"], "max_bytes": context["projection_cap_bytes"], **({'selection': selection} if selection is not None else {})} if context else {}),
            )
        if (context or runtime_enabled(config)) and pending_value.get("projection") is None:
            package_helper(config, "pending_publication").record_projection(config["state_dir"], snapshot)
        files = [{"path": snapshot["path"], "sha256": snapshot["sha256"]}]
        snapshot_fields["publication_snapshot"] = snapshot
    else:
        snapshot = snapshot_database(config["publication_worker_database"])
        files = [{"path": snapshot["path"], "sha256": snapshot["sha256"]}]
        if snapshot["wal"]:
            files.append(
                {"path": snapshot["wal"]["path"], "sha256": snapshot["wal"]["sha256"]}
            )
    prior_boundary = {}
    if runtime_enabled(config):
        with runtime_connection(config) as conn:
            boundary = package_helper(config, "runtime_authority").publication_control(conn).get("publication_cursor")
        if boundary is not None:
            prior_boundary["previous_publication_boundary"] = boundary
    return {
        **prior_boundary,
        **({"entry_clock": config.get("_publication_clock") or _ENTRY_CLOCK} if _ENTRY_CLOCK is not None else {}),
        **({'runtime_contract':runtime_publication_contract(config)} if runtime_enabled(config) else {}),
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
        **({"publication_policy": config["publication_policy"]} if context else {}),
    }


def validate_publication(result, request):
    require(
        isinstance(result, dict) and result.get("schema_version") == 1,
        "invalid publication receipt",
    )
    for key in ("publication_snapshot", "recovery_generation_binding", "publication_policy", "previous_publication_boundary", "publication_selection", "runtime_contract"):
        require(
            (key in result) == (key in request),
            "publication " + key + " presence differs",
        )
    for key in PUBLICATION_BINDINGS + tuple(
        key
        for key in ("publication_snapshot", "recovery_generation_binding", "publication_policy", "previous_publication_boundary", "publication_selection", "runtime_contract")
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


def unresolved_publications(root, config=None, *, deadline_at=None):
    """Scan controls once, sharing one bounded operational closure for receipts."""
    def check_deadline():
        if deadline_at is not None and time.monotonic() >= deadline_at:
            raise TimeoutError("Unresolved publication scan deadline exhausted")

    def read_control(path):
        check_deadline()
        value = read_json(path)
        check_deadline()
        return value

    def control_digest(path):
        if deadline_at is None:
            return digest(path)
        check_deadline()
        result = hashlib.sha256()
        with path.open("rb") as stream:
            while True:
                check_deadline()
                chunk = stream.read(1024 * 1024)
                if not chunk:
                    break
                result.update(chunk)
        check_deadline()
        return result.hexdigest()

    def control_reference(reference, run_dir):
        if deadline_at is None:
            return evidence_path(reference, run_dir)
        check_deadline()
        require(isinstance(reference, dict), "evidence reference must be an object")
        name = reference.get("path")
        require(isinstance(name, str) and name, "evidence path missing")
        path = (run_dir / name).resolve()
        require(path.is_relative_to(run_dir.resolve()), "evidence escapes this run directory")
        require(path.is_file(), "evidence file missing")
        require(reference.get("sha256") == control_digest(path), "evidence SHA-256 mismatch")
        return path

    check_deadline()
    unknown = []
    history = closure = None
    history_root = root.parent.parent
    outcomes = ([Path(config["_runtime_active_control"]) / "outcome.json"]
                if config and runtime_enabled(config) and config.get("_runtime_active_run") else
                ([] if config and runtime_enabled(config) else root.glob("*/outcome.json")))
    for outcome in outcomes:
        check_deadline()
        if not outcome.exists():
            continue
        if (outcome.parent / "terminal-history-retirement.json").exists():
            require(config is not None, "Retired history requires the canonical classifier")
            if history is None:
                history = package_helper(config, "terminal_history")
                check_deadline()
                closure = history.operational_closure(history_root, deadline_at=deadline_at)
                check_deadline()
            classified = history.classify_history(
                outcome.parent, root=history_root, closure=closure, deadline_at=deadline_at)
            check_deadline()
            if classified["disposition"] == "retired_terminal":
                continue
        value = read_control(outcome)
        if value.get("publication_status") not in {
            "unknown_requires_review_no_replay",
            "incomplete",
        }:
            continue
        no_write = outcome.parent / "publication_no_write_resolution.json"
        if no_write.exists():
            resolved = read_control(no_write)
            original = outcome.parent / "publication_request.json"
            proof = resolved.get("proof", {})
            gate = proof.get("prior_gate", {})
            require(
                resolved.get("run_id") == value["run_id"]
                and resolved.get("previous_outcome_sha256") == control_digest(outcome)
                and resolved.get("original_request_sha256") == control_digest(original)
                and resolved.get("original_intent_sha256") == control_digest(outcome.parent / "publication_intent.json")
                and proof.get("kind") == "verified_unstarted_publication"
                and proof.get("generation_not_started") is True
                and gate.get("state") == "complete"
                and timestamp(gate["completed_at"]) < timestamp(read_control(original)["generated_at"]),
                "invalid unstarted publication resolution",
            )
            for key in ("prior_plan", "prior_result"):
                require(control_digest(Path(proof[key]["path"])) == proof[key]["sha256"],
                        "unstarted publication journal evidence changed")
            require(read_control(Path(proof["prior_result"]["path"])) == gate,
                    "unstarted publication result differs")
            continue
        resolution = outcome.parent / "publication_resolution.json"
        if resolution.exists():
            resolved = read_control(resolution)
            require(
                resolved.get("run_id") == value["run_id"]
                and resolved.get("previous_outcome_sha256") == control_digest(outcome),
                "invalid publication resolution",
            )
            request_path = control_reference(resolved["request"], outcome.parent)
            result_path = control_reference(resolved["result"], outcome.parent)
            recovery_request = read_control(request_path)
            original_path = outcome.parent / "publication_request.json"
            original = read_control(original_path)
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
                == control_digest(original_path)
                and recovery_request.get("original_publication_intent_sha256")
                == control_digest(outcome.parent / "publication_intent.json")
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
                    == read_control(
                        outcome.parent / "publication_generation_binding.json"
                    ),
                    "recovery borrowed a different publication generation",
                )
            validate_publication(read_control(result_path), recovery_request)
            check_deadline()
        else:
            unknown.append(outcome)
    check_deadline()
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


def resume_publication(config, expected, descriptor, *, deadline_at=None):
    if not runtime_enabled(config):
        return _resume_publication(config, expected, descriptor, deadline_at=deadline_at)
    continuous = RUNTIME_CLOCK.budget(config['publication_timeout_seconds'],
                                     parent=_ENTRY_CLOCK or RUNTIME_CLOCK.inherited())
    config['_recovery_clock'] = continuous
    try:
        with package_helper(config,'publication_deadline').continuous_budget(continuous):
            return _resume_publication(config, expected, descriptor, deadline_at=deadline_at)
    finally:
        config.pop('_recovery_clock',None)


def _resume_publication(config, expected, descriptor, *, deadline_at=None):
    """Explicitly resume the publisher's same journal; never rerun the worker."""
    validation_deadline = time.monotonic() + min(
        config["publication_timeout_seconds"],
        config["total_timeout_seconds"] - config["terminate_grace_seconds"],
    )
    if deadline_at is not None:
        validation_deadline = min(validation_deadline, deadline_at - config["terminate_grace_seconds"])
    OBSERVABILITY.storage_check(config)
    storage_context = config.get("_storage_context")
    unknown = unresolved_publications(config["state_dir"] / "runs", config, deadline_at=validation_deadline)
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
        "{request_path}": str(config['state_dir'] / 'runs' / original['run_id'] / 'request.json' if runtime_enabled(config) else run_dir / 'request.json'),
        "{report_path}": original["worker_acceptance_path"],
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
        recovery_clock = config.get("_recovery_clock")
        if recovery_clock:
            environment[RUNTIME_CLOCK.ENVIRONMENT] = json.dumps(recovery_clock)
        if storage_context:
            bridge = package_helper(config, "storage_cycle")
            package_helper(config, "dispatcher_storage").require_admission(
                bridge.recheck_cycle(config, storage_context, "publication_admitted"))
            environment.update(bridge.child_environment(storage_context, "publisher"))
        with ExitStack() as stack:
            if storage_context:
                stdout = stderr = subprocess.PIPE
            else:
                stdout = stack.enter_context((attempt / "stdout.log").open("wb"))
                stderr = stack.enter_context((attempt / "stderr.log").open("wb"))
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
            if storage_context:
                package_helper(config, "dispatcher_storage").wait_logged(
                    process, (attempt / "stdout.log", attempt / "stderr.log"),
                    storage_context["ledger"], timeout, deadline.timestamp(),
                    **({"continuous_budget":recovery_clock} if recovery_clock else {}))
            else:
                wait_with_deadlines(process, timeout, deadline.timestamp(), continuous_budget=recovery_clock)
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
            publication_exit_code=0,
            readback_verified=result.get("readback_verified") is True,
            pending_backlog_empty=result.get("pending_backlog_empty") is True,
            reasons=[],
            worker_reexecuted=False,
        )
        if runtime_enabled(config) and result.get("pending_backlog_empty") is True:
            with runtime_connection(config) as conn:
                package_helper(config, "runtime_authority").finish_publication(conn, original["run_id"])
    except Exception as exc:
        result_state.update(
            status="process_failure",
            publication_status="unknown_requires_review_no_replay",
            reasons=[type(exc).__name__ + ": " + str(exc)],
            failure_stage="publication", failure_type=type(exc).__name__,
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


def locked_tick(config, expected, lock_descriptor, recovered=None, *, storage_context=None, resume=None,
                deadline_at=None):
    OBSERVABILITY.storage_check(config)
    run_id = resume["run_id"] if resume else (storage_context["ledger"].cycle_id if storage_context else str(uuid.uuid4()))
    started = now()
    hard_end = time.monotonic() + config["total_timeout_seconds"]
    if deadline_at is not None:
        hard_end = min(hard_end, deadline_at)
    require(hard_end > time.monotonic(), "no tick budget remains after admission")
    wall_end = datetime.fromtimestamp(min(
        time.time() + max(0, hard_end - time.monotonic()),
        config.get("_entry_wall_deadline", float("inf"))), timezone.utc)
    accepted_dir = config["state_dir"] / "runs" / run_id
    run_dir = Path(resume.get('publication_control_dir', accepted_dir)) if resume else accepted_dir
    if resume is None:
        run_dir.mkdir(parents=True, exist_ok=False)
    proof_paths = {'acceptance':accepted_dir/'acceptance.json','worker_result':accepted_dir/'worker_result.json'}
    if resume and resume.get('proof_files'):
        proof_paths = package_helper(config,'pending_publication').proof_paths(accepted_dir,resume['proof_files'])
    request_path, report_path = accepted_dir / "request.json", proof_paths['acceptance']
    manifest_path = accepted_dir / "source_manifest.json"
    if resume is None:
        manifest_bytes = config["source_manifest_path"].read_bytes()
        if storage_context:
            helper = package_helper(config, "dispatcher_storage")
            with helper.charged_write(storage_context["ledger"], manifest_path, len(manifest_bytes)):
                manifest_path.write_bytes(manifest_bytes)
        else:
            manifest_path.write_bytes(manifest_bytes)
    request = {
        "schema_version": 1,
        "run_id": run_id,
        "started_at": started.isoformat(),
        "deadline_at": (
            min(started + timedelta(seconds=config["timeout_seconds"]), wall_end)
        ).isoformat(),
        "tick_deadline_at": wall_end.isoformat(),
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
    if config.get('policy_audit'):
        request['policy_audit'] = config['policy_audit']
    if config.get('policy_authority_location') is not None:
        request['policy_authority_location'] = config['policy_authority_location']
    if runtime_enabled(config) and 'jobagg.remediation_worker' in config.get('worker_argv',[]):
        request['expected_implementation_sha256']=runtime_publication_contract(config)['jobagg_implementation_sha256']
        request['runtime_contract']=runtime_publication_contract(config)
        if _ENTRY_CLOCK is not None:
            request['entry_clock']=_ENTRY_CLOCK
    if resume is not None:
        request = read_json(request_path)
    elif storage_context:
        request["storage_limits"] = storage_context["limits"]
        request["semantic_history_policy"] = {
            key: config["publication_policy"][key] for key in ("policy_epoch", "max_history_bytes")}
    record = {
        **({"entry_clock": _ENTRY_CLOCK} if _ENTRY_CLOCK is not None else {}),
        "schema_version": 1,
        "run_id": run_id,
        "origin": config.get("_invocation_origin", "manual"),
        'runtime_control': runtime_enabled(config),
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
        **({"accepted_work_resumed": True, "original_started_at": request["started_at"]} if resume else {}),
    }

    if runtime_enabled(config) and resume is None:
        with runtime_connection(config) as conn:
            package_helper(config, "runtime_authority").begin(conn, run_id, config["_config_sha256"],
                control_dir=run_dir, publication_scope=runtime_publication_scope(config))
        config["_runtime_active_run"] = run_id
        config["_runtime_active_control"] = str(run_dir)

    def save():
        atomic_json(run_dir / "state.json", record)
        atomic_json(config["state_dir"] / "state.json", record)

    previous, process = {}, None
    concurrency_context = None
    skip_publication = False

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
        command_clock = RUNTIME_CLOCK.budget(phase_timeout,
            parent=(config.get('_publication_clock') if phase != 'worker' else None) or _ENTRY_CLOCK or RUNTIME_CLOCK.inherited())
        if phase == 'worker':
            child_clock = RUNTIME_CLOCK.budget(config['timeout_seconds'], parent=command_clock)
        else:
            child_clock = command_clock
        record["phase"] = phase + "_starting"
        save()
        stdout_path = run_dir / (
            "stdout.log" if phase == "worker" else "publication_stdout.log"
        )
        stderr_path = run_dir / (
            "stderr.log" if phase == "worker" else "publication_stderr.log"
        )
        with ExitStack() as stack:
            if storage_context:
                stdout = stderr = subprocess.PIPE
            else:
                stdout = stack.enter_context(stdout_path.open("wb"))
                stderr = stack.enter_context(stderr_path.open("wb"))
            OBSERVABILITY.storage_check(config)
            process = subprocess.Popen(
                argv,
                cwd=cwd,
                env={**environment, RUNTIME_CLOCK.ENVIRONMENT: json.dumps(child_clock), "PYTHONPATH": str(cwd),
                     **(package_helper(config, "storage_cycle").child_environment(
                         storage_context, "worker" if phase == "worker" else "publisher")
                        if storage_context else {})},
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
                if storage_context:
                    package_helper(config, "dispatcher_storage").wait_logged(
                        process, (stdout_path, stderr_path), storage_context["ledger"],
                        phase_timeout, absolute_deadline,
                        **({"continuous_budget":command_clock} if runtime_enabled(config) else {}))
                else:
                    wait_with_deadlines(process, phase_timeout, absolute_deadline, continuous_budget=command_clock)
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
        if resume is None:
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
            if request.get('runtime_contract'):
                require(read_json(report_path).get('runtime_contract_sha256')==request['runtime_contract']['sha256'],
                        'Worker did not verify its admitted complete runtime contract')
            if timed_out:
                status = "incomplete"
                reasons.append(
                    "Worker reached its deadline and returned valid acceptance during graceful shutdown"
                )
            record.update(
                worker_status=status,
                worker_stop_reason=read_json(report_path).get('tick_stop_reason'),
                worker_progress=read_json(report_path).get('concurrency', {}).get('accepted_progress'),
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
                    **({"request_sha256": digest(request_path),
                        "config_sha256": config["_config_sha256"],
                        "database_binding": package_helper(config, "pending_publication").database_binding(
                        config["publication_worker_database"])} if storage_context or runtime_enabled(config) else {}),
                },
            )
            save()
            if runtime_enabled(config):
                accepted_progress = read_json(report_path).get("concurrency", {}).get("accepted_progress")
                with runtime_connection(config) as conn:
                    skip_publication = package_helper(config, "runtime_authority").proven_empty(
                        conn, run_id, accepted_progress=accepted_progress,
                        contract_sha256=runtime_publication_contract(config)['sha256'])
                if skip_publication:
                    record.update(publication_status="proven_empty", backlog_authoritatively_empty=True,
                                  accepted_progress=0)
                else:
                    with runtime_connection(config) as conn:
                        scoped_noop=package_helper(config,'runtime_containment').can_close_scoped_noop(
                            conn,run_id,accepted_progress=accepted_progress,
                            contract_sha256=runtime_publication_contract(config)['sha256'],**runtime_containment_args(config))
                        held=package_helper(config,'runtime_containment').summary(conn)
                    if scoped_noop:
                        skip_publication=True
                        record.update(publication_status='scoped_hold_noop',backlog_authoritatively_empty=False,
                                      pending_backlog_empty=False,publication_held=held,accepted_progress=0)
                        status='incomplete'
                        reasons.append('Scoped integrity or historical holds remain; no accepted work in this batch')
            if not skip_publication and (storage_context or runtime_enabled(config)):
                pending = package_helper(config, "pending_publication")
                pending.seal(config["state_dir"], run_id,
                             config_sha256=config["_config_sha256"],
                             database=config["publication_worker_database"],
                             lease=storage_context["ledger"].wire() if storage_context else {"runtime_epoch": runtime_active(config)["epoch"]},
                             projection_target=(Path(storage_context["limits"]["scratch_dir"]) / run_id / "publication_snapshot.sqlite3" if storage_context else None))
                if storage_context:
                    storage_context["ledger"].transition("worker_admitted", "accepted_pending")
        else:
            proof = read_json(proof_paths["worker_result"])
            status, reasons = proof["status"], list(proof["reasons"])
            record.update(worker_exit_code=proof.get('worker_exit_code'), worker_dispatched=False,
                          publication_only=proof.get('schema') == 'native-accepted-queue-proof-1',
                          worker_status=status, worker_reasons=reasons,
                          worker_report_sha256=digest(report_path), phase="worker_validated")
            save()
        replacements = {"{request_path}": str(request_path), "{report_path}": str(report_path),
                        "{run_id}": run_id, "{parallel_sources}": str(request.get("parallel_sources", 1))}
        if config["publication_argv"] and not skip_publication:
            record["phase"] = "projection"
            publication_end = min(hard_end, time.monotonic()+config['publication_timeout_seconds'])
            publication_wall_end = min(wall_end.timestamp(), time.time()+config['publication_timeout_seconds'])
            config['_publication_clock'] = RUNTIME_CLOCK.budget(config['publication_timeout_seconds'],
                parent=_ENTRY_CLOCK or RUNTIME_CLOCK.inherited())
            remaining = min(
                config["publication_timeout_seconds"],
                hard_end - time.monotonic() - config["terminate_grace_seconds"],
            )
            require(remaining > 0, "no publication budget remains")
            if runtime_enabled(config):
                remaining = phase_remaining(config, publication_end, publication_wall_end, "projection")
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
            if runtime_enabled(config):
                remaining = phase_remaining(config, publication_end, publication_wall_end, "publisher")
            require(remaining > 0, "snapshot preparation exhausted total publication budget")
            publication_deadline = now() + timedelta(seconds=remaining)
            pub_request.update(generated_at=now().isoformat(),
                               deadline_at=publication_deadline.isoformat(),
                               deadline_epoch=publication_deadline.timestamp())
            if storage_context:
                bridge = package_helper(config, "storage_cycle")
                controls = package_helper(config, "dispatcher_storage")
                phase = storage_context["ledger"].snapshot()["phase"]
                controls.require_admission(bridge.recheck_cycle(config, storage_context, phase))
                OBSERVABILITY.storage_check(config)
                if phase == "accepted_pending":
                    storage_context["ledger"].transition("accepted_pending", "publication_admitted")
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
            if storage_context or runtime_enabled(config):
                package_helper(config, "pending_publication").release_after_intent(config["state_dir"], run_id=run_id)
            if runtime_enabled(config):
                with runtime_connection(config) as conn:
                    helper = package_helper(config, "runtime_authority")
                    if helper.validate(conn)['phase'] == 'worker':
                        helper.transition(conn, run_id, "worker", "accepted")
                    helper.transition(conn, run_id, "accepted", "publisher")
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
            drained = {'drained': result.get('pending_backlog_empty') is True,
                       'empty': result.get('pending_backlog_empty') is True, 'held': None}
            if runtime_enabled(config) and result['status'] in {'published', 'noop'}:
                drained = runtime_acknowledge(config, run_id, result, pub_request, lock_descriptor)
            record.update(
                publication_status=result["status"],
                readback_verified=result.get("readback_verified") is True,
                pending_backlog_empty=drained['empty'],
                publication_drain_complete=drained['drained'], publication_held=drained['held'],
                publication_boundary=result.get("publication_boundary"),
                publication_result_sha256=digest(pub_result_path),
                publication_recovery_allowed=result["status"] == "incomplete",
            )
            if result["status"] in {"incomplete", "deferred"}:
                status = "incomplete"
                reasons.append("Publisher reported bounded incomplete work")
        record.update(status=status, reasons=reasons)
    except Exception as exc:
        storage_deferred = storage_context is not None and isinstance(
            exc, package_helper(config, "storage_budget").StorageBudgetDeferred)
        record.update(
            status="storage_deferred" if storage_deferred else "process_failure",
            reasons=[f"{type(exc).__name__}: {exc}"],
            failure_stage=record.get("phase", "unknown"), failure_type=type(exc).__name__,
        )
        if storage_deferred:
            record["storage_deferral"] = True
        if record["publication_status"] == "intent_recorded" or (run_dir / "publication_intent.json").exists():
            record["publication_status"] = "unknown_requires_review_no_replay"
        elif (storage_context or runtime_enabled(config)) and (package_helper(config, "pending_publication").inspect(config["state_dir"])
                                  or (storage_deferred and (run_dir / "worker_result.json").exists())):
            record.update(status="accepted_work_pending", publication_status="not_started",
                          worker_reexecuted=False)
        elif storage_deferred:
            record.update(review_required=True, worker_outcome_unknown="worker_pid" in record,
                          worker_reexecuted=False)
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
            if runtime_enabled(config) and record.get("publication_status") == "proven_empty":
                with runtime_connection(config) as conn:
                    package_helper(config, "runtime_authority").close_empty(conn, run_id, accepted_progress=0,
                        contract_sha256=runtime_publication_contract(config)['sha256'])
            if runtime_enabled(config) and record.get('publication_status')=='scoped_hold_noop':
                with runtime_connection(config) as conn:
                    package_helper(config,'runtime_containment').close_scoped_noop(conn,run_id,accepted_progress=0,
                        contract_sha256=runtime_publication_contract(config)['sha256'],**runtime_containment_args(config))
            if (runtime_enabled(config) and record.get("publication_status") in {"published", "noop"}
                    and record.get('publication_drain_complete', record.get('pending_backlog_empty')) is True):
                with runtime_connection(config) as conn:
                    helper = package_helper(config, "runtime_authority")
                    if record.get("publication_boundary") is not None:
                        helper.remember_publication_boundary(conn, run_id, record["publication_boundary"])
                    helper.finish_publication(conn, run_id)
            if (runtime_enabled(config) and record.get('publication_status') in {'published', 'noop'}
                    and record.get('publication_drain_complete', record.get('pending_backlog_empty')) is False):
                runtime_advance(config, run_id, run_dir, lock_descriptor)
            if record["status"] == "complete":
                atomic_json(config["state_dir"] / "last_complete.json", record)
        finally:
            for signum, handler in previous.items():
                signal.signal(signum, handler)
    return record


def _main(argv=None):
    global _ENTRY_DEADLINES, _ENTRY_CLOCK
    entry_monotonic, entry_wall = time.monotonic(), time.time()
    _ENTRY_DEADLINES = (entry_monotonic + 840, entry_wall + 840)
    OBSERVABILITY.DEADLINE_CHECK = check_entry_budget
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--invocation-origin", choices=("manual", "natural"), default="manual")
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
    budgeted_regular_output = False
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
            budgeted_regular_output = bool(raw.get("storage_budget", {}).get("enabled")) and stat.S_ISREG(os.fstat(sys.stdout.fileno()).st_mode)
            if not raw.get("storage_budget", {}).get("enabled"):
                attempt, intent = OBSERVABILITY.start_attempt(config_path, raw)
        raw_entry = read_json(config_path)
        entry_limit = raw_entry.get("total_timeout_seconds", 840)
        require(type(entry_limit) in (int, float) and 0 < entry_limit <= 86400, "invalid entry budget")
        _ENTRY_DEADLINES = (
            min(entry_monotonic + entry_limit, float(os.environ.get("JOBAGG_ENTRY_MONOTONIC_DEADLINE", "inf"))),
            min(entry_wall + entry_limit, float(os.environ.get("JOBAGG_ENTRY_WALL_DEADLINE", "inf"))))
        _ENTRY_CLOCK = RUNTIME_CLOCK.clamp(_ENTRY_CLOCK, entry_limit)
        os.environ[RUNTIME_CLOCK.ENVIRONMENT] = json.dumps(_ENTRY_CLOCK)
        OBSERVABILITY.DEADLINE_CHECK = check_entry_budget
        check_entry_budget()
        config, expected = load_config(
            config_path, record_storage_health=args.execute and not raw.get("storage_budget", {}).get("enabled") if args.execute else False,
            allow_storage_maintenance=args.execute,
        )
        config["_invocation_origin"] = args.invocation_origin
        config["_entry_monotonic_deadline"], config["_entry_wall_deadline"] = _ENTRY_DEADLINES
        check_entry_budget()
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
                "storage_retention": config["storage_retention"],
                "writes_performed": False,
            }
            print(json.dumps(result, indent=2))
            return 0
        os.umask(0o077)
        if budgeted_regular_output:
            log = config["storage_cycle"].get("controller_log_path")
            require(log is not None, "Regular stdout requires an admitted controller_log_path")
            target, output = Path(log).stat(), os.fstat(sys.stdout.fileno())
            require((target.st_dev, target.st_ino) == (output.st_dev, output.st_ino)
                    and fcntl.fcntl(sys.stdout.fileno(), fcntl.F_GETFL) & os.O_APPEND,
                    "Dispatcher stdout does not match its admitted append-only log")
            config["_controller_stdout_regular"] = True
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
    if not budgeted_regular_output:
        print(json.dumps(result, indent=2))
    _ENTRY_DEADLINES = None
    OBSERVABILITY.DEADLINE_CHECK = lambda: None
    return EXIT[result["status"]]


def main(argv=None):
    global _ENTRY_DEADLINES, _ENTRY_CLOCK
    original_clock = os.environ.get(RUNTIME_CLOCK.ENVIRONMENT)
    try:
        _ENTRY_CLOCK = RUNTIME_CLOCK.budget(840, parent=RUNTIME_CLOCK.inherited())
        os.environ[RUNTIME_CLOCK.ENVIRONMENT] = json.dumps(_ENTRY_CLOCK)
        check_entry_budget()
        return _main(argv)
    except (ValueError, TimeoutError) as exc:
        print(json.dumps({'status':'process_failure','reasons':[str(exc)],'review_required':True}))
        return EXIT['process_failure']
    finally:
        _ENTRY_DEADLINES = None
        _ENTRY_CLOCK = None
        if original_clock is None:
            os.environ.pop(RUNTIME_CLOCK.ENVIRONMENT, None)
        else:
            os.environ[RUNTIME_CLOCK.ENVIRONMENT] = original_clock
        OBSERVABILITY.DEADLINE_CHECK = lambda: None


if __name__ == "__main__":
    sys.exit(main())
