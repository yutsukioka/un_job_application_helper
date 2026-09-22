"""Bounded deterministic source API/HTTP worker, with an isolated compatible SQLite output.

Default invocation is a read-only preview. No LLM, schedule installation, live
publication or inferred global completeness is performed by this module.
"""

from __future__ import annotations

import argparse
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import asdict, fields, replace
from datetime import UTC, datetime
import fcntl
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import ssl
import threading
import time
import urllib.error
from urllib.parse import urlsplit
from uuid import uuid4

from jobagg.adapters.base import AdapterContext, get_adapter_class
from jobagg.atomic_files import atomic_write_text
from jobagg.db import JobDatabase
from jobagg.detail_quality import DETAIL_QUALITY_COMPLETE, detail_quality_status
from jobagg.http import JobAggHTTPClient
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
from jobagg.remediation_scheduling import Task, select_due
from jobagg.remediation_concurrency import HARD_CEILING
from jobagg.robots import load_policy
from jobagg.pipelines.host_recovery import host_eligibility, classify_failure
from jobagg.vacancy_outcomes import (
    DetailIdentityMismatch, UNAVAILABLE_STATUSES, captured_unavailable,
)

VERSION = "deterministic-fetch-v1"
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


def task_key(source, kind, identity=""):
    return hashlib.sha256(f"{source}\0{kind}\0{identity}".encode()).hexdigest()


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


