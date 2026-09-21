"""Local dispatcher attempt receipts, condition-bound failure holds and mount gate."""

from __future__ import annotations

from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import stat
import tempfile
import uuid


def utc():
    return datetime.now(timezone.utc).isoformat()


def lexical(base, value):
    return Path(os.path.abspath(os.path.join(str(base), str(value))))


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".tick-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(value, stream, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def sha(path):
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def file_condition(path, *, contents=False):
    path = Path(path)
    result = {"path": str(path), "resolved_path": str(path.resolve())}
    try:
        s = path.stat()
        result.update(
            exists=True,
            size=s.st_size,
            mtime_ns=s.st_mtime_ns,
            ctime_ns=s.st_ctime_ns,
            device=s.st_dev,
            inode=s.st_ino,
            regular=stat.S_ISREG(s.st_mode),
        )
        if contents and stat.S_ISREG(s.st_mode):
            result["sha256"] = sha(path)
    except FileNotFoundError:
        result["exists"] = False
    return result


def storage_check(config):
    guard = config.get("storage_guard")
    if not guard:
        return {"configured": False, "ready": True}
    mount = Path(guard["mount_root"])
    sentinel = Path(guard["sentinel_path"])
    if not os.path.ismount(mount):
        raise ValueError("Configured storage volume is not mounted: " + str(mount))
    if not sentinel.resolve().is_relative_to(mount.resolve()):
        raise ValueError("Storage sentinel escapes configured volume")
    if (
        sentinel.is_symlink()
        or not sentinel.is_file()
        or sha(sentinel) != guard["sentinel_sha256"]
    ):
        raise ValueError("Storage sentinel identity is absent or changed")
    if sentinel.stat().st_dev != mount.stat().st_dev:
        raise ValueError("Storage sentinel is on another filesystem")
    if config["attempt_state_dir"].resolve().is_relative_to(mount.resolve()):
        raise ValueError("Attempt receipts must remain outside the guarded volume")
    for key in ("state_dir", "shared_lock_path", "publication_worker_database"):
        path = config.get(key)
        if path is not None and not path.resolve().is_relative_to(mount.resolve()):
            raise ValueError("Guarded runtime path escapes configured volume: " + key)
    for key in ("state_dir", "shared_lock_path", "publication_worker_database"):
        path = config.get(key)
        if path is not None and not path.parent.is_dir():
            raise ValueError("Guarded runtime parent is missing: " + key)
    for argv in (config.get("worker_argv", []), config.get("publication_argv", [])):
        for index, arg in enumerate(argv):
            if arg in {
                "--workspace",
                "--worker-database",
                "--output-dir",
                "--state-dir",
                "--shared-lock",
            }:
                if (
                    index + 1 >= len(argv)
                    or not Path(argv[index + 1]).is_absolute()
                    or not Path(argv[index + 1])
                    .resolve()
                    .is_relative_to(mount.resolve())
                ):
                    raise ValueError(
                        "Guarded command runtime path escapes configured volume: " + arg
                    )
                if not Path(argv[index + 1]).parent.is_dir():
                    raise ValueError(
                        "Guarded command runtime parent is missing: " + arg
                    )
    reserve = guard.get("min_free_bytes", 0)
    free = os.statvfs(mount)
    available = free.f_bavail * free.f_frsize
    if available < reserve:
        raise ValueError("Configured storage free space is below static reserve")
    # Nonexistent mounted volume never gets created and no data path falls back.
    return {
        "configured": True,
        "ready": True,
        "mount_root": str(mount),
        "sentinel_sha256": guard["sentinel_sha256"],
        "device": mount.stat().st_dev,
        "available_bytes": available,
        "min_free_bytes": reserve,
        "reserve_is_estimate_guarantee": False,
    }


