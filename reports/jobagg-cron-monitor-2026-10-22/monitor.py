#!/usr/bin/env python3
"""Read-only continuous monitor for the JobAgg every-15-minute dispatcher cron.

The monitor observes the deployed dispatcher through its own durable evidence:

- attempt receipts in ``attempt_state_dir/attempts`` (one per cron tick, including
  ``lock_busy`` and maintenance outcomes);
- the nested dispatcher result in each attempt's ``outcome.json`` (worker and
  publication status, timeouts and reasons);
- ``attempt_state_dir/health.json`` for invocation/publication/retention ages;
- ``attempt_state_dir/storage-health.json`` plus ``statvfs`` on the guarded volume;
- ``attempt_state_dir/failure_condition.json`` for the three-unchanged-failure hold;
- the installed crontab entry and running ``runner.py`` processes.

It never writes to dispatcher state, databases, logs, receipts or the shared lock.
All output is written under ``--out-dir`` (default: ``runtime/`` beside this file):

- ``latest.json`` atomically stores the bounded cycle window, deduplication IDs,
  alert transitions and A8 observations in one restart-safe checkpoint.
- ``monitor.lock`` prevents concurrent observation writers for this output root.

Observation discovery is explicitly bounded; truncated history is incomplete.
No process inspection, crontab command or provider dispatch is part of sampling.

A 15-minute invocation is one bounded dispatcher batch. Counts here describe
observed ticks and publications; they do not certify complete source coverage.
See packages/jobagg/docs/change-and-review-policy.md (R1, R5, R8).
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import signal
import subprocess
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
CRON_CADENCE_SECONDS = 15 * 60
EXPECTED_CRON_FIELDS = ["*/15", "*", "*", "*", "*"]
PUBLICATION_WARN_SECONDS = 45 * 60
PUBLICATION_CRITICAL_SECONDS = 90 * 60
HEARTBEAT_WARN_SECONDS = CRON_CADENCE_SECONDS + 120
HEARTBEAT_CRITICAL_SECONDS = 2 * CRON_CADENCE_SECONDS + 120
HOLD_WARN_FAILURES = 2
HOLD_CRITICAL_FAILURES = 3
WINDOW_HOURS = 24
STALE_PROCESS_GRACE_SECONDS = 180
PUBLISHED_STATUSES = {"published", "noop"}
TERMINAL_ATTEMPT_STATUSES = {
    "complete",
    "incomplete",
    "process_failure",
    "lock_busy",
    "maintenance_paused",
    "storage_deferred",
    "accepted_work_pending",
    "publication_held",
    "unchanged_failure_hold",
}
SEVERITY_RANK = {"info": 0, "warning": 1, "critical": 2}
STOP_REQUESTED = False


def utcnow() -> datetime:
    return datetime.now(tz=UTC)


def iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat()


def parse_iso(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(UTC)


def seconds_between(start: datetime | None, end: datetime | None) -> float | None:
    if start is None or end is None:
        return None
    return (end - start).total_seconds()


def age_seconds(value: Any, now: datetime) -> float | None:
    parsed = parse_iso(value)
    return seconds_between(parsed, now)


def read_json(path: Path, default: Any = None) -> Any:
    try:
        if path.is_symlink() or path.stat().st_size > 1024 * 1024:
            return default
        return json.loads(path.read_text())
    except (FileNotFoundError, NotADirectoryError):
        return default
    except (json.JSONDecodeError, OSError):
        return default


def sha256_file(path: Path) -> str | None:
    try:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
    except OSError:
        return None


def atomic_write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".monitor-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w") as stream:
            json.dump(value, stream, indent=2, sort_keys=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def append_jsonl(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(value, sort_keys=False)
    with path.open("a") as stream:
        stream.write(line)
        stream.write("\n")
        stream.flush()


def append_log(path: Path, line: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as stream:
        stream.write(line.rstrip("\n"))
        stream.write("\n")
        stream.flush()


def gib(value: float | int | None) -> float | None:
    if value is None:
        return None
    return round(float(value) / (1024**3), 2)


def local_time(value: datetime) -> str:
    try:
        return value.astimezone().isoformat()
    except (ValueError, OSError):
        return value.isoformat()


def percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return round(ordered[0], 2)
    index = max(0, min(len(ordered) - 1, int(round(fraction * (len(ordered) - 1)))))
    return round(ordered[index], 2)


def parse_elapsed(text: str) -> float | None:
    """Parse a BSD ``ps`` etime value such as ``27:12`` or ``01-02:03:04``."""
    value = text.strip()
    days = 0
    if "-" in value:
        day_text, value = value.split("-", 1)
        try:
            days = int(day_text)
        except ValueError:
            return None
    parts = value.split(":")
    try:
        numbers = [int(part) for part in parts]
    except ValueError:
        return None
    if len(numbers) == 3:
        hours, minutes, seconds = numbers
    elif len(numbers) == 2:
        hours, minutes, seconds = 0, numbers[0], numbers[1]
    elif len(numbers) == 1:
        hours, minutes, seconds = 0, 0, numbers[0]
    else:
        return None
    return days * 86400 + hours * 3600 + minutes * 60 + seconds


def resolve_config_path(path: Path) -> Path:
    return Path(os.path.abspath(path)).resolve()


def load_config(path: Path) -> tuple[dict[str, Any], Path]:
    resolved = resolve_config_path(path)
    value = read_json(resolved)
    if not isinstance(value, dict):
        raise ValueError(f"Dispatcher config is missing or not an object: {resolved}")
    return value, resolved


def resolve_relative(base: Path, value: str | Path) -> Path:
    candidate = Path(value)
    if not candidate.is_absolute():
        candidate = base / candidate
    return Path(os.path.abspath(candidate)).resolve()


def collect_processes(config_path: Path) -> list[dict[str, Any]]:
    """Find running dispatcher runner processes bound to this exact config."""
    try:
        completed = subprocess.run(
            ["ps", "-Ao", "pid=,etime=,command="],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    target = str(config_path)
    rows: list[dict[str, Any]] = []
    for line in completed.stdout.splitlines():
        parts = line.strip().split(None, 2)
        if len(parts) < 3:
            continue
        pid, elapsed_text, command = parts
        if "runner.py" not in command or target not in command:
            continue
        if command.startswith("/bin/sh ") or command.startswith("sh -c "):
            continue
        try:
            pid_value = int(pid)
        except ValueError:
            continue
        rows.append(
            {
                "pid": pid_value,
                "elapsed_seconds": parse_elapsed(elapsed_text),
                "command_sha256": hashlib.sha256(command.encode()).hexdigest(),
            }
        )
    return rows


def collect_cron(config_path: Path) -> dict[str, Any]:
    """Read the installed crontab entry; never modify it."""
    try:
        completed = subprocess.run(
            ["crontab", "-l"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return {
            "available": False,
            "error": type(exc).__name__,
            "present": None,
            "matches_config": None,
            "cadence_fields": None,
        }
    if completed.returncode != 0:
        return {
            "available": False,
            "error": (completed.stderr or "crontab -l failed").strip()[:200],
            "present": None,
            "matches_config": None,
            "cadence_fields": None,
        }
    target = str(config_path)
    entries = [
        line.strip()
        for line in completed.stdout.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    jobagg_entries = [line for line in entries if "runner.py" in line and "jobagg" in line.lower()]
    matches = [
        line
        for line in jobagg_entries
        if target in line and line.split()[:5] == EXPECTED_CRON_FIELDS
    ]
    return {
        "available": True,
        "error": None,
        "present": bool(jobagg_entries),
        "matches_config": bool(matches),
        "cadence_fields": matches[0].split()[:5] if matches else None,
        "entry_sha256": hashlib.sha256(matches[0].encode()).hexdigest() if matches else None,
    }


def collect_storage(config: dict[str, Any], attempt_dir: Path, now: datetime) -> dict[str, Any]:
    guard = config.get("storage_guard") if isinstance(config.get("storage_guard"), dict) else {}
    health = read_json(attempt_dir / "storage-health.json", {})
    if not isinstance(health, dict):
        health = {}
    mount = guard.get("mount_root")
    free_bytes: int | None = None
    mount_present = False
    if isinstance(mount, str) and mount:
        try:
            mount_present = os.path.ismount(mount)
            if mount_present:
                stat = os.statvfs(mount)
                free_bytes = stat.f_bavail * stat.f_frsize
        except OSError:
            free_bytes = None
    reserve = health.get("reserve_bytes", guard.get("min_free_bytes"))
    headroom = health.get("publication_headroom_bytes", 0)
    if not isinstance(headroom, int):
        headroom = 0
    runway = health.get("estimated_runway_seconds")
    if not isinstance(runway, (int, float)):
        runway = None
    status = health.get("status")
    if not isinstance(status, str):
        status = "unavailable"
    critical_floor = None
    if isinstance(reserve, int):
        critical_floor = reserve + headroom
    warning_floor = None
    if isinstance(reserve, int):
        warning_floor = max(3 * reserve, 64 * (1024**3))
    critical = bool(
        status == "critical"
        or (critical_floor is not None and free_bytes is not None and free_bytes <= critical_floor)
    )
    warning = bool(
        status == "warning"
        or (warning_floor is not None and free_bytes is not None and free_bytes < warning_floor)
        or (runway is not None and runway < 48 * 3600)
    )
    retention = config.get("storage_retention") if isinstance(config.get("storage_retention"), dict) else {}
    return {
        "mount_root": mount,
        "mount_present": mount_present,
        "status": status,
        "observed_at": health.get("observed_at"),
        "observed_age_seconds": age_seconds(health.get("observed_at"), now),
        "available_bytes": free_bytes,
        "available_GiB": gib(free_bytes),
        "reported_available_GiB": gib(health.get("available_bytes")),
        "reserve_bytes": reserve,
        "reserve_GiB": gib(reserve),
        "publication_headroom_GiB": gib(headroom),
        "critical_floor_GiB": gib(critical_floor),
        "warning_floor_GiB": gib(warning_floor),
        "runway_hours": round(runway / 3600, 1) if runway is not None else None,
        "estimate_basis": health.get("estimate_basis"),
        "retention_enabled": bool(retention.get("enabled")),
        "retention_keep_completed": retention.get("keep_completed"),
        "retention_max_groups": retention.get("max_groups"),
        "retention_max_seconds": retention.get("max_seconds"),
        "critical": critical,
        "warning": warning,
    }


def collect_failure_condition(attempt_dir: Path) -> dict[str, Any]:
    value = read_json(attempt_dir / "failure_condition.json", {})
    if not isinstance(value, dict):
        value = {}
    reasons = value.get("reasons")
    if not isinstance(reasons, list):
        reasons = []
    count = value.get("unchanged_failures")
    if not isinstance(count, int):
        count = 0
    return {
        "unchanged_failures": count,
        "reasons": [str(item)[:500] for item in reasons[:6]],
        "updated_at": value.get("updated_at"),
        "unresolved_publication": bool(value.get("unresolved_publication")),
    }


def build_attempt_record(
    directory: Path,
    intent: dict[str, Any],
    outcome: dict[str, Any] | None,
    now: datetime,
) -> dict[str, Any]:
    result = outcome.get("result") if isinstance(outcome, dict) else None
    if not isinstance(result, dict):
        result = {}
    status = outcome.get("status") if isinstance(outcome, dict) else "running"
    started = parse_iso(intent.get("started_at"))
    finished = parse_iso(outcome.get("finished_at")) if isinstance(outcome, dict) else None
    terminal = bool(outcome) and finished is not None
    duration = seconds_between(started, finished if terminal else now)
    reasons = result.get("reasons")
    if not isinstance(reasons, list):
        reasons = outcome.get("reasons") if isinstance(outcome, dict) else []
    if not isinstance(reasons, list):
        reasons = [str(reasons)] if reasons else []
    reasons_text = [str(item) for item in reasons]
    worker_timeout = result.get("worker_timeout") if isinstance(result.get("worker_timeout"), dict) else None
    publication_timeout = (
        result.get("publication_timeout")
        if isinstance(result.get("publication_timeout"), dict)
        else None
    )
    publication_status = result.get("publication_status")
    publication_exit = result.get("publication_exit_code")
    publication_ok = publication_status in PUBLISHED_STATUSES and publication_exit == 0
    worker_exit = result.get("worker_exit_code")
    config_info = intent.get("config") if isinstance(intent.get("config"), dict) else {}
    lower_reasons = [item.lower() for item in reasons_text]
    return {
        "attempt_id": directory.name,
        "intent_path": str(directory / "intent.json"),
        "outcome_path": str(directory / "outcome.json") if outcome else None,
        "started_at": iso(started) if started else intent.get("started_at"),
        "finished_at": iso(finished) if finished else None,
        "duration_seconds": round(duration, 2) if duration is not None else None,
        "terminal": terminal,
        "in_progress": not terminal,
        "status": status,
        "result_status": result.get("status"),
        "phase": result.get("phase"),
        "run_id": result.get("run_id"),
        "lock_busy": status == "lock_busy",
        "dispatch": bool(result) and status != "lock_busy",
        "worker_status": result.get("worker_status"),
        "worker_exit_code": worker_exit,
        "worker_timeout": bool(worker_timeout),
        "worker_timeout_seconds": worker_timeout.get("timeout_seconds") if worker_timeout else None,
        "worker_error": worker_exit not in (None, 0) or bool(worker_timeout),
        "publication_status": publication_status,
        "publication_exit_code": publication_exit,
        "publication_timeout": bool(publication_timeout),
        "publication_timeout_seconds": (
            publication_timeout.get("timeout_seconds") if publication_timeout else None
        ),
        "publication_ok": publication_ok,
        "publication_attempted": publication_exit is not None
        or publication_status not in (None, "not_started"),
        "unresolved_publication": bool(result.get("unresolved_publication")),
        "reason_count": len(reasons_text),
        "reasons_head": [item[:300] for item in reasons_text[:5]],
        "reason_summary": str(outcome.get("reason"))[:300] if isinstance(outcome, dict) and outcome.get("reason") else None,
        "worker_incomplete_reasons": sum(1 for item in lower_reasons if "incomplete" in item),
        "blocked_or_error_reasons": sum(
            1
            for item in lower_reasons
            if any(token in item for token in ("blocked", "parser_error", "integrity", "unavailable"))
        ),
        "config_path": config_info.get("resolved_path") or config_info.get("path"),
        "config_sha256": config_info.get("sha256"),
    }


def a8_readiness(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Three natural successes, actual approved overlap and pressure downshift.

    A quiet/no-op tick is useful service evidence but never publication readback.
    Unknown origin/readback/overlap remains incomplete.
    """
    terminal = sorted((r for r in records if r.get('terminal')), key=lambda r: r.get('started_at') or '')
    streak = 0
    overlap = downshift = False
    for record in terminal:
        success = (record.get('origin') == 'natural' and record.get('worker_exit_code') == 0
                   and record.get('publication_ok') and record.get('readback_verified') is True
                   and record.get('pressure_free') is True
                   and record.get('publication_held') is None
                   and record.get('worker_stop_reason') != 'work_deadline')
        streak = streak + 1 if success else 0
        overlap = overlap or bool(success and record.get('controller_approved') is True
                                  and (record.get('parallel_sources') or 0) >= 2
                                  and (record.get('peak_active_sources') or 0) >= 2)
        downshift = downshift or record.get('safe_downshift_verified') is True
    return {'consecutive_natural_successes': streak, 'actual_approved_overlap': overlap,
            'safe_downshift_verified': downshift, 'ready': streak >= 3 and overlap and downshift}


