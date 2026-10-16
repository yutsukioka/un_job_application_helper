"""Cooperative, fail-closed accounting for the fixed JobAgg active scope.

This is not a filesystem quota.  Hard mode deliberately cannot activate until a
real allocation-enforcement backend covers every mutable root.  Reservations
must be consumed *before* bounded writes; they do not intercept arbitrary OS,
SQLite, subprocess, or third-party writes.  The runner's existing owner lock
must cover inventory/admission and creation of a cycle.  The small durable ledger
serializes cooperating subprocesses, retains interrupted reservations, and is
trusted owner-writable local control state, not a cryptographically signed grant.
"""

from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import re
import stat
import time
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from .atomic_json_store import AtomicJsonStore, AtomicJsonStoreError

TOTAL_LIMIT = 100_000_000_000
CATEGORY_LIMITS = {
    "essential": 40_000_000_000,
    "publication": 15_000_000_000,
    "evidence": 10_000_000_000,
    "diagnostics": 5_000_000_000,
    "work": 30_000_000_000,
}
PHYSICAL_RESERVE_BYTES = 20 * 1024**3
LEDGER_MAX_BYTES = 256 * 1024
# Old + replacement JSON, mutation lock and directory allocations must fit too.
CONTROL_ENVELOPE_BYTES = 3 * LEDGER_MAX_BYTES
PROJECTION_RECEIPT_MAX_BYTES = 64 * 1024
PROJECTION_CONTROL_BYTES = 3 * PROJECTION_RECEIPT_MAX_BYTES + 16 * 1024
MIN_PROJECTION_ALLOCATION_BYTES = PROJECTION_CONTROL_BYTES + 8192
PHASES = ("worker_admitted", "accepted_pending", "publication_admitted", "completed")
ROLE_PHASES = {
    "worker": ("worker_admitted",),
    "publisher": ("publication_admitted",),
    "controller": PHASES[:-1],
}


class StorageBudgetError(ValueError):
    """Invalid scope, evidence, or reservation; never a provider failure."""


class StorageBudgetDeferred(StorageBudgetError):
    """Finite admitted storage is unavailable; accepted work stays preserved."""


def _integer(value: Any, label: str, *, positive: bool = False) -> int:
    if type(value) is not int or value < int(positive):
        raise StorageBudgetError(f"{label} must be a {'positive' if positive else 'nonnegative'} integer")
    return value


def _bytes_map(value: Mapping[str, Any], *, complete: bool = True) -> dict[str, int]:
    if not isinstance(value, Mapping) or set(value) - set(CATEGORY_LIMITS):
        raise StorageBudgetError("unknown budget category")
    if complete and set(value) != set(CATEGORY_LIMITS):
        raise StorageBudgetError("incomplete category envelope")
    return {key: _integer(value.get(key, 0), key) for key in CATEGORY_LIMITS}


def _canonical(path: str | Path) -> Path:
    value = Path(path)
    if not value.is_absolute():
        raise StorageBudgetError("budget paths must be absolute")
    # Registered existing roots are canonical. Required future output paths may
    # not exist; resolve existing parents so symlink escapes are still rejected.
    return value.resolve()


def _within(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


@dataclass(frozen=True)
class BudgetRoot:
    name: str
    path: Path
    category: str
    writers: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "path": str(self.path), "category": self.category,
                "writers": list(self.writers)}


