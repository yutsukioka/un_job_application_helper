"""Dispatcher bridge for cooperative fixed-budget cycle and maintenance escrow.

Calls are made under the existing dispatcher owner lock. This module never opens
a database or fetches a provider; it inventories registered filesystem metadata.
An incomplete registry or unsupported hard-cap backend prevents activation.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import time
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path
from typing import Any

from .storage_budget import (
    CATEGORY_LIMITS,
    CONTROL_ENVELOPE_BYTES,
    MIN_PROJECTION_ALLOCATION_BYTES,
    Admission,
    BudgetConfig,
    BudgetLedger,
    StorageBudgetDeferred,
    StorageBudgetError,
    assess,
    inventory,
    parse_budget_config,
)

LEASE_ENV = "JOBAGG_STORAGE_BUDGET_LEASE"
LIMITS_ENV = "JOBAGG_STORAGE_CYCLE_LIMITS"
PATH_OPTIONS = {"--workspace", "--worker-database", "--output-dir", "--state-dir",
                "--shared-lock", "--registry", "--source-registry", "--config",
                "--policy", "--publication-policy", "--storage-policy"}
REQUIRED_CONFIG_PATHS = ("_config_path", "source_manifest_path", "_registry_path", "state_dir",
                         "attempt_state_dir", "shared_lock_path", "publication_worker_database",
                         "worker_cwd", "publication_cwd")
SUPPORTED_ENTRYPOINTS = {"worker_argv": "jobagg.remediation_worker",
                         "publication_argv": "jobagg.publish_worker"}


def _positive(value: Any, label: str) -> int:
    if type(value) is not int or value <= 0:
        raise StorageBudgetError(f"{label} must be finite positive bytes")
    return value


def _growth(value: Any, label: str) -> dict[str, int]:
    if not isinstance(value, Mapping) or set(value) != set(CATEGORY_LIMITS):
        raise StorageBudgetError(f"{label} must cover every category")
    result = {}
    for category, limit in CATEGORY_LIMITS.items():
        amount = value[category]
        if type(amount) is not int or not 0 <= amount <= limit:
            raise StorageBudgetError(f"{label} exceeds category ceiling")
        result[category] = amount
    return result


def validate_config(rawbudget: Mapping[str, Any] | None, cyclelimits: Mapping[str, Any] | None) -> dict[str, Any]:
    budget = parse_budget_config(rawbudget)
    if not budget.enabled:
        if cyclelimits:
            raise StorageBudgetError("cycle limits require an enabled storage budget")
        return {"storage_budget": {"enabled": False}, "storage_cycle": None}
    if not isinstance(cyclelimits, Mapping):
        raise StorageBudgetError("finite storage_cycle limits required")
    limits = dict(cyclelimits)
    allowed = {"ingress_cap_bytes", "accepted_cap_bytes", "database_growth_cap_bytes", "control_cap_bytes",
               "scratch_dir", "support_paths", "inventory_seconds", "output_dir", "maintenance_growth",
               "publisher_write_limits", "response_cap_bytes", "controller_log_path",
               "policy_transaction_cap_bytes", "controller_sql_cap_bytes"}
    if set(limits) - allowed:
        raise StorageBudgetError("unknown storage cycle limit")
    for key in ("ingress_cap_bytes", "accepted_cap_bytes", "database_growth_cap_bytes", "control_cap_bytes"):
        limits[key] = _positive(limits.get(key), key)
    if limits["control_cap_bytes"] < CONTROL_ENVELOPE_BYTES:
        raise StorageBudgetError("control cap does not cover durable escrow")
    for key in ('policy_transaction_cap_bytes', 'controller_sql_cap_bytes'):
        if key in limits:
            limits[key] = _positive(limits[key], key)
    response_cap = _positive(limits.get("response_cap_bytes", min(50 * 1024**2, limits["ingress_cap_bytes"])), "response cap")
    if response_cap > min(50 * 1024**2, limits["ingress_cap_bytes"]):
        raise StorageBudgetError("response cap exceeds finite ingress or 50 MiB limit")
    limits["response_cap_bytes"] = response_cap
    scratch = Path(limits.get("scratch_dir", ""))
    if not scratch.is_absolute() or budget.root_for(scratch).category != "work":
        raise StorageBudgetError("scratch must be an explicitly registered work-category path")
    limits["scratch_dir"] = str(scratch.resolve())
    supports = limits.get("support_paths")
    if not isinstance(supports, list) or not supports or any(not isinstance(item, str) or not Path(item).is_absolute() for item in supports):
        raise StorageBudgetError("explicit source/runtime/build/test/temp support paths required")
    limits["support_paths"] = [str(Path(path).resolve()) for path in supports]
    for path in limits["support_paths"]:
        budget.root_for(path)
        if not Path(path).exists():
            raise StorageBudgetError("registered support path is missing")
    seconds = limits.get("inventory_seconds", 30)
    if type(seconds) not in (int, float) or not 0 < seconds <= 120:
        raise StorageBudgetError("inventory time must be bounded by 120 seconds")
    limits["inventory_seconds"] = seconds
    if limits.get("output_dir") is not None:
        output = Path(limits["output_dir"])
        if not output.is_absolute():
            raise StorageBudgetError("output_dir must be absolute")
        limits["output_dir"] = str(output.resolve())
        budget.root_for(output)
    if limits.get("controller_log_path") is not None:
        log = Path(limits["controller_log_path"])
        if (not log.is_absolute() or log.is_symlink()
                or budget.root_for(log).category != "diagnostics"):
            raise StorageBudgetError("controller log must be a direct registered diagnostics path")
        limits["controller_log_path"] = str(log.resolve())
    if limits.get("maintenance_growth") is not None:
        limits["maintenance_growth"] = _growth(limits["maintenance_growth"], "maintenance envelope")
        if limits["maintenance_growth"]["diagnostics"] < CONTROL_ENVELOPE_BYTES:
            raise StorageBudgetError("maintenance controls do not cover durable escrow")
    # Every admitted publisher must have an enforced finite writer cap. A
    # metadata growth estimate alone does not constrain its actual writes.
    limits["publisher_write_limits"] = _growth(limits.get("publisher_write_limits"), "publisher envelope")
    return {"storage_budget": budget.to_dict(), "storage_cycle": limits}


def validate_entrypoints(config: Mapping[str, Any]) -> None:
    """Accept only the two CLI modules that consume the child budget lease.

    A registered interpreter is not sufficient: scripts, alternative modules,
    command strings and ambiguous interpreter options can bypass those guards.
    Only the write-suppression/unbuffered flags are supported before ``-m``.
    """
    for key, module in SUPPORTED_ENTRYPOINTS.items():
        values = config.get(key)
        if (not isinstance(values, list) or not values
                or not all(isinstance(value, str) and value for value in values)
                or not Path(values[0]).is_absolute()):
            raise StorageBudgetError(f"{key} requires an explicit budget-aware module command")
        index = 1
        flags = set()
        while index < len(values) and values[index] in {"-B", "-u"}:
            if values[index] in flags:
                raise StorageBudgetError(f"{key} has ambiguous interpreter options")
            flags.add(values[index])
            index += 1
        if values[index:index + 2] != ["-m", module]:
            raise StorageBudgetError(f"{key} must execute -m {module}")
        if any(value.startswith(("-c", "-m")) for value in values[index + 2:]):
            raise StorageBudgetError(f"{key} has an extra interpreter command switch")


def _configuration(config: Mapping[str, Any]) -> tuple[BudgetConfig, dict[str, Any] | None]:
    normalized = validate_config(config.get("storage_budget"), config.get("storage_cycle"))
    budget = parse_budget_config(normalized["storage_budget"])
    if budget.enabled:
        validate_entrypoints(config)
        if not isinstance(config.get("_config_sha256"), str) or not re.fullmatch(r"[a-f0-9]{64}", config["_config_sha256"]):
            raise StorageBudgetError("exact dispatcher configuration hash required")
        # Parent config bytes, finite stage caps, command paths and supported
        # writers are immutable for the admitted cycle, including restarts.
        binding = {"cycle_limits": normalized["storage_cycle"],
                   "config_sha256": config.get("_config_sha256"),
                   "paths": {key: str(config.get(key)) for key in REQUIRED_CONFIG_PATHS},
                   "worker_argv": config.get("worker_argv"), "publication_argv": config.get("publication_argv")}
        budget = replace(budget, binding_sha256=hashlib.sha256(json.dumps(
            binding, sort_keys=True, separators=(",", ":")).encode()).hexdigest())
    return budget, normalized["storage_cycle"]


def _argv_paths(config: Mapping[str, Any], key: str, cwd: Path) -> list[Path]:
    values = config.get(key, [])
    if not isinstance(values, list) or not all(isinstance(value, str) for value in values):
        raise StorageBudgetError("invalid storage command paths")
    result = []
    for index, value in enumerate(values):
        if value in PATH_OPTIONS:
            if index + 1 >= len(values) or "{" in values[index + 1]:
                raise StorageBudgetError("unresolved active command path")
            path = Path(values[index + 1])
            result.append((path if path.is_absolute() else cwd / path).resolve())
        elif Path(value).is_absolute():
            # Includes Python/executable and any additional absolute flags or
            # script arguments, without guessing that a bare module is a path.
            result.append(Path(value).resolve())
    if values and not Path(values[0]).is_absolute():
        raise StorageBudgetError("runtime executable must have an explicit absolute path")
    return result


def required_paths(config: Mapping[str, Any], limits: Mapping[str, Any]) -> list[Path]:
    result = []
    future_controls = set()
    for key in REQUIRED_CONFIG_PATHS:
        value = config.get(key)
        if value is None:
            raise StorageBudgetError(f"active path missing: {key}")
        path = Path(value)
        if not path.is_absolute():
            raise StorageBudgetError(f"active path is not absolute: {key}")
        if not path.exists():
            raise StorageBudgetError(f"active path is missing: {key}")
        if key in {"worker_cwd", "publication_cwd", "state_dir", "attempt_state_dir"} and not path.is_dir():
            raise StorageBudgetError(f"active directory differs: {key}")
        if key in {"_config_path", "source_manifest_path", "_registry_path", "publication_worker_database"} and not path.is_file():
            raise StorageBudgetError(f"active file differs: {key}")
        result.append(path.resolve())
    result.extend(_argv_paths(config, "worker_argv", Path(config["worker_cwd"])))
    result.extend(_argv_paths(config, "publication_argv", Path(config["publication_cwd"])))
    result.extend(Path(path) for path in limits["support_paths"])
    result.append(Path(limits["scratch_dir"]))
    for key in ("maintenance_file",):
        if config.get(key):
            control = Path(config[key]).resolve()
            result.append(control)
            future_controls.add(control)
            if not control.parent.is_dir():
                raise StorageBudgetError("future control parent is missing")
    guard = config.get("storage_guard") or {}
    if guard.get("sentinel_path"):
        result.append(Path(guard["sentinel_path"]).resolve())
    if limits.get("controller_log_path"):
        log = Path(limits["controller_log_path"])
        if not log.is_file() or log.is_symlink():
            raise StorageBudgetError("controller log is not an existing regular file")
        result.append(log)
    if any(not path.exists() and path not in future_controls for path in result):
        raise StorageBudgetError("required runtime/support path is missing")
    return sorted(set(result))


def _output_directory(config: Mapping[str, Any], limits: Mapping[str, Any]) -> Path:
    values = config.get("publication_argv", [])
    candidates = []
    for index, value in enumerate(values):
        if value == "--output-dir" and index + 1 < len(values):
            path = Path(values[index + 1])
            candidates.append((path if path.is_absolute() else Path(config["publication_cwd"]) / path).resolve())
    if limits.get("output_dir"):
        candidates.append(Path(limits["output_dir"]))
    if not candidates or len(set(candidates)) != 1:
        raise StorageBudgetError("one unambiguous publication output directory required")
    return candidates[0]


def _file_bytes(path: Path) -> int:
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or not hasattr(info, "st_blocks"):
        raise StorageBudgetError("basis file is not a supported regular file")
    return max(info.st_size, info.st_blocks * 512)


def _basis(config: Mapping[str, Any], limits: Mapping[str, Any], *, deadline: float) -> dict[str, int]:
    database = Path(config["publication_worker_database"])
    worker = _file_bytes(database)
    for suffix in ("-wal", "-shm"):
        sidecar = Path(str(database) + suffix)
        if sidecar.exists() or sidecar.is_symlink():
            worker += _file_bytes(sidecar)
    output = _output_directory(config, limits)
    db_bytes = export_bytes = entries = 0
    with os.scandir(output) as children:
        for child in children:
            entries += 1
            if entries > 10_000 or time.monotonic() >= deadline:
                raise StorageBudgetDeferred("publication_basis_incomplete")
            if child.name.endswith((".sqlite3", ".sqlite3-wal", ".sqlite3-shm")):
                db_bytes += _file_bytes(Path(child.path))
            elif child.name.endswith((".json", ".csv")):
                export_bytes += _file_bytes(Path(child.path))
    return {"worker_db_bytes": worker, "output_db_bytes": db_bytes, "export_bytes": export_bytes}


def _free_bytes(budget: BudgetConfig) -> dict[int, int]:
    values = {}
    for root in budget.roots:
        info = root.path.stat()
        if not hasattr(os, "statvfs"):
            raise StorageBudgetDeferred("physical_free_backend_unavailable")
        free = os.statvfs(root.path)
        available = free.f_bavail * free.f_frsize
        values[info.st_dev] = min(values.get(info.st_dev, available), available)
    return values


def _assess(config: Mapping[str, Any], budget: BudgetConfig, limits: Mapping[str, Any],
            envelope: Mapping[str, int], *, deadline: float) -> Admission:
    observed = inventory(budget, deadline=deadline)
    paths = required_paths(config, limits)
    paths.append(_output_directory(config, limits))
    for role, targets in {
        "controller": [config["state_dir"], config["attempt_state_dir"], limits["scratch_dir"], budget.ledger_path],
        "worker": [config["publication_worker_database"], limits["scratch_dir"]],
        "publisher": [_output_directory(config, limits), limits["scratch_dir"]],
    }.items():
        writers = [writer for writer, declared in budget.writer_roles.items() if declared == role]
        if len(writers) != 1:
            raise StorageBudgetError("each supported cycle role must have one writer")
        for path in targets:
            budget.root_for(path, writers[0])
    return assess(budget, observed, projected_growth=envelope, physical_free_bytes=_free_bytes(budget),
                  required_paths=paths, writer_ids=tuple(budget.writer_roles))


def _worker_control_categories(config: Mapping[str, Any], budget: BudgetConfig) -> set[str]:
    workspace = Path(config["publication_worker_database"]).parent
    paths = [workspace / "ticks", workspace / "listing_checkpoints",
             Path(str(config["shared_lock_path"]) + ".worker-policy"),
             Path(config["state_dir"]), Path(config["attempt_state_dir"])]
    return {budget.root_for(path).category for path in paths}


def prepare_cycle(config: Mapping[str, Any], run_id: str) -> dict[str, Any] | None:
    budget, limits = _configuration(config)
    if not budget.enabled:
        return None
    assert limits is not None
    deadline = time.monotonic() + limits["inventory_seconds"]
    basis = _basis(config, limits, deadline=deadline)
    projection_cap = 2 * (basis["worker_db_bytes"] + limits["database_growth_cap_bytes"])
    if projection_cap < MIN_PROJECTION_ALLOCATION_BYTES:
        raise StorageBudgetError("projection cap cannot cover finite snapshot recovery controls")
    # Enforced publisher caps are added to worker/projection/control escrow,
    # never inferred from an unusually small previous publication.
    envelope = {"essential": limits["ingress_cap_bytes"] + limits["accepted_cap_bytes"] + limits["database_growth_cap_bytes"],
                "publication": 0, "evidence": 0, "diagnostics": limits["control_cap_bytes"],
                "work": projection_cap + limits["ingress_cap_bytes"]}
    envelope = {category: amount + limits["publisher_write_limits"][category] for category, amount in envelope.items()}
    # Mixed legacy layouts keep ticks/checkpoints/policy beside essential data.
    # Charge the bounded control allowance to each actual hosting category; the
    # dedicated diagnostic ledger reserve remains independent and prepaid.
    for category in _worker_control_categories(config, budget) - {"diagnostics"}:
        envelope[category] += limits["control_cap_bytes"]
    decision = _assess(config, budget, limits, envelope, deadline=deadline)
    if not decision.ready:
        raise StorageBudgetDeferred(";".join(decision.reasons))
    ledger = BudgetLedger.create(decision, cycle_id=run_id,
                                metadata={"projection_cap_bytes": projection_cap, "basis": basis})
    return {"ledger": ledger, "admission": decision, "envelope": envelope, "limits": limits,
            "basis": basis, "required_paths": required_paths(config, limits), "maintenance": False,
            "projection_cap_bytes": projection_cap}


def attach_context(config: Mapping[str, Any], wire: Mapping[str, str]) -> dict[str, Any]:
    """Restore the same escrow; a denied fresh assessment never resets its work."""
    budget, limits = _configuration(config)
    if not budget.enabled or limits is None:
        raise StorageBudgetError("cannot restore a cycle without its configured budget")
    ledger = BudgetLedger.attach(wire)
    state = ledger.snapshot()
    if state["scope_hash"] != budget.scope_hash:
        raise StorageBudgetError("restored cycle configuration/scope differs")
    if budget.writer_roles.get(ledger.writer_id) != "controller":
        raise StorageBudgetError("restoring a cycle requires its controller capability")
    basis = _basis(config, limits, deadline=time.monotonic() + limits["inventory_seconds"])
    # Reopening cannot enlarge the original admitted projection cap, even when
    # a later stat observes a larger DB. The pending writer fence must also pass.
    projection = state["metadata"].get("projection_cap_bytes")
    maintenance = state["metadata"].get("maintenance") is True
    if type(projection) is not int or projection < 0 or (not maintenance and projection == 0):
        raise StorageBudgetError("original projection envelope is missing")
    context = {"ledger": ledger, "envelope": state["envelope"], "limits": limits, "basis": basis,
               "required_paths": required_paths(config, limits), "maintenance": maintenance,
               "projection_cap_bytes": projection}
    context["admission"] = recheck_cycle(config, context, state["phase"])
    return context


def existing_context(config: Mapping[str, Any]) -> dict[str, Any] | None:
    """Find unfinished escrow under the owner lock, without creating a new one."""
    budget, _ = _configuration(config)
    if not budget.enabled:
        return None
    assert budget.ledger_path is not None
    ledger = BudgetLedger.existing_controller(budget.ledger_path)
    return attach_context(config, ledger.wire()) if ledger is not None else None


def prepare_maintenance(config: Mapping[str, Any], run_id: str, *,
                        pending_context: Mapping[str, Any] | None = None) -> dict[str, Any] | None:
    budget, limits = _configuration(config)
    if not budget.enabled:
        return None
    assert limits is not None
    if "maintenance_growth" not in limits:
        raise StorageBudgetDeferred("finite_maintenance_envelope_missing")
    envelope = limits["maintenance_growth"]
    if pending_context is not None:
        ledger = pending_context["ledger"]
        state = ledger.snapshot()
        if state["phase"] not in {"accepted_pending", "publication_admitted"}:
            raise StorageBudgetDeferred("maintenance_requires_quiet_pending_publication")
        if state["operations"]:
            raise StorageBudgetDeferred("maintenance_pending_operations_unsettled")
        available = ledger.remaining_envelope()
        if any(envelope[key] > available[key] for key in CATEGORY_LIMITS):
            raise StorageBudgetDeferred("pending_cycle_maintenance_envelope_insufficient")
        decision = recheck_cycle(config, pending_context, state["phase"])
        if not decision.ready:
            raise StorageBudgetDeferred("maintenance_admission_denied:" + ";".join(decision.reasons))
        # No second escrow and no closing the pending publication's ledger. The
        # owner serializes this independent maintenance with publication replay.
        return {**pending_context, "admission": decision, "maintenance": True,
                "maintenance_reuses_pending": True, "maintenance_envelope": envelope}
    decision = _assess(config, budget, limits, envelope, deadline=time.monotonic() + limits["inventory_seconds"])
    if not decision.ready:
        # Over-budget legacy migration needs its own reviewed mechanism. Routine
        # maintenance never receives an implicit permission to exceed 100 GB.
        raise StorageBudgetDeferred("maintenance_admission_denied:" + ";".join(decision.reasons))
    ledger = BudgetLedger.create(decision, cycle_id=run_id,
                                metadata={"maintenance": True, "projection_cap_bytes": 0})
    return {"ledger": ledger, "admission": decision, "envelope": envelope, "limits": limits,
            "required_paths": required_paths(config, limits), "maintenance": True}


def recheck_cycle(config: Mapping[str, Any], context: Mapping[str, Any], phase: str) -> Admission:
    budget, limits = _configuration(config)
    if not budget.enabled or limits is None:
        raise StorageBudgetError("active cycle lost its budget configuration")
    ledger = context["ledger"]
    state = ledger.snapshot()
    if state["scope_hash"] != budget.scope_hash or state["phase"] != phase:
        raise StorageBudgetError("cycle recheck phase/scope differs")
    # Settled operation bytes are already in the fresh inventory. Outstanding
    # grants retain their full uncertain charge, even if a child crashed after
    # consuming its allowance. Thus restarts never obtain optimistic free space.
    envelope = {category: state["envelope"][category] - state["settled"][category] for category in CATEGORY_LIMITS}
    envelope["diagnostics"] = max(envelope["diagnostics"], CONTROL_ENVELOPE_BYTES)
    decision = _assess(config, budget, limits, envelope, deadline=time.monotonic() + limits["inventory_seconds"])
    if isinstance(context, dict):
        context["admission"] = decision
    return decision


def child_environment(context: Mapping[str, Any] | None, role: str) -> dict[str, str]:
    if context is None:
        return {}
    if not context["admission"].ready:
        raise StorageBudgetDeferred("child_cycle_admission_denied")
    ledger = context["ledger"]
    config = context["admission"].config
    candidates = [writer for writer, declared in config.writer_roles.items() if declared == role]
    if len(candidates) != 1:
        raise StorageBudgetError("child role must map to one supported writer")
    wire = ledger.for_writer(candidates[0]).wire()
    return {LEASE_ENV: json.dumps(wire, sort_keys=True, separators=(",", ":")),
            LIMITS_ENV: json.dumps(context["limits"], sort_keys=True, separators=(",", ":")),
            "TMPDIR": context["limits"]["scratch_dir"], "TMP": context["limits"]["scratch_dir"],
            "TEMP": context["limits"]["scratch_dir"], "PYTHONDONTWRITEBYTECODE": "1"}
