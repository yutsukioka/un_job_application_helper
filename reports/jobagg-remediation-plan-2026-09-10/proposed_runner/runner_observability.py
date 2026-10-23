"""Local dispatcher attempt receipts, condition-bound failure holds and mount gate."""

from __future__ import annotations

from contextlib import nullcontext
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import stat
import tempfile
import time
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


def DEADLINE_CHECK():
    return None


def sha(path):
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            DEADLINE_CHECK()
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


def storage_check(config, *, require_reserve=True):
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
    storage_health = record_storage_health(
        config, available, reserve, persist=config.get("_record_storage_health", False)
    )
    if available < reserve and require_reserve:
        raise ValueError("Configured storage free space is below static reserve")
    # Nonexistent mounted volume never gets created and no data path falls back.
    return {
        "configured": True,
        "ready": available >= reserve,
        "mount_root": str(mount),
        "sentinel_sha256": guard["sentinel_sha256"],
        "device": mount.stat().st_dev,
        "available_bytes": available,
        "min_free_bytes": reserve,
        "reserve_is_estimate_guarantee": False,
        "early_warning": storage_health,
    }


def record_storage_health(config, available, reserve, *, persist=True):
    """Persistent local alert transitions before the hard reserve is exhausted.

    Runway uses observed volume consumption, not an invented jobagg-only rate.
    Measurements and clear events remain on the independent internal volume.
    """
    guard = config["storage_guard"]
    warning = guard.get("warning_free_bytes", max(3 * reserve, 64 * 1024**3))
    headroom = guard.get("publication_headroom_bytes", 8 * 1024**3)
    runway_limit = guard.get("warning_runway_seconds", 48 * 3600)
    for value in (warning, headroom, runway_limit):
        if type(value) is not int or value < 0:
            raise ValueError("Storage warning limits must be nonnegative integers")
    directory = config["attempt_state_dir"]
    if persist:
        directory.mkdir(parents=True, exist_ok=True)
    owner = (directory / "storage-health.lock").open("a+") if persist else nullcontext()
    with owner as lock:
        if persist:
            fcntl.flock(lock, fcntl.LOCK_EX)
        path = directory / "storage-health.json"
        prior = json.loads(path.read_text()) if path.exists() else {}
        now = datetime.now(timezone.utc).timestamp()
        samples = [sample for sample in prior.get("samples", []) if 0 < now - sample["at"] <= 86400]
        rate = None
        if samples and now - samples[0]["at"] >= 60:
            rate = max(0, (samples[0]["available_bytes"] - available) / (now - samples[0]["at"]))
        runway = max(0, available - reserve - headroom) / rate if rate else None
        status = ("critical" if available < reserve + headroom else
                  "warning" if available < warning or (runway is not None and runway < runway_limit) else "ok")
        events = prior.get("transitions", [])
        if status != prior.get("status"):
            events = [*events, {"at": now, "from": prior.get("status"), "to": status}][-100:]
        result = {"status": status, "observed_at": utc(), "available_bytes": available,
                  "reserve_bytes": reserve, "publication_headroom_bytes": headroom,
                  "volume_consumption_bytes_per_second": rate, "estimated_runway_seconds": runway,
                  "estimate_basis": "observed_volume_free_space; includes other writers",
                  "transitions": events}
        if persist:
            write(path, {**result, "samples": [*samples, {"at": now, "available_bytes": available}][-96:]})
        return result


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
        # Compact producer-owned observation index. Raw historical receipts
        # remain available to separate bounded audit/rebuild tools.
        active = health.get("active_attempts", {})
        recent = health.get("recent_attempts", [])
        identifier = value["attempt_id"]
        prior = active.get(identifier) or next((item for item in recent if item["attempt_id"] == identifier), None)
        sequence = prior.get("sequence") if prior else health.get("attempt_sequence", 0) + 1
        health["attempt_sequence"] = max(sequence, health.get("attempt_sequence", 0))
        root = directory / "attempts" / identifier
        entry = {"attempt_id": identifier, "sequence": sequence}
        if (root / "intent.json").is_file():
            entry["intent_sha256"] = sha(root / "intent.json")
        else:
            health["attempt_index_overflow"] = True
        if value.get("finished_at"):
            if (root / "outcome.json").is_file():
                entry["outcome_sha256"] = sha(root / "outcome.json")
            else:
                entry["outcome_sha256"] = None
                health["attempt_index_overflow"] = True
            if prior and prior.get("outcome_sha256") not in (None, entry["outcome_sha256"]):
                raise ValueError("A finalized attempt receipt changed")
            active.pop(identifier, None)
            recent = [*filter(lambda item: item["attempt_id"] != identifier, recent), entry][-128:]
        elif prior is not None or len(active) < 128:
            active[identifier] = entry
        else:
            health["attempt_index_overflow"] = True
        health.update(attempt_index_schema=1, active_attempts=active, recent_attempts=recent)
        health.setdefault("attempt_index_started_at", value["started_at"])
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
            health["accepted_work_pending"] = result.get("status") == "accepted_work_pending"
            health["pending_accepted_run_id"] = result.get("run_id") if health["accepted_work_pending"] else None
            if (result.get("publication_status") == "proven_empty" and
                    result.get("backlog_authoritatively_empty") is True and result.get("worker_exit_code") == 0):
                health["last_proven_empty_at"] = completed_at
                health["last_proven_empty_attempt_id"] = value["attempt_id"]
            if isinstance(result.get("storage_budget"), dict):
                health["storage_budget"] = result["storage_budget"]
            retention = result.get("storage_retention")
            if isinstance(retention, dict):
                health["last_retention_status"] = retention.get("status")
                health["last_retention_reason"] = retention.get("reason")
                health["last_retention_event_at"] = value.get("finished_at")
                if retention.get("status") == "complete":
                    health["last_retention_complete_at"] = value.get("finished_at")
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
            and not health.get("accepted_work_pending", False)
            and health.get("last_attempt_status")
            not in {"process_failure", "unchanged_failure_hold", "storage_deferred", "accepted_work_pending", "publication_held"}
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
    refs = []
    for path in sorted(paths):
        DEADLINE_CHECK()
        refs.append(file_condition(path, contents=True))
    config["_stable_release_sha256"] = hashlib.sha256(json.dumps(
        [{"path": row["path"], "sha256": row.get("sha256")} for row in refs], sort_keys=True).encode()).hexdigest()
    intents = ([config["state_dir"] / "runs" / config["_runtime_active_run"] / "publication_intent.json"]
               if config.get("_runtime_active_run") else
               ([] if config.get("runtime_control") or "jobagg.remediation_worker" in config.get("worker_argv", [])
                else sorted((config["state_dir"] / "runs").glob("*/publication_intent.json"))))
    for p in intents:
        DEADLINE_CHECK()
        if not p.exists():
            continue
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