@dataclass(frozen=True)
class BudgetConfig:
    enabled: bool
    mode: str = "cooperative"
    roots: tuple[BudgetRoot, ...] = ()
    writer_roles: Mapping[str, str] = field(default_factory=dict)
    ledger_path: Path | None = None
    offline_backup_paths: tuple[Path, ...] = ()
    physical_reserve_bytes: int = PHYSICAL_RESERVE_BYTES
    max_entries: int = 1_000_000
    inventory_max_age_seconds: int = 30
    binding_sha256: str = ""
    category_rules: tuple[Mapping[str, str], ...] = ()

    @property
    def scope_hash(self) -> str:
        return _digest(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {"schema_version": 1, "enabled": self.enabled, "mode": self.mode,
                "total_limit_bytes": TOTAL_LIMIT, "category_limits": dict(CATEGORY_LIMITS),
                "roots": [root.to_dict() for root in self.roots],
                "writer_roles": dict(self.writer_roles),
                "ledger_path": str(self.ledger_path) if self.ledger_path else None,
                "offline_backup_paths": [str(path) for path in self.offline_backup_paths],
                "physical_reserve_bytes": self.physical_reserve_bytes,
                "max_entries": self.max_entries,
                "inventory_max_age_seconds": self.inventory_max_age_seconds,
                "binding_sha256": self.binding_sha256,
                "category_rules": [dict(rule) for rule in self.category_rules]}

    def root_for(self, path: str | Path, writer_id: str | None = None) -> BudgetRoot:
        target = _canonical(path)
        return self.classified_root(target, writer_id=writer_id)

    def classified_root(self, target: Path, *, writer_id: str | None = None,
                        directory: bool | None = None) -> BudgetRoot:
        """Classify the path itself; inventory never follows symlink inode names."""
        matches = [root for root in self.roots if _within(target, root.path)]
        if not matches:
            raise StorageBudgetError("unregistered active path")
        root = max(matches, key=lambda item: len(item.path.parts))
        if writer_id is not None and writer_id not in root.writers:
            raise StorageBudgetError("writer is not registered for active path")
        if directory is None:
            directory = target.is_dir()
        if not directory and target.parent == root.path:
            categories = {rule["category"] for rule in self.category_rules
                          if rule["root"] == root.name and fnmatch.fnmatchcase(target.name, rule["pattern"])}
            if len(categories) > 1:
                raise StorageBudgetError("ambiguous file category rules")
            if categories:
                root = replace(root, category=categories.pop())
        return root


def parse_budget_config(value: Mapping[str, Any] | None) -> BudgetConfig:
    if value is None:
        return BudgetConfig(enabled=False)
    if not isinstance(value, Mapping) or type(value.get("enabled", False)) is not bool:
        raise StorageBudgetError("invalid storage budget configuration")
    if not value.get("enabled", False):
        return BudgetConfig(enabled=False)
    if type(value.get("schema_version", 1)) is not int or value.get("schema_version", 1) != 1:
        raise StorageBudgetError("unsupported storage budget schema")
    if value.get("total_limit_bytes", TOTAL_LIMIT) != TOTAL_LIMIT:
        raise StorageBudgetError("total storage limit is fixed at 100000000000 bytes")
    configured_limits = value.get("category_limits", CATEGORY_LIMITS)
    if (configured_limits != CATEGORY_LIMITS or not isinstance(configured_limits, Mapping)
            or any(type(amount) is not int for amount in configured_limits.values())):
        raise StorageBudgetError("category ceilings cannot expand or borrow from another category")
    mode = value.get("mode", "cooperative")
    if mode not in {"cooperative", "hard"}:
        raise StorageBudgetError("unknown storage budget mode")
    roles = value.get("writer_roles", {})
    if not isinstance(roles, dict) or not roles or any(
        not isinstance(name, str) or not name or role not in ROLE_PHASES
        for name, role in roles.items()
    ) or "controller" not in roles.values():
        raise StorageBudgetError("complete writer role registry required")
    roots = []
    for entry in value.get("roots", []):
        if not isinstance(entry, Mapping):
            raise StorageBudgetError("invalid budget root")
        name, category = entry.get("name"), entry.get("category")
        writers = entry.get("writers", [])
        if not isinstance(name, str) or not name or category not in CATEGORY_LIMITS:
            raise StorageBudgetError("invalid budget root category/name")
        if not isinstance(writers, list) or not writers or any(w not in roles for w in writers):
            raise StorageBudgetError("unknown root writer")
        if not isinstance(entry.get("path"), (str, Path)):
            raise StorageBudgetError("budget root path missing")
        roots.append(BudgetRoot(name, _canonical(entry["path"]), category, tuple(sorted(set(writers)))))
    if not roots or len({root.path for root in roots}) != len(roots) or len({root.name for root in roots}) != len(roots):
        raise StorageBudgetError("missing or duplicate active roots")
    offline = tuple(_canonical(path) for path in value.get("offline_backup_paths", []))
    if any(_within(path, root.path) or _within(root.path, path) for path in offline for root in roots):
        raise StorageBudgetError("offline backup must be disjoint from every active root")
    reserve = _integer(value.get("physical_reserve_bytes", PHYSICAL_RESERVE_BYTES), "physical reserve")
    if reserve < PHYSICAL_RESERVE_BYTES:
        raise StorageBudgetError("physical reserve cannot be reduced below 20 GiB")
    binding = value.get("binding_sha256", "")
    if not isinstance(binding, str) or (binding and not re.fullmatch(r"[a-f0-9]{64}", binding)):
        raise StorageBudgetError("invalid runtime binding hash")
    if not isinstance(value.get("ledger_path"), (str, Path)):
        raise StorageBudgetError("budget ledger path missing")
    if Path(value["ledger_path"]).is_symlink():
        raise StorageBudgetError("budget ledger cannot be a symlink")
    rules = value.get("category_rules", [])
    if not isinstance(rules, list):
        raise StorageBudgetError("category rules must be a list")
    checked_rules = []
    for rule in rules:
        if (not isinstance(rule, dict) or set(rule) != {"root", "pattern", "category"}
                or rule["root"] not in {root.name for root in roots}
                or rule["category"] not in CATEGORY_LIMITS
                or not isinstance(rule["pattern"], str) or not rule["pattern"]
                or "/" in rule["pattern"] or "\\" in rule["pattern"]
                or rule["pattern"] in {".", ".."}):
            raise StorageBudgetError("invalid immediate-child category rule")
        checked_rules.append(dict(rule))
    config = BudgetConfig(True, mode, tuple(sorted(roots, key=lambda root: str(root.path))), dict(roles),
                          _canonical(value["ledger_path"]), offline, reserve,
                          _integer(value.get("max_entries", 1_000_000), "max entries", positive=True),
                          _integer(value.get("inventory_max_age_seconds", 30), "inventory age", positive=True),
                          binding, tuple(checked_rules))
    if config.root_for(config.ledger_path).category != "diagnostics":
        raise StorageBudgetError("ledger must be charged to diagnostics")
    return config


@dataclass(frozen=True)
class Inventory:
    scope_hash: str
    complete: bool
    charged_bytes: Mapping[str, int]
    logical_bytes: int
    allocated_bytes: int
    root_identities: Mapping[str, tuple[int, int]]
    device_charged_bytes: Mapping[int, int]
    entries: int
    errors: tuple[str, ...]
    observed_monotonic: float

    def to_dict(self) -> dict[str, Any]:
        return {"scope_hash": self.scope_hash, "complete": self.complete,
                "charged_bytes": dict(self.charged_bytes), "total_charged_bytes": sum(self.charged_bytes.values()),
                "logical_bytes": self.logical_bytes, "allocated_bytes": self.allocated_bytes,
                "root_identities": {key: list(value) for key, value in self.root_identities.items()},
                "device_charged_bytes": dict(self.device_charged_bytes), "entries": self.entries,
                "errors": list(self.errors), "accounting": "unique_inode_max_logical_allocated_plus_directory_blocks"}


def inventory(config: BudgetConfig, *, deadline: float | None = None) -> Inventory:
    """Bounded metadata inventory; incomplete/unsupported observations deny admission.

    Hard links count once; distinct APFS clones count independently. Sparse logical
    lengths count in full. Nested category roots override their ancestors. Internal
    symlinks are charged but never traversed; escaping links and special files fail.
    Directory blocks are included. This still does not certify filesystem overhead.
    """
    totals = {key: 0 for key in CATEGORY_LIMITS}
    logical = allocated = entries = 0
    identities: dict[str, tuple[int, int]] = {}
    devices: dict[int, int] = {}
    seen: dict[tuple[int, int], str] = {}
    errors = []
    if not config.enabled:
        errors.append("storage_budget_disabled")
    try:
        for root in config.roots:
            info = root.path.stat()
            if not stat.S_ISDIR(info.st_mode):
                raise StorageBudgetError("active root is not a directory")
            identities[str(root.path)] = (info.st_dev, info.st_ino)
        anchors = [root.path for root in config.roots if not any(
            root.path != other.path and _within(root.path, other.path) for other in config.roots)]
        pending = list(anchors)
        while pending:
            if deadline is not None and time.monotonic() >= deadline:
                raise StorageBudgetError("inventory_deadline")
            path = pending.pop()
            info = path.lstat()
            entries += 1
            if entries > config.max_entries:
                raise StorageBudgetError("inventory_entry_limit")
            # Do not resolve a symlink to classify the link inode itself.
            category = config.classified_root(path, directory=stat.S_ISDIR(info.st_mode)).category
            key = (info.st_dev, info.st_ino)
            if key in seen:
                if seen[key] != category:
                    raise StorageBudgetError("hardlink_crosses_categories")
                continue
            seen[key] = category
            if not hasattr(info, "st_blocks"):
                raise StorageBudgetError("allocated_accounting_unsupported")
            blocks = info.st_blocks * 512
            length = 0 if stat.S_ISDIR(info.st_mode) else info.st_size
            charged = max(length, blocks)
            logical += length
            allocated += blocks
            totals[category] += charged
            devices[info.st_dev] = devices.get(info.st_dev, 0) + charged
            if stat.S_ISLNK(info.st_mode):
                config.root_for(path.resolve(strict=True))
            elif stat.S_ISDIR(info.st_mode):
                with os.scandir(path) as children:
                    for child in children:
                        if entries + len(pending) >= config.max_entries:
                            raise StorageBudgetError("inventory_entry_limit")
                        if deadline is not None and time.monotonic() >= deadline:
                            raise StorageBudgetError("inventory_deadline")
                        pending.append(Path(child.path))
                after = path.stat()
                if (info.st_dev, info.st_ino, info.st_mtime_ns, info.st_ctime_ns) != (
                    after.st_dev, after.st_ino, after.st_mtime_ns, after.st_ctime_ns
                ):
                    raise StorageBudgetError("inventory_changed")
            elif not stat.S_ISREG(info.st_mode):
                raise StorageBudgetError("unsupported_active_file")
        for path, identity in identities.items():
            info = Path(path).stat()
            if (info.st_dev, info.st_ino) != identity:
                raise StorageBudgetError("root_identity_changed")
    except (OSError, ValueError, RuntimeError) as exc:
        errors.append(str(exc) if isinstance(exc, StorageBudgetError) else "inventory_read_failed")
    return Inventory(config.scope_hash, not errors, totals, logical, allocated, identities,
                     devices, entries, tuple(errors), time.monotonic())


@dataclass(frozen=True)
class Admission:
    config: BudgetConfig
    inventory: Inventory
    projected_growth: Mapping[str, int]
    device_growth: Mapping[int, int]
    ready: bool
    reasons: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        projected = {category: self.inventory.charged_bytes[category] + self.projected_growth[category]
                     for category in CATEGORY_LIMITS}
        return {"ready": self.ready, "mode": self.config.mode, "hard_enforcement": False,
                "reasons": list(self.reasons), "scope_hash": self.config.scope_hash,
                "total_limit_bytes": TOTAL_LIMIT, "category_limits": dict(CATEGORY_LIMITS),
                "inventory": self.inventory.to_dict(), "projected_growth": dict(self.projected_growth),
                "projected_total_bytes": sum(self.inventory.charged_bytes.values()) + sum(self.projected_growth.values()),
                "category_projected_bytes": projected,
                "category_headroom_bytes": {category: max(0, limit - projected[category])
                                            for category, limit in CATEGORY_LIMITS.items()},
                "alerts": ["category_near_ceiling:" + category for category, limit in CATEGORY_LIMITS.items()
                           if projected[category] * 10 >= limit * 9],
                "device_growth": dict(self.device_growth),
                "offline_backup_paths": [str(path) for path in self.config.offline_backup_paths]}


def assess(config: BudgetConfig, observed: Inventory, *, projected_growth: Mapping[str, int],
           physical_free_bytes: Mapping[int, int], device_growth: Mapping[int, int] | None = None,
           required_paths: Sequence[str | Path] = (), writer_ids: Sequence[str] = (),
           enforcement: Any = None) -> Admission:
    growth = _bytes_map(projected_growth)
    reasons = []
    if not config.enabled:
        reasons.append("storage_budget_disabled")
    if not observed.complete:
        reasons.append("inventory_incomplete")
    if observed.scope_hash != config.scope_hash:
        reasons.append("inventory_scope_mismatch")
    if time.monotonic() - observed.observed_monotonic > config.inventory_max_age_seconds:
        reasons.append("inventory_stale")
    # No boolean, manifest, sampled statvfs value or caller-supplied JSON can
    # promote cooperative accounting into an OS allocation guarantee.
    if config.mode == "hard":
        reasons.append("hard_enforcement_backend_unavailable")
    if growth["diagnostics"] < CONTROL_ENVELOPE_BYTES:
        reasons.append("control_envelope_insufficient")
    for path in required_paths:
        try:
            config.root_for(path)
        except StorageBudgetError:
            reasons.append("unregistered_required_path")
    if any(writer not in config.writer_roles for writer in writer_ids):
        reasons.append("unregistered_writer")
    for category, limit in CATEGORY_LIMITS.items():
        if observed.charged_bytes[category] + growth[category] > limit:
            reasons.append(f"category_exhausted:{category}")
    if sum(observed.charged_bytes.values()) + sum(growth.values()) > TOTAL_LIMIT:
        reasons.append("total_budget_exhausted")
    # Without a per-device allocation plan, charge the entire remaining cycle
    # peak to *each* device. This is conservative, never shared-free-space credit.
    plan = ({device: sum(growth.values()) for device in observed.device_charged_bytes}
            if device_growth is None else dict(device_growth))
    if set(plan) != set(observed.device_charged_bytes):
        reasons.append("device_envelope_incomplete")
    # Grants are category-scoped, not device-scoped. Until durable per-device
    # subdivisions exist, every device must carry the entire cycle envelope.
    if any(_integer(value, "device growth") < sum(growth.values()) for value in plan.values()):
        reasons.append("device_envelope_understates_growth")
    for device, required in plan.items():
        available = physical_free_bytes.get(device)
        if available is None:
            reasons.append("physical_free_unknown")
        elif _integer(available, "physical free") < config.physical_reserve_bytes + required:
            reasons.append(f"physical_reserve_insufficient:{device}")
    return Admission(config, observed, growth, plan, not reasons, tuple(dict.fromkeys(reasons)))


def estimate_cycle_growth(*, worker_db_bytes: int, output_db_bytes: int, export_bytes: int,
                          ingress_cap_bytes: int, accepted_cap_bytes: int,
                          database_growth_cap_bytes: int, control_cap_bytes: int = CONTROL_ENVELOPE_BYTES,
                          export_replication: int = 2) -> dict[str, int]:
    """Conservative finite completion escrow; callers must enforce the input caps.

    Includes full worker projection, full output beforeimages, old/new exports,
    SQLite DB/WAL coexistence and worst-case JSON escaping of bounded accepted
    content. It is intentionally not a last-tick growth extrapolation. These are
    cooperative writer caps, not a claim SQLite/HTTP independently enforces them.
    """
    values = locals()
    for key, value in values.items():
        _integer(value, key, positive=key == "export_replication")
    return {
        "essential": ingress_cap_bytes + accepted_cap_bytes + database_growth_cap_bytes,
        "publication": output_db_bytes + export_bytes + accepted_cap_bytes * 6 * export_replication,
        "evidence": 0,
        "diagnostics": max(control_cap_bytes, CONTROL_ENVELOPE_BYTES),
        "work": 2 * worker_db_bytes + output_db_bytes + export_bytes
                + database_growth_cap_bytes * 2 + ingress_cap_bytes,
    }


def _validate_ledger(value: Any) -> None:
    if not isinstance(value, dict) or value.get("schema_version") != 1:
        raise StorageBudgetError("invalid budget ledger")
    config = parse_budget_config(value["config"])
    if value.get("scope_hash") != config.scope_hash or value.get("phase") not in PHASES:
        raise StorageBudgetError("budget ledger binding differs")
    if not isinstance(value.get("cycle_id"), str) or not value["cycle_id"]:
        raise StorageBudgetError("invalid budget cycle")
    if set(value.get("tokens", {})) != set(config.writer_roles):
        raise StorageBudgetError("budget writer registry differs")
    for token in value["tokens"].values():
        if not isinstance(token, str) or len(token) != 32:
            raise StorageBudgetError("invalid budget capability")
    envelope, settled = _bytes_map(value["envelope"]), _bytes_map(value["settled"])
    if any(envelope[key] > CATEGORY_LIMITS[key] for key in CATEGORY_LIMITS):
        raise StorageBudgetError("budget envelope exceeds fixed ceilings")
    if not isinstance(value.get("metadata"), dict):
        raise StorageBudgetError("invalid budget metadata")
    binding = {key: value[key] for key in ("scope_hash", "cycle_id", "root_identities", "envelope", "initial_usage", "metadata")}
    if value.get("admission_hash") != _digest(binding):
        raise StorageBudgetError("budget admission binding differs")
    initial = _bytes_map(value["initial_usage"])
    if any(initial[key] + envelope[key] > CATEGORY_LIMITS[key] for key in CATEGORY_LIMITS):
        raise StorageBudgetError("budget admission exceeds category ceilings")
    reserved = {key: 0 for key in CATEGORY_LIMITS}
    if not isinstance(value.get("operations"), dict):
        raise StorageBudgetError("invalid budget operations")
    for operation in value["operations"].values():
        category = operation["category"]
        maximum = _integer(operation["maximum"], "operation maximum", positive=True)
        consumed = _integer(operation["consumed"], "operation consumed")
        root = config.root_for(operation["path"], operation["writer_id"])
        if category not in CATEGORY_LIMITS or root.category != category or consumed > maximum:
            raise StorageBudgetError("budget operation binding differs")
        reserved[category] += maximum
    completions = value.get("completions", {})
    if not isinstance(completions, dict) or len(completions) > 64:
        raise StorageBudgetError("invalid budget operation completions")
    completed_bytes = dict.fromkeys(CATEGORY_LIMITS, 0)
    for operation_id, operation in completions.items():
        if (not isinstance(operation_id, str) or not operation_id or operation_id in value["operations"]
                or not isinstance(operation, dict)
                or not re.fullmatch(r"[a-f0-9]{64}", str(operation.get("proof_sha256")))):
            raise StorageBudgetError("invalid budget operation completion proof")
        category = operation["category"]
        maximum = _integer(operation["maximum"], "completed maximum", positive=True)
        consumed = _integer(operation["consumed"], "completed consumed")
        root = config.root_for(operation["path"], operation["writer_id"])
        if category not in CATEGORY_LIMITS or root.category != category or consumed > maximum:
            raise StorageBudgetError("budget operation completion binding differs")
        completed_bytes[category] += consumed
    if any(completed_bytes[key] > settled[key] for key in CATEGORY_LIMITS):
        raise StorageBudgetError("budget completion exceeds settled charge")
    if any(settled[key] + reserved[key] > envelope[key] for key in CATEGORY_LIMITS):
        raise StorageBudgetError("budget ledger exceeds envelope")
    if value["phase"] == "completed" and value["operations"]:
        raise StorageBudgetError("completed budget has pending reservations")


class BudgetLedger:
    """Durable cycle escrow and phase-fenced child capabilities.

    A new cycle never replaces an unfinished one. Stale operations keep their
    entire grant until an explicit holder settles; no timeout automatically
    releases space. Settlement records consumed bytes, not optimistic estimates.
    """

    def __init__(self, path: Path, cycle_id: str, scope_hash: str, writer_id: str, token: str):
        self.path, self.cycle_id, self.scope_hash = path, cycle_id, scope_hash
        self.writer_id, self.token = writer_id, token

    def _store(self) -> AtomicJsonStore:
        if self.path.is_symlink() or not self.path.is_file():
            raise StorageBudgetError("budget ledger must be an existing regular file")
        return AtomicJsonStore(self.path, default_factory=dict, validator=_validate_ledger,
                               encoder=lambda value: json.dumps(value, sort_keys=True, separators=(",", ":")).encode(),
                               maximum_bytes=LEDGER_MAX_BYTES)

    @classmethod
    def create(cls, admission: Admission, *, cycle_id: str, metadata: Mapping[str, Any] | None = None) -> BudgetLedger:
        if not admission.ready:
            raise StorageBudgetDeferred(";".join(admission.reasons))
        if not isinstance(cycle_id, str) or not cycle_id:
            raise StorageBudgetError("cycle identity required")
        config = admission.config
        path = config.ledger_path
        assert path is not None
        if path.is_symlink():
            raise StorageBudgetError("budget ledger cannot be a symlink")
        if time.monotonic() - admission.inventory.observed_monotonic > config.inventory_max_age_seconds:
            raise StorageBudgetDeferred("inventory_stale")
        for root, identity in admission.inventory.root_identities.items():
            info = Path(root).stat()
            if (info.st_dev, info.st_ino) != identity:
                raise StorageBudgetError("root identity changed before cycle")
        tokens = {writer: uuid.uuid4().hex for writer in config.writer_roles}
        try:
            metadata = json.loads(json.dumps(dict(metadata or {}), allow_nan=False))
        except (TypeError, ValueError) as exc:
            raise StorageBudgetError("budget metadata must be finite JSON") from exc
        document = {"schema_version": 1, "config": config.to_dict(), "scope_hash": config.scope_hash,
                    "cycle_id": cycle_id, "phase": "worker_admitted", "tokens": tokens,
                    "root_identities": {key: list(value) for key, value in admission.inventory.root_identities.items()},
                    "envelope": dict(admission.projected_growth),
                    "initial_usage": dict(admission.inventory.charged_bytes), "metadata": metadata,
                    "settled": {key: CONTROL_ENVELOPE_BYTES if key == "diagnostics" else 0 for key in CATEGORY_LIMITS},
                    "operations": {}}
        document["admission_hash"] = _digest({key: document[key] for key in
            ("scope_hash", "cycle_id", "root_identities", "envelope", "initial_usage", "metadata")})
        # AtomicJsonStore's missing default is used only for initial creation;
        # an existing malformed or incomplete record is never replaced.
        absent = {"new_budget_creation": uuid.uuid4().hex}
        def validate_existing(value):
            if value == absent and not path.exists():
                return
            _validate_ledger(value)
        store = AtomicJsonStore(path, default_factory=lambda: dict(absent), validator=validate_existing,
                                encoder=lambda value: json.dumps(value, sort_keys=True, separators=(",", ":")).encode(),
                                maximum_bytes=LEDGER_MAX_BYTES)
        def create(value):
            if value != absent and value["phase"] != "completed":
                raise StorageBudgetDeferred("unfinished_budget_cycle")
            _validate_ledger(document)
            value.clear()
            value.update(document)
            return None, True
        try:
            store.mutate(create)
        except AtomicJsonStoreError as exc:
            raise StorageBudgetError("budget_control_invalid_or_unwritable") from exc
        controller = next(writer for writer, role in config.writer_roles.items() if role == "controller")
        return cls(path, cycle_id, config.scope_hash, controller, tokens[controller])

    @classmethod
    def attach(cls, wire: Mapping[str, str]) -> BudgetLedger:
        if not isinstance(wire, Mapping) or set(wire) != {"ledger_path", "cycle_id", "scope_hash", "writer_id", "token"}:
            raise StorageBudgetError("invalid budget lease wire")
        if not all(isinstance(value, str) and value for value in wire.values()):
            raise StorageBudgetError("invalid budget lease values")
        if Path(wire["ledger_path"]).is_symlink():
            raise StorageBudgetError("budget ledger cannot be a symlink")
        ledger = cls(_canonical(wire["ledger_path"]), wire["cycle_id"], wire["scope_hash"], wire["writer_id"], wire["token"])
        ledger._read()
        return ledger

    @classmethod
    def existing_controller(cls, path: str | Path) -> BudgetLedger | None:
        """Attach under the external owner lock; never create/reset a ledger."""
        if Path(path).is_symlink():
            raise StorageBudgetError("budget ledger cannot be a symlink")
        target = _canonical(path)
        if not target.exists() and not target.is_symlink():
            return None
        if target.is_symlink() or not target.is_file() or target.stat().st_size > LEDGER_MAX_BYTES:
            raise StorageBudgetError("invalid existing budget ledger")
        try:
            value = json.loads(target.read_bytes())
            _validate_ledger(value)
        except (ValueError, KeyError, TypeError, OSError) as exc:
            raise StorageBudgetError("invalid existing budget ledger") from exc
        if value["phase"] == "completed":
            return None
        config = parse_budget_config(value["config"])
        controllers = [writer for writer, role in config.writer_roles.items() if role == "controller"]
        if len(controllers) != 1:
            raise StorageBudgetError("one controller capability required for recovery")
        writer = controllers[0]
        return cls.attach({"ledger_path": str(target), "cycle_id": value["cycle_id"],
                           "scope_hash": value["scope_hash"], "writer_id": writer,
                           "token": value["tokens"][writer]})

    def wire(self) -> dict[str, str]:
        return {"ledger_path": str(self.path), "cycle_id": self.cycle_id, "scope_hash": self.scope_hash,
                "writer_id": self.writer_id, "token": self.token}

    def _check(self, document: Mapping[str, Any], *, enforce_phase: bool = True) -> BudgetConfig:
        config = parse_budget_config(document["config"])
        if (document["cycle_id"] != self.cycle_id or document["scope_hash"] != self.scope_hash
                or document["tokens"].get(self.writer_id) != self.token or config.ledger_path != self.path):
            raise StorageBudgetError("budget lease binding differs")
        if enforce_phase and document["phase"] not in ROLE_PHASES[config.writer_roles[self.writer_id]]:
            raise StorageBudgetDeferred("budget_writer_phase_denied")
        for root in config.roots:
            info = root.path.stat()
            if [info.st_dev, info.st_ino] != document["root_identities"].get(str(root.path)):
                raise StorageBudgetError("budget root identity differs")
        return config

    def _read(self, *, enforce_phase: bool = True) -> dict[str, Any]:
        try:
            value = self._store().read()
            self._check(value, enforce_phase=enforce_phase)
            return value
        except AtomicJsonStoreError as exc:
            raise StorageBudgetError("invalid durable budget ledger") from exc

    def _mutate(self, mutation):
        try:
            return self._store().mutate(mutation)
        except AtomicJsonStoreError as exc:
            raise StorageBudgetError("budget_control_invalid_or_unwritable") from exc

    def for_writer(self, writer_id: str) -> BudgetLedger:
        value = self._read()
        config = parse_budget_config(value["config"])
        if config.writer_roles[self.writer_id] != "controller" or writer_id not in value["tokens"]:
            raise StorageBudgetError("budget capability delegation denied")
        return BudgetLedger(self.path, self.cycle_id, self.scope_hash, writer_id, value["tokens"][writer_id])

    def validate_writer(self, *, expected_cycle_id: str, expected_scope_hash: str,
                        expected_ledger_path: str | Path, allowed_phases: Sequence[str],
                        required_path: str | Path) -> dict[str, str]:
        value = self._read()
        if (expected_cycle_id != self.cycle_id or expected_scope_hash != self.scope_hash
                or _canonical(expected_ledger_path) != self.path or value["phase"] not in allowed_phases):
            raise StorageBudgetError("budget writer scope differs")
        parse_budget_config(value["config"]).root_for(required_path, self.writer_id)
        return {"phase": value["phase"], "cycle_id": self.cycle_id, "writer_id": self.writer_id,
                "scope_hash": self.scope_hash}

    def category_for(self, path: str | Path) -> str:
        value = self._read()
        return parse_budget_config(value["config"]).root_for(path, self.writer_id).category

    def remaining_envelope(self) -> dict[str, int]:
        value = self._read()
        return {category: value["envelope"][category] - value["settled"][category] - sum(
            operation["maximum"] for operation in value["operations"].values()
            if operation["category"] == category) for category in CATEGORY_LIMITS}

    def transition(self, expected_phase: str, new_phase: str) -> None:
        if (expected_phase, new_phase) not in {
            ("worker_admitted", "accepted_pending"), ("accepted_pending", "publication_admitted"),
            ("publication_admitted", "completed"), ("worker_admitted", "completed"),
        }:
            raise StorageBudgetError("invalid budget phase transition")
        def mutate(value):
            config = self._check(value)
            if config.writer_roles[self.writer_id] != "controller" or value["phase"] != expected_phase:
                raise StorageBudgetError("budget phase authority differs")
            if value["operations"]:
                raise StorageBudgetDeferred("budget_operations_unsettled")
            value["phase"] = new_phase
            return None, True
        self._mutate(mutate)

    def reserve(self, category: str, max_bytes: int, *, path: str | Path,
                operation_id: str | None = None) -> OperationReservation:
        maximum = _integer(max_bytes, "operation maximum", positive=True)
        target = _canonical(path)
        operation_id = operation_id or uuid.uuid4().hex
        if not isinstance(operation_id, str) or not operation_id:
            raise StorageBudgetError("operation identity required")
        def mutate(value):
            config = self._check(value)
            root = config.root_for(target, self.writer_id)
            if root.category != category:
                raise StorageBudgetError("operation category differs")
            if operation_id in value["operations"] or operation_id in value.get("completions", {}):
                raise StorageBudgetError("operation identity already reserved")
            outstanding = sum(item["maximum"] for item in value["operations"].values() if item["category"] == category)
            if value["settled"][category] + outstanding + maximum > value["envelope"][category]:
                raise StorageBudgetDeferred("operation_envelope_exhausted")
            value["operations"][operation_id] = {"category": category, "maximum": maximum, "consumed": 0,
                                                 "path": str(target), "writer_id": self.writer_id}
            return None, True
        self._mutate(mutate)
        return OperationReservation(self, operation_id)

    def resume(self, operation_id: str) -> OperationReservation:
        value = self._read()
        if value["operations"].get(operation_id, {}).get("writer_id") != self.writer_id:
            raise StorageBudgetError("operation reservation differs")
        return OperationReservation(self, operation_id)

    def snapshot(self) -> dict[str, Any]:
        value = self._read(enforce_phase=False)
        return {**{key: value[key] for key in ("cycle_id", "scope_hash", "phase", "envelope", "settled", "operations", "metadata")},
                "completions": value.get("completions", {})}


class OperationReservation:
    def __init__(self, ledger: BudgetLedger, operation_id: str):
        self.ledger, self.operation_id = ledger, operation_id

    def consume(self, count: int) -> None:
        """Durably charge bytes before a bounded write, never after it."""
        count = _integer(count, "write bytes")
        def mutate(value):
            self.ledger._check(value)
            operation = value["operations"].get(self.operation_id)
            if operation is None or operation["writer_id"] != self.ledger.writer_id:
                raise StorageBudgetError("operation reservation differs")
            if operation["consumed"] + count > operation["maximum"]:
                raise StorageBudgetDeferred("ingress_or_write_limit_exceeded")
            operation["consumed"] += count
            return None, bool(count)
        self.ledger._mutate(mutate)

    def settle(self, actual_bytes: int | None = None, *, proof_sha256: str | None = None) -> None:
        """Release only unused grant. Already consumed charges cannot be refunded.

        The optional value is an assertion, not permission to lower measured
        writes. Deleted temporaries need a new complete inventory in a new cycle.
        """
        if proof_sha256 is not None and not re.fullmatch(r"[a-f0-9]{64}", str(proof_sha256)):
            raise StorageBudgetError("invalid settlement proof digest")
        def mutate(value):
            self.ledger._check(value)
            operation = value["operations"].get(self.operation_id)
            if operation is None or operation["writer_id"] != self.ledger.writer_id:
                raise StorageBudgetError("operation reservation differs")
            if actual_bytes is not None and _integer(actual_bytes, "actual bytes") != operation["consumed"]:
                raise StorageBudgetError("settlement must retain consumed bytes")
            if proof_sha256 is not None:
                completions = value.setdefault("completions", {})
                if len(completions) >= 64:
                    raise StorageBudgetDeferred("budget_completion_receipts_exhausted")
                completions[self.operation_id] = {**operation, "proof_sha256": proof_sha256}
            value["settled"][operation["category"]] += operation["consumed"]
            del value["operations"][self.operation_id]
            return None, True
        self.ledger._mutate(mutate)
