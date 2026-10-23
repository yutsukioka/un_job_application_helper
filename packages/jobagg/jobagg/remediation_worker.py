"""Bounded deterministic source API/HTTP worker, with an isolated compatible SQLite output.

Default invocation is a read-only preview. No LLM, schedule installation, live
publication or inferred global completeness is performed by this module.
"""

from __future__ import annotations

import argparse
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from contextlib import contextmanager, nullcontext
from copy import deepcopy
from dataclasses import asdict, fields, replace
from datetime import UTC, datetime
import fcntl
import gzip
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import threading
import time
import urllib.error
from urllib.parse import urlsplit
from uuid import uuid4

from jobagg.adapters.base import AdapterContext, get_adapter_class
from jobagg.adapters.idb_api import IDBInventoryChanged
from jobagg.accepted_detail_lineage import ensure_detail_lineage_indexes
from jobagg.atomic_files import atomic_write_text, admitted_file_writes
from jobagg.db import JobDatabase
from jobagg.detail_quality import DETAIL_QUALITY_COMPLETE, detail_quality_status
from jobagg.http import JobAggHTTPClient, ResponseTooLargeError
from jobagg.storage_budget import BudgetLedger, StorageBudgetDeferred, StorageBudgetError
from jobagg.semantic_history import SemanticCapacityError
from jobagg.models import JobRecord
from jobagg.pipelines.http_checkpoint import (
    DurableCapture,
    HostIneligible,
    finite_epoch,
    safe_error,
)
from jobagg.pipelines.inventory_checks import source_capability, verify_listing
from jobagg.pipelines.worker_policy import SharedPolicy
from jobagg.pipelines.sync_source import (
    _http_client_for_source,
    _listing_hash,
    _persist_completed_detail,
    fetch_schedule_policy,
    load_sources,
    register_builtin_adapters,
)
from jobagg.remediation_scheduling import Task, select_due, task_key
from jobagg.remediation_concurrency import HARD_CEILING
from jobagg.source_health import worker_source_health, listing_recovery_pending
from jobagg.robots import load_policy
from jobagg.pipelines.host_recovery import host_eligibility, classify_failure
from jobagg.vacancy_outcomes import (
    DetailIdentityMismatch, UNAVAILABLE_STATUSES, captured_unavailable,
)

VERSION = "deterministic-fetch-v1"
# Reviewed inventory/storage repairs must not reopen failures merely by resealing.
# Only retry comparisons recognize these exact reviewed predecessors;
# startup integrity and every new receipt still use the full current binding.
# Registry, robots, owner and semantic-input changes are never aliased.
RETRY_EQUIVALENT_IMPLEMENTATIONS = (
    # M0 reconciled production predecessor; M1 preserves charged retry semantics.
    "4642596ad8c19900a6d2cf474c144d22907df89b90e26ee78aa7d0c709f70c39",
    # Exact active local repair; standalone compression preserves task retry meaning.
    "48f37af1a7b4e674547cc10a8d6db13cf3b27bf70c65cb2d4da3a31f5acda903",
    # Exact deployed predecessor: local scheduling repair preserves retry accounting.
    "72f7e64ad22881759854b4fc33bbcddc83bc7998e758e872f1c678c399bc2b08",
    "ca42922ee7982fdc51cd05a3a1d3e9911f23760feaa0bba6e1b3b049e40e30e0",
    "985d89ddc9b99e0e6d869b8d9175adc69818b216f5b9261fcb04cbd3fc760f3b",
    # Merged inventory-verifier release 33edfb9c; the storage delta preserves retries.
    "7ec4f5b4d37797599eed3ae04af4c18992dd8a2d7e3c01db742e6d3a8c34362b",
    # Exact 4aa8b8f production predecessor; this policy preserves provider retry accounting.
    "fa57f6e056aad8afd2ded124162343b18ff98eb7319fce9fcdd2063307630ea3",
)
# Applicability seal for this one reviewed successor. Only this literal is
# normalized when checking scope, avoiding a self-referential hash. The full
# implementation fingerprint below still includes every byte of every module.
RETRY_SUCCESSOR_SCOPE_SHA256 = "f7ddc51178e8d4ca8b825e8091117b122a668fe649ac86f883962d318c8f1824"
# Exact local guard exception types, not error-message matching. These remain
# failed/held attempts, but are not evidence of fresh site or runtime pressure.
LOCAL_POLICY_ERRORS = frozenset({"HostIneligible", "SSRFProtectionError"})
DATE_FIELDS = {"posted_at", "closes_at", "first_seen_at", "last_seen_at"}
PUBLIC_FIELDS = (
    "title",
    "apply_url",
    "source_url",
    "description",
    "location",
    "department",
    "employment_type",
    "posted_at",
    "closes_at",
    "closes_at_local",
    "closes_tz",
)


def utc():
    return datetime.now(UTC).isoformat()


def dump(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=lambda x: x.isoformat())


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def epoch(value):
    result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("Explicit timezone required")
    return result.timestamp()


def record(payload):
    payload = deepcopy(payload)
    for key in DATE_FIELDS:
        if payload.get(key):
            payload[key] = datetime.fromisoformat(payload[key].replace("Z", "+00:00"))
    return JobRecord(
        **{
            key: value
            for key, value in payload.items()
            if key in {f.name for f in fields(JobRecord)}
        }
    )


def document_disposition_changed(prior, current):
    """Reconsider a dismissal only for new body or current content evidence."""
    old_body = prior.get("parent_description_sha256")
    new_body = current.get("parent_description_sha256")
    if not new_body or any(
        prior.get(key) != current.get(key) for key in ("job_key", "url", "attachment_id")
    ):
        return False
    if old_body and old_body != new_body:
        return True

    def content_evidence(payload):
        evidence = set()

        def classified(value):
            return (
                isinstance(value, dict)
                and value.get("decision") == "candidate"
                and value.get("region") == "content"
                and value.get("reason") in {"explicit_job_document", "document_purpose_unresolved"}
            )

        classification = payload.get("classification")
        if classified(classification):
            evidence.add(dump({"classification": classification}))
        for item in payload.get("provenance", []):
            if not isinstance(item, dict) or not item.get("field_sha256"):
                continue
            if classified(item.get("classification")) or (
                item.get("kind") == "declared_required_url"
                and item.get("path") == "raw.required_attachment_urls"
                and item.get("source") == "supplied_job_record_current_top_level_declaration"
            ):
                evidence.add(dump(item))
        return evidence

    return bool(content_evidence(current) - content_evidence(prior))


def frame_listing(payload, source_id, external_id):
    """Return one exact immutable listing; merged database rows are not dispatch inputs."""
    path = Path(payload["frame_path"])
    if sha(path) != payload["frame_sha256"]:
        raise ValueError("Original listing frame changed")
    frame = json.loads(path.read_text())
    matches = [
        job
        for job in frame.get("jobs", [])
        if job.get("source_id") == source_id and str(job.get("external_id")) == str(external_id)
    ]
    if frame.get("source_id") != source_id or len(matches) != 1:
        raise ValueError("Listing frame source/job identity missing or ambiguous")
    if payload.get("listing") != matches[0]:
        raise ValueError("Queued listing differs from its exact original frame")
    return record(matches[0])


def browser_receipt(capture):
    renderer = getattr(capture, "browser_renderer", None)
    path = getattr(renderer, "last_receipt", None)
    if path is None:
        return None
    path = Path(path)
    value = json.loads(path.read_text())
    for label in ("html", "text"):
        content = Path(value[label + "_path"])
        if sha(content) != value[label + "_sha256"]:
            raise ValueError("Browser rendered evidence bytes changed")
    return {"path": str(path), "sha256": sha(path)}


def implementation_hash():
    root = Path(__file__).resolve().parent
    return hashlib.sha256(
        dump(
            {str(path.relative_to(root)): sha(path) for path in sorted(root.rglob("*.py"))}
        ).encode()
    ).hexdigest()


def _retry_compatibility_manifests():
    """Derive full and scope manifests from the same complete byte snapshot."""
    root = Path(__file__).resolve().parent
    full, scope = {}, {}
    declaration = ('RETRY_SUCCESSOR_SCOPE_SHA256 = "' + RETRY_SUCCESSOR_SCOPE_SHA256 + '"').encode()
    found = False
    for path in sorted(root.rglob("*.py")):
        name = str(path.relative_to(root))
        content = path.read_bytes()
        full[name] = hashlib.sha256(content).hexdigest()
        if name == "remediation_worker.py":
            if content.count(declaration) != 1:
                return None
            content = content.replace(declaration, b'RETRY_SUCCESSOR_SCOPE_SHA256 = "<reviewed-successor>"')
            found = True
        scope[name] = hashlib.sha256(content).hexdigest()
    return (full, scope) if found else None


def reviewed_retry_successor():
    manifests = _retry_compatibility_manifests()
    if manifests is None:
        return None
    full, scope = manifests
    if hashlib.sha256(dump(scope).encode()).hexdigest() != RETRY_SUCCESSOR_SCOPE_SHA256:
        return None
    return hashlib.sha256(dump(full).encode()).hexdigest()


def write_json(path, value):
    atomic_write_text(path, dump(value) + "\n")


def interval_peak(intervals, key=None):
    """Observed monotonic overlap; adjacent or zero-length intervals do not overlap."""
    events = []
    for index, item in enumerate(intervals):
        start, end = item["started_monotonic"], item["finished_monotonic"]
        if end > start:
            value = item[key] if key else index
            events.extend(((start, 1, value), (end, -1, value)))
    active, peak = {}, 0
    for _, delta, value in sorted(events, key=lambda event: (event[0], event[1])):
        active[value] = active.get(value, 0) + delta
        if not active[value]:
            del active[value]
        peak = max(peak, len(active))
    return peak