def stable_failure_scope(config, stage):
    # Configuration/release provenance stays distinct from mutable evidence.
    return {"config_sha256": config.get("_config_sha256"),
            "release_sha256": config.get("_stable_release_sha256"),
            "worker_cwd": str(config.get("worker_cwd")),
            "publication_cwd": str(config.get("publication_cwd")), "stage": stage}


def failure_hold(config, fingerprint, *, stage=None):
    path = config["attempt_state_dir"] / "failure_condition.json"
    if not path.exists():
        return None
    prior = json.loads(path.read_text())
    selected = stage or prior.get("stage", "worker")
    end_to_end = prior.get('circuits', {}).get('end_to_end', {})
    selected_episode = prior.get('circuits', {}).get(selected, {})
    stage_failures = max([selected_episode.get('unchanged_failures', 0), *[value.get('unchanged_failures', 0) for value in selected_episode.get('class_episodes', {}).values()]])
    if end_to_end.get('unchanged_failures', 0) >= 3 and stage_failures < 3:
        selected = 'end_to_end'
    circuit = prior.get("circuits", {}).get(selected)
    # Preserve pre-M1 holds until explicitly reviewed; a release change isn't recovery.
    if circuit is None and prior.get("unchanged_failures", 0) >= 3:
        circuit = prior
    held_episode = max([circuit, *circuit.get("class_episodes", {}).values()],
                       key=lambda item: item.get("unchanged_failures", 0)) if circuit else None
    if held_episode and held_episode.get("unchanged_failures", 0) >= 3:
        probe = circuit.get("reviewed_probe") or {}
        if (probe.get("state") == "available" and probe.get("expires_at", 0) >= time.time()
                and probe.get("scope") == stable_failure_scope(config, selected)):
            probe.update(state="reserved", reserved_at=utc())
            circuit["reviewed_probe"] = probe
            prior.setdefault("circuits", {})[selected] = circuit
            write(path, prior)  # Consume before dispatch, including crash/admission gaps.
            return None
        return {"status": "unchanged_failure_hold", "stage": selected,
                "condition_sha256": fingerprint, "unchanged_failures": held_episode["unchanged_failures"],
                "failure_class": held_episode.get("failure_class"),
                "reasons": held_episode.get("reasons", []), "next_eligible_at": None,
                "review_required": True, "retry_ceiling": 3,
                "first_occurrence_at": circuit.get("first_occurrence_at"),
                "last_occurrence_at": circuit.get("updated_at"),
                "unresolved_publication": circuit.get("unresolved_publication", False),
                "reason": "Repeated stage failure; explicit reviewed recovery required, no automatic probe."}
    return None