def start_attempt(config_path, raw):
    base = config_path.parent
    default = lexical(base, raw.get("state_dir", "dispatcher-attempts"))
    directory = lexical(base, raw.get("attempt_state_dir", str(default)))
    guard = raw.get("storage_guard")
    if guard:
        mount = lexical(base, guard["mount_root"])
        if "attempt_state_dir" not in raw or directory.resolve().is_relative_to(
            mount.resolve()
        ):
            raise ValueError(
                "Guarded storage requires independent internal attempt_state_dir"
            )
    attempt = directory / "attempts" / str(uuid.uuid4())
    value = {
        "schema_version": 1,
        "attempt_id": attempt.name,
        "started_at": utc(),
        "pid": os.getpid(),
        "config": file_condition(config_path, contents=True),
        "status": "started",
    }
    write(attempt / "intent.json", value)
    update_health(directory, value)
    return attempt, value


def update_health(directory, value):
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / "health.lock").open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        path = directory / "health.json"
        health = (
            json.loads(path.read_text()) if path.exists() else {"schema_version": 1}
        )
        latest = value["started_at"] >= health.get("last_invocation_at", "")
        result = value.get("result") or {}
        completed_at = result.get("finished_at") or value.get("finished_at")
        if latest:
            health.update(
                last_invocation_at=value["started_at"],
                last_attempt_id=value["attempt_id"],
                last_attempt_status=value["status"],
                invocation_alive=True,
            )
            if value.get("finished_at"):
                health["last_attempt_finished_at"] = value["finished_at"]
            health["last_reasons"] = result.get("reasons", [])[:5]
        if (
            result.get("worker_exit_code") == 0
            and completed_at
            and completed_at >= health.get("last_worker_success_at", "")
        ):
            health["last_worker_success_at"] = completed_at
        publication_event = result.get("publication_status") in {
            "published",
            "noop",
            "unknown_requires_review_no_replay",
            "incomplete",
        } or result.get("unresolved_publication")
        if (
            publication_event
            and completed_at
            and completed_at >= health.get("last_publication_event_at", "")
        ):
            health["last_publication_event_at"] = completed_at
            if result.get("publication_status") in {"published", "noop"}:
                health["last_publication_complete_at"] = completed_at
                health["last_publication_run_id"] = result.get("run_id")
                health["unresolved_publication"] = False
            else:
                health["unresolved_publication"] = True
        health["publication_currently_confirmed"] = (
            bool(health.get("last_publication_complete_at"))
            and not health.get("unresolved_publication", False)
            and health.get("last_attempt_status")
            not in {"process_failure", "unchanged_failure_hold"}
        )
        health["operational_ready"] = (
            health.get("last_attempt_status") in {"complete", "incomplete"}
            and health["publication_currently_confirmed"]
        )
        health["note"] = (
            "Invocation timestamp is liveness only; publication success is separate and never inferred from cron activity."
        )
        write(path, health)


def finish_attempt(attempt, intent, result):
    value = {
        **intent,
        "status": result["status"],
        "finished_at": utc(),
        "result": result,
    }
    write(attempt / "outcome.json", value)
    update_health(attempt.parent.parent, value)