def observation_paths(config, config_path, output):
    base = config_path.parent
    state = resolve_relative(base, config.get('state_dir', 'dispatcher-attempts'))
    attempts = resolve_relative(base, config.get('attempt_state_dir', str(state)))
    protected = {state, attempts, resolve_relative(base, config.get('worker_cwd', '.')),
                 resolve_relative(base, config.get('publication_cwd', '.'))}
    if config.get('publication_worker_database'):
        protected.add(resolve_relative(base, config['publication_worker_database']).parent)
    if config.get('shared_lock_path'):
        protected.add(resolve_relative(base, config['shared_lock_path']))
    output = output.absolute()
    if output.resolve() != output or any(output == root or output.is_relative_to(root) for root in protected):
        raise ValueError('Monitor output must be a direct separate path outside production roots')
    return state, attempts, output


def sample(config: dict[str, Any], config_path: Path, output: Path, *, max_attempts: int = 256) -> dict[str, Any]:
    """One bounded read-only observation. Writes are confined to monitor output.

    The bounded discovery window is explicit: an oversized receipt directory is
    reported as incomplete, never interpreted as no history or healthy silence.
    """
    import time
    state, attempts, output = observation_paths(config, config_path, output)
    now = utcnow()
    started = time.monotonic()
    health = read_json(attempts / 'health.json', {})
    candidates = []
    incomplete = False
    directory = attempts / 'attempts'
    index = {}
    if health.get('attempt_index_schema') == 1:
        indexed = [*health.get('active_attempts', {}).values(), *health.get('recent_attempts', [])]
        for item in indexed:
            identifier = item.get('attempt_id')
            if not isinstance(identifier, str) or not identifier or Path(identifier).name != identifier:
                incomplete = True
                continue
            index[identifier] = item
        candidates = [directory / identifier for identifier in index]
        incomplete = incomplete or bool(health.get('attempt_index_overflow')) or len(candidates) > max_attempts
        candidates = candidates[-max_attempts:]
    elif directory.exists():
        with os.scandir(directory) as entries:
            for entry in entries:
                if len(candidates) >= max_attempts or time.monotonic() - started >= 5:
                    incomplete = True
                    break
                if entry.is_dir(follow_symlinks=False):
                    candidates.append(Path(entry.path))
    records = []
    latest_attempt = health.get('last_attempt_id')
    if isinstance(latest_attempt, str) and Path(latest_attempt).name == latest_attempt:
        latest_path = directory / latest_attempt
        candidates = [latest_path, *[p for p in candidates if p != latest_path]][:max_attempts]
    for path in candidates:
        if time.monotonic()-started >= 5:
            incomplete = True
            break
        intent = read_json(path / 'intent.json')
        if isinstance(intent, dict):
            outcome = read_json(path / 'outcome.json')
            proof = index.get(path.name)
            if proof is not None:
                if (sha256_file(path / 'intent.json') != proof.get('intent_sha256') or
                        (proof.get('outcome_sha256') and (not isinstance(outcome, dict) or
                         sha256_file(path / 'outcome.json') != proof['outcome_sha256']))):
                    incomplete = True
                    continue
            record = build_attempt_record(path, intent, outcome, now)
            result = (outcome or {}).get('result') or {}
            # These are explicit receipts, not guesses from cadence or timestamps.
            concurrency = result.get('concurrency') or {}
            record.update(origin=result.get('origin'), readback_verified=result.get('readback_verified'),
                          controller_approved=concurrency.get('controller_approved'),
                          parallel_sources=concurrency.get('limit_used'),
                          peak_active_sources=concurrency.get('peak_active_sources'),
                          safe_downshift_verified=concurrency.get('safe_downshift_verified'),
                          pressure_free=concurrency.get('qualifying') is True,
                          publication_held=result.get('publication_held'),
                          worker_stop_reason=result.get('worker_stop_reason'))
            records.append(record)
        elif path.name in index:
            incomplete = True
    previous = read_json(output / 'latest.json', {})
    prior_ids = previous.get('terminal_attempt_ids', [])
    seen = set(prior_ids)
    for record in sorted(records, key=lambda r: r.get('started_at') or ''):
        if record['terminal'] and record['attempt_id'] not in seen:
            seen.add(record['attempt_id'])
    # Keep deduplication and observation in a single atomic record. No append/
    # checkpoint crash window that duplicates cycles after restart.
    completed = {r['attempt_id']: r for r in previous.get('cycles', [])}
    completed.update({r['attempt_id']: r for r in records if r['terminal']})
    ordered = sorted(completed.values(), key=lambda r: r.get('started_at') or '')[-max_attempts:]
    alerts = []
    invocation_age = age_seconds(health.get('last_invocation_at'), now)
    if invocation_age is None or invocation_age > HEARTBEAT_CRITICAL_SECONDS:
        alerts.append('invocation_stale_or_unknown')
    if health.get('accepted_work_pending'):
        alerts.append('accepted_work_pending')
    if health.get('unresolved_publication'):
        alerts.append('publication_recovery_pending')
    quiet = (health.get('last_proven_empty_at') is not None and
             health.get('last_proven_empty_attempt_id') == health.get('last_attempt_id') and
             not health.get('accepted_work_pending') and not health.get('unresolved_publication'))
    published_age = age_seconds(health.get('last_publication_complete_at'), now)
    if not quiet and (published_age is None or published_age > PUBLICATION_CRITICAL_SECONDS):
        alerts.append('publication_progress_stale_or_unknown')
    if incomplete:
        alerts.append('receipt_discovery_incomplete')
    failure = collect_failure_condition(attempts)
    if failure['unchanged_failures'] >= HOLD_CRITICAL_FAILURES:
        alerts.append('repeated_stage_failure')
    value = {'schema_version': SCHEMA_VERSION, 'observed_at': iso(now), 'config_path': str(config_path),
             'discovery_complete': not incomplete, 'health': health, 'alerts': sorted(alerts),
             'quiet_proven_empty': quiet,
             'alert_changes': {'opened': sorted(set(alerts)-set(previous.get('alerts', []))),
                               'resolved': sorted(set(previous.get('alerts', []))-set(alerts))},
             'cycles': ordered, 'terminal_attempt_ids': [r['attempt_id'] for r in ordered],
             'a8': a8_readiness(ordered), 'failure_condition': failure,
             'observation_seconds': time.monotonic()-started,
             'scope': 'Bounded receipt observation; unknown origin/readback never earns A8 credit.'}
    if incomplete:
        value['a8']['ready'] = False
    atomic_write_json(output / 'latest.json', value)
    return value