def record_failure(config, fingerprint, result):
    path = config["attempt_state_dir"] / "failure_condition.json"
    previous = json.loads(path.read_text()) if path.exists() else {}
    circuits = previous.get("circuits", {})
    stamp = utc()
    # Reset only stages that actually reached their validated success boundary.
    successful = []
    starved_worker = (result.get('worker_stop_reason') == 'work_deadline'
                      and result.get('worker_progress') == 0)
    if not starved_worker and result.get("worker_exit_code") == 0 and result.get("worker_status") in {"complete", "incomplete"}:
        successful.append("worker")
    if result.get("publication_status") in {"published", "noop"} and result.get("publication_exit_code") == 0:
        successful.extend(["projection", "publication"])
    history_refs = previous.get("history_refs", [])
    for stage in successful:
        if stage == "worker" and result.get("accepted_work_resumed"):
            continue  # Re-reading an older worker proof is not fresh recovery.
        resolved = circuits.get(stage)
        if resolved is not None:
            history = {"stage": stage, "episode": resolved, "resolved_at": stamp,
                       "resolution": "validated_stage_success", "run_id": result.get("run_id"),
                       "publication_result_sha256": result.get("publication_result_sha256")}
            key = hashlib.sha256(json.dumps(history, sort_keys=True).encode()).hexdigest()
            target = config["attempt_state_dir"] / "circuit-history" / (key + ".json")
            if target.exists():
                if json.loads(target.read_text()) != history:
                    raise ValueError("Circuit history changed")
            else:
                write(target, history)
            history_refs = [*history_refs, {"path": str(target), "episode_sha256": key}][-64:]
        circuits.pop(stage, None)
    failed = (starved_worker or result.get('status') == 'process_failure' or
              (result.get('status') == 'accepted_work_pending' and
               any(str(reason).startswith('TimeoutError:') for reason in result.get('reasons', []))))
    durable_progress = ((result.get('publication_status') in {'published', 'noop'}
                         and result.get('publication_exit_code') == 0
                         and (result.get('readback_verified') is True or not config.get('runtime_control',
                              'jobagg.remediation_worker' in config.get('worker_argv', []))))
                        or (result.get('publication_status') == 'proven_empty'
                            and result.get('backlog_authoritatively_empty') is True and not starved_worker))
    if durable_progress:
        successful.append('end_to_end')
    # Stage successes above never erase a whole-service failure episode.
    if failed and not durable_progress:
        prior_service = circuits.get('end_to_end', {})
        circuits['end_to_end'] = {**prior_service, 'stage':'end_to_end', 'failure_class':'no_end_to_end_progress',
            'scope':stable_failure_scope(config, 'end_to_end'),
            'unchanged_failures':prior_service.get('unchanged_failures',0)+1,
            'first_occurrence_at':prior_service.get('first_occurrence_at',stamp), 'updated_at':stamp,
            'retry_ceiling':3, 'reasons':result.get('reasons',[]),
            'recovery_path':'reviewed single probe with preserved stage episodes'}
    if durable_progress and 'end_to_end' in circuits:
        resolved_service = circuits.pop('end_to_end')
        history = {'stage':'end_to_end','episode':resolved_service,'resolution':'verified_publication_or_authoritative_empty','resolved_at':stamp}
        key = hashlib.sha256(json.dumps(history, sort_keys=True).encode()).hexdigest()
        target = config['attempt_state_dir'] / 'circuit-history' / (key+'.json')
        if not target.exists():
            write(target, history)
        history_refs = [*history_refs, {'path':str(target),'episode_sha256':key}][-64:]
    if (starved_worker or result.get("status") == "process_failure" or
            (result.get("status") == "accepted_work_pending" and
             any(str(reason).startswith("TimeoutError:") for reason in result.get("reasons", [])))):
        raw_stage = 'worker' if starved_worker else result.get("failure_stage", "worker")
        stage = ("publication" if raw_stage.startswith("publication") else
                 "projection" if raw_stage in {"projection", "worker_validated"} else "worker")
        envelope = circuits.get(stage, {})
        failure_class = ('zero_task_work_deadline' if starved_worker else "timeout" if result.get(stage + "_timeout") else
                         "process_exit" if result.get(stage + "_exit_code") not in (None, 0)
                         else result.get("failure_type", "unknown_process_failure"))
        episodes = dict(envelope.get("class_episodes", {}))
        if envelope.get("failure_class", failure_class) != failure_class:
            episodes[envelope["failure_class"]] = {key: value for key, value in envelope.items() if key != "class_episodes"}
            prior = episodes.pop(failure_class, {})
        else:
            prior = envelope
        count = prior.get("unchanged_failures", 0) + 1
        circuit = {"stage": stage, "failure_class": failure_class, "class_episodes": episodes,
                   "scope": stable_failure_scope(config, stage),
                   "condition_sha256": fingerprint, "unchanged_failures": count,
                   "first_occurrence_at": prior.get("first_occurrence_at", stamp),
                   "updated_at": stamp, "reasons": result.get("reasons", []),
                   "retry_ceiling": 3, "next_eligible_at": None if count >= 3 else stamp,
                   "recovery_path": "explicit reviewed single stage probe, then validated stage success",
                   "probe_review_history": envelope.get("probe_review_history", []),
                   "reviewed_probe": envelope.get("reviewed_probe"),
                   "unresolved_publication": result.get("unresolved_publication", False)}
        circuits[stage] = circuit
    else:
        stage = previous.get("stage", "worker")
        circuit = circuits.get(stage, {"condition_sha256": fingerprint, "unchanged_failures": 0, "updated_at": stamp})
    write(path, {**circuit, "schema_version": 2, "stage": stage,
                 "circuits": circuits, "history_refs": history_refs})