class SerializedWorkerDatabase(JobDatabase):
    """Per-thread database facade sharing one coordinator transaction lock."""

    def __init__(self, path, lock):
        super().__init__(path)
        self._worker_lock = lock

    @contextmanager
    def connect(self):
        with self._worker_lock, super().connect() as conn:
            yield conn

    @contextmanager
    def connection_scope(self):
        with self._worker_lock, super().connection_scope() as conn:
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
        self.tick_id = None

    @property
    def db(self):
        # Nested transactions reuse only this thread's connection. The lock also
        # serializes readers: no SQLite connection is used across worker threads.
        if not hasattr(self._database_local, "database"):
            self._database_local.database = SerializedWorkerDatabase(
                self.workspace / "jobs.sqlite3", self._database_lock
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
                counts = {
                    f"{row[0]}:{row[1]}": row[2]
                    for row in conn.execute(
                        "SELECT kind,status,count(*) FROM remediation_tasks GROUP BY kind,status"
                    )
                }
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

    def retry_input_fingerprint(self, payload):
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
        return hashlib.sha256(dump({"payload": semantic, "binding": self.binding}).encode()).hexdigest()

    def enqueue(self, conn, source, kind, identity, payload, *, due=None, refresh=False):
        now = time.time()
        key = task_key(source, kind, identity)
        old = conn.execute(
            "SELECT status,payload,eligible_at,last_error,receipt FROM remediation_tasks WHERE task_id=?",
            (key,),
        ).fetchone()
        if old and old["status"] in {"inflight", "interrupted", *UNAVAILABLE_STATUSES}:
            return key
        if old and old["status"] in {"dead_letter", "blocked"}:
            receipt = json.loads(old["receipt"] or "{}")
            # Legacy blocks lack a typed input/version binding; use the
            # evidence-checked repair command rather than guessing their cause.
            if old["status"] == "blocked" and not receipt.get("retry_input_sha256"):
                return key
            failed_input = receipt.get("retry_input_sha256") or self.retry_input_fingerprint(json.loads(old["payload"]))
            if failed_input == self.retry_input_fingerprint(payload):
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
            if old["status"] == "pending" and old["last_error"]:
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
        if attempts is None:
            attempts = self.shared_policy.events(source.id)
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
        return max(now, due)

    @staticmethod
    def check_deadline(deadline):
        if deadline is not None and time.time() >= deadline:
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
        source = self.by_id[task["source_id"]]
        payload = json.loads(task["payload"])
        # Workday's public site and first API request can use different hosts.
        # Documents already carry their exact current public request URL.
        if payload.get("url"):
            return (urlsplit(payload["url"]).hostname or "").lower()
        return source_request_host(source)

    def choose(self, *, excluded_kinds=(), excluded_sources=(), excluded_hosts=(), deadline=None):
        """Choose without multiplying policy-file reads by the pending queue size."""
        self.check_deadline(deadline)
        now = time.time()
        with self.db.connect() as conn:
            rows = [
                dict(row)
                for row in conn.execute("""SELECT t.*,coalesce(a.last_attempt_at,0) AS last_attempt_at
                    FROM remediation_tasks t LEFT JOIN
                    (SELECT task_id,max(started_at) AS last_attempt_at FROM remediation_attempts GROUP BY task_id) a
                    ON a.task_id=t.task_id WHERE t.status='pending'""")
                if row["kind"] not in excluded_kinds and row["source_id"] in self.by_id
            ]
            served = {
                row["source_id"]: row["last_service"]
                for row in conn.execute("SELECT source_id,last_service FROM remediation_sources")
            }
            kind_served = {
                row["kind"]: row["last_service"]
                for row in conn.execute(
                    "SELECT kind,max(started_at) AS last_service FROM remediation_attempts GROUP BY kind"
                )
            }
            circuit_sources = {
                row["source_id"] for row in conn.execute(
                    "SELECT source_id FROM source_circuit_breakers WHERE state IN('open','half_open')"
                )
            }
        self.check_deadline(deadline)
        # One complete history validation, never one scan per job. Claims perform
        # a fresh validation so a selection snapshot cannot authorize a request.
        events = (
            self.shared_policy.event_snapshot(check_deadline=lambda: self.check_deadline(deadline))
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
        return lookup.get(chosen.task_id) if chosen else None

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
                   "error": reason, "unavailable_rechecks": old.get("unavailable_rechecks", 0)}
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
        write_json(frame_path, frame)
        # upsert_job intentionally merges/mutates JobRecord objects. Dispatch must
        # retain the original listing shape, especially Workday externalPath.
        dispatch_payloads = json.loads(dump(frame))["jobs"]
        paths = sorted((target / "http").glob("*.json"))
        proof = verify_listing(source, jobs, paths)
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
        if receipt.get("retry_input_sha256") != self.retry_input_fingerprint(json.loads(task["payload"])):
            return 0
        return int(receipt.get("incomplete_response_count", 0))

    def retry_after_error(self, task, target, exc):
        """Retry only eligibility deferrals or proven, non-held transport failures."""
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
        if any(isinstance(error, ssl.SSLError) for error in chain):
            return None
        deferred = [error for error in chain if isinstance(error, HostIneligible)
                    and error.category in {"budget", "cooldown"}]
        if deferred and not any(isinstance(error, urllib.error.HTTPError) for error in chain):
            return {"category": "eligibility_deferred",
                    "eligible_at": max(time.time() + 60, *(error.eligible_at or 0 for error in deferred))}
        transient = any(isinstance(error, (TimeoutError, ConnectionError)) for error in chain)
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
        if (
            meta.get("state") != "failed"
            or meta.get("body_captured") is not False
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
        return self.claim(task, deadline=deadline)

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
            result = self._perform_claimed(task, deadline, token)
            interval["outcome"] = result
            return result
        except BaseException as exc:
            interval["unhandled_error_type"] = type(exc).__name__
            raise
        finally:
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
        try:
            self.check_deadline(deadline)
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
            if isinstance(exc, ValueError):
                unavailable = self.finish_unavailable(task, token, target, reason)
                if unavailable:
                    return unavailable
            retry = self.retry_after_error(task, target, exc)
            from jobagg.vacancy_outcomes import IncompleteDetailResponse
            incomplete_count = self.incomplete_response_count(task) + int(isinstance(exc, IncompleteDetailResponse))
            status = "dead_letter" if classify_failure(exc) == "local_policy" else ("pending" if retry else "blocked")
            with self.db.connection_scope() as conn:
                self.finish(
                    conn,
                    task,
                    token,
                    status,
                    {"capture_directory": str(target), "error": reason, "retry_decision": retry,
                     "incomplete_response_count": incomplete_count,
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
        snapshot_complete = True
        try:
            self.choose(deadline=deadline)
        except HostIneligible:
            snapshot_complete = False
        access = [
            meta for meta in captures
            if meta.get("status_code") in {401, 403, 429}
            or "Fresh host access challenge:" in meta.get("error", "")
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
            "eligible_backlog_remaining": self._eligible_counts[2] if snapshot_complete else 0,
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
        with self.db.connect() as conn:
            for source in self.by_id.values():
                state = conn.execute(
                    "SELECT * FROM remediation_sources WHERE source_id=?", (source.id,)
                ).fetchone()
                rows = [
                    dict(row)
                    for row in conn.execute(
                        "SELECT kind,status,count(*) AS n FROM remediation_tasks WHERE source_id=? GROUP BY kind,status",
                        (source.id,),
                    )
                ]
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
                                "SELECT kind,external_id,status,last_error FROM remediation_tasks WHERE source_id=? AND status IN('blocked','interrupted','unavailable_pending_inventory','listing_detail_conflict') ORDER BY discovered_at LIMIT 20",
                                (source.id,),
                            )
                        ],
                        "earliest_pending_queue_time": conn.execute(
                            "SELECT min(eligible_at) FROM remediation_tasks WHERE source_id=? AND status='pending'",
                            (source.id,),
                        ).fetchone()[0],
                        "source_policy_hold": source_holds.get(source.id),
                        "capability": source_capability(source),
                    }
                )
        return {
            "schema_version": 1,
            "run_id": request["run_id"] if request else self.tick_id,
            "source_manifest_sha256": request["source_manifest_sha256"]
            if request
            else self.binding["registry_sha256"],
            "generated_at": utc(),
            "status": "incomplete",
            "sources": sources,
            "disabled_sources": [source_capability(s) for s in self.sources if not s.enabled],
            "binding": self.binding,
            "database": str(self.db.path),
            "completeness_certified": False,
            "live_publication_performed": False,
        }

    def tick(self, *, execute=False, request=None):
        if request is not None and (
            type(request.get("parallel_sources", 1)) is not int
            or request.get("parallel_sources", 1) != self.parallel_sources
        ):
            raise ValueError("Dispatcher request and worker parallel source limit differ")
        preview = self.preview()
        if not execute:
            return preview
        self.tick_id = str(uuid4())
        tick_started_at = utc()
        self._task_intervals = []
        self._request_intervals = []
        self._eligible_counts = (0, 0, 0)
        self._eligible_peaks = (0, 0)
        deadline = time.time() + self.max_seconds
        if request:
            deadline = min(deadline, epoch(request["deadline_at"]))
        if deadline <= time.time():
            raise ValueError("Request deadline already passed")
        with shared_owner(self.shared_lock):
            self.initialize()
            register_builtin_adapters()
            self.seed_listings()
            outcomes = []
            detail_tasks = 0
            # The outer dispatcher deadline includes startup and reporting. Stop
            # request/selection work early enough to persist a truthful report.
            report_reserve = min(15.0, self.max_seconds * 0.1)
            work_deadline = deadline - report_reserve
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
            if (
                self.binding["registry_sha256"] != sha(self.registry)
                or self.binding["robots_sha256"] != sha(self.robots_path)
                or self.binding["implementation_sha256"] != implementation_hash()
            ):
                raise ValueError("Input code/config changed during tick; no acceptance report")
            concurrency = self.concurrency_report(tick_started_at, deadline)
            concurrency_path = self.workspace / "ticks" / f"{self.tick_id}.concurrency.json"
            write_json(concurrency_path, concurrency)
            report = self.report(request)
            report["concurrency"] = concurrency
            report["concurrency_receipt"] = {"path": str(concurrency_path), "sha256": sha(concurrency_path)}
            report["tick_outcomes"] = outcomes
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
    try:
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
        result = worker.tick(execute=a.execute, request=request)
        if a.report and a.execute:
            write_json(a.report, result)
        print(dump(result), flush=True)
        return 0  # Valid incomplete reports are mapped to exit2 by the outer dispatcher.
    except BlockingIOError:
        print(dump({"status": "lock_busy", "network_requests": 0}), flush=True)
        return 75
    except (ValueError, OSError, KeyError, TypeError) as exc:
        print(
            dump(
                {"status": "process_failure", "error_type": type(exc).__name__, "reason": str(exc)}
            ),
            flush=True,
        )
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