def main(argv: list[str] | None = None) -> int:
    import time
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True, type=Path)
    parser.add_argument('--out-dir', required=True, type=Path)
    parser.add_argument('--once', action='store_true')
    parser.add_argument('--interval-seconds', type=float, default=60)
    parser.add_argument('--duration-seconds', type=float, default=24*3600)
    parser.add_argument('--max-attempts', type=int, default=256)
    args = parser.parse_args(argv)
    if not 1 <= args.interval_seconds <= 60 or not 0 < args.duration_seconds <= 24*3600 or not 1 <= args.max_attempts <= 4096:
        parser.error('Polling/duration/discovery bounds are invalid')
    global STOP_REQUESTED
    STOP_REQUESTED = False
    config, path = load_config(args.config)
    observation_paths(config, path, args.out_dir)
    args.out_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock = (args.out_dir / 'monitor.lock').open('a+')
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        lock.close()
        return 75
    end = time.monotonic() + args.duration_seconds
    wall_end = utcnow() + timedelta(seconds=args.duration_seconds)
    def stop(_signum, _frame):
        global STOP_REQUESTED
        STOP_REQUESTED = True
    prior = {signum: signal.signal(signum, stop) for signum in (signal.SIGINT, signal.SIGTERM)}
    try:
        while not STOP_REQUESTED and time.monotonic() < end and utcnow() < wall_end:
            snapshot = sample(config, path, args.out_dir, max_attempts=args.max_attempts)
            if args.once:
                print(json.dumps(snapshot, indent=2))
                break
            time.sleep(min(args.interval_seconds, max(0, end-time.monotonic()),
                           max(0, (wall_end-utcnow()).total_seconds())))
    finally:
        for signum, handler in prior.items():
            signal.signal(signum, handler)
        lock.close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