@contextmanager
def shared_owner(path):
    """Cooperate with the prototype dispatcher or take the same explicit owner."""
    path = Path(path).absolute()
    if path.resolve() != path:
        raise ValueError("Shared owner path must not be aliased/symlinked")
    inherited = os.environ.get("JOBAGG_SHARED_LOCK_FD")
    if inherited is not None:
        fd = int(inherited)
        actual = path.stat()
        opened = os.fstat(fd)
        if (actual.st_dev, actual.st_ino) != (opened.st_dev, opened.st_ino):
            raise ValueError("Inherited owner does not match configured shared lock")
        with path.open("r+") as probe:
            try:
                fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                # A busy independent probe alone could belong to another process.
                # The inherited open description itself must already own the lock.
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                yield fd
                return
            raise ValueError("Inherited owner does not hold the shared lock")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+") as owner:
        fcntl.flock(owner, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield owner.fileno()



class WorkerStorage:
    """Finite cooperative byte accounting; never a filesystem quota claim.

    Every grant is charged before writing. SQLite's deliberately conservative
    bound reserves a full dirty-page WAL plus bounded main-file growth, disables
    cache spills and automatic checkpoints, and limits the database page count.
    Unsupported browser buffering is refused before claiming a provider attempt.
    """
    REQUEST_OVERHEAD = 256 * 1024
    KEYS = ("ingress_cap_bytes", "accepted_cap_bytes", "database_growth_cap_bytes", "control_cap_bytes")

    def __init__(self, worker, lease, limits):
        self.worker, self.lease, self.limits = worker, dict(lease), dict(limits)
        self.ledger = BudgetLedger.attach(lease)
        self.lock = threading.RLock()
        for key in self.KEYS:
            if type(limits.get(key)) is not int or limits[key] <= 0:
                raise StorageBudgetError("finite_worker_storage_limits_required")
        self.caps = dict(zip(("ingress", "accepted", "database", "control"), (limits[k] for k in self.KEYS)))
        self.used = {key: 0 for key in self.caps}
        self.reserved = dict(self.used)
        self.requests, self.initial = {}, {}
        self.completion = {}
        self.host_requests = {}
        self.local = threading.local()
        self.response_cap = limits.get("response_cap_bytes", 50 * 1024 * 1024)
        if type(self.response_cap) is not int or not 0 < self.response_cap <= 50 * 1024 * 1024:
            raise StorageBudgetError("invalid_response_storage_limit")
        self.transaction_growth = max(4096, self.caps["database"] // (16 * worker.max_tasks + 32))
        for path in (worker.workspace, worker.db.path, worker.shared_policy.root, worker.shared_lock):
            self.ledger.category_for(path)
        self.validate_database_paths()

    def validate_database_paths(self):
        category = self.ledger.category_for(self.worker.db.path)
        for suffix in ("", "-wal", "-shm", "-journal"):
            path = Path(str(self.worker.db.path) + suffix)
            if path.is_symlink() or self.ledger.category_for(path) != category:
                raise StorageBudgetError("database_sidecar_scope_differs")

    def reserve(self, stage, count, path):
        with self.lock:
            if self.used[stage] + self.reserved[stage] + count > self.caps[stage]:
                raise StorageBudgetDeferred(stage + "_cycle_limit_exhausted")
            category = self.ledger.category_for(path)
            grant = self.ledger.reserve(category, count, path=path)
            self.reserved[stage] += count
            return WorkerStorageGrant(self, stage, count, grant, category)

    def ensure_directory(self, path, *, grant=None, exist_ok=True):
        from jobagg.publication_storage import ensure_directory

        path = Path(path).absolute()
        with self.lock:
            self.ledger.category_for(path)
            if path.resolve() != path:
                raise StorageBudgetError("directory_path_must_not_alias")
            if path.exists() and not path.is_dir():
                raise NotADirectoryError(path)
            hosts = self.worker.shared_policy.root / "hosts"
            if grant is None and (path == hosts or hosts in path.parents):
                task_id = getattr(self.local, "task_id", None)
                grant = self.completion.get(task_id, {}).get("first_host")

            def charge_directory(directory, count):
                category = self.ledger.category_for(directory)
                if grant is not None and grant.category == category:
                    grant.consume(count)
                    return
                # A parent can belong to another registered category. Its bytes
                # must not be hidden in the child's already-admitted grant.
                stage = grant.stage if grant is not None else self.stage_for(directory)
                parent_grant = self.reserve(stage, count, directory)
                try:
                    parent_grant.consume(count)
                finally:
                    parent_grant.close()

            ensure_directory(path, charge_directory=charge_directory, exist_ok=exist_ok)

    def request_grant(self):
        # The response limit includes room for gzip framing, receipts, directory
        # allocation and an explicit bounded overflow receipt. It never expands.
        limit = min(self.response_cap, (self.caps["ingress"] - self.REQUEST_OVERHEAD) * 1000 // 1001)
        if limit <= 0:
            raise StorageBudgetDeferred("ingress_receipt_envelope_unavailable")
        grant = self.reserve("ingress", limit + limit // 1000 + self.REQUEST_OVERHEAD,
                             self.worker.workspace / "captures")
        grant.body_limit = limit
        return grant

    def host_paths(self, host):
        lock = self.worker.shared_policy.root / "hosts" / ("host-" + hashlib.sha256(host.encode()).hexdigest()[:24] + ".lock")
        return lock, lock.with_suffix(".json")

    def host_grant(self, host):
        lock, state = self.host_paths(host)
        size = state.stat().st_size if state.exists() else 0
        return self.reserve("control", 2 * self.allocation(size) + 65536, lock)

    def bind_request(self, path, grant=None, *, host):
        new = grant is None
        grant = grant or self.request_grant()
        task_id = getattr(self.local, "task_id", None)
        resources = self.completion.get(task_id, {})
        control = resources.pop("first_host", None)
        try:
            control = control or self.host_grant(host)
        except BaseException:
            if new:
                grant.close()
            raise
        with self.lock:
            self.requests[str(path.with_suffix(""))] = grant
            grant.host_paths = self.host_paths(host)
            grant.host_grant = control
            for host_path in grant.host_paths:
                self.host_requests[str(host_path)] = control
        return grant

    def release_request(self, path, grant):
        with self.lock:
            self.requests.pop(str(path.with_suffix("")), None)
            for host_path in grant.host_paths:
                self.host_requests.pop(str(host_path), None)
        grant.host_grant.close()
        grant.close()

    def _request_for(self, path):
        return self.requests.get(str(path.with_name(path.name.split(".")[0])))

    @staticmethod
    def allocation(count):
        # Charge newly written file extents and a conservative metadata block;
        # old bytes are already inventoried and are never credited back here.
        return ((count + 4095) // 4096) * 4096 + 4096

    def stage_for(self, path):
        if self.worker.workspace in path.parents:
            relative = path.relative_to(self.worker.workspace)
            return "control" if relative.parts[0] in {"ticks", "worker_workspace.json", "storage_holds"} else "accepted"
        return "control"

    def charge_file(self, path, count):
        path = Path(path).absolute()
        self.ledger.category_for(path)  # Recheck symlink/scope before each write.
        amount = self.allocation(count)
        with self.lock:
            grant = self._request_for(path) or self.host_requests.get(str(path))
            if grant is not None:
                grant.consume(amount)
                return
        task_id = getattr(self.local, "task_id", None)
        if task_id in self.completion and (
            "storage_holds" in path.parts or path.parent == self.worker.shared_policy.root / "attempts"
            or path == self.worker.shared_policy.root / "attempt_index.json"
        ):
            category = self.ledger.category_for(path)
            self.completion[task_id]["control"][category].consume(amount)
            return
        grant = self.reserve(self.stage_for(path), amount, path)
        try:
            grant.consume(amount)
        finally:
            grant.close()

    @contextmanager
    def file_write(self, path, count):
        self.charge_file(path, count)
        yield

    def accepted(self, value):
        # Count exact encoded acceptance payload before parsing results enter a
        # transaction. File copies and SQLite's physical bound remain separate.
        size = len(dump(value).encode())
        grant = self.reserve("accepted", max(1, size), self.worker.db.path)
        try:
            grant.consume(size)
        finally:
            grant.close()

    def database_bound(self, conn):
        page = conn.execute("PRAGMA page_size").fetchone()[0]
        pages = conn.execute("PRAGMA page_count").fetchone()[0]
        growth_pages = max(1, self.transaction_growth // page)
        maximum = pages + growth_pages
        # Full dirty-page WAL (each page once with cache_spill OFF), main-file
        # growth and WAL index allocation. This may defer a large legacy DB.
        bound = (maximum * (page + 24) + 32 + growth_pages * page
                 + ((maximum + 4095) // 4096) * 32768 + 65536)
        return maximum, bound

    @contextmanager
    def policy_database_write(self, conn, path):
        # Narrow native SQL mutates a constant number of indexed rows. The
        # reviewed bound must include its largest retained event and WAL/scratch;
        # do not substitute a guessed production threshold for measurement.
        maximum = self.limits.get('policy_transaction_cap_bytes')
        if type(maximum) is not int or maximum < 4096:
            raise StorageBudgetDeferred('measured_native_policy_transaction_cap_required')
        grant = self.reserve('database', maximum, path)
        grant.consume(maximum)
        try:
            conn.execute('PRAGMA cache_spill=OFF')
            conn.execute('PRAGMA wal_autocheckpoint=0')
            yield
        finally:
            grant.close()

    def preclaim(self, task):
        source = self.worker.by_id[task["source_id"]]
        if source.extra.get("browser_render") and task["kind"] != "document":
            raise StorageBudgetDeferred("bounded_browser_reader_unavailable")
        with sqlite3.connect(self.worker.db.path.as_uri() + "?mode=ro", uri=True) as conn:
            _, bound = self.database_bound(conn)
        # Keep enough capacity for claim, acceptance or rollback, and the durable
        # storage hold. A local shortage must occur before charging the attempt.
        with self.lock:
            if (self.used["database"] + self.reserved["database"] + 3 * bound
                    + 4 * self.transaction_growth + 65536 > self.caps["database"]):
                raise StorageBudgetDeferred("database_completion_envelope_unavailable")
            index = self.worker.shared_policy.root / "attempt_index.json"
            control = self.allocation(index.stat().st_size + 128) + 65536
            if self.used["control"] + self.reserved["control"] + control > self.caps["control"]:
                raise StorageBudgetDeferred("attempt_completion_envelope_unavailable")
        task_id = task["task_id"]
        grants = []
        try:
            request = self.request_grant()
            grants.append(request)
            controls = {}
            paths = (self.worker.shared_policy.root / "attempt_index.json",
                     self.worker.workspace / "storage_holds")
            for path in paths:
                category = self.ledger.category_for(path)
                if category not in controls:
                    grant = self.reserve("control", control, path)
                    grants.append(grant)
                    controls[category] = grant
            emergency = self.reserve("database", bound + 4 * self.transaction_growth + 65536, self.worker.db.path)
            grants.append(emergency)
            claim_database = self.reserve("database", bound, self.worker.db.path)
            grants.append(claim_database)
            first_host = self.host_grant(self.worker.task_host(task))
            grants.append(first_host)
            self.initial[task_id] = request
            self.completion[task_id] = {"control": controls, "database": emergency,
                                        "claim_database": claim_database, "first_host": first_host}
        except BaseException:
            for grant in grants:
                grant.close()
            raise

    @contextmanager
    def task_scope(self, task_id):
        previous = getattr(self.local, "task_id", None)
        self.local.task_id = task_id
        try:
            yield
        finally:
            self.local.task_id = previous

    def release_task(self, task_id):
        initial = self.initial.pop(task_id, None)
        if initial:
            initial.close()
        saved = self.completion.pop(task_id, None)
        if saved:
            for grant in [*saved["control"].values(), saved["database"],
                          saved.get("claim_database"), saved.get("first_host")]:
                if grant is not None:
                    grant.close()

    def activate(self, policy):
        from jobagg.semantic_history import activate_semantic_history
        if not isinstance(policy, dict) or set(policy) != {"policy_epoch", "max_history_bytes"}:
            raise StorageBudgetError("explicit_semantic_history_policy_required")
        if not self.worker.db.path.exists():
            raise StorageBudgetError("storage_activation_requires_initialized_workspace")
        self.ledger.validate_writer(expected_cycle_id=self.lease["cycle_id"],
            expected_scope_hash=self.lease["scope_hash"], expected_ledger_path=self.lease["ledger_path"],
            allowed_phases=("worker_admitted",), required_path=self.worker.db.path)
        self.validate_database_paths()
        conn = sqlite3.connect(self.worker.db.path)
        conn.row_factory = sqlite3.Row
        grant = None
        try:
            if conn.execute("PRAGMA journal_mode").fetchone()[0].lower() != "wal":
                raise StorageBudgetError("storage_worker_requires_existing_wal_database")
            maximum, bound = self.database_bound(conn)
            grant = self.reserve("database", bound, self.worker.db.path)
            grant.consume(bound)
            conn.execute("PRAGMA cache_spill=OFF")
            conn.execute("PRAGMA wal_autocheckpoint=0")
            conn.execute(f"PRAGMA max_page_count={maximum}")
            conn.execute("BEGIN IMMEDIATE")
            activate_semantic_history(conn, **policy, ledger_path=self.lease["ledger_path"],
                                      scope_hash=self.lease["scope_hash"])
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()
            if grant is not None:
                grant.close()

    @contextmanager
    def database_write(self, conn):
        from jobagg.semantic_history import admitted_semantic_write, semantic_history_enabled
        if not semantic_history_enabled(conn):
            raise StorageBudgetError("activated_semantic_history_required")
        state = conn.execute("SELECT policy_epoch FROM semantic_history_state WHERE id=1").fetchone()
        maximum, bound = self.database_bound(conn)
        conn.execute("PRAGMA cache_spill=OFF")
        conn.execute("PRAGMA wal_autocheckpoint=0")
        conn.execute(f"PRAGMA max_page_count={maximum}")
        grant = None
        admission_error = None
        committing = False
        writes = {sqlite3.SQLITE_INSERT, sqlite3.SQLITE_UPDATE, sqlite3.SQLITE_DELETE}
        denied = {sqlite3.SQLITE_ATTACH, sqlite3.SQLITE_DETACH, sqlite3.SQLITE_CREATE_TABLE,
                  sqlite3.SQLITE_DROP_TABLE, sqlite3.SQLITE_ALTER_TABLE, sqlite3.SQLITE_CREATE_TEMP_TABLE,
                  sqlite3.SQLITE_CREATE_INDEX, sqlite3.SQLITE_DROP_INDEX}
        def authorize(action, first, second, database, trigger):
            nonlocal grant, admission_error
            if action in denied or action == sqlite3.SQLITE_SAVEPOINT:
                return sqlite3.SQLITE_DENY
            if action == sqlite3.SQLITE_TRANSACTION and first == "COMMIT":
                if not committing:
                    return sqlite3.SQLITE_DENY
                try:
                    from jobagg.runtime_source_head import owner
                    owner(self.worker._runtime_owner_fd, self.worker.shared_lock)
                except (ValueError, OSError, TypeError):
                    return sqlite3.SQLITE_DENY
            if (action == sqlite3.SQLITE_PRAGMA and second is not None
                    and first.lower() not in {"table_info", "table_xinfo", "index_list", "index_info", "index_xinfo", "foreign_key_list"}):
                return sqlite3.SQLITE_DENY
            if action in writes and grant is None:
                try:
                    task_id = getattr(self.local, "task_id", None)
                    if getattr(self.local, "finishing_hold", False) and task_id in self.completion:
                        grant = self.completion[task_id]["database"]
                        if bound > grant.maximum:
                            raise StorageBudgetDeferred("storage_hold_database_envelope_changed")
                    else:
                        grant = self.reserve("database", bound, self.worker.db.path)
                    grant.consume(bound)
                except StorageBudgetError as exc:
                    admission_error = exc
                    return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK
        try:
            task_id = getattr(self.local, "task_id", None)
            resources = self.completion.get(task_id, {})
            grant = resources.pop("claim_database", None)
            if grant is not None:
                # The first task transaction owns its global reservation before
                # shared_policy.reserve can charge a durable provider attempt.
                if bound > grant.maximum:
                    raise StorageBudgetDeferred("storage_claim_database_envelope_changed")
                grant.consume(bound)
            with admitted_semantic_write(conn, policy_epoch=state[0], run_id=self.lease["cycle_id"],
                                         lease=self.lease, authorizer=authorize):
                try:
                    yield conn
                except sqlite3.Error as exc:
                    if admission_error is not None:
                        raise admission_error from exc
                    if getattr(exc, "sqlite_errorcode", None) == sqlite3.SQLITE_FULL:
                        raise StorageBudgetDeferred("database_page_envelope_exhausted") from exc
                    raise
                committing = True
                conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            if grant is not None:
                grant.close()

    def report(self):
        return {"mode": "cooperative", "physical_hard_cap_enforced": False,
                "limits": self.caps, "charged_bytes": dict(self.used),
                "outstanding_bytes": dict(self.reserved), "cycle_id": self.lease["cycle_id"]}


class WorkerStorageGrant:
    def __init__(self, owner, stage, maximum, grant, category):
        self.owner, self.stage, self.maximum, self.grant = owner, stage, maximum, grant
        self.category = category
        self.consumed, self.closed = 0, False

    def consume(self, count):
        with self.owner.lock:
            if self.closed or self.consumed + count > self.maximum:
                raise StorageBudgetDeferred(self.stage + "_write_limit_exceeded")
            self.grant.consume(count)
            self.consumed += count

    def close(self):
        with self.owner.lock:
            if self.closed:
                return
            self.grant.settle(self.consumed)
            self.owner.reserved[self.stage] -= self.maximum
            self.owner.used[self.stage] += self.consumed
            self.closed = True

class SerializedWorkerDatabase(JobDatabase):
    """Per-thread database facade sharing one coordinator transaction lock."""

    def __init__(self, path, lock, worker=None):
        super().__init__(path)
        self._worker_lock, self.worker = lock, worker

    @contextmanager
    def connect(self):
        with self.connection_scope() as conn:
            yield conn

    @contextmanager
    def connection_scope(self):
        with self._worker_lock:
            nested = self._persistent_conn is not None
            storage = getattr(self.worker, "storage", None)
            if storage is not None and not nested:
                storage.validate_database_paths()
            with super().connection_scope() as conn:
                deadline = getattr(self.worker, "_sql_deadline", getattr(self.worker, "_report_deadline", None))
                if deadline is not None and not nested:
                    self.worker.check_deadline(deadline)
                    conn.execute("PRAGMA busy_timeout=" + str(max(1, int(min(1, max(0, deadline-time.time()))*1000))))
                    from jobagg.publication_deadline import expired
                    conn.set_progress_handler(lambda: int(time.time() >= deadline or time.monotonic() >= self.worker._elapsed_deadline or expired(None)), 256)
                storage = getattr(self.worker, "storage", None)
                from jobagg.runtime_authority import writer
                with (storage.database_write(conn) if storage is not None and not nested else nullcontext()):
                    with (writer(conn, run_id=getattr(self.worker, "_runtime_run_id", None),
                                 owner_path=self.worker.shared_lock, owner_fd=getattr(self.worker, '_runtime_owner_fd', None),
                                 budgeted=storage is not None) if not nested else nullcontext()):
                        yield conn


def source_request_host(source):
    """Host the scheduler checks before a source's first API/listing request."""
    url = source.extra.get("cxs_base_url") or source.extra.get("listing_url") or source.base_url
    return (urlsplit(url).hostname or "").lower()


class Worker:
    def __init__(
        self,
        *,
        registry,
        robots,
        workspace,
        shared_lock,
        max_tasks=3,
        max_seconds=300,
        max_requests_per_task=200,
        client_factory=None,
        policy_bootstrap=None,
        max_detail_tasks=None,
        parallel_sources=1,
    ):
        self.registry = Path(registry).resolve()
        self.robots_path = Path(robots).resolve()
        self.workspace = Path(workspace).absolute()
        self.shared_lock = Path(shared_lock).absolute()
        if self.workspace.resolve() != self.workspace or self.workspace == self.shared_lock:
            raise ValueError("Workspace/lock alias rejected")
        if (
            not 1 <= max_tasks <= 100
            or not 1 <= max_requests_per_task <= 1000
            or not 0 < max_seconds <= 600
        ):
            raise ValueError(
                "Tick budgets must be finite:1–100 tasks,1–1000 HTTP/task,<=600 seconds"
            )
        self.max_tasks = max_tasks
        self.max_detail_tasks = max_tasks if max_detail_tasks is None else max_detail_tasks
        if type(self.max_detail_tasks) is not int or not 0 <= self.max_detail_tasks <= max_tasks:
            raise ValueError("Detail task budget must be an integer within the total task budget")
        self.max_seconds = max_seconds
        self.max_requests = max_requests_per_task
        if type(parallel_sources) is not int or not 1 <= parallel_sources <= HARD_CEILING:
            raise ValueError(f"Parallel source limit must be an integer from 1 to {HARD_CEILING}")
        self.parallel_sources = parallel_sources
        self._database_lock = threading.RLock()
        self._database_local = threading.local()
        self._artifact_lock = threading.RLock()
        self._metrics_lock = threading.RLock()
        self._task_intervals = []
        self._request_intervals = []
        self._eligible_counts = (0, 0, 0)
        self._eligible_peaks = (0, 0)
        self.sources = load_sources(self.registry)
        if len({s.id for s in self.sources}) != len(self.sources):
            raise ValueError("Duplicate configured source IDs")
        self.by_id = {s.id: s for s in self.sources if s.enabled}
        self.shared_policy = SharedPolicy(self.shared_lock, self.by_id, policy_bootstrap)
        self.policy = load_policy(self.robots_path)
        self.client_factory = client_factory or _http_client_for_source
        self.binding = {
            "version": VERSION,
            "registry_sha256": sha(self.registry),
            "robots_sha256": sha(self.robots_path),
            "implementation_sha256": implementation_hash(),
            "shared_lock": str(self.shared_lock),
        }
        self._retry_compatible_successor = reviewed_retry_successor()
        self.tick_id = None
        self.storage = None

    @property
    def db(self):
        # Nested transactions reuse only this thread's connection. The lock also
        # serializes readers: no SQLite connection is used across worker threads.
        if not hasattr(self._database_local, "database"):
            self._database_local.database = SerializedWorkerDatabase(
                self.workspace / "jobs.sqlite3", self._database_lock, self
            )
        return self._database_local.database

    def preview(self):
        if self.db.path.resolve() != self.db.path:
            raise ValueError("Workspace database path must not alias another database")
        marker = self.workspace / "worker_workspace.json"
        if marker.exists() and json.loads(marker.read_text()) != self.binding:
            raise ValueError(
                "Workspace code/config drift requires a separately reviewed generation"
            )
        if self.workspace.exists() and any(self.workspace.iterdir()) and not marker.exists():
            raise ValueError(
                "Nonempty directory is not an initialized worker workspace; live/manual DBs are refused"
            )
        counts = {}
        if self.db.path.exists():
            with self._database_lock, sqlite3.connect(self.db.path.as_uri() + "?mode=ro", uri=True) as conn:
                conn.row_factory = sqlite3.Row
                deadline = getattr(self, "_report_deadline", None)
                if deadline is not None:
                    conn.execute("PRAGMA busy_timeout=1000")
                    conn.set_progress_handler(lambda: int(time.time() >= deadline or time.monotonic() >= self._elapsed_deadline), 256)
                from jobagg.worker_summaries import counts as task_counts
                for row in task_counts(conn):
                    key = f"{row['kind']}:{row['status']}"
                    counts[key] = counts.get(key, 0) + row["n"]
        return {
            "status": "dry_run",
            "network_requests": 0,
            "database_writes": 0,
            "workspace": str(self.workspace),
            "binding": self.binding,
            "enabled_sources": [source_capability(s) for s in self.sources if s.enabled],
            "disabled_sources": [source_capability(s) for s in self.sources if not s.enabled],
            "task_counts": counts,
            "normal_unicef_detail_starts_per_hour": 10,
            "tick_limits": {
                "tasks": self.max_tasks,
                "detail_tasks": self.max_detail_tasks,
                "requests_per_task": self.max_requests,
                "seconds": self.max_seconds,
                "parallel_sources": self.parallel_sources,
            },
            "completeness_certified": False,
            "publication": "isolated compatible SQLite only; no live target",
            "shared_policy": self.shared_policy.inspect(),
        }

    def initialize(self):
        self.preview()
        from jobagg.runtime_authority import binding as runtime_binding
        runtime_prepared = self.db.path.exists() and runtime_binding(self.db.path) is not None
        if self.storage is not None or runtime_prepared:
            if self.shared_policy.inspect()["status"] != "existing" or not self.db.path.exists():
                raise StorageBudgetError("storage_activation_requires_initialized_workspace")
            # Schema migrations and activation are separately admitted setup.
            # Never implicitly commit DDL inside an activated worker transaction.
            with self.db.connection_scope() as conn:
                conn.execute("UPDATE remediation_tasks SET status='interrupted',last_error='Previous durable claim lacks an atomic completion; review required' WHERE status='inflight'")
            return
        self.shared_policy.initialize()
        self.workspace.mkdir(parents=True, exist_ok=True)
        marker = self.workspace / "worker_workspace.json"
        if not marker.exists():
            write_json(marker, self.binding)
        self.db.initialize()
        with self.db.connect() as conn:
            conn.executescript(
                """
CREATE TABLE IF NOT EXISTS remediation_sources(source_id TEXT PRIMARY KEY,next_list_at REAL NOT NULL DEFAULT0,
 last_list_at REAL,listing_ids TEXT,listing_proof TEXT,last_service REAL NOT NULL DEFAULT0);
CREATE TABLE IF NOT EXISTS remediation_tasks(task_id TEXT PRIMARY KEY,source_id TEXT NOT NULL,kind TEXT NOT NULL,
 external_id TEXT NOT NULL,payload TEXT NOT NULL,status TEXT NOT NULL,eligible_at REAL NOT NULL,
 discovered_at REAL NOT NULL,attempts INTEGER NOT NULL DEFAULT0,claim TEXT,last_error TEXT,receipt TEXT);
CREATE TABLE IF NOT EXISTS remediation_attempts(attempt_id TEXT PRIMARY KEY,task_id TEXT NOT NULL,source_id TEXT NOT NULL,
 kind TEXT NOT NULL,started_at REAL NOT NULL,finished_at REAL,status TEXT NOT NULL,evidence TEXT);
CREATE TABLE IF NOT EXISTS remediation_observations(job_key TEXT PRIMARY KEY,source_id TEXT NOT NULL,checked_at REAL NOT NULL,
 source_description_sha256 TEXT NOT NULL,database_description_sha256 TEXT NOT NULL,proof TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS remediation_documents(task_id TEXT PRIMARY KEY,job_key TEXT NOT NULL,source_id TEXT NOT NULL,
 url TEXT NOT NULL,content_sha256 TEXT NOT NULL,text_sha256 TEXT NOT NULL,manifest TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS attachment_blobs(content_sha256 TEXT PRIMARY KEY,media_type TEXT,size_bytes INTEGER NOT NULL,content BLOB NOT NULL);
""".replace("DEFAULT0", "DEFAULT 0")
            )
            ensure_detail_lineage_indexes(conn)
            conn.executescript("""
CREATE INDEX IF NOT EXISTS idx_remediation_attempts_task_started
 ON remediation_attempts(task_id,started_at DESC);
CREATE INDEX IF NOT EXISTS idx_remediation_attempts_kind_started
 ON remediation_attempts(kind,started_at DESC);
CREATE INDEX IF NOT EXISTS idx_remediation_attempts_source_started
 ON remediation_attempts(source_id,started_at DESC,attempt_id DESC);
CREATE INDEX IF NOT EXISTS idx_remediation_attempts_source_status_finished
 ON remediation_attempts(source_id,status,finished_at DESC);
CREATE INDEX IF NOT EXISTS idx_remediation_tasks_status_kind_source
 ON remediation_tasks(status,kind,source_id);
CREATE INDEX IF NOT EXISTS idx_remediation_tasks_source_kind_status
 ON remediation_tasks(source_id,kind,status);
""")
            from jobagg.worker_summaries import ensure
            ensure(conn)
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(remediation_sources)")}
            if "host" not in columns:
                conn.execute("ALTER TABLE remediation_sources ADD COLUMN host TEXT")
            # A killed process may have sent a request. Never silently refund or retry it.
            conn.execute(
                "UPDATE remediation_tasks SET status='interrupted',last_error='Previous durable claim lacks an atomic completion; review required' WHERE status='inflight'"
            )
            for source in self.by_id.values():
                conn.execute(
                    "INSERT OR IGNORE INTO remediation_sources(source_id) VALUES(?)", (source.id,)
                )
                conn.execute(
                    "UPDATE remediation_sources SET host=? WHERE source_id=? AND host IS NULL",
                    (source_request_host(source), source.id),
                )

    def retry_input_fingerprint(self, payload, *, implementation_sha256=None):
        semantic = deepcopy(payload)
        # A fresh frame path/timestamp is not a parser or URL repair.
        semantic.pop("frame_path", None)
        semantic.pop("frame_sha256", None)
        listing = semantic.get("listing")
        if isinstance(listing, dict):
            for key in ("first_seen_at", "last_seen_at", "normalized_hash", "posting_fingerprint"):
                listing.pop(key, None)
            if isinstance(listing.get("raw"), dict):
                listing["raw"].pop("_jobagg_listing_verification", None)
        binding = self.binding if implementation_sha256 is None else {
            **self.binding, "implementation_sha256": implementation_sha256,
        }
        return hashlib.sha256(dump({"payload": semantic, "binding": binding}).encode()).hexdigest()

    def retry_input_matches(self, saved, payload):
        if saved == self.retry_input_fingerprint(payload):
            return True
        successor = getattr(self, "_retry_compatible_successor", None)
        if successor is None or self.binding["implementation_sha256"] != successor:
            return False
        return any(saved == self.retry_input_fingerprint(payload, implementation_sha256=prior)
                   for prior in RETRY_EQUIVALENT_IMPLEMENTATIONS)

    def enqueue(self, conn, source, kind, identity, payload, *, due=None, refresh=False):
        now = time.time()
        key = task_key(source, kind, identity)
        old = conn.execute(
            "SELECT status,payload,eligible_at,last_error,receipt FROM remediation_tasks WHERE task_id=?",
            (key,),
        ).fetchone()
        if kind == "detail":
            from jobagg.deadline_review import (
                active_hold, blocks_detail, matches_hold_review, record_hold,
                release_hold, reviewed_future_extension,
            )
            current = conn.execute(
                "SELECT source_id,external_id,closes_at,raw_json FROM jobs WHERE source_id=? AND external_id=?",
                (source, str(identity)),
            ).fetchone()
            proposed_deadline = payload.get("listing", {}).get("closes_at")
            if blocks_detail(current, proposed_deadline):
                if not old:
                    conn.execute(
                        "INSERT INTO remediation_tasks(task_id,source_id,kind,external_id,payload,status,eligible_at,discovered_at) VALUES(?,?,?,?,?,?,?,?)",
                        (key, source, kind, identity, dump(payload), "past_deadline", due or now, now),
                    )
                    record_hold(conn, key, source, identity, current, proposed_deadline)
                elif old["status"] == "pending":
                    # This classification never clears errors/receipts or changes
                    # attempts, claims, eligible_at, or protected hold states.
                    conn.execute(
                        "UPDATE remediation_tasks SET status='past_deadline' WHERE task_id=? AND status='pending'",
                        (key,),
                    )
                    record_hold(conn, key, source, identity, current, proposed_deadline)
                elif old["status"] == "past_deadline":
                    record_hold(conn, key, source, identity, current, proposed_deadline)
                return key
            if old and old["status"] == "past_deadline":
                if (not reviewed_future_extension(current, proposed_deadline)
                        or not matches_hold_review(active_hold(conn, key), current)):
                    return key
                release_hold(conn, key, {'kind': 'positive_future_deadline', 'deadline': proposed_deadline})
                refresh = True
        if old and old["status"] in {"inflight", "interrupted", "storage_held", *UNAVAILABLE_STATUSES}:
            return key
        if old and old["status"] in {"dead_letter", "blocked"}:
            receipt = json.loads(old["receipt"] or "{}")
            # Legacy blocks lack a typed input/version binding; use the
            # evidence-checked repair command rather than guessing their cause.
            if old["status"] == "blocked" and not receipt.get("retry_input_sha256"):
                return key
            failed_input = receipt.get("retry_input_sha256") or self.retry_input_fingerprint(json.loads(old["payload"]))
            if self.retry_input_matches(failed_input, payload):
                return key
            refresh = True
        if old and kind == "document" and old["status"] == "not_required":
            prior = json.loads(old["payload"])
            if not document_disposition_changed(prior, payload):
                return key
            refresh = True
        if not old:
            conn.execute(
                "INSERT INTO remediation_tasks(task_id,source_id,kind,external_id,payload,status,eligible_at,discovered_at) VALUES(?,?,?,?,?,?,?,?)",
                (key, source, kind, identity, dump(payload), "pending", due or now, now),
            )
        elif refresh or old["status"] == "pending":
            eligible = due if due is not None else now
            if old["status"] in {"pending", "past_deadline"} and old["last_error"]:
                eligible = max(eligible, old["eligible_at"])
            conn.execute(
                "UPDATE remediation_tasks SET payload=?,status='pending',eligible_at=? WHERE task_id=?",
                (dump(payload), eligible, key),
            )
        return key

    def seed_listings(self):
        now = time.time()
        with self.db.connection_scope() as conn:
            for source in self.by_id.values():
                state = conn.execute(
                    "SELECT * FROM remediation_sources WHERE source_id=?", (source.id,)
                ).fetchone()
                if state["next_list_at"] <= now:
                    self.enqueue(conn, source.id, "listing", "", {}, refresh=True)
                    policy = fetch_schedule_policy(source)
                    interval = float(policy["list_fetch_interval_minutes"]) * 60
                    conn.execute(
                        "UPDATE remediation_sources SET next_list_at=? WHERE source_id=?",
                        (now + interval, source.id),
                    )

    def policy_due(self, source, kind, now, *, attempts=None):
        if kind != "detail":
            return now
        revision = self.shared_policy.admission_revision(source.id)
        cache = getattr(self, "_policy_due_cache", {})
        cached = cache.get(source.id)
        if revision is not None and cached is not None and cached[0] == revision:
            return max(now, cached[1])
        policy = fetch_schedule_policy(source)
        minimum = float(
            policy.get("detail_min_delay_seconds")
            or policy.get("oracle_detail_min_delay_seconds")
            or 0
        )
        jitter = float(
            policy.get("detail_jitter_seconds") or policy.get("oracle_detail_jitter_seconds") or 0
        )
        batch = int(policy.get("detail_batch_size") or policy.get("oracle_detail_batch_size") or 0)
        pause = float(
            policy.get("detail_batch_pause_seconds")
            or policy.get("oracle_detail_batch_pause_seconds")
            or 0
        )
        native_due = self.shared_policy.native_due(source.id, now, minimum=minimum,
            jitter=jitter, batch=batch, pause=pause)
        if native_due is not None:
            return native_due
        if attempts is None:
            attempts = self.shared_policy.admission_events(source.id)
        if not attempts:
            return now
        finishes = [
            row.get("finished_at") or row.get("eligibility_accounting_until") or row["started_at"]
            for row in attempts
        ]
        due = finishes[-1] + minimum + jitter
        count = 0
        previous = None
        for row, end in zip(attempts, finishes):
            if previous is None or (pause and row["started_at"] - previous >= pause):
                count = 0
            count += 1
            previous = end
        if batch and count >= batch:
            due = max(due, finishes[-1] + pause)
        if source.id == "unicef_pageup":
            active = sorted(end for end in finishes if end > now - 3600)
            if len(active) >= 10:
                due = max(due, active[len(active) - 10] + 3600)
        if revision is not None:
            cache[source.id] = (revision, due)
            self._policy_due_cache = cache
        return max(now, due)

    def check_deadline(self, deadline):
        from jobagg.publication_deadline import expired
        from jobagg.runtime_clock import remaining
        work_clock = getattr(self, '_work_clock', None)
        work_expired = (work_clock is not None and deadline == getattr(self, '_work_deadline', None)
                        and remaining(work_clock) <= 0)
        if work_expired or expired(None) or ((deadline is not None and time.time() >= deadline)
                or time.monotonic() >= getattr(self, "_elapsed_deadline", float("inf"))):
            raise HostIneligible(
                "Work budget exhausted; preserving final-report time", category="budget"
            )

    def host_state(self, host):
        path = (
            self.shared_policy.root
            / "hosts"
            / ("host-" + hashlib.sha256(host.encode()).hexdigest()[:24] + ".json")
        )
        if path.is_symlink():
            raise ValueError("Shared host policy state must not be aliased")
        state = json.loads(path.read_text()) if path.exists() else {}
        if not isinstance(state.get("stopped", False), bool):
            raise ValueError("Malformed shared host stop state")
        finite_epoch(state.get("eligible_at", 0))
        return state

    def task_host(self, task):
        if '_host' in task:
            return task['_host']
        source = self.by_id[task["source_id"]]
        payload = json.loads(task["payload"])
        # Workday's public site and first API request can use different hosts.
        # Documents already carry their exact current public request URL.
        if payload.get("url"):
            return (urlsplit(payload["url"]).hostname or "").lower()
        return source_request_host(source)

    def choose(self, *, excluded_kinds=(), excluded_sources=(), excluded_hosts=(), deadline=None):
        from jobagg.publication_deadline import continuous_budget
        work_clock = getattr(self, '_work_clock', None) if deadline == getattr(self, '_work_deadline', None) else None
        try:
            with continuous_budget(work_clock):
                return self._choose(excluded_kinds=excluded_kinds, excluded_sources=excluded_sources,
                                    excluded_hosts=excluded_hosts, deadline=deadline)
        except sqlite3.OperationalError:
            # A cancelled read is unknown selection, not an empty frontier.
            self.check_deadline(deadline)
            raise

    def _choose(self, *, excluded_kinds=(), excluded_sources=(), excluded_hosts=(), deadline=None):
        """Choose without multiplying policy-file reads by the pending queue size."""
        self.check_deadline(deadline)
        now = time.time()
        with self.db.connect() as conn:
            from jobagg.task_headers import exists as headers_exist, candidates
            native_headers = headers_exist(conn)
            if native_headers:
                rows = candidates(conn, self, now, excluded_kinds=excluded_kinds,
                    excluded_sources=excluded_sources, excluded_hosts=excluded_hosts)
            else:
                rows = [
                    dict(row)
                    for row in conn.execute("""SELECT t.*,coalesce(
                        (SELECT max(a.started_at) FROM remediation_attempts a WHERE a.task_id=t.task_id),0
                        ) AS last_attempt_at FROM remediation_tasks t WHERE t.status='pending'""")
                    if row["kind"] not in excluded_kinds and row["source_id"] in self.by_id
                ]
            source_states = {row["source_id"]: dict(row) for row in conn.execute(
                "SELECT source_id,last_service,last_list_at,listing_proof FROM remediation_sources")}
            served = {key: state["last_service"] for key, state in source_states.items()}
            if native_headers:
                kind_served = dict(conn.execute('SELECT kind,last_service FROM jobagg_kind_service'))
            else:
                kind_served = {
                    row["kind"]: row["last_service"]
                    for row in conn.execute(
                        "SELECT 'listing' AS kind,max(started_at) AS last_service FROM remediation_attempts WHERE kind='listing' "
                        "UNION ALL SELECT 'detail',max(started_at) FROM remediation_attempts WHERE kind='detail' "
                        "UNION ALL SELECT 'document',max(started_at) FROM remediation_attempts WHERE kind='document'"
                    )
                }
                kind_served = {kind: stamp for kind, stamp in kind_served.items() if stamp is not None}
            circuit_sources = {
                row["source_id"] for row in conn.execute(
                    "SELECT source_id FROM source_circuit_breakers WHERE state IN('open','half_open')"
                )
            }
        self.check_deadline(deadline)
        # Every call validates the complete history inventory and file metadata;
        # unchanged parsed bytes are reused only inside this process. Claims
        # still validate again, so a selection cannot authorize a later request.
        events = (
            self.shared_policy.admission_events()
            if any(row["kind"] == "detail" for row in rows)
            else {}
        )
        policy_due = {}
        hosts = {}
        tasks = []
        lookup = {}
        for index, row in enumerate(rows):
            if index % 64 == 0:
                self.check_deadline(deadline)
            source = self.by_id[row["source_id"]]
            if row["kind"] == "document" and source.extra.get("fetch_attachments", True) is False:
                continue
            host = self.task_host(row)
            if source.id in excluded_sources or host in excluded_hosts:
                continue
            if host not in hosts:
                hosts[host] = self.host_state(host)
            state = hosts[host]
            if row["kind"] != "listing" and listing_recovery_pending(source_states.get(source.id), state):
                continue
            eligibility = host_eligibility(state, now)
            if eligibility["category"] == "review":
                continue
            key = (source.id, row["kind"])
            if key not in policy_due:
                policy_due[key] = self.policy_due(
                    source, row["kind"], now, attempts=events.get(source.id, [])
                )
            due = max(
                row["eligible_at"], eligibility["eligible_at"], policy_due[key]
            )
            task = Task(
                row["task_id"],
                source.id,
                host,
                row["discovered_at"],
                due,
                0,  # Eligibility deferrals are not failed attempts.
                kind=row["kind"],
                last_attempt_at=row["last_attempt_at"],
            )
            tasks.append(task)
            lookup[task.task_id] = row
        held_sources = circuit_sources | {
            source_id for source_id in {task.source for task in tasks}
            if self.shared_policy.source_hold(source_id)
        }
        eligible = [task for task in tasks if task.eligible_at <= now and task.source not in held_sources]
        # A due current listing is the safest recovery probe. This is only a
        # priority among already eligible tasks, never a quota/cooldown bypass.
        recovering = {task.source for task in eligible if task.kind == "listing"
                      and hosts.get(task.host, {}).get("recovery")}
        if recovering:
            tasks = [task for task in tasks if task.source not in recovering or task.kind == "listing"]
        self._eligible_counts = (
            len({task.source for task in eligible}),
            len({task.host for task in eligible}),
            len(eligible),
        )
        self._eligible_peaks = tuple(
            max(old, current) for old, current in zip(self._eligible_peaks, self._eligible_counts[:2])
        )
        chosen = select_due(tasks, now=now, source_last_served=served, kind_last_served=kind_served)
        self.check_deadline(deadline)
        if chosen is None:
            return None
        if native_headers:
            with self.db.connect() as conn:
                return dict(conn.execute('SELECT * FROM remediation_tasks WHERE task_id=?', (chosen.task_id,)).fetchone())
        return lookup[chosen.task_id]

    def claim(self, task, *, deadline=None):
        self.check_deadline(deadline)
        token = str(uuid4())
        now = time.time()
        with self.db.connection_scope() as conn:
            row = conn.execute(
                "SELECT status,eligible_at,payload,source_id,kind FROM remediation_tasks WHERE task_id=?",
                (task["task_id"],),
            ).fetchone()
            if not row or row["status"] != "pending":
                raise ValueError("Task already claimed")
            if any(row[key] != task[key] for key in ("payload", "source_id", "kind")):
                raise ValueError("Selected task changed before durable reservation")
            state = self.host_state(self.task_host(task))
            if task["kind"] != "listing" and listing_recovery_pending(
                conn.execute("SELECT last_list_at,listing_proof FROM remediation_sources WHERE source_id=?",
                             (task["source_id"],)).fetchone(), state
            ):
                raise HostIneligible("Fresh complete listing required before detail recovery", category="cooldown")
            eligibility = host_eligibility(state, now)
            if eligibility["category"] == "review":
                raise HostIneligible("Host stopped before durable reservation")
            if not eligibility["allowed"] or max(row["eligible_at"], eligibility["eligible_at"]) > now:
                raise HostIneligible(
                    "Task/host eligibility changed before durable reservation", category="cooldown"
                )
            if self.policy_due(self.by_id[task["source_id"]], task["kind"], now) > now:
                raise HostIneligible("Source pacing/quota changed before durable reservation")
            self.check_deadline(deadline)
            self.shared_policy.reserve(
                token, task["source_id"], task["kind"], now, self.workspace, task["task_id"]
            )
            conn.execute(
                "UPDATE remediation_tasks SET status='inflight',claim=?,attempts=attempts+1 WHERE task_id=?",
                (token, task["task_id"]),
            )
            conn.execute(
                "INSERT INTO remediation_attempts VALUES(?,?,?,?,?,?,?,?)",
                (
                    token,
                    task["task_id"],
                    task["source_id"],
                    task["kind"],
                    now,
                    None,
                    "reserved",
                    None,
                ),
            )
            conn.execute(
                "UPDATE remediation_sources SET last_service=? WHERE source_id=?",
                (now, task["source_id"]),
            )
        return token

    def finish(self, conn, task, token, status, receipt, error=None, due=0):
        self.shared_policy.finish(token, status)
        conn.execute(
            "UPDATE remediation_tasks SET status=?,receipt=?,last_error=?,eligible_at=? WHERE task_id=? AND claim=?",
            (status, dump(receipt), error, due, task["task_id"], token),
        )
        conn.execute(
            "UPDATE remediation_attempts SET finished_at=?,status=?,evidence=? WHERE attempt_id=?",
            (time.time(), status, dump(receipt), token),
        )
        if status == 'done':
            from jobagg.accepted_evidence import record_event
            record_event(conn, task=task, attempt_id=token, binding=self.binding,
                         run_id=getattr(self, '_runtime_run_id', None),
                         request_sha256=getattr(self,'_runtime_request_sha256',None),
                         request_hash_kind='file' if getattr(self,'_dispatcher_request_sha256',None) else 'canonical',
                         runtime_contract_sha256=(getattr(self,'_runtime_contract_value',None) or {}).get('sha256'))

    def context(self, source, target, task, deadline):
        client = self.client_factory(source, self.policy)
        phase = {"kind": task["kind"], "job_id": task["external_id"] or None}
        if task["kind"] == "document":
            payload = json.loads(task["payload"])
            phase.update(
                job_id=payload["job_key"].split(":", 1)[1],
                attachment_id=payload["attachment_id"],
            )
        capture = DurableCapture(
            client,
            self.policy,
            target,
            {},
            lock_root=self.shared_policy.root / "hosts",
            max_requests=self.max_requests,
            default_header_origin=source.base_url,
            phase=phase,
            deadline_at=deadline,
            source=source,
            storage=self.storage,
            # Task cleanup retains ownership even if capture/adapter setup fails.
            first_storage_grant=(self.storage.initial.get(task["task_id"]) if self.storage else None),
        )
        capture.current_id = phase["job_id"]
        original_transport = capture.original

        def measured_transport(url, **kwargs):
            # DurableCapture calls this only after the actual host lock, pacing
            # and durable dispatch record. Queue/lock waiting is not HTTP overlap.
            native_dispatch = kwargs.pop("_native_dispatch", None)
            interval = {
                "source_id": source.id,
                "host": (urlsplit(url).hostname or "").lower(),
                "attempt_id": target.name,
                "capture_path": str(target / "http" / f"{capture.count:05d}.json"),
                "request_url_sha256": hashlib.sha256(url.encode()).hexdigest(),
                "started_at": utc(),
                "started_monotonic": time.monotonic(),
                "opener_entries": 0,
                "opener_observation": (
                    "chromium_cdp_native_v1" if native_dispatch else "native_http_client"
                    if getattr(original_transport, "__func__", None) is JobAggHTTPClient._request
                    else "custom_transport_unknown"
                ),
            }
            original_opener = client._opener

            class ObservedOpener:
                def open(self, *args, **kwargs):
                    interval["opener_entries"] += 1
                    return original_opener.open(*args, **kwargs)

                def __getattr__(self, name):
                    return getattr(original_opener, name)

            client._opener = ObservedOpener()
            try:
                response = (native_dispatch or original_transport)(url, **kwargs)
                interval["status_code"] = response.status_code
                return response
            except BaseException as exc:
                interval["error_type"] = type(exc).__name__
                raise
            finally:
                client._opener = original_opener
                interval.update(finished_at=utc(), finished_monotonic=time.monotonic())
                with self._metrics_lock:
                    self._request_intervals.append(interval)

        capture.original = measured_transport
        capture.native_observer = measured_transport
        client._request = capture.request
        if source.extra.get("browser_render") and task["kind"] != "document":
            from jobagg.browser_fetch import install_browser_transport

            install_browser_transport(client, capture, source)
        adapter = get_adapter_class(source.adapter or source.ats_family)(
            AdapterContext(source, client, capture.checker)
        )
        if (source.adapter or source.ats_family) == "pageup":
            # The ordinary adapter's final empty-fragment fallback constructs a
            # stateless client. Keep this worker's entire request chain guarded.
            def guarded_public_detail(url):
                adapter.ensure_allowed(url)
                text = client.get(
                    url,
                    headers={
                        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                        "Referer": str(source.extra.get("listing_url") or source.base_url),
                    },
                    timeout_seconds=adapter._detail_timeout_seconds(),
                ).text
                if not text.strip():
                    raise ValueError(
                        "PageUp public detail remains empty; no unguarded stateless retry"
                    )
                return text

            adapter._fetch_public_detail_html = guarded_public_detail
        return adapter, client, capture

    def accepted_listing_retention(self, conn, listing):
        """Verify accepted body evidence before keeping detail fields out of listing writes."""
        observation = conn.execute(
            "SELECT * FROM remediation_observations WHERE job_key=?", (listing.identity_key(),)
        ).fetchone()
        if observation is None:
            return None
        current = conn.execute(
            "SELECT * FROM jobs WHERE job_key=?", (listing.identity_key(),)
        ).fetchone()
        if current is None:
            raise ValueError("Accepted detail observation has no job row")
        current = dict(current)
        body_sha = hashlib.sha256((current.get("description") or "").encode()).hexdigest()
        if (
            observation["source_id"] != listing.source_id
            or body_sha != observation["source_description_sha256"]
            or body_sha != observation["database_description_sha256"]
        ):
            raise ValueError("Accepted detail observation differs from current source/body")
        from jobagg.pipelines.live_publication import _capture

        proof = json.loads(observation["proof"])
        if (
            proof.get("source_id") != listing.source_id
            or str(proof.get("external_id")) != str(listing.external_id)
            or proof.get("parsed_source_text_sha256") != body_sha
        ):
            raise ValueError("Accepted detail proof differs from current source/body")
        # The mutable task can already be pending/blocked for a newer listing.
        # Bind the retained observation to its immutable successful attempt.
        attempts = conn.execute(
            "SELECT attempt_id,evidence FROM remediation_attempts WHERE task_id=? AND source_id=? AND kind='detail' AND status='done' AND finished_at IS NOT NULL ORDER BY finished_at DESC",
            (task_key(listing.source_id, "detail", str(listing.external_id)), listing.source_id),
        ).fetchall()
        for old in attempts:
            receipt = json.loads(old["evidence"] or "{}")
            path = Path(receipt.get("detail_path", "/missing"))
            if not path.is_file() or sha(path) != receipt.get("detail_sha256"):
                continue
            artifact = json.loads(path.read_text())
            parsed = artifact.get("job", {})
            if (
                parsed.get("source_id") != listing.source_id
                or str(parsed.get("external_id")) != str(listing.external_id)
                or parsed.get("description") != current["description"]
                or artifact.get("proof") != proof
            ):
                continue
            # Validate the original accepted artifact, without treating any later
            # listing-induced metadata drift as fresh source evidence or repairing
            # it here. Such drift is surfaced and schedules a fresh detail.
            if (
                detail_quality_status(
                    title=parsed.get("title"),
                    description=parsed.get("description"),
                    raw=parsed.get("raw", {}),
                )
                != DETAIL_QUALITY_COMPLETE
            ):
                raise ValueError("Accepted detail artifact parser quality rejected")
            captures = [
                _capture(item["path"], item["sha256"]) for item in proof.get("captures", [])
            ]
            if not any(
                meta
                and meta.get("phase", {}).get("kind") == "detail"
                and str(meta.get("phase", {}).get("job_id")) == str(listing.external_id)
                for meta in captures
            ):
                raise ValueError("Accepted detail lacks an identity-bound HTTP capture")
            return {
                "job_key": listing.identity_key(),
                "accepted_attempt_id": old["attempt_id"],
                "accepted_artifact": {"path": str(path), "sha256": sha(path)},
                "public_fields_preserved": list(PUBLIC_FIELDS),
                "existing_metadata_drift": [
                    key for key in PUBLIC_FIELDS if current.get(key) != parsed.get(key)
                ],
                "scope": "Listing frame cannot overwrite accepted detail; existing drift requires fresh detail or reviewed repair",
            }
        raise ValueError("Accepted detail lacks its bound completed artifact")

    def verified_unavailable_receipt(self, task):
        """Recheck saved bytes and original dispatch binding before queue transitions."""
        receipt = json.loads(task["receipt"] or "{}")
        evidence = receipt.get("vacancy_unavailable")
        if not isinstance(evidence, dict) or not evidence.get("captures"):
            return None
        payload = json.loads(task["payload"])
        # A retry may carry a newer listing; the unavailable receipt preserves
        # the immutable frame actually used for that response.
        original = json.loads(Path(receipt["frame_path"]).read_text())
        if sha(receipt["frame_path"]) != receipt["frame_sha256"]:
            raise ValueError("Unavailable task original listing frame changed")
        matches = [job for job in original.get("jobs", []) if job.get("source_id") == task["source_id"]
                   and str(job.get("external_id")) == str(task["external_id"])]
        if original.get("source_id") != task["source_id"] or len(matches) != 1:
            raise ValueError("Unavailable task original listing identity ambiguous")
        frame_listing(payload, task["source_id"], task["external_id"])
        paths = []
        for item in evidence["captures"]:
            if sha(item["path"]) != item["sha256"]:
                raise ValueError("Unavailable detail capture changed")
            paths.append(item["path"])
        current = captured_unavailable(task["source_id"], task["external_id"], paths)
        if current != evidence:
            raise ValueError("Unavailable detail classification no longer matches its capture")
        return evidence

    def reconcile_unavailable_present(self, conn, task, proof, frame_path):
        evidence = self.verified_unavailable_receipt(task)
        if (not evidence or not proof.get("complete") or not proof.get("started_at")
                or epoch(proof["started_at"]) <= epoch(evidence["observed_at"])):
            return
        receipt = json.loads(task["receipt"])
        if evidence.get("category") == "vacancy_detail_denied":
            # A source-specific public detail refusal is not proof of closure.
            # A newer complete inventory can schedule one daily recheck while
            # preserving the prior accepted detail and every charged attempt.
            rechecks = receipt.get("detail_denied_rechecks", 0)
            if type(rechecks) is not int or rechecks < 0:
                raise ValueError("Detail denial retry accounting is malformed")
            receipt["detail_denied_rechecks"] = rechecks + 1
            due = max(time.time(), epoch(evidence["observed_at"]) + 86400)
            receipt["listing_reconciliation"] = {
                "status": "still_listed_after_detail_denial", "frame_path": str(frame_path),
                "frame_sha256": sha(frame_path), "enumeration": proof,
                "closure_inferred": False, "bounded_recheck_scheduled": True,
                "not_before": due,
            }
            conn.execute("UPDATE remediation_tasks SET status='pending',eligible_at=?,receipt=?,last_error=? WHERE task_id=?",
                         (due, dump(receipt), "Public vacancy still listed; detail recheck limited to once per day", task["task_id"]))
            return
        retries = receipt.get("unavailable_rechecks", 0)
        if type(retries) is not int or retries not in {0, 1}:
            raise ValueError("Unavailable detail retry accounting is malformed")
        status = "pending" if retries == 0 else "listing_detail_conflict"
        receipt["unavailable_rechecks"] = 1
        receipt["listing_reconciliation"] = {"status": "listing_detail_conflict", "frame_path": str(frame_path),
            "frame_sha256": sha(frame_path), "enumeration": proof, "closure_inferred": False,
            "bounded_recheck_scheduled": retries == 0}
        due = max(time.time(), epoch(evidence["observed_at"]) + 3600)
        conn.execute("UPDATE remediation_tasks SET status=?,eligible_at=?,receipt=?,last_error=? WHERE task_id=?",
                     (status, due, dump(receipt), "Current complete inventory conflicts with explicit unavailable detail", task["task_id"]))

    def finish_unavailable(self, task, token, target, reason):
        """Expected unavailability has durable evidence, never accepted detail credit."""
        if task["kind"] != "detail":
            return None
        evidence = captured_unavailable(task["source_id"], task["external_id"], (target / "http").glob("*.json"))
        if not evidence:
            return None
        payload = json.loads(task["payload"])
        frame_listing(payload, task["source_id"], task["external_id"])
        old = json.loads(task.get("receipt") or "{}")
        receipt = {"vacancy_unavailable": evidence, "frame_path": payload["frame_path"],
                   "frame_sha256": payload["frame_sha256"], "capture_directory": str(target),
                   "error": reason, "unavailable_rechecks": old.get("unavailable_rechecks", 0),
                   "detail_denied_rechecks": old.get("detail_denied_rechecks", 0)}
        with self.db.connection_scope() as conn:
            self.finish(conn, task, token, "unavailable_pending_inventory", receipt, reason)
            # The next regular tick coalesces all failed IDs into one due listing.
            # Source quotas and actual-host cooldowns still gate dispatch.
            conn.execute("UPDATE remediation_sources SET next_list_at=min(next_list_at,?) WHERE source_id=?",
                         (time.time() + 60, task["source_id"]))
        return {"status": "unavailable_pending_inventory", "vacancy_unavailable": True,
                "task_id": task["task_id"], "category": evidence["category"]}

    def do_listing(self, source, adapter, capture, target, task, token):
        from jobagg.baseline_inventory import retain_baseline_for_listing

        jobs = adapter.fetch_jobs()
        keys = [job.identity_key() for job in jobs]
        if len(keys) != len(set(keys)) or any(
            job.source_id != source.id or not job.external_id for job in jobs
        ):
            raise ValueError("Listing source identity missing or duplicated")
        observed = utc()
        frame = {
            "source_id": source.id,
            "observed_at": observed,
            "jobs": [asdict(job) for job in jobs],
            "diagnostics": asdict(adapter.run_diagnostics),
        }
        if rendered := browser_receipt(capture):
            frame["browser_render_receipt"] = rendered
        frame_path = target / "listing.json"
        if self.storage:
            self.storage.accepted(frame)
        write_json(frame_path, frame)
        # upsert_job intentionally merges/mutates JobRecord objects. Dispatch must
        # retain the original listing shape, especially Workday externalPath.
        dispatch_payloads = json.loads(dump(frame))["jobs"]
        paths = sorted((target / "http").glob("*.json"))
        proof = verify_listing(source, jobs, paths, deadline_at=getattr(self,"_elapsed_deadline",None), deadline_epoch=getattr(self,"_sql_deadline",None))
        with self.db.connection_scope() as conn:
            retained = {}
            for job in jobs:
                protection = self.accepted_listing_retention(conn, job)
                if protection is None:
                    protection = retain_baseline_for_listing(conn, job)
                if protection:
                    retained[job.identity_key()] = protection
                else:
                    self.db.upsert_job(job)
            write_json(
                target / "listing_retention.json",
                {
                    "source_id": source.id,
                    "observed_at": observed,
                    "frame_sha256": sha(frame_path),
                    "retained_details": list(retained.values()),
                },
            )
            self.db.record_listing_observation(
                source.id,
                set(keys),
                observed_at=datetime.fromisoformat(observed),
                inventory_complete=proof["complete"],
                evidence_ref=str(frame_path),
            )
            for dispatch_payload in dispatch_payloads:
                job = record(dispatch_payload)
                previous = conn.execute(
                    "SELECT checked_at FROM remediation_observations WHERE job_key=?",
                    (job.identity_key(),),
                ).fetchone()
                old_task = conn.execute(
                    "SELECT * FROM remediation_tasks WHERE task_id=?",
                    (task_key(source.id, "detail", str(job.external_id)),),
                ).fetchone()
                changed = old_task is not None and _listing_hash(
                    record(json.loads(old_task["payload"])["listing"])
                ) != _listing_hash(job)
                changed = changed or bool(
                    retained.get(job.identity_key(), {}).get("existing_metadata_drift")
                )
                due = (
                    time.time()
                    if not previous or changed
                    else max(time.time(), previous["checked_at"] + 86400)
                )
                if old_task is not None and old_task["status"] == "pending":
                    due = min(due, old_task["eligible_at"])
                if old_task is not None and old_task["status"] in UNAVAILABLE_STATUSES:
                    self.reconcile_unavailable_present(conn, old_task, proof, frame_path)
                self.enqueue(
                    conn,
                    source.id,
                    "detail",
                    str(job.external_id),
                    {
                        "listing": dispatch_payload,
                        "frame_path": str(frame_path),
                        "frame_sha256": sha(frame_path),
                    },
                    due=due,
                    refresh=True,
                )
            if proof["complete"]:
                for pending in conn.execute(
                    "SELECT * FROM remediation_tasks WHERE source_id=? AND kind='detail' AND status IN('pending','unavailable_pending_inventory','listing_detail_conflict')",
                    (source.id,),
                ).fetchall():
                    prior = record(json.loads(pending["payload"])["listing"])
                    if prior.identity_key() not in keys:
                        if pending["status"] in UNAVAILABLE_STATUSES:
                            evidence = self.verified_unavailable_receipt(pending)
                            if not evidence or not proof.get("started_at") or epoch(proof["started_at"]) <= epoch(evidence["observed_at"]):
                                continue
                        receipt = json.loads(pending["receipt"] or "{}")
                        receipt["listing_reconciliation"] = {"status": "not_observed", "frame_path": str(frame_path),
                            "frame_sha256": sha(frame_path), "enumeration": proof, "closure_inferred": False}
                        conn.execute(
                            "UPDATE remediation_tasks SET status='not_observed',last_error='Absent from a new independently complete configured inventory; closure not inferred',receipt=? WHERE task_id=?",
                            (dump(receipt), pending["task_id"]),
                        )
            conn.execute(
                "UPDATE remediation_sources SET last_list_at=?,listing_ids=?,listing_proof=? WHERE source_id=?",
                (epoch(observed), dump(keys), dump(proof), source.id),
            )
            self.finish(
                conn,
                task,
                token,
                "done",
                {
                    "frame_path": str(frame_path),
                    "frame_sha256": sha(frame_path),
                    "enumeration": proof,
                    "listing_retention": {
                        "path": str(target / "listing_retention.json"),
                        "sha256": sha(target / "listing_retention.json"),
                    },
                },
            )
        return {"listing_observed": len(jobs), "independent_enumeration": proof["complete"]}

    def do_detail(self, source, adapter, capture, target, task, token):
        from jobagg.pipelines.document_tasks import discover_document_inventory

        payload = json.loads(task["payload"])
        listing = frame_listing(payload, source.id, task["external_id"])
        fetch = getattr(adapter, "fetch_detail_for_listing_item", None)
        if not callable(fetch):
            raise ValueError(
                "Adapter lacks deterministic full-detail method; browser/provider implementation required"
            )
        detail = fetch(deepcopy(listing.raw))
        if (
            detail is None
            or detail.identity_key() != listing.identity_key()
            or detail.source_id != source.id
        ):
            raise DetailIdentityMismatch("Public detail is unavailable or returned a different job identity")
        quality = detail_quality_status(
            title=detail.title, description=detail.description, raw=detail.raw
        )
        if quality != DETAIL_QUALITY_COMPLETE:
            raise ValueError("Full-detail parser rejected content: " + quality)
        captures = sorted((target / "http").glob("*.json"))
        if not any(
            (meta := json.loads(p.read_text())).get("status_code") == 200
            and meta.get("phase", {}).get("kind") == "detail"
            and str(meta.get("phase", {}).get("job_id")) == str(detail.external_id)
            and meta.get("body_captured") is True
            for p in captures
        ):
            raise ValueError("No successful current HTTP detail observation")
        document_inventory = (discover_document_inventory(detail)
            if source.extra.get("fetch_attachments", True) is not False else {
                "candidates": [], "discovery_complete": False,
                "completeness_certified": False, "excluded_by_scope": True,
                "reason": "Supplementary attachments excluded by user configuration",
            })
        candidates = document_inventory["candidates"]
        expected = json.loads(dump(asdict(detail)))
        if self.storage:
            self.storage.accepted(expected)
        body_sha = hashlib.sha256((detail.description or "").encode()).hexdigest()
        proof = {
            "version": VERSION,
            "source_id": source.id,
            "external_id": detail.external_id,
            "observed_at": utc(),
            "parsed_source_text_sha256": body_sha,
            "independent_whole_public_text_verified": False,
            "required_public_metadata_verified": False,
            "attachment_discovery_complete": False,
            "completeness_certified": False,
            "parser_quality": quality,
            "document_inventory": document_inventory,
            "captures": [{"path": str(p), "sha256": sha(p)} for p in captures],
            "scope": "Exact parsed public body stored; absent metadata retention is identified separately. Independent full public field/section and incorporation contracts remain separate.",
        }
        if rendered := browser_receipt(capture):
            proof["browser_render_receipt"] = rendered
        write_json(
            target / "detail.json",
            {
                "job": expected,
                "proof": proof,
                "document_candidates": candidates,
                "document_inventory": document_inventory,
            },
        )
        with self.db.connection_scope() as conn:
            projected = deepcopy(detail)
            current = conn.execute(
                "SELECT * FROM jobs WHERE job_key=?", (detail.identity_key(),)
            ).fetchone()
            if current is not None:
                self.db._merge_existing_detail_fields(projected, current)
            stored_expected = json.loads(dump(asdict(projected)))
            # Merge retention may supply absent metadata, but cannot silently
            # replace fresh prose, identities, URLs or explicitly parsed values.
            for field in PUBLIC_FIELDS:
                if (
                    field in {"description", "title", "source_url", "apply_url"}
                    or expected.get(field) is not None
                ) and stored_expected.get(field) != expected.get(field):
                    raise ValueError("Detail merge replaced an explicit public field: " + field)
            _persist_completed_detail(self.db, listing, detail, _listing_hash(listing))
            actual = self.db.get_job(detail.identity_key())
            for field in PUBLIC_FIELDS:
                if actual.get(field) != stored_expected.get(field):
                    raise ValueError("Atomic detail readback differs: " + field)
            proof["retained_metadata_fields"] = {
                field: {
                    "parsed_value": expected.get(field),
                    "stored_value": stored_expected.get(field),
                    "basis": "Existing database merge retention; not a new public claim",
                }
                for field in PUBLIC_FIELDS
                if expected.get(field) != stored_expected.get(field)
            }
            write_json(
                target / "detail.json",
                {
                    "job": stored_expected,
                    "parsed_job": expected,
                    "proof": proof,
                    "document_candidates": candidates,
                    "document_inventory": document_inventory,
                },
            )
            raw = actual["raw"]
            raw["_deterministic_fetch_observation"] = proof
            raw["attachment_verification"] = {
                "complete": False,
                "discovery_complete": False,
                "reason": ("Supplementary attachments excluded by user configuration"
                           if source.extra.get("fetch_attachments", True) is False
                           else "Current deterministic document coverage pending/unsupported"),
                "excluded_by_scope": source.extra.get("fetch_attachments", True) is False,
            }
            conn.execute(
                "UPDATE jobs SET raw_json=?,application_ready=0 WHERE job_key=?",
                (dump(raw), detail.identity_key()),
            )
            conn.execute(
                "INSERT OR REPLACE INTO remediation_observations VALUES(?,?,?,?,?,?)",
                (
                    detail.identity_key(),
                    source.id,
                    time.time(),
                    body_sha,
                    hashlib.sha256((actual["description"] or "").encode()).hexdigest(),
                    dump(proof),
                ),
            )
            for candidate in candidates:
                self.enqueue_document(
                    conn,
                    source.id,
                    {
                        **candidate,
                        "job_key": detail.identity_key(),
                        "parent_description_sha256": body_sha,
                        "depth": 0,
                    },
                    refresh=True,
                )
            self.finish(
                conn,
                task,
                token,
                "done",
                {
                    "detail_path": str(target / "detail.json"),
                    "detail_sha256": sha(target / "detail.json"),
                    "document_candidates": len(candidates),
                    "scope_verified": False,
                },
            )
            from jobagg.accepted_evidence import record as record_accepted_evidence
            from jobagg.runtime_authority import state as runtime_state
            active = runtime_state(conn)
            acceptance = None
            if active is not None:
                if self.binding['implementation_sha256'] != implementation_hash():
                    raise ValueError('Code changed before atomic acceptance reference')
                acceptance = {'kind':'native_atomic_acceptance','run_id':self._runtime_run_id,
                              'binding':self.binding,'transaction_id':active['source_transaction_id']}
            record_accepted_evidence(conn, job_key=detail.identity_key(), task_id=task['task_id'],
                attempt_id=token, artifact_path=target / 'detail.json', artifact_sha256=sha(target / 'detail.json'),
                job=stored_expected, proof=proof, body_sha256=body_sha, acceptance_proof=acceptance)
        return {"detail_stored": detail.identity_key(), "documents_queued": len(candidates)}

    def enqueue_document(self, conn, source_id, payload, *, refresh=False):
        if self.by_id[source_id].extra.get("fetch_attachments", True) is False:
            return None
        """Queue literal links conservatively; record bounded discovery holds."""
        key = self.enqueue(
            conn, source_id, "document", payload["attachment_id"], payload, refresh=refresh
        )
        count = conn.execute(
            "SELECT count(*) FROM remediation_tasks WHERE kind='document' AND status!='not_required' AND json_extract(payload,'$.job_key')=?",
            (payload["job_key"],),
        ).fetchone()[0]
        reason = None
        if payload.get("printed_url_dispatch_requires_review"):
            reason = "Printed URL punctuation/continuation requires source review before dispatch"
        elif payload.get("depth", 0) > 3:
            reason = "Recursive document depth requires review"
        elif count > 100:
            reason = "Per-job document dispatch ceiling requires review"
        if reason:
            conn.execute(
                "UPDATE remediation_tasks SET status='blocked',last_error=? WHERE task_id=? AND status='pending'",
                (reason, key),
            )
        return key

    def do_document(self, source, client, capture, target, task, token):
        from jobagg.pipelines.document_tasks import extract_document

        payload = json.loads(task["payload"])
        if payload.get("printed_url_dispatch_requires_review") or payload.get("depth", 0) > 3:
            raise ValueError("Document dispatch requires explicit source review")
        response = client.get(payload["url"])
        if response.status_code != 200:
            raise ValueError("Document did not return HTTP200")
        data = response.content
        if self.storage:
            self.storage.accepted({"document_bytes": len(data), "payload": payload})
        document = extract_document(data, response.headers.get("Content-Type", ""), response.url)
        digest = hashlib.sha256(data).hexdigest()
        blob = self.workspace / "blobs" / digest
        # Independent sources can link the same binary. No thread may inspect a
        # half-written content-addressed file or race another exclusive creation.
        with self._artifact_lock:
            blob.parent.mkdir(exist_ok=True)
            if blob.exists() and sha(blob) != digest:
                raise ValueError("Existing immutable binary differs")
            if not blob.exists():
                if self.storage:
                    self.storage.charge_file(blob, len(data))
                with blob.open("xb") as out:
                    out.write(data)
                    out.flush()
                    os.fsync(out.fileno())
        document = {
            **document,
            "attachment_id": payload["attachment_id"],
            "job_key": payload["job_key"],
            "source_id": source.id,
            "url": payload["url"],
            "final_url": response.url,
            "retrieved_at": utc(),
            "binary_path": str(blob),
            "parent_description_sha256": payload["parent_description_sha256"],
            "purpose_state": payload.get("purpose_state", "unknown"),
            "whole_job_complete": False,
            "capture_paths": [str(p) for p in sorted((target / "http").glob("*.json"))],
        }
        manifest = target / "document.json"
        write_json(manifest, document)
        with self.db.connection_scope() as conn:
            current = self.db.get_job(payload["job_key"])
            if (
                not current
                or hashlib.sha256((current.get("description") or "").encode()).hexdigest()
                != payload["parent_description_sha256"]
            ):
                raise ValueError(
                    "Parent public body changed; document association requires rediscovery"
                )
            conn.execute(
                "INSERT OR IGNORE INTO attachment_blobs VALUES(?,?,?,?)",
                (digest, response.headers.get("Content-Type"), len(data), data),
            )
            conn.execute(
                "INSERT OR REPLACE INTO remediation_documents VALUES(?,?,?,?,?,?,?)",
                (
                    task["task_id"],
                    payload["job_key"],
                    source.id,
                    payload["url"],
                    digest,
                    document["text_sha256"],
                    dump(document),
                ),
            )
            raw = current["raw"]
            dismissed_count = conn.execute(
                "SELECT count(*) FROM remediation_documents d WHERE d.job_key=? AND EXISTS (SELECT 1 FROM remediation_tasks t WHERE t.task_id=d.task_id AND t.status='not_required')",
                (payload["job_key"],),
            ).fetchone()[0]
            docs = [
                json.loads(row["manifest"])
                for row in conn.execute(
                    "SELECT d.manifest FROM remediation_documents d WHERE d.job_key=? AND NOT EXISTS (SELECT 1 FROM remediation_tasks t WHERE t.task_id=d.task_id AND t.status='not_required')",
                    (payload["job_key"],),
                )
            ]
            current_docs = [
                doc
                for doc in docs
                if doc.get("parent_description_sha256") == payload["parent_description_sha256"]
            ]
            raw["attachments"] = [
                {
                    **doc,
                    "binary_ref": {
                        "table": "attachment_blobs",
                        "key": doc["content_sha256"],
                        "column": "content",
                    },
                }
                for doc in current_docs
            ]
            raw["attachment_verification"] = {
                "complete": False,
                "discovery_complete": False,
                "required_document_count": len(current_docs),
                "historical_document_associations_retained_in_queue": len(docs) - len(current_docs),
                "dismissed_document_associations_retained_in_queue": dismissed_count,
                "reason": "Independent incorporation, fidelity and full public source scope gates not certified",
            }
            conn.execute(
                "UPDATE jobs SET raw_json=?,application_ready=0 WHERE job_key=?",
                (dump(raw), payload["job_key"]),
            )
            # Every discovered child remains explicit, even when unsupported or depth capped.
            children = document.get("document_links", [])
            for child in children:
                identity = hashlib.sha256(
                    (payload["job_key"] + "\n" + child["url"]).encode()
                ).hexdigest()
                child_payload = {
                    **payload,
                    **child,
                    "attachment_id": identity,
                    "url": child["url"],
                    "depth": payload.get("depth", 0) + 1,
                    "purpose_state": "unresolved",
                    "printed_url_dispatch_requires_review": child.get("kind") == "printed_url",
                    "parent_document_sha256": digest,
                }
                self.enqueue_document(conn, source.id, child_payload)
            self.finish(
                conn,
                task,
                token,
                "done",
                {
                    "manifest": str(manifest),
                    "sha256": sha(manifest),
                    "fidelity_status": document.get("fidelity_status", "unverified"),
                },
            )
        return {
            "document_captured": digest,
            "fidelity_status": document.get("fidelity_status", "unverified"),
        }

    def incomplete_response_count(self, task):
        receipt = json.loads(task.get("receipt") or "{}")
        if not self.retry_input_matches(receipt.get("retry_input_sha256"), json.loads(task["payload"])):
            return 0
        return int(receipt.get("incomplete_response_count", 0))

    def inventory_change_count(self, task):
        receipt = json.loads(task.get("receipt") or "{}")
        if not self.retry_input_matches(receipt.get("retry_input_sha256"), json.loads(task["payload"])):
            return 0
        return int(receipt.get("inventory_change_count", 0))

    def retry_after_error(self, task, target, exc):
        """Retry eligibility deferrals, bounded typed responses, or proven transport failures."""
        if isinstance(exc, IDBInventoryChanged) and task["kind"] == "listing":
            # Count semantic failures independently of durable reservations and
            # host/budget deferrals; the third failure requires task review.
            count = self.inventory_change_count(task) + 1
            if count >= 3:
                return None
            return {"category": exc.reason, "inventory_change_count": count,
                    "eligible_at": time.time() + 900}
        if isinstance(exc, BlockingIOError) or (
            isinstance(exc, HostIneligible) and exc.category in {"budget", "cooldown"}
        ):
            return {
                "category": "eligibility_deferred",
                "eligible_at": max(time.time() + 60, getattr(exc, "eligible_at", 0) or 0),
            }
        from jobagg.vacancy_outcomes import IncompleteDetailResponse
        if isinstance(exc, IncompleteDetailResponse):
            count = self.incomplete_response_count(task) + 1
            paths = sorted((target / "http").glob("*.json"))
            if not paths or count >= 3 or self.shared_policy.source_hold(task["source_id"]):
                return None
            meta = json.loads(paths[-1].read_text())
            if (meta.get("state") != "response_captured" or meta.get("status_code") != 200
                    or meta.get("body_captured") is not True
                    or meta.get("phase", {}).get("kind") != "detail"
                    or str(meta.get("phase", {}).get("job_id")) != str(task["external_id"])
                    or self.host_state((urlsplit(meta["url"]).hostname or "").lower()).get("stopped")):
                return None
            return {"category": "incomplete_detail_response", "incomplete_response_count": count,
                    "capture": {"path": str(paths[-1]), "sha256": sha(paths[-1])},
                    "eligible_at": time.time() + 300 * (2 ** (count - 1))}
        chain = []
        cause = exc
        while cause is not None and cause not in chain:
            chain.append(cause)
            if isinstance(cause, urllib.error.URLError) and isinstance(cause.reason, Exception):
                chain.append(cause.reason)
            cause = cause.__cause__
        if classify_failure(exc) == "tls_validation":
            return None
        deferred = [error for error in chain if isinstance(error, HostIneligible)
                    and error.category in {"budget", "cooldown"}]
        if deferred and not any(isinstance(error, urllib.error.HTTPError) for error in chain):
            return {"category": "eligibility_deferred",
                    "eligible_at": max(time.time() + 60, *(error.eligible_at or 0 for error in deferred))}
        transient = classify_failure(exc) == "transient_transport"
        statuses = [error.code for error in chain if isinstance(error, urllib.error.HTTPError)]
        transient |= bool(statuses) and statuses[-1] in {408, 429, 500, 502, 503, 504}
        paths = sorted((target / "http").glob("*.json"))
        if not paths:
            return None
        path = paths[-1]
        meta = json.loads(path.read_text())
        category = meta.get("failure_category")
        if category is not None:
            # Typed transport evidence can record HTTP 200 headers followed by
            # a body-read timeout; the incomplete body remains unaccepted.
            if category not in {"transient_transport", "rate_limit"}:
                return None
        elif not transient or meta.get("status_code") not in {None, 408, 429, 500, 502, 503, 504}:
            return None
        phase = meta.get("phase", {})
        matching_phase = phase.get("kind") == task["kind"] or (
            phase.get("kind") == "robots" and phase.get("originating_phase") == task["kind"]
        )
        body_captured = meta.get("body_captured")
        if body_captured is True:
            # Only a typed, source/phase-bound transient HTTP response may
            # requeue after its complete error body was durably captured.
            if (not statuses or statuses[-1] != meta.get("status_code")
                    or statuses[-1] not in {408, 429, 500, 502, 503, 504}
                    or category != ("rate_limit" if statuses[-1] == 429 else "transient_transport")
                    or classify_failure(exc) != category
                    or meta.get("source_binding", {}).get("source_id") != task["source_id"]
                    or phase.get("job_id") != (task["external_id"] or None)):
                return None
            try:
                artifact = Path(meta["artifact"])
                if artifact.resolve() != path.with_suffix(".body.gz").resolve():
                    return None
                digest = hashlib.sha256()
                size = 0
                with gzip.open(artifact, "rb") as source:
                    for block in iter(lambda: source.read(1024 * 1024), b""):
                        digest.update(block)
                        size += len(block)
                        if size > meta["body_bytes"]:
                            return None
                if size != meta["body_bytes"] or digest.hexdigest() != meta.get("body_sha256"):
                    return None
            except (KeyError, TypeError, ValueError, OSError, EOFError):
                return None
        elif body_captured is not False:
            return None
        if (
            meta.get("state") != "failed"
            or meta.get("error_type") != type(exc).__name__
            or not matching_phase
            or not meta.get("finished_at")
            or not meta.get("started_at")
        ):
            return None
        state = self.host_state((urlsplit(meta["url"]).hostname or "").lower())
        eligibility = host_eligibility(state, time.time())
        if eligibility["category"] == "review" or Path(state.get("evidence", "")).resolve() != path.resolve():
            return None
        if self.shared_policy.source_hold(task["source_id"]):
            return None
        return {
            "category": "guarded_transient_transport",
            "capture": {"path": str(path), "sha256": sha(path)},
            "eligible_at": max(time.time() + 60, eligibility["eligible_at"], task.get("eligible_at", 0)),
        }

    def admit(self, task, deadline):
        """Coordinator-only checks and durable claim, in selection order."""
        self.check_deadline(deadline)
        source = self.by_id[task["source_id"]]
        with self.db.connect() as conn:
            breakers = conn.execute(
                "SELECT 1 FROM source_circuit_breakers WHERE source_id=? AND state IN('open','half_open')",
                (source.id,),
            ).fetchall()
        if breakers or self.shared_policy.source_hold(source.id):
            with self.db.connect() as conn:
                conn.execute(
                    "UPDATE remediation_tasks SET status='blocked',last_error='Existing source circuit requires review' WHERE task_id=?",
                    (task["task_id"],),
                )
            return None
        minimum = float(source.extra.get("listing_min_budget_seconds", 0)) if task["kind"] == "listing" else 0
        minimum = max(6.0, minimum)
        if deadline is not None and deadline - time.time() < minimum:
            raise HostIneligible("Insufficient request budget before reservation", category="budget")
        if self.storage:
            self.storage.preclaim(task)
        try:
            with (self.storage.task_scope(task["task_id"]) if self.storage else nullcontext()):
                return self.claim(task, deadline=deadline)
        except BaseException:
            if self.storage:
                self.storage.release_task(task["task_id"])
            raise

    def perform(self, task, deadline):
        token = self.admit(task, deadline)
        if token is None:
            return {"blocked": "source_circuit"}
        return self.perform_claimed(task, deadline, token)

    def perform_claimed(self, task, deadline, token):
        interval = {
            "task_id": task["task_id"], "source_id": task["source_id"],
            "kind": task["kind"], "scheduling_host": self.task_host(task),
            "attempt_id": token, "started_at": utc(),
            "started_monotonic": time.monotonic(),
        }
        try:
            with admitted_file_writes(self.storage.file_write if self.storage else None):
                with (self.storage.task_scope(task["task_id"]) if self.storage else nullcontext()):
                    from jobagg.publication_deadline import continuous_budget
                    with continuous_budget(getattr(self, '_phase_clock', None)):
                        result = self._perform_claimed(task, deadline, token)
            interval["outcome"] = result
            return result
        except BaseException as exc:
            interval["unhandled_error_type"] = type(exc).__name__
            if isinstance(exc, StorageBudgetDeferred):
                raise RuntimeError("Storage completion failed after a durable claim; retained evidence requires review") from exc
            raise
        finally:
            if self.storage:
                self.storage.release_task(task["task_id"])
            interval.update(finished_at=utc(), finished_monotonic=time.monotonic())
            with self._metrics_lock:
                self._task_intervals.append(interval)

    def _perform_claimed(self, task, deadline, token):
        source = self.by_id[task["source_id"]]
        target = self.workspace / "captures" / token
        # Listing adapters may never turn their teasers into completed detail tasks.
        configured = replace(
            source, extra={**source.extra, "fetch_details": task["kind"] != "listing"}
        )
        capture = None
        from jobagg import runtime_clock, publication_deadline
        work_clock = getattr(self, '_work_clock', None) or runtime_clock.budget(
            max(0,deadline-time.time()), parent=publication_deadline.current_budget())
        try:
            with publication_deadline.continuous_budget(work_clock):
                self.check_deadline(deadline)
                if self.storage:
                    self.storage.ensure_directory(
                        target, grant=self.storage.initial.get(task["task_id"]), exist_ok=False,
                    )
                else:
                    target.mkdir(parents=True)
                adapter, client, capture = self.context(configured, target, task, deadline)
                if task["kind"] == "listing":
                    adapter.listing_checkpoint_path = self.workspace / "listing_checkpoints" / (source.id + ".json")
                    return self.do_listing(source, adapter, capture, target, task, token)
                if task["kind"] == "detail":
                    return self.do_detail(source, adapter, capture, target, task, token)
                return self.do_document(source, client, capture, target, task, token)
        except Exception as exc:
            # Preserve received bytes and durable attempt. Semantic errors block only
            # this task; transport host decisions are made by the HTTP checkpoint.
            reason = safe_error(exc)
            from jobagg.verification_budget import VerificationBudgetExceeded
            if isinstance(exc,VerificationBudgetExceeded):
                # Verification exhausted work time after capture. Preserve the
                # provider charge and bytes; use only the existing report reserve
                # for a small local deferral, with the coordinator DB lock held.
                with self._database_lock:
                    previous=self._sql_deadline
                    self._sql_deadline=self._report_deadline
                    try:
                        with self.db.connection_scope() as conn:
                            self.finish(conn,task,token,'verification_deferred',{'capture_directory':str(target),
                                'provider_failure':False,'verification_complete':False,'reason':reason},reason)
                            conn.execute("UPDATE remediation_tasks SET status='pending' WHERE task_id=? AND claim=?",(task['task_id'],token))
                    finally:
                        self._sql_deadline=previous
                return {'status':'pending','verification_deferred':True,'provider_failure':False,
                        'task_id':task['task_id'],'error_type':type(exc).__name__}
            if isinstance(exc, (ResponseTooLargeError, StorageBudgetError, SemanticCapacityError)):
                return self.finish_storage_hold(task, token, target, exc)
            if isinstance(exc, ValueError):
                unavailable = self.finish_unavailable(task, token, target, reason)
                if unavailable:
                    return unavailable
            retry = self.retry_after_error(task, target, exc)
            from jobagg.vacancy_outcomes import IncompleteDetailResponse
            incomplete_count = self.incomplete_response_count(task) + int(isinstance(exc, IncompleteDetailResponse))
            inventory_count = self.inventory_change_count(task) + int(
                isinstance(exc, IDBInventoryChanged) and task["kind"] == "listing"
            )
            status = "dead_letter" if classify_failure(exc) == "local_policy" else ("pending" if retry else "blocked")
            with self.db.connection_scope() as conn:
                self.finish(
                    conn,
                    task,
                    token,
                    status,
                    {"capture_directory": str(target), "error": reason, "retry_decision": retry,
                     "incomplete_response_count": incomplete_count,
                     **({"inventory_change_count": inventory_count,
                         "inventory_change_reason": IDBInventoryChanged.reason}
                        if inventory_count else {}),
                     "retry_input_sha256": self.retry_input_fingerprint(json.loads(task["payload"]))},
                    reason,
                    retry["eligible_at"] if retry else 0,
                )
            return {
                "status": status,
                "reason": reason,
                "error_type": type(exc).__name__,
                "database_error": isinstance(exc, sqlite3.Error),
                "task_id": task["task_id"],
                "eligible_at": retry["eligible_at"] if retry else None,
            }

        finally:
            if capture is not None and capture.first_storage_grant is not None:
                capture.first_storage_grant.close()
                capture.first_storage_grant = None

    def finish_storage_hold(self, task, token, target, exc):
        captures = []
        partial = False
        for path in sorted((target / "http").glob("*.json")):
            meta = json.loads(path.read_text())
            partial |= bool(meta.get("storage_limited"))
            captures.append({"path": str(path), "sha256": sha(path),
                             "body_complete": meta.get("body_complete", meta.get("body_captured", False)),
                             "storage_limited": bool(meta.get("storage_limited"))})
        outcome = ("partial_ingress_storage_held" if partial else
                   "complete_capture_storage_held" if any(item["body_complete"] for item in captures)
                   else "storage_admission_held")
        receipt = {"schema_version": 1, "attempt_id": token, "task_id": task["task_id"],
                   "source_id": task["source_id"], "outcome": outcome, "storage_limited": True,
                   "captures": captures, "capture_directory": str(target), "error": safe_error(exc),
                   "automatic_retry_allowed": False, "requires_explicit_storage_retry_grant": True,
                   "prior_accepted_data_preserved": True}
        # This small independent receipt survives a DB envelope failure too.
        write_json(self.workspace / "storage_holds" / (token + ".json"), receipt)
        if self.storage:
            self.storage.local.finishing_hold = True
        try:
            with self.db.connection_scope() as conn:
                self.finish(conn, task, token, "storage_held", receipt, safe_error(exc))
                conn.execute("UPDATE remediation_attempts SET status=? WHERE attempt_id=?", (outcome, token))
        finally:
            if self.storage:
                self.storage.local.finishing_hold = False
        return {"status": "storage_held", "storage_outcome": outcome,
                "task_id": task["task_id"], "automatic_retry_allowed": False,
                "provider_failure": False, "retained_captures": len(captures)}

    def run_parallel(self, work_deadline):
        """Admit in one order; join every task before reporting or releasing owner."""
        outcomes, active = [], {}
        dispatched = detail_tasks = 0
        admission_open = True
        fatal = None
        stop_reason = "task_budget"
        with ThreadPoolExecutor(max_workers=self.parallel_sources, thread_name_prefix="jobagg") as pool:
            while active or (admission_open and dispatched < self.max_tasks):
                while admission_open and len(active) < self.parallel_sources and dispatched < self.max_tasks:
                    excluded = {"detail"} if detail_tasks >= self.max_detail_tasks else set()
                    try:
                        task = self.choose(
                            excluded_kinds=excluded,
                            excluded_sources={item[1]["source_id"] for item in active.values()},
                            excluded_hosts={self.task_host(item[1]) for item in active.values()},
                            deadline=work_deadline,
                        )
                        self.check_deadline(work_deadline)
                        if task is None:
                            stop_reason = "no_eligible_task_within_kind_budgets"
                            break
                        token = self.admit(task, work_deadline)
                    except StorageBudgetDeferred:
                        stop_reason = "storage_deferred"
                        admission_open = False
                        break
                    except HostIneligible as exc:
                        stop_reason = "work_deadline" if exc.category == "budget" else "selection_deferred"
                        admission_open = False
                        break
                    dispatched += 1
                    if task["kind"] == "detail":
                        detail_tasks += 1
                    if token is None:
                        outcomes.append((dispatched, {"blocked": "source_circuit"}))
                        continue
                    # No second initialization, selection or claim in the thread.
                    future = pool.submit(self.perform_claimed, task, work_deadline, token)
                    active[future] = (dispatched, task)
                if not active:
                    break
                done, _ = wait(active, timeout=0.25, return_when=FIRST_COMPLETED)
                for future in sorted(done, key=lambda value: active[value][0]):
                    order, _ = active.pop(future)
                    try:
                        outcomes.append((order, future.result()))
                    except BaseException as exc:
                        # Stop new admissions but drain other durable claims. An
                        # unresolved claim never becomes a successful acceptance.
                        fatal = fatal or exc
                        admission_open = False
                if time.time() >= work_deadline:
                    stop_reason = "work_deadline"
                    admission_open = False
        if fatal is not None:
            raise fatal
        if dispatched >= self.max_tasks and stop_reason != "work_deadline":
            stop_reason = "task_budget"
        return [outcome for _, outcome in sorted(outcomes)], stop_reason

    def concurrency_report(self, started_at, deadline):
        with self._metrics_lock:
            tasks = deepcopy(self._task_intervals)
            requests = deepcopy(self._request_intervals)
        captures = []
        for item in requests:
            path = Path(item["capture_path"])
            meta = json.loads(path.read_text())
            if (
                meta.get("request_url_sha256") != item["request_url_sha256"]
                or not meta.get("finished_at")
            ):
                raise ValueError("Concurrency measurement/capture identity mismatch")
            item["capture_sha256"] = sha(path)
            # The same policy exception can arise before dispatch or after
            # reading a response. Exclude only proven native pre-opener denial;
            # old/custom observations without this evidence stay conservative.
            item["local_policy_denial"] = (
                meta.get("error_type") in LOCAL_POLICY_ERRORS
                and meta.get("status_code") is None
                and item.get("opener_observation") == "native_http_client"
                and item.get("opener_entries") == 0
            )
            captures.append(meta)
        # The last revisioned selection is observational only. A report must
        # never select work or repeat admission/history validation.
        snapshot_complete = getattr(self, "_report_eligibility_complete", False)
        access = [
            meta for meta in captures
            if (meta.get("status_code") in {401, 403, 429}
                or "Fresh host access challenge:" in meta.get("error", ""))
            and meta.get("failure_category") != "vacancy_detail_denied"
        ]
        failed = [item for item in tasks if item.get("outcome", {}).get("status") in {"blocked", "dead_letter"}]
        http_intervals = [item for item in requests if not item["local_policy_denial"]]
        return {
            "batch_id": self.tick_id, "started_at": started_at, "finished_at": utc(),
            "limit_used": self.parallel_sources,
            "eligible_distinct_sources": self._eligible_peaks[0],
            "eligible_distinct_hosts": self._eligible_peaks[1],
            "peak_active_tasks": interval_peak(tasks),
            "peak_active_sources": interval_peak(tasks, "source_id"),
            "peak_active_scheduling_hosts": interval_peak(tasks, "scheduling_host"),
            "peak_active_hosts": interval_peak(http_intervals, "host"),
            "peak_http_requests": interval_peak(http_intervals),
            "local_policy_request_attempts": len(requests) - len(http_intervals),
            "attempted_tasks": len(tasks),
            "dead_letter_tasks": sum(item.get("outcome", {}).get("status") == "dead_letter" for item in tasks),
            "accepted_progress": sum(any(key in item.get("outcome", {}) for key in
                ("listing_observed", "detail_stored", "document_captured")) for item in tasks),
            "eligible_backlog_remaining": self._eligible_counts[2] if snapshot_complete else None,
            "last_selection_eligible_tasks": self._eligible_counts[2],
            "eligibility_snapshot_complete": snapshot_complete,
            "new_access_blocks": len(access),
            "transport_failures": sum(meta.get("state") == "failed" and meta not in access
                and (meta.get("failure_category") == "transient_transport"
                     if meta.get("failure_category") is not None
                     else meta.get("error_type") not in LOCAL_POLICY_ERRORS) for meta in captures),
            "policy_holds": sum(item.get("outcome", {}).get("error_type") in LOCAL_POLICY_ERRORS for item in failed),
            "vacancies_unavailable": sum(item.get("outcome", {}).get("vacancy_unavailable") is True for item in tasks),
            "runtime_errors": sum(item.get("outcome", {}).get("error_type") not in LOCAL_POLICY_ERRORS for item in failed),
            "integrity_errors": sum(item.get("outcome", {}).get("error_type") in
                {"ValueError", "KeyError", "TypeError", "DetailIdentityMismatch"} for item in failed),
            "database_errors": sum(item.get("outcome", {}).get("database_error", False) for item in failed),
            "failure_classes": {
                "source_identity_rejection": sum(item.get("outcome", {}).get("error_type") == "DetailIdentityMismatch" for item in failed),
                "suspected_integrity_failure": sum(item.get("outcome", {}).get("error_type") in
                    {"ValueError", "KeyError", "TypeError", "DetailIdentityMismatch"} for item in failed),
                "database_pressure": sum(item.get("outcome", {}).get("database_error", False) for item in failed),
                "controller_disposition": "retain_conservative_pressure_until_reviewed",
            },
            "control_cycle_complete": snapshot_complete,
            "killed": False, "publication_status": "not_run",
            "task_intervals": sorted(tasks, key=lambda item: item["started_monotonic"]),
            "request_intervals": sorted(requests, key=lambda item: item["started_monotonic"]),
            "scope": "Observed task and guarded transport overlap; source completeness and publication remain separate.",
        }

    def report(self, request=None):
        from jobagg.source_health import source_health
        sources = []
        source_holds = json.loads((self.shared_policy.root / "source_holds.json").read_text())
        report_now = time.time()
        events = getattr(self, "_report_policy_events", None)
        if events is None:
            events = self.shared_policy.admission_events()
        host_cache = {}
        with self.db.connect() as conn:
            for source in self.by_id.values():
                state = conn.execute(
                    "SELECT * FROM remediation_sources WHERE source_id=?", (source.id,)
                ).fetchone()
                from jobagg.worker_summaries import counts
                rows = [{key: row[key] for key in ("kind", "status", "n")} for row in counts(conn, source.id)]
                sources.append(
                    {
                        "source_id": source.id,
                        "fetch_health": source_health(conn, source.id, hold=(
                            source_holds.get(source.id)
                            or self.host_state((urlsplit(source.base_url).hostname or "").lower()).get("stopped")
                        )),
                        "attachments_in_scope": source.extra.get("fetch_attachments", True) is not False,
                        "status": "incomplete",
                        "reason": ("Independent whole public text/metadata scope and live publication are not certified; supplementary attachments excluded."
                                   if source.extra.get("fetch_attachments", True) is False else
                                   "Independent whole public text/metadata/document scope and live publication are not certified by this worker."),
                        "listed_current": len(json.loads(state["listing_ids"] or "[]")),
                        "listing_observed": state["last_list_at"] is not None,
                        "listing_observed_at": state["last_list_at"],
                        "enumeration": json.loads(state["listing_proof"] or "{}"),
                        "task_counts": rows,
                        "blocking_examples": [
                            dict(row)
                            for row in conn.execute(
                                "SELECT kind,external_id,status,last_error FROM remediation_tasks WHERE source_id=? AND status IN('blocked','interrupted','storage_held','unavailable_pending_inventory','listing_detail_conflict') ORDER BY discovered_at LIMIT 20",
                                (source.id,),
                            )
                        ],
                        "earliest_pending_queue_time": conn.execute(
                            "SELECT min(eligible_at) FROM remediation_tasks WHERE source_id=? AND status='pending'",
                            (source.id,),
                        ).fetchone()[0],
                        "source_policy_hold": source_holds.get(source.id),
                        "health": worker_source_health(
                            self, conn, source, state, now=report_now,
                            source_hold=source_holds.get(source.id),
                            events=events.get(source.id, []), host_cache=host_cache,
                        ),
                        "capability": source_capability(source),
                    }
                )
        eligible_sources = [row for row in sources if row["health"]["eligible_pending_tasks"] > 0]
        self._eligible_counts = (len(eligible_sources),
            len({host for row in eligible_sources for host in row["health"].get("pending_eligible_hosts", [])}),
            sum(row["health"]["eligible_pending_tasks"] for row in sources))
        self._report_eligibility_complete = True
        return {
            "schema_version": 1,
            "run_id": request["run_id"] if request else self.tick_id,
            "source_manifest_sha256": request["source_manifest_sha256"]
            if request
            else self.binding["registry_sha256"],
            "generated_at": utc(),
            "status": "incomplete",
            "sources": sources,
            "source_health_summary": {
                "observed_at": report_now,
                "enabled_sources": len(sources),
                "stale_listing_sources": [s["source_id"] for s in sources if s["health"]["listing_stale"]],
                "listing_sources_requiring_review": [s["source_id"] for s in sources
                    if s["health"]["listing"]["next_permitted_attempt_at"] is None],
                "scope": "Listing freshness and eligibility; separate from publication and full-detail completeness.",
            },
            "disabled_sources": [source_capability(s) for s in self.sources if not s.enabled],
            "binding": self.binding,
            "database": str(self.db.path),
            "completeness_certified": False,
            "live_publication_performed": False,
        }

    def tick(self, *, execute=False, request=None):
        if not execute:
            return self._tick_admitted(execute=False, request=request)
        lease_raw = os.environ.get("JOBAGG_STORAGE_BUDGET_LEASE")
        limits_raw = os.environ.get("JOBAGG_STORAGE_CYCLE_LIMITS")
        requested = (request or {}).get("storage_limits")
        if bool(lease_raw) != bool(limits_raw) or (requested is not None and not lease_raw):
            raise StorageBudgetError("storage_lease_and_limits_required_together")
        if lease_raw:
            lease, limits = json.loads(lease_raw), json.loads(limits_raw)
            if request and (request.get("run_id") != lease.get("cycle_id") or requested != limits):
                raise StorageBudgetError("storage_request_lease_binding_differs")
            self.storage = WorkerStorage(self, lease, limits)
        from jobagg import runtime_clock, publication_deadline
        self._phase_clock = getattr(self, '_entry_continuous_budget', None) or runtime_clock.budget(
            self.max_seconds, parent=publication_deadline.current_budget())
        with admitted_file_writes(self.storage.file_write if self.storage else None), publication_deadline.continuous_budget(self._phase_clock):
            try:
                return self._tick_admitted(execute=True, request=request)
            finally:
                # Neither a read budget nor a writer capability survives release
                # of the shared owner. Standalone maintenance must re-admit.
                self._report_deadline = self._sql_deadline = None
                self._elapsed_deadline = float("inf")
                self._runtime_run_id = None
                self._runtime_owner_fd = None
                self._phase_clock = self._work_clock = self._work_deadline = None
                self._runtime_contract_value = None
                self._runtime_request_sha256 = None
                if getattr(self.shared_policy, '_native', None) is not None:
                    self.shared_policy._native.owner_fd = None
                    self.shared_policy._native.storage = None
                    self.shared_policy._native.check_deadline = lambda: None
                self._policy_due_cache = {}
                if hasattr(self, "_report_policy_events"):
                    del self._report_policy_events
                if hasattr(self.shared_policy, "_batch_events"):
                    del self.shared_policy._batch_events

    def _tick_admitted(self, *, execute=False, request=None):
        if request is not None and (
            type(request.get("parallel_sources", 1)) is not int
            or request.get("parallel_sources", 1) != self.parallel_sources
        ):
            raise ValueError("Dispatcher request and worker parallel source limit differ")
        if not execute:
            return self.preview()
        if request and request.get('expected_implementation_sha256') is not None and request['expected_implementation_sha256']!=self.binding['implementation_sha256']:
            raise ValueError('Worker implementation differs from admitted runtime contract')
        self._elapsed_deadline = min(time.monotonic() + self.max_seconds,
                                     getattr(self, "_entry_elapsed_deadline", float("inf")))
        self._runtime_run_id = (request or {}).get("run_id")
        self._runtime_request_sha256=getattr(self,'_dispatcher_request_sha256',None) or hashlib.sha256(dump(request or {}).encode()).hexdigest()
        self.tick_id = str(uuid4())
        tick_started_at = utc()
        self._task_intervals = []
        self._request_intervals = []
        self._eligible_counts = (0, 0, 0)
        self._eligible_peaks = (0, 0)
        self._policy_due_cache = {}
        deadline = min(time.time() + self.max_seconds, getattr(self, "_entry_wall_deadline", float("inf")))
        if request:
            deadline = min(deadline, epoch(request["deadline_at"]))
        if deadline <= time.time():
            raise ValueError("Request deadline already passed")
        self.check_deadline(deadline)
        self._report_deadline = deadline
        self._sql_deadline = deadline
        with shared_owner(self.shared_lock) as owner_fd:
            self._runtime_owner_fd = owner_fd
            self._runtime_contract_value=(request or {}).get('runtime_contract')
            if self._runtime_contract_value:
                from jobagg.runtime_contract import verify
                from jobagg.verification_budget import scope
                with scope(self._elapsed_deadline,deadline):
                    verify(self._runtime_contract_value,package_root=Path(__file__).parent.parent)
            if self.storage:
                self.storage.activate((request or {}).get("semantic_history_policy"))
            if self.db.path.exists():
                from jobagg.runtime_authority import state as runtime_state
                with self.db.connect() as conn:
                    active=runtime_state(conn)
                    if active and json.loads(active['control_json']).get('publication_scope') is not None and (not self._runtime_contract_value or not getattr(self,'_dispatcher_request_sha256',None)):
                        raise ValueError('Native dispatcher runtime contract required before fetching')
                    if active and (active['phase']!='worker' or active['run_id']!=self._runtime_run_id or active['revision']!=active['start_revision']):
                        raise ValueError('Native worker batch already mutated; controller must reconcile acceptance/claims before fetching')
            self.initialize()
            self.shared_policy.begin_batch(check_deadline=lambda: self.check_deadline(deadline),
                owner_fd=owner_fd, audit=(request or {}).get('policy_audit'), storage=self.storage)
            register_builtin_adapters()
            self.seed_listings()
            outcomes = []
            detail_tasks = 0
            # The outer dispatcher deadline includes startup and reporting. Stop
            # request/selection work early enough to persist a truthful report.
            report_reserve = min(15.0, self.max_seconds * 0.1)
            work_deadline = deadline - report_reserve
            from jobagg import runtime_clock, publication_deadline
            self._work_deadline = work_deadline
            self._work_clock = runtime_clock.budget(max(0,work_deadline-time.time()),
                                                   parent=publication_deadline.current_budget())
            self._sql_deadline = work_deadline
            stop_reason = "task_budget"
            if self.parallel_sources > 1:
                outcomes, stop_reason = self.run_parallel(work_deadline)
            else:
                for _ in range(self.max_tasks):
                    excluded = {"detail"} if detail_tasks >= self.max_detail_tasks else set()
                    try:
                        self.check_deadline(work_deadline)
                        task = self.choose(excluded_kinds=excluded, deadline=work_deadline)
                        self.check_deadline(work_deadline)
                        if not task:
                            stop_reason = "no_eligible_task_within_kind_budgets"
                            break
                        self.check_deadline(work_deadline)
                        outcome = self.perform(task, work_deadline)
                    except StorageBudgetDeferred:
                        stop_reason = "storage_deferred"
                        break
                    except HostIneligible as exc:
                        # No durable reservation exists for a pre-claim deferral.
                        # Break rather than spinning on the same now-ineligible task.
                        stop_reason = (
                            "work_deadline" if exc.category == "budget" else "selection_deferred"
                        )
                        break
                    if task["kind"] == "detail":
                        detail_tasks += 1
                    outcomes.append(outcome)
                    if outcome.get('verification_deferred'):
                        stop_reason='work_deadline'
                        break
            if (
                self.binding["registry_sha256"] != sha(self.registry)
                or self.binding["robots_sha256"] != sha(self.robots_path)
                or self.binding["implementation_sha256"] != implementation_hash()
            ):
                raise ValueError("Input code/config changed during tick; no acceptance report")
            if self._runtime_contract_value:
                from jobagg.runtime_contract import verify
                from jobagg.verification_budget import scope
                with scope(self._elapsed_deadline,deadline):
                    verify(self._runtime_contract_value,package_root=Path(__file__).parent.parent)
            self._sql_deadline = deadline
            self._report_policy_events = self.shared_policy.admission_events()
            self.shared_policy.finish_batch(check_deadline=lambda: self.check_deadline(deadline))
            self.check_deadline(deadline)
            report = self.report(request)
            native_policy = self.shared_policy.native_authority()
            if native_policy is not None:
                report["shared_policy_json_export"] = native_policy.json_export_status()
            from jobagg.runtime_clock import inherited
            if inherited() is not None:
                report["entry_clock"] = inherited()
            if self._runtime_contract_value:
                report['runtime_contract_sha256']=self._runtime_contract_value['sha256']
            concurrency = self.concurrency_report(tick_started_at, deadline)
            concurrency_path = self.workspace / "ticks" / f"{self.tick_id}.concurrency.json"
            write_json(concurrency_path, concurrency)
            report["concurrency"] = concurrency
            report["concurrency_receipt"] = {"path": str(concurrency_path), "sha256": sha(concurrency_path)}
            report["tick_outcomes"] = outcomes
            if self.storage:
                report["storage"] = self.storage.report()
                report["storage_held_tasks"] = sum(row.get("status") == "storage_held" for row in outcomes)
            report["tick_stop_reason"] = stop_reason
            report["deadline_at"] = datetime.fromtimestamp(deadline, UTC).isoformat()
            report["work_deadline_at"] = datetime.fromtimestamp(work_deadline, UTC).isoformat()
            report["report_reserve_seconds"] = report_reserve
            write_json(self.workspace / "ticks" / f"{self.tick_id}.json", report)
            return report


def validate_request(path, report, registry):
    request = json.loads(Path(path).read_text())
    if (
        request.get("schema_version") != 1
        or Path(request["report_path"]).resolve() != Path(report).resolve()
    ):
        raise ValueError("Invalid dispatcher request/report binding")
    manifest = Path(request["source_manifest_path"]).resolve()
    if sha(manifest) != request["source_manifest_sha256"]:
        raise ValueError("Source manifest changed")
    registry = Path(registry).resolve()
    bound = request["source_registry"]
    if registry != Path(bound["path"]).resolve() or sha(registry) != bound["sha256"]:
        raise ValueError("Registry differs from dispatcher request")
    configured = load_sources(registry)
    expected = {s.id for s in configured if s.enabled}
    manifest_sources = json.loads(manifest.read_text())["sources"]
    if len({s["source_id"] for s in manifest_sources}) != len(manifest_sources) or any(
        type(s["enabled"]) is not bool for s in manifest_sources
    ):
        raise ValueError("Duplicate/malformed manifest source scope")
    if {s.id: s.enabled for s in configured} != {
        s["source_id"]: s["enabled"] for s in manifest_sources
    }:
        raise ValueError("Disabled/source universe differs from dispatcher registry")
    if expected != {s["source_id"] for s in manifest_sources if s["enabled"]} or sorted(
        expected
    ) != sorted(request["expected_source_ids"]):
        raise ValueError("Enabled source universe differs from dispatcher request")
    if (
        not request.get("run_id")
        or epoch(request["started_at"]) > time.time() + 1
        or epoch(request["deadline_at"]) <= time.time()
        or epoch(request["deadline_at"]) <= epoch(request["started_at"])
    ):
        raise ValueError("Stale/future dispatcher request")
    return request


def main(argv=None):
    from jobagg import runtime_clock
    entered_continuous = runtime_clock.now()
    entered_monotonic, entered_wall = time.monotonic(), time.time()
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--registry", type=Path, required=True)
    p.add_argument("--robots", type=Path, required=True)
    p.add_argument("--workspace", type=Path, required=True)
    p.add_argument("--shared-lock", type=Path, required=True)
    p.add_argument("--max-tasks", type=int, default=3)
    p.add_argument("--max-seconds", type=float, default=300)
    p.add_argument("--max-detail-tasks", type=int)
    p.add_argument("--parallel-sources", type=int, default=1)
    p.add_argument("--max-requests-per-task", type=int, default=200)
    p.add_argument(
        "--policy-bootstrap",
        type=Path,
        help="Reviewed migration for first shared policy initialization only",
    )
    p.add_argument("--request", type=Path)
    p.add_argument("--report", type=Path)
    p.add_argument("--execute", action="store_true")
    a = p.parse_args(argv)
    from jobagg.publication_deadline import continuous_budget, current_budget
    phase = runtime_clock.budget(a.max_seconds, parent=current_budget(),
                                started=entered_continuous, started_wall=entered_wall)
    with continuous_budget(phase):
        return _run_worker(a, entered_monotonic, entered_wall, phase)


def _run_worker(a, entered_monotonic, entered_wall, phase):
    try:
        from jobagg.publication_deadline import expired
        if expired(None):
            raise HostIneligible("Worker entry budget expired", category="budget")
        if bool(a.request) != bool(a.report):
            raise ValueError("--request and --report must be supplied together")
        request = validate_request(a.request, a.report, a.registry) if a.request else None
        worker = Worker(
            registry=a.registry,
            robots=a.robots,
            workspace=a.workspace,
            shared_lock=a.shared_lock,
            max_tasks=a.max_tasks,
            max_seconds=a.max_seconds,
            policy_bootstrap=a.policy_bootstrap,
            max_detail_tasks=a.max_detail_tasks,
            parallel_sources=a.parallel_sources,
            max_requests_per_task=a.max_requests_per_task,
        )
        worker._dispatcher_request_sha256=sha(a.request) if a.request else None
        worker._entry_continuous_budget = phase
        worker._entry_elapsed_deadline = entered_monotonic + a.max_seconds
        worker._entry_wall_deadline = entered_wall + a.max_seconds
        result = worker.tick(execute=a.execute, request=request)
        if a.report and a.execute:
            with admitted_file_writes(worker.storage.file_write if worker.storage else None):
                write_json(a.report, result)
        print(dump(result), flush=True)
        return 0  # Valid incomplete reports are mapped to exit2 by the outer dispatcher.
    except BlockingIOError:
        print(dump({"status": "lock_busy", "network_requests": 0}), flush=True)
        return 75
    except (ValueError, OSError, KeyError, TypeError, HostIneligible, sqlite3.Error) as exc:
        print(
            dump(
                {"status": "process_failure", "error_type": type(exc).__name__, "reason": str(exc)}
            ),
            flush=True,
        )
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
