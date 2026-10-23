"""Shared durable pacing/attempt ledger across isolated database generations.

The caller holds the exact shared writer lock. Creating the first store requires
an explicit reviewed bootstrap; silently resetting prior writer state is refused.
"""

from datetime import datetime, UTC
import hashlib
import json
import re
from pathlib import Path
import time
from functools import wraps
import threading

from jobagg.atomic_files import atomic_write_text


# The process-wide owner still excludes other dispatchers. Its organization
# threads also need mutual exclusion for the shared read/modify/write index.
_STORE_LOCKS = {}
_STORE_LOCKS_GUARD = threading.Lock()


def _synchronized(method):
    @wraps(method)
    def call(self, *args, **kwargs):
        with self._thread_lock:
            return method(self, *args, **kwargs)
    return call


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save(path, value):
    atomic_write_text(path, json.dumps(value, sort_keys=True, ensure_ascii=False) + "\n")


class SharedPolicy:
    def __init__(self, owner, source_ids, bootstrap=None):
        self.owner = Path(owner).absolute()
        self.root = Path(str(self.owner) + ".worker-policy")
        self.marker = Path(str(self.owner) + ".worker-policy.json")
        self.source_ids = set(source_ids)
        self.bootstrap = Path(bootstrap).resolve() if bootstrap else None
        with _STORE_LOCKS_GUARD:
            self._thread_lock = _STORE_LOCKS.setdefault(str(self.root), threading.RLock())

    @_synchronized
    def inspect(self):
        if self.root.resolve() != self.root or self.marker.resolve() != self.marker:
            raise ValueError("Shared policy paths must not be symlinked/aliased")
        if self.marker.exists():
            marker = json.loads(self.marker.read_text())
            manifest = self.root / "bootstrap.json"
            if (
                marker.get("shared_lock") != str(self.owner)
                or not manifest.exists()
                or digest(manifest) != marker.get("bootstrap_sha256")
            ):
                raise ValueError(
                    "Shared policy bootstrap missing/changed; no quota reset permitted"
                )
            value = json.loads(manifest.read_text())
            if not self.source_ids.issubset(set(value["scope_source_ids"])):
                raise ValueError("Source scope not covered by shared policy migration")
            if (
                not (self.root / "attempts").is_dir()
                or not (self.root / "hosts").is_dir()
                or not (self.root / "attempt_index.json").exists()
            ):
                raise ValueError("Shared policy history removed; reviewed recovery required")
            return {"status": "existing", "path": str(self.root)}
        if self.root.exists():
            raise ValueError("Unsealed shared policy store requires review")
        if not self.bootstrap:
            return {"status": "reviewed_bootstrap_required", "path": str(self.root)}
        value = json.loads(self.bootstrap.read_text())
        if value.get("schema_version") != 1 or value.get("shared_lock") != str(self.owner):
            raise ValueError("Policy bootstrap does not bind the exact shared owner")
        if (
            value.get("prior_writers_reviewed") is not True
            or value.get("no_unmigrated_policy_state") is not True
        ):
            raise ValueError("Policy bootstrap requires explicit prior-writer migration review")
        stamp = datetime.fromisoformat(value["reviewed_at"].replace("Z", "+00:00"))
        if stamp.tzinfo is None or not -1 <= time.time() - stamp.timestamp() <= 3600:
            raise ValueError("Policy migration review is stale/future")
        if not self.source_ids.issubset(set(value["scope_source_ids"])):
            raise ValueError("Bootstrap omits an enabled source")
        for evidence in value["evidence"]:
            if digest(evidence["path"]) != evidence["sha256"]:
                raise ValueError("Policy migration input evidence changed")
        ids = set()
        for event in value["detail_attempts"]:
            if event["attempt_id"] in ids or event["source_id"] not in value["scope_source_ids"]:
                raise ValueError("Duplicate/unscoped policy attempt")
            ids.add(event["attempt_id"])
            start = float(event["started_at"])
            end = float(event.get("finished_at") or start)
            if not 0 <= start <= end <= time.time() + 1:
                raise ValueError("Malformed original policy attempt time")
        for host, state in value["host_states"].items():
            if host != host.lower() or not host or "/" in host or ":" in host:
                raise ValueError("Malformed actual host policy key")
            if not isinstance(state, dict) or not isinstance(state.get("stopped", False), bool):
                raise ValueError("Malformed original host policy state")
        for name, state in value.get("host_states_by_digest", {}).items():
            if not re.fullmatch(r"host-[0-9a-f]{24}", name) or not isinstance(state, dict):
                raise ValueError("Malformed opaque original host policy key")
        if not isinstance(value["source_holds"], dict):
            raise ValueError("Malformed source policy holds")
        return {
            "status": "reviewed_bootstrap_ready",
            "path": str(self.root),
            "bootstrap_sha256": digest(self.bootstrap),
        }

    @_synchronized
    def initialize(self):
        state = self.inspect()
        if state["status"] == "existing":
            return
        if state["status"] != "reviewed_bootstrap_ready":
            raise ValueError(
                "First execution requires --policy-bootstrap from reviewed prior writer state"
            )
        value = json.loads(self.bootstrap.read_text())
        self.root.mkdir(parents=True)
        (self.root / "attempts").mkdir()
        (self.root / "hosts").mkdir()
        save(self.root / "attempt_index.json", [])
        # Preserve the submitted exact bytes and every original attempt time.
        atomic_write_text(self.root / "bootstrap.json", self.bootstrap.read_text())
        for event in value["detail_attempts"]:
            path = (
                self.root
                / "attempts"
                / (hashlib.sha256(event["attempt_id"].encode()).hexdigest() + ".json")
            )
            save(path, {**event, "kind": "detail", "origin": "reviewed_prior_writer_migration"})
        save(
            self.root / "attempt_index.json",
            sorted(p.name for p in (self.root / "attempts").glob("*.json")),
        )
        host_states = {
            "host-" + hashlib.sha256(host.encode()).hexdigest()[:24]: state
            for host, state in value["host_states"].items()
        }
        for name, original in value.get("host_states_by_digest", {}).items():
            if name in host_states and host_states[name] != original:
                raise ValueError("Conflicting original host state")
            host_states[name] = original
        for name, original in host_states.items():
            path = self.root / "hosts" / name
            save(path.with_suffix(".json"), original)
            atomic_write_text(path.with_suffix(".lock"), str(original.get("last_request_at", 0)))
        save(self.root / "source_holds.json", value["source_holds"])
        save(
            self.marker,
            {
                "version": 1,
                "shared_lock": str(self.owner),
                "bootstrap_sha256": digest(self.root / "bootstrap.json"),
                "initialized_at": datetime.now(UTC).isoformat(),
            },
        )

    @_synchronized
    def event_snapshot(self, *, check_deadline=None):
        """Validate and read history once per selection while the caller owns the lock.

        Include unindexed reservation files: a crash between the durable reservation
        and index update must never refund an attempt. This snapshot is deliberately
        not cached across selections or used as authority for a later claim.
        """
        check = check_deadline or (lambda: None)
        check()
        expected = json.loads((self.root / "attempt_index.json").read_text())
        if (
            not isinstance(expected, list)
            or any(
                not isinstance(name, str) or not re.fullmatch(r"[0-9a-f]{64}\.json", name)
                for name in expected
            )
            or len(expected) != len(set(expected))
        ):
            raise ValueError("Malformed shared attempt history index")
        paths = {path.name: path for path in (self.root / "attempts").glob("*.json")}
        if not set(expected).issubset(paths):
            raise ValueError("Shared attempt history removed; no quota reset permitted")
        grouped = {}
        for index, path in enumerate(paths.values()):
            if index % 64 == 0:
                check()
            if not path.is_file() or path.is_symlink():
                raise ValueError("Shared attempt history removed/aliased; no quota reset permitted")
            event = json.loads(path.read_text())
            if event["kind"] == "detail":
                grouped.setdefault(event["source_id"], []).append(event)
        for events in grouped.values():
            events.sort(key=lambda event: event["started_at"])
        check()
        return grouped

    def events(self, source_id):
        return self.event_snapshot().get(source_id, [])

    @_synchronized
    def reserve(self, attempt_id, source_id, kind, started_at, workspace, task_id):
        path = self.root / "attempts" / (hashlib.sha256(attempt_id.encode()).hexdigest() + ".json")
        if path.exists():
            raise ValueError("Durable shared attempt already exists")
        save(
            path,
            {
                "attempt_id": attempt_id,
                "source_id": source_id,
                "kind": kind,
                "started_at": started_at,
                "finished_at": None,
                "workspace": str(workspace),
                "task_id": task_id,
                "status": "reserved",
            },
        )
        index = self.root / "attempt_index.json"
        save(index, sorted({*json.loads(index.read_text()), path.name}))

    @_synchronized
    def finish(self, attempt_id, status):
        path = self.root / "attempts" / (hashlib.sha256(attempt_id.encode()).hexdigest() + ".json")
        event = json.loads(path.read_text())
        event.update(finished_at=time.time(), status=status)
        save(path, event)

    @_synchronized
    def source_hold(self, source_id):
        return json.loads((self.root / "source_holds.json").read_text()).get(source_id)