def condition_fingerprint(config, runner_path):
    paths = {
        Path(runner_path),
        Path(__file__),
        config["_config_path"],
        config["source_manifest_path"],
        config["_registry_path"],
    }
    for cwd in {config["worker_cwd"], config["publication_cwd"]}:
        package = cwd / "jobagg"
        if package.is_dir():
            paths.update(package.rglob("*.py"))
    for arg in config["worker_argv"] + config["publication_argv"]:
        if arg.endswith(".py") and Path(arg).is_file():
            paths.add(Path(arg))
    refs = [file_condition(p, contents=True) for p in sorted(paths)]
    for p in sorted((config["state_dir"] / "runs").glob("*/publication_intent.json")):
        outcome = p.with_name("outcome.json")
        if not outcome.exists():
            continue
        o = json.loads(outcome.read_text())
        if (
            o.get("publication_status")
            not in {"unknown_requires_review_no_replay", "incomplete"}
            or p.with_name("publication_resolution.json").exists()
        ):
            continue
        refs.extend(
            file_condition(p.with_name(name), contents=True)
            for name in (
                "publication_intent.json",
                "publication_request.json",
                "outcome.json",
                "acceptance.json",
                "publication_generation_binding.json",
            )
        )
    argv = config.get("publication_argv", [])
    for index, arg in enumerate(argv[:-1]):
        if arg == "--output-dir":
            gate_path = Path(argv[index + 1]) / ".jobagg-publication-state.json"
            refs.append(file_condition(gate_path, contents=True))
            if gate_path.is_file():
                try:
                    gate = json.loads(gate_path.read_text())
                    plan_path = gate.get("plan_path")
                    if isinstance(plan_path, str) and Path(plan_path).is_absolute():
                        refs.append(file_condition(plan_path, contents=True))
                except (ValueError, AttributeError):
                    pass  # Invalid gate bytes are still fingerprinted; validator holds.
    db = config.get("publication_worker_database")
    if db:
        # Stat changes re-arm strict validation; hashes remain the validator's duty.
        refs.extend(file_condition(str(db) + suffix) for suffix in ("", "-wal", "-shm"))
    guard = config.get("storage_guard")
    if guard:
        refs.append(file_condition(guard["sentinel_path"], contents=True))
        refs.append({"mounted": os.path.ismount(guard["mount_root"])})
    # A storage-space change can unblock an ENOSPC failure without editing history.
    try:
        free = os.statvfs(config["state_dir"])
        reserve = (config.get("storage_guard") or {}).get("min_free_bytes", 0)
        available = free.f_bavail * free.f_frsize
        refs.append(
            {"reserve_met": available >= reserve}
            if reserve
            else {"available_GiB": available // (1024**3)}
        )
    except FileNotFoundError:
        pass
    payload = json.dumps(refs, sort_keys=True).encode()
    return hashlib.sha256(payload).hexdigest()


def failure_hold(config, fingerprint):
    path = config["attempt_state_dir"] / "failure_condition.json"
    if not path.exists():
        return None
    prior = json.loads(path.read_text())
    if (
        prior.get("condition_sha256") == fingerprint
        and prior.get("unchanged_failures", 0) >= 3
    ):
        return {
            "status": "unchanged_failure_hold",
            "condition_sha256": fingerprint,
            "unchanged_failures": prior["unchanged_failures"],
            "reasons": prior["reasons"],
            "unresolved_publication": prior.get("unresolved_publication", False),
            "reason": "Three unchanged failures; awaiting changed config/code/evidence/storage condition before retry.",
        }
    return None


def record_failure(config, fingerprint, result):
    path = config["attempt_state_dir"] / "failure_condition.json"
    previous = json.loads(path.read_text()) if path.exists() else {}
    if result["status"] != "process_failure":
        if result["status"] not in {
            "unchanged_failure_hold",
            "lock_busy",
            "maintenance_paused",
        }:
            write(
                path,
                {
                    "condition_sha256": fingerprint,
                    "unchanged_failures": 0,
                    "updated_at": utc(),
                },
            )
        return
    reasons = result.get("reasons", [])
    same = (
        previous.get("condition_sha256") == fingerprint
        and previous.get("reasons") == reasons
    )
    write(
        path,
        {
            "condition_sha256": fingerprint,
            "unchanged_failures": previous.get("unchanged_failures", 0) + 1
            if same
            else 1,
            "reasons": reasons,
            "unresolved_publication": result.get("unresolved_publication", False),
            "updated_at": utc(),
        },
    )


def health_view(directory, *, max_age_seconds=1800):
    path = Path(directory) / "health.json"
    if not path.exists():
        return {
            "status": "health",
            "invocation_alive": False,
            "publication_currently_confirmed": False,
            "reason": "No durable invocation record",
            "writes_performed": False,
        }
    value = json.loads(path.read_text())
    current = datetime.now(timezone.utc)
    last = datetime.fromisoformat(value["last_invocation_at"])
    age = (current - last).total_seconds()
    value.update(
        status="health",
        invocation_alive=0 <= age <= max_age_seconds,
        invocation_age_seconds=age,
        evaluated_at=current.isoformat(),
        writes_performed=False,
    )
    completed = value.get("last_publication_complete_at")
    value["publication_age_seconds"] = (
        (current - datetime.fromisoformat(completed)).total_seconds()
        if completed
        else None
    )
    return value