def authorize_stage_probe(config, stage, *, evidence_path, evidence_sha256, expires_at):
    """Explicit operator review API; no counter reset, one attempt per review.

    Call under the actual shared owner after reviewing the corrected release and
    configuration. This API is never invoked by a natural dispatcher tick.
    """
    if stage not in {"worker", "projection", "publication", "end_to_end"}:
        raise ValueError("Unknown reviewed probe stage")
    evidence = Path(evidence_path)
    if sha(evidence) != evidence_sha256 or type(expires_at) not in (int, float) or not time.time() < expires_at:
        raise ValueError("Probe review evidence or expiration differs")
    path = config["attempt_state_dir"] / "failure_condition.json"
    value = json.loads(path.read_text())
    circuit = value.get("circuits", {}).get(stage)
    if circuit is None:
        raise ValueError("No stage episode to review")
    used = circuit.get("probe_review_history", [])
    if evidence_sha256 in used:
        raise ValueError("This review already authorized its single probe")
    circuit["probe_review_history"] = [*used, evidence_sha256]
    circuit["reviewed_probe"] = {"state": "available", "review_sha256": evidence_sha256,
                                  "expires_at": expires_at, "scope": stable_failure_scope(config, stage)}
    value["circuits"][stage] = circuit
    write(path, value)


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
