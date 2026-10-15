"""Local, owner-guarded retirement of terminal publication history.

Legacy retirement needs an exact verified-backup attestation. Future expiration
needs committed local semantic coverage; it never opens an offline backup.
Neither a preview nor an unfinished intent changes a publication's outcome.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import re
import stat
from datetime import UTC, datetime
from pathlib import Path

INTENT = "terminal-history-retirement.intent.json"
RECEIPT = "terminal-history-retirement.json"
POLICIES = {"legacy_backup_retirement", "future_window_expiration"}
_MODULES = {}
_GENERATION = re.compile(r"[a-f0-9]{32}\Z")
_PAYLOAD_DIRS = {"beforeimages", "before_blobs", "blobs", "exports-v2", "before_exports", "before_exports_verified"}
_CONTROL_NAMES = {"result.json", "exports.json", "export-checkpoints.json",
                  "storage-retention.json", "storage-retention-validation.json",
                  "semantic-coverage.json", "terminal-object-gc.intent.json", "terminal-object-gc.json"}


def _module(name):
    # Also works for the standalone dispatcher's reviewed sibling loader.
    if name not in {"storage_retention", "storage_cold_archive", "terminal_publication", "semantic_history", "publication_artifacts"}:
        raise ValueError("Unknown reviewed history helper")
    if name not in _MODULES:
        path = Path(__file__).with_name(name + ".py")
        spec = importlib.util.spec_from_file_location("jobagg_history_" + name, path)
        module = importlib.util.module_from_spec(spec)
        before = path.lstat()
        if not stat.S_ISREG(before.st_mode):
            raise ValueError("History helper is not a direct regular file")
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(descriptor, "rb") as handle:
            opened = os.fstat(handle.fileno())
            source = handle.read()
            after = os.fstat(handle.fileno())
        def identity(info):
            return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
        if not identity(before) == identity(opened) == identity(after) == identity(path.lstat()):
            raise ValueError("History helper changed while reading")
        # Only allowlisted sibling module bytes are executed; never a user path.
        exec(compile(source, str(path), "exec"), module.__dict__)  # noqa: S102  # nosec B102
        _MODULES[name] = module
    return _MODULES[name]


def _retention():
    return _module("storage_retention")


def _check(value, message):
    if not value:
        raise ValueError("Terminal history: " + message)


def _digest_value(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                    ensure_ascii=True).encode()).hexdigest()


def _layout(subject, root=None):
    subject = Path(subject).absolute()
    inferred = subject.parents[2]
    root = inferred if root is None else Path(root).absolute()
    _check(root == inferred and root.resolve() == root, "subject root differs or is linked")
    if subject.parent == root / "deterministic-dispatcher-proposal" / "runs":
        kind = "run"
    elif subject.parent == root / "deterministic-live-publication" / "generations":
        _check(bool(_GENERATION.fullmatch(subject.name)), "invalid generation identity")
        kind = "generation"
    else:
        raise ValueError("Terminal history: subject is outside publication history")
    _retention()._directory(subject)
    _check(subject.resolve() == subject, "subject contains linked parents")
    return root, subject, kind


def _controls(root, paths, deadline_at):
    result = {}
    for path in sorted(set(map(Path, paths))):
        _check(path.is_relative_to(root), "control is outside root")
        relative = str(path.relative_to(root))
        safe = _retention()._inside(root, relative)
        result[relative] = _retention()._digest(safe, deadline_at=deadline_at)
    return result


def _validate_local(subject, root=None, *, deadline_at=None):
    root, subject, kind = _layout(subject, root)
    helper = _retention()
    intent_path, receipt_path = subject / INTENT, subject / RECEIPT
    if not intent_path.exists() and not intent_path.is_symlink():
        _check(not receipt_path.exists() and not receipt_path.is_symlink(), "completion lacks intent")
        return {"disposition": "legacy_active", "subject_kind": kind, "subject_id": subject.name}
    intent = helper._json(intent_path)
    _check(intent.get("schema_version") == 1 and intent.get("subject_kind") == kind
           and intent.get("subject_id") == subject.name
           and intent.get("subject_path") == str(subject)
           and intent.get("root") == str(root) and intent.get("policy") in POLICIES,
           "intent identity or policy differs")
    _check(intent.get("phase") == "intent" and intent.get("automatic_replay_allowed") is False,
           "invalid intent phase/replay authority")
    controls = intent.get("controls")
    _check(isinstance(controls, dict) and controls, "missing immutable controls")
    for relative, expected in controls.items():
        _check(helper._digest(helper._inside(root, relative), deadline_at=deadline_at) == expected,
               "retained control changed")
    files = intent.get("files")
    _check(isinstance(files, list) and files, "empty payload manifest")
    _check(len({item.get("path") for item in files}) == len(files), "duplicate payload path")
    for item in files:
        _check(isinstance(item, dict), "invalid payload entry")
        parts = Path(item.get("path", "")).parts
        _check((kind == "run" and parts == ("publication_snapshot.sqlite3",)) or
               (kind == "generation" and (parts == ("plan.json",) or
                (len(parts) == 2 and parts[0] in _PAYLOAD_DIRS))), "unrecognized expiring payload")
        helper._inside(subject, item.get("path"))
        helper._sha(item.get("sha256"))
        _check(type(item.get("size")) is int and item["size"] >= 0, "invalid payload length")
    if kind == "run":
        _check(len(files) == 1, "unexpected run payload count")
    shared_objects = _validated_shared_objects(root, intent.get("shared_objects", []))
    state = subject / "state.json"
    if kind == "run" and state.exists():
        _check(helper._json(state).get("status") != "running", "retirement refers to running run")
    authority = intent.get("authority")
    _check(isinstance(authority, dict) and authority.get("policy") == intent["policy"],
           "missing policy authority")
    _check(intent.get("terminal_kind") in {"published", "noop", "recovered_published", "verified_unstarted"},
           "invalid terminal outcome")
    if intent["policy"] == "legacy_backup_retirement":
        expected, authority_paths = _backup_authority(
            {"path": authority.get("attestation_path"), "sha256": authority.get("attestation_sha256")},
            subject, files, shared_objects, root, deadline_at)
        _check(authority == expected and all(str(path.relative_to(root)) in controls for path in authority_paths),
               "unverified legacy backup authority")
    else:
        _check(authority.get("policy_epoch") and authority.get("semantic_coverage"),
               "future expiration lacks semantic authority")
        coverage = _module("semantic_history").validate_semantic_coverage(
            authority["semantic_coverage"], subject_kind=kind, subject_id=subject.name,
            policy_epoch=authority["policy_epoch"], allowed_roots=authority.get("allowed_roots", []),
            deadline_at=deadline_at)
        _check(_digest_value(coverage) == authority.get("coverage_sha256"),
               "committed semantic authority changed")
        _semantic_files_covered(subject, files, coverage)
    if not receipt_path.exists() and not receipt_path.is_symlink():
        return {"disposition": "retirement_pending", "intent": intent,
                "subject_kind": kind, "subject_id": subject.name}
    receipt = helper._json(receipt_path)
    _check(receipt.get("schema_version") == 1 and receipt.get("phase") == "complete"
           and receipt.get("intent_sha256") == helper._digest(intent_path)["sha256"]
           and receipt.get("subject_id") == subject.name
           and receipt.get("policy") == intent["policy"], "completion binding differs")
    for item in files:
        path = helper._inside(subject, item["path"])
        _check(not path.exists() and not path.is_symlink(), "completed payload reappeared")
    if kind == "generation":
        for dirname in _PAYLOAD_DIRS:
            directory = subject / dirname
            if directory.exists():
                helper._directory(directory)
                _check(not any(directory.iterdir()), "completed generation has unclassified payload")
    expected_availability = ("backup_only" if intent["policy"] == "legacy_backup_retirement"
                             else "expired_unavailable")
    _check(receipt.get("availability") == expected_availability, "false byte availability")
    return {"disposition": "retired_terminal", "intent": intent, "receipt": receipt,
            "subject_kind": kind, "subject_id": subject.name,
            "terminal_kind": intent["terminal_kind"], "availability": expected_availability}


def operational_closure(root, *, selected_runs=(), selected_generations=(), deadline_at=None):
    """Read mandatory publication roots; ambiguous metadata fails closed.

    A terminal receipt discharges only its own historical origin. Ordinary
    unresolved runs continue to pin their generation and no-write proof edges.
    """
    root = Path(root).absolute()
    helper = _retention()
    gate_path = root / "output" / ".jobagg-publication-state.json"
    gate = helper._json(gate_path)
    _check(gate.get("state") == "complete" and gate.get("status") == "published"
           and gate.get("database_transactions_complete") is True,
           "maintenance requires complete published live gate")
    current = gate.get("generation_id")
    _check(isinstance(current, str) and _GENERATION.fullmatch(current), "invalid current generation")
    generations = root / "deterministic-live-publication" / "generations"
    protected_generations = set(selected_generations) | {current}
    protected_runs = set(selected_runs)
    _selected_evidence(root, selected_runs, selected_generations, deadline_at)
    completed = []
    for subject in sorted(generations.iterdir()):
        helper._check_deadline(deadline_at)
        if subject.name == ".DS_Store":
            continue
        local = _validate_local(subject, root, deadline_at=deadline_at)
        if local["disposition"] == "retired_terminal":
            _check(subject.name != current, "current generation was retired")
            continue
        if local["disposition"] == "retirement_pending":
            continue
        group = helper._generation_group(subject, deadline_at=deadline_at)
        if group is None:
            protected_generations.add(subject.name)
            continue
        # A usable checkpoint must still have its exact plan and a complete
        # export journal. Unknown/legacy groups remain protected above.
        completed.append((group["time"], subject.name, group))
    current_group = next((group for _, identifier, group in completed if identifier == current), None)
    _check(current_group is not None, "current generation has no usable complete checkpoint")
    _usable_generation(current_group, deadline_at)
    previous = None
    for timestamp, identifier, group in sorted(completed, key=lambda item: (item[0], item[1]), reverse=True):
        # Missing historical copies cannot become the rollback checkpoint.
        if (identifier != current and timestamp <= current_group["time"]
                and (group["path"] / "plan.json").is_file()
                and all((group["path"] / item["path"]).is_file() for item in group["files"])):
            _usable_generation(group, deadline_at)
            previous = identifier
            break
    _check(previous is not None, "no usable previous generation")
    protected_generations.add(previous)
    origins = {}
    for run in sorted((root / "deterministic-dispatcher-proposal" / "runs").iterdir()):
        helper._check_deadline(deadline_at)
        if run.name == ".DS_Store":
            continue
        local = _validate_local(run, root, deadline_at=deadline_at)
        if local["disposition"] == "retired_terminal":
            continue
        classification = _module("terminal_publication").classify_run(
            run, allow_missing_generation_result=True, deadline_at=deadline_at)
        references = set(classification["generation_ids"])
        # These readers still dereference historical generation controls. Only
        # this run's completed terminal receipt can discharge that audit edge.
        if classification["terminal_kind"] in {"verified_unstarted", "recovered_published"}:
            protected_generations.update(references)
        if references & {current, previous} or classification["disposition"] != "terminal":
            protected_runs.add(run.name)
            protected_generations.update(references)
            origins[run.name] = sorted(references)
    return {"runs": sorted(protected_runs), "generations": sorted(protected_generations),
            "current": current, "previous": previous, "origins": origins,
            "gate_sha256": helper._digest(gate_path)["sha256"]}


def _usable_generation(group, deadline_at):
    helper = _retention()
    helper._validate_selected(group, deadline_at=deadline_at)
    plan = helper._json(group["path"] / "plan.json")
    for relative, expected in _module("storage_cold_archive")._plan_beforeimage_bindings(group["path"], plan).items():
        before_path = helper._inside(group["path"], relative)
        _check(helper._digest(before_path, deadline_at=deadline_at)["sha256"] == expected,
               "usable checkpoint beforeimage differs")
        before = helper._json(before_path)
        for document in before.get("related", {}).get("job_attachments", []):
            digest = document.get("content_sha256")
            if digest:
                helper._sha(digest)
                _check(helper._digest(group["path"] / "before_blobs" / digest,
                                      deadline_at=deadline_at)["sha256"] == digest,
                       "usable checkpoint document beforeimage differs")
    for change in plan.get("changes", []):
        for document in change.get("documents", []):
            digest = helper._sha(document.get("content_sha256"))
            path = group["path"] / "blobs" / digest
            _check(document.get("prepared_binary_path") == str(path)
                   and helper._digest(path, deadline_at=deadline_at)["sha256"] == digest,
                   "usable checkpoint incoming document differs")
    for item in group["files"]:
        actual = helper._digest(group["path"] / item["path"], deadline_at=deadline_at)
        _check(actual == {key: item[key] for key in ("sha256", "size")},
               "usable generation export bytes differ")


def classify_history(subject, *, root=None, deadline_at=None, closure=None):
    """Validate completed local receipts before obsolete payload readers run."""
    root, subject, kind = _layout(subject, root)
    result = _validate_local(subject, root, deadline_at=deadline_at)
    if result["disposition"] == "retired_terminal":
        protection = closure or operational_closure(root, deadline_at=deadline_at)
        _check(subject.name not in protection["runs" if kind == "run" else "generations"],
               "retired subject has a current operational reference")
    return result


def _generation_files(subject, deadline_at):
    helper = _retention()
    group = helper._generation_group(subject, deadline_at=deadline_at)
    _check(group is not None, "generation lacks verified export/plan contract")
    helper._validate_selected(group, deadline_at=deadline_at)
    expected = {item["path"]: item for item in group["files"]}
    paths = [subject / "plan.json"]
    for dirname in sorted(_PAYLOAD_DIRS):
        directory = subject / dirname
        if not directory.exists():
            continue
        helper._directory(directory)
        for path in sorted(directory.iterdir()):
            helper._regular(path)
            if dirname in {"exports-v2", "before_exports", "before_exports_verified"}:
                relative = str(path.relative_to(subject))
                _check(relative in expected, "unknown export payload")
                actual = helper._digest(path, deadline_at=deadline_at)
                _check(actual == {key: expected[relative][key] for key in ("sha256", "size")},
                       "export payload differs from verified journal")
            paths.append(path)
    allowed = _CONTROL_NAMES | _PAYLOAD_DIRS | {"plan.json", INTENT, RECEIPT}
    _check(all(path.name in allowed for path in subject.iterdir()), "unclassified generation file")
    return group, paths


def _validated_shared_objects(root, entries):
    _check(isinstance(entries, list), "shared object manifest is not a list")
    store = root / "deterministic-live-publication" / "publication-objects"
    seen = set()
    for entry in entries:
        _check(isinstance(entry, dict) and set(entry) == {"path", "sha256", "size"},
               "invalid shared object entry")
        _retention()._sha(entry["sha256"])
        path = Path(entry["path"])
        _check(path.parent == store and path.name == entry["sha256"] and str(path) not in seen,
               "duplicate or escaped shared object")
        _check(type(entry["size"]) is int and 0 < entry["size"] <= 1024 * 1024,
               "invalid shared object length")
        seen.add(str(path))
    return entries


def _backup_authority(ref, subject, files, shared_objects, root, deadline_at):
    helper = _retention()
    _check(isinstance(ref, dict), "missing backup attestation reference")
    attestation_path = helper._inside(root, ref.get("path"))
    fingerprint = helper._digest(attestation_path, deadline_at=deadline_at)
    _check(fingerprint["sha256"] == helper._sha(ref.get("sha256")), "backup attestation changed")
    attestation = helper._json(attestation_path)
    _check(attestation.get("schema_version") == 1 and attestation.get("status") == "verified"
           and attestation.get("verification_kind") == "source_destination_full_sha256"
           and isinstance(attestation.get("backup_id"), str) and attestation["backup_id"]
           and isinstance(attestation.get("backup_root"), str)
           and Path(attestation["backup_root"]).is_absolute(), "backup is not fully verified")
    helper._sha(attestation.get("manifest_sha256"))
    entries = attestation.get("files")
    _check(isinstance(entries, dict), "missing exact backup file coverage")
    for item in [*files, *shared_objects]:
        # Shared objects have exact absolute paths; per-subject payload is relative.
        source = str(subject / item["path"])
        entry = entries.get(source, {})
        destination = entry.get("destination_relative_path")
        _check(isinstance(destination, str) and destination and not Path(destination).is_absolute()
               and ".." not in Path(destination).parts
               and entry.get("source_sha256") == item["sha256"]
               and entry.get("destination_sha256") == item["sha256"]
               and type(entry.get("size_bytes")) is int and entry["size_bytes"] == item["size"],
               "backup does not cover payload or shared object")
    return {"policy": "legacy_backup_retirement", "backup_verified": True,
            "backup_id": attestation["backup_id"], "backup_root": attestation["backup_root"],
            "attestation_sha256": fingerprint["sha256"],
            "attestation_path": str(attestation_path.relative_to(root)),
            "manifest_sha256": attestation["manifest_sha256"]}, [attestation_path]


def _semantic_files_covered(subject, files, coverage):
    covered = {(item["path"], item["sha256"], item["size_bytes"]) for item in coverage["artifacts"]}
    for item in files:
        if Path(item["path"]).parts[0] not in {"exports-v2", "before_exports", "before_exports_verified"}:
            _check((str(subject / item["path"]), item["sha256"], item["size"]) in covered,
                   "useful semantic payload lacks committed readback coverage")


def _authority(policy, evidence, subject, files, root, deadline_at, shared_objects):
    helper = _retention()
    _check(policy in POLICIES and isinstance(evidence, dict), "unknown retirement policy")
    if policy == "legacy_backup_retirement":
        return _backup_authority(evidence.get("backup_attestation"), subject, files,
                                 shared_objects, root, deadline_at)
    epoch = evidence.get("policy_epoch")
    _check(isinstance(epoch, str) and epoch, "future policy epoch missing")
    activation = helper._timestamp(evidence.get("activated_at"))
    plan = helper._json(subject / "plan.json")
    _check(helper._timestamp(plan.get("created_at")) >= activation, "pre-epoch generation")
    coverage = _module("semantic_history").validate_semantic_coverage(
        evidence.get("semantic_coverage"), subject_kind="generation", subject_id=subject.name,
        policy_epoch=epoch, allowed_roots=evidence.get("allowed_roots", []),
        deadline_at=deadline_at,
    )
    _semantic_files_covered(subject, files, coverage)
    return {"policy": policy, "policy_epoch": epoch,
            "semantic_coverage": evidence["semantic_coverage"],
            "coverage_sha256": _digest_value(coverage), "allowed_roots": evidence.get("allowed_roots", []),
            "activated_at": evidence["activated_at"]}, [subject / "semantic-coverage.json"]


def retire_subject(root, subject, *, policy, evidence, execute=False, owner_fd=None,
                   shared_lock=None, deadline_at=None, selected_runs=(), selected_generations=(), budget=None):
    """Preview or perform an exact resumable retirement; never publishes/replays."""
    root, subject, kind = _layout(subject, root)
    helper = _retention()
    if execute:
        helper._check_owner(owner_fd, shared_lock)
        _check(budget is not None, "execution requires an admitted storage budget")
    closure = operational_closure(root, selected_runs=selected_runs,
                                  selected_generations=selected_generations, deadline_at=deadline_at)
    _check(subject.name not in closure["runs" if kind == "run" else "generations"],
           "subject is in protected operational closure")
    local = _validate_local(subject, root, deadline_at=deadline_at)
    if local["disposition"] == "retired_terminal":
        return {"status": "already_retired", "subject_id": subject.name, "deleted_bytes": 0}
    if local["disposition"] == "retirement_pending":
        intent = local["intent"]
        _check(intent["policy"] == policy and intent["evidence_sha256"] == _digest_value(evidence),
               "resume changed policy or authority")
    else:
        if kind == "run":
            _check(policy == "legacy_backup_retirement", "future snapshots use derived-copy retention")
            terminal = _module("terminal_publication").classify_run(
                subject, allow_missing_generation_result=True, deadline_at=deadline_at)
            _check(terminal["disposition"] == "terminal", "run has no authentic terminal proof")
            paths = [subject / "publication_snapshot.sqlite3"]
            control_paths = [path for path in terminal["controls"] if Path(path).is_relative_to(subject)]
            terminal_kind, deficits = terminal["terminal_kind"], terminal["known_deficits"]
        else:
            group, paths = _generation_files(subject, deadline_at)
            control_paths = [path for path in group["controls"] if path.name != "plan.json"]
            terminal_kind, deficits = "published", []
        files = []
        for path in paths:
            proof = helper._digest(path, deadline_at=deadline_at, include_identity=True)
            files.append({"path": str(path.relative_to(subject)), **proof})
        shared_objects = _shared_inventory(subject, group if kind == "generation" else None, deadline_at)
        authority, authority_controls = _authority(policy, evidence, subject, files, root, deadline_at, shared_objects)
        intent = {"schema_version": 1, "phase": "intent", "root": str(root),
                  "subject_kind": kind, "subject_id": subject.name, "subject_path": str(subject),
                  "policy": policy, "terminal_kind": terminal_kind, "known_deficits": deficits,
                  "automatic_replay_allowed": False, "files": files, "shared_objects": shared_objects,
                  "controls": _controls(root, [*control_paths, *authority_controls], deadline_at),
                  "authority": authority, "evidence_sha256": _digest_value(evidence),
                  "closure_sha256": _digest_value(closure),
                  "created_at": datetime.now(UTC).isoformat()}
    if not execute:
        return {"status": "eligible", "subject_id": subject.name, "policy": policy,
                "files": intent["files"], "estimated_bytes": sum(x["size"] for x in intent["files"])}
    intent_path = subject / INTENT
    if not intent_path.exists():
        for item in intent["files"]:
            helper._unchanged(helper._inside(subject, item["path"]), item["identity"])
        _seal_budgeted(intent_path, intent, budget)
    deleted = 0
    for item in intent["files"]:
        helper._check_deadline(deadline_at)
        path = helper._inside(subject, item["path"])
        if not path.exists() and not path.is_symlink():
            continue  # Only this exact durable intent permits missing payload.
        helper._unchanged(path, item["identity"])
        path.unlink()
        helper._sync_dir(path.parent)
        deleted += item["size"]
    _check(operational_closure(root, selected_runs=selected_runs,
                              selected_generations=selected_generations, deadline_at=deadline_at)
           == closure, "operational closure changed during retirement")
    _check(_controls(root, [root / path for path in intent["controls"]], deadline_at) == intent["controls"],
           "immutable controls changed during retirement")
    _seal_budgeted(subject / RECEIPT, {
        "schema_version": 1, "phase": "complete", "subject_id": subject.name, "policy": policy,
        "intent_sha256": helper._digest(intent_path)["sha256"],
        "availability": "backup_only" if policy == "legacy_backup_retirement" else "expired_unavailable",
        "completed_at": datetime.now(UTC).isoformat(),
    }, budget)
    return {"status": "retired", "subject_id": subject.name, "deleted_bytes": deleted}


def maintain_history(root, *, policy, execute=False, owner_fd=None, shared_lock=None,
                     deadline_at=None, max_groups=1, budget=None):
    """Activated bounded maintenance; no providers, recovery or offline IO."""
    if not isinstance(policy, dict) or policy.get("enabled") is not True:
        return {"status": "disabled", "selected": [], "deleted_bytes": 0}
    _check(type(max_groups) is int and max_groups > 0, "invalid maintenance group bound")
    if execute:
        _retention()._check_owner(owner_fd, shared_lock)
        _check(budget is not None, "execution requires an admitted storage budget")
    root = Path(root).absolute()
    options = {"selected_runs": policy.get("selected_runs", []),
               "selected_generations": policy.get("selected_generations", [])}
    closure = operational_closure(root, deadline_at=deadline_at, **options)
    selected, deferred = [], []
    calls = {"execute": execute, "owner_fd": owner_fd, "shared_lock": shared_lock,
             "deadline_at": deadline_at, "budget": budget}
    legacy = policy.get("legacy_retirements", [])
    _check(isinstance(legacy, list), "legacy retirement list differs")
    for entry in legacy:
        _check(isinstance(entry, dict) and entry.get("subject_kind") in {"run", "generation"},
               "invalid explicit legacy subject")
        identifier = entry.get("subject_id")
        _check(isinstance(identifier, str) and Path(identifier).name == identifier,
               "invalid legacy subject identity")
        kind = entry["subject_kind"]
        base = (root / "deterministic-dispatcher-proposal" / "runs" if kind == "run" else
                root / "deterministic-live-publication" / "generations")
        subject = base / identifier
        if identifier in closure["runs" if kind == "run" else "generations"]:
            deferred.append({"subject_id": identifier, "reason": "operational_closure"})
            continue
        if _validate_local(subject, root, deadline_at=deadline_at)["disposition"] == "retired_terminal":
            continue
        selected.append(retire_subject(root, subject, policy="legacy_backup_retirement",
                                       evidence={"backup_attestation": entry.get("backup_attestation")},
                                       **calls, **options))
        if len(selected) >= max_groups:
            break
    generations = root / "deterministic-live-publication" / "generations"
    if len(selected) < max_groups:
        for subject in sorted(generations.iterdir()):
            _retention()._check_deadline(deadline_at)
            if subject.name in closure["generations"] or subject.name == ".DS_Store":
                continue
            local = _validate_local(subject, root, deadline_at=deadline_at)
            if local["disposition"] == "retired_terminal":
                if local["intent"].get("shared_objects") and not (subject / "terminal-object-gc.json").exists():
                    collected = collect_retired_objects(root, subject, **calls)
                    if collected["status"] == "deferred":
                        deferred.append({"subject_id": subject.name, **collected})
                        continue
                    selected.append(collected)
                else:
                    continue
            else:
                ref_path = subject / "semantic-coverage.json"
                if not ref_path.exists():
                    deferred.append({"subject_id": subject.name, "reason": "semantic_coverage_unavailable"})
                    continue
                evidence = {"policy_epoch": policy.get("policy_epoch"),
                            "activated_at": policy.get("activated_at"),
                            "allowed_roots": policy.get("allowed_roots", []),
                            "semantic_coverage": _retention()._json(ref_path)}
                selected.append(retire_subject(root, subject, policy="future_window_expiration",
                                               evidence=evidence, **calls, **options))
            if len(selected) >= max_groups:
                break
    if execute and selected:
        closure = operational_closure(root, deadline_at=deadline_at, **options)
    return {"status": "complete", "selected": selected, "deferred": deferred,
            "protected": closure, "deleted_bytes": sum(x.get("deleted_bytes", 0) for x in selected),
            "worker_dispatched": False, "publisher_dispatched": False, "recovery_attempted": False}


def _seal_budgeted(path, value, budget, *, replace=False):
    """Reserve the simultaneous temporary/control bytes before writing them."""
    _check(budget is not None, "control write requires an admitted storage budget")
    size = len(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()) + 1
    reservation = budget.reserve(budget.category_for(path), size * 2 + 8192, path=path)
    try:
        reservation.consume(size * 2 + 8192)
        writer = _retention()._replace_json if replace else _retention()._seal_json
        writer(path, value)
    finally:
        reservation.settle()


def _shared_inventory(subject, group, deadline_at):
    if group is None:
        return []
    artifacts = _module("publication_artifacts")
    objects = {}
    for item in group["files"]:
        if not item.get("logical_path") or not (subject / item["path"]).exists():
            continue
        _retention()._check_deadline(deadline_at)
        for entry in artifacts.artifact_inventory(subject / item["logical_path"],
                                                   item["logical_sha256"], item["logical_size"], deadline_at=deadline_at)[1:]:
            objects[entry["path"]] = entry
    return [objects[path] for path in sorted(objects)]


def _object_references(root, deadline_at):
    """Every retained sidecar is a root, even for unresolved/selected history."""
    helper, artifacts = _retention(), _module("publication_artifacts")
    references, controls = set(), {}
    generations = root / "deterministic-live-publication" / "generations"
    for generation in sorted(generations.iterdir()):
        if generation.name == ".DS_Store":
            continue
        helper._directory(generation)
        for name in artifacts.DIRECTORIES:
            directory = generation / name
            if not directory.exists():
                continue
            helper._directory(directory)
            for parent, directories, filenames in os.walk(directory, followlinks=False):
                helper._check_deadline(deadline_at)
                for child in directories:
                    helper._directory(Path(parent) / child)
                for filename in sorted(filenames):
                    helper._check_deadline(deadline_at)
                    path = Path(parent) / filename
                    helper._regular(path)
                    if not path.name.endswith(".publication.json"):
                        continue
                    logical = Path(str(path)[:-len(".publication.json")])
                    references.update(map(str, artifacts.referenced_objects(logical, deadline_at=deadline_at)))
                    controls[str(path)] = helper._digest(path, deadline_at=deadline_at)
    return references, controls


def collect_retired_objects(root, subject, *, execute=False, owner_fd=None, shared_lock=None,
                            deadline_at=None, budget=None):
    """GC only objects proved by this terminal receipt and no surviving sidecar.

    Unknown/orphan objects are never guessed disposable. Deletion does not grant
    new capacity: the controller must perform a fresh complete inventory.
    """
    root, subject, kind = _layout(subject, root)
    _check(kind == "generation", "shared object GC requires a generation")
    helper = _retention()
    if execute:
        helper._check_owner(owner_fd, shared_lock)
        _check(budget is not None, "execution requires an admitted storage budget")
    local = classify_history(subject, root=root, deadline_at=deadline_at)
    _check(local["disposition"] == "retired_terminal", "GC requires completed terminal retirement")
    store = root / "deterministic-live-publication" / "publication-objects"
    entries = _validated_shared_objects(root, local["intent"].get("shared_objects", []))
    gc_intent_path, gc_receipt_path = subject / "terminal-object-gc.intent.json", subject / "terminal-object-gc.json"
    retirement_sha = helper._digest(subject / RECEIPT)["sha256"]
    references, controls = _object_references(root, deadline_at)
    if gc_receipt_path.exists():
        done, planned = helper._json(gc_receipt_path), helper._json(gc_intent_path)
        _validate_gc_intent(planned, entries, subject, retirement_sha)
        _check(done.get("phase") == "complete" and done.get("intent_sha256") == helper._digest(gc_intent_path)["sha256"]
               and planned.get("retirement_sha256") == retirement_sha, "object GC completion differs")
        # Content-addressed bytes may legitimately be recreated by later runs.
        return {"status": "already_collected", "deleted_bytes": 0}
    if gc_intent_path.exists():
        intent = helper._json(gc_intent_path)
        _validate_gc_intent(intent, entries, subject, retirement_sha)
    else:
        selected, discharged = [], []
        for item in entries:
            path = Path(item["path"])
            _check(path.parent == store and path.name == item["sha256"], "shared object escaped store")
            helper._sha(item["sha256"])
            if str(path) in references:
                continue
            if not path.exists() and not path.is_symlink():
                proof = _previous_collection(root, subject, item, deadline_at)
                _check(proof is not None, "shared object missing without verified collection proof")
                discharged.append({"object": item, **proof})
                continue
            actual = helper._digest(path, deadline_at=deadline_at, include_identity=True)
            _check({key: actual[key] for key in ("sha256", "size")} ==
                   {key: item[key] for key in ("sha256", "size")}, "shared object differs")
            selected.append({"path": str(path), **actual})
        intent = {"schema_version": 1, "subject_id": subject.name,
                  "retirement_sha256": retirement_sha, "files": selected,
                  "previous_collections": discharged}
    _validate_gc_intent(intent, entries, subject, retirement_sha)
    protected_objects = [item["path"] for item in intent["files"] if item["path"] in references]
    if protected_objects:
        return {"status": "deferred", "reason": "object_acquired_live_reference",
                "protected_objects": protected_objects, "deleted_bytes": 0}
    if not execute:
        return {"status": "eligible", "files": intent["files"],
                "estimated_bytes": sum(item["size"] for item in intent["files"])}
    if not gc_intent_path.exists():
        _seal_budgeted(gc_intent_path, intent, budget)
    deleted = 0
    for item in intent["files"]:
        helper._check_deadline(deadline_at)
        path = Path(item["path"])
        _check(path.parent == store and path.name == item["sha256"], "GC intent escaped object store")
        if not path.exists() and not path.is_symlink():
            continue
        helper._unchanged(path, item["identity"])
        path.unlink()
        helper._sync_dir(path.parent)
        deleted += item["size"]
    _check(_object_references(root, deadline_at) == (references, controls), "object references changed")
    _seal_budgeted(gc_receipt_path, {"schema_version": 1, "phase": "complete",
                                  "intent_sha256": helper._digest(gc_intent_path)["sha256"]}, budget)
    return {"status": "collected", "deleted_bytes": deleted}


def _validate_gc_intent(intent, entries, subject, retirement_sha):
    _check(intent.get("schema_version") == 1 and intent.get("retirement_sha256") == retirement_sha
           and intent.get("subject_id") == subject.name and isinstance(intent.get("files"), list),
           "object GC intent differs")
    permitted = {entry["path"]: entry for entry in entries}
    seen = set()
    for item in intent["files"]:
        _check(isinstance(item, dict) and set(item) == {"path", "sha256", "size", "identity"},
               "invalid GC intent file")
        _check(item["path"] not in seen and permitted.get(item["path"]) ==
               {key: item[key] for key in ("path", "sha256", "size")},
               "GC intent object is outside terminal receipt")
        identity = item["identity"]
        _check(isinstance(identity, list) and len(identity) == 5
               and all(type(value) is int for value in identity) and identity[2] == item["size"],
               "invalid GC intent identity")
        seen.add(item["path"])
    previous = intent.get("previous_collections", [])
    _check(isinstance(previous, list), "invalid prior collection evidence")
    root = subject.parents[2]
    for entry in previous:
        _check(isinstance(entry, dict) and isinstance(entry.get("object"), dict),
               "invalid prior collection object")
        item = entry["object"]
        _check(item.get("path") not in seen and permitted.get(item.get("path")) == item,
               "prior collection object is outside terminal receipt")
        receipt = _retention()._inside(root, entry.get("receipt_path"))
        _check(receipt.name == "terminal-object-gc.json" and receipt.parent != subject
               and receipt.parent.parent == subject.parent
               and _retention()._digest(receipt)["sha256"] == entry.get("receipt_sha256"),
               "prior collection receipt changed")
        seen.add(item["path"])


def _selected_evidence(root, runs, generations, deadline_at):
    helper, charge = _retention(), 0
    shared = set()
    for base, identifiers in ((root / "deterministic-dispatcher-proposal" / "runs", runs),
                              (root / "deterministic-live-publication" / "generations", generations)):
        _check(len(set(identifiers)) == len(identifiers), "duplicate selected evidence identity")
        for identifier in identifiers:
            _check(isinstance(identifier, str) and Path(identifier).name == identifier,
                   "invalid selected evidence identity")
            directory = helper._directory(base / identifier)
            for parent, directories, files in os.walk(directory, followlinks=False):
                helper._check_deadline(deadline_at)
                for name in directories:
                    helper._directory(Path(parent) / name)
                for name in files:
                    helper._check_deadline(deadline_at)
                    info = helper._regular(Path(parent) / name)
                    path = Path(parent) / name
                    charge += max(info.st_size, info.st_blocks * 512)
                    if name.endswith(".publication.json"):
                        logical = Path(str(path)[:-len(".publication.json")])
                        shared.update(_module("publication_artifacts").referenced_objects(logical, deadline_at=deadline_at))
                    elif name == "storage-cold-archive.json":
                        manifest = _module("storage_cold_archive")._manifest_metadata(directory)
                        shared.update(Path(entry["object_path"]) for entry in manifest["objects"].values())
                    elif name.endswith(".retained.json"):
                        receipt = helper._json(path)
                        helper._sha(receipt.get("sha256"))
                        _check(receipt.get("original_path") == str(path)[:-len(".retained.json")],
                               "selected archived export binding differs")
                        shared.add(directory.parent.parent / "retained-blobs" / (receipt["sha256"] + ".gz"))
    for path in shared:
        helper._check_deadline(deadline_at)
        info = helper._regular(path)
        charge += max(info.st_size, info.st_blocks * 512)
    _check(charge <= 10_000_000_000, "selected evidence exceeds ten GB ceiling")
    return charge


def _previous_collection(root, subject, item, deadline_at):
    helper = _retention()
    for previous in sorted((root / "deterministic-live-publication" / "generations").iterdir()):
        helper._check_deadline(deadline_at)
        if previous == subject or not (previous / "terminal-object-gc.json").exists():
            continue
        local = _validate_local(previous, root, deadline_at=deadline_at)
        _check(local["disposition"] == "retired_terminal", "prior GC lost terminal authority")
        prior_intent = previous / "terminal-object-gc.intent.json"
        intent, receipt = helper._json(prior_intent), helper._json(previous / "terminal-object-gc.json")
        _validate_gc_intent(intent, _validated_shared_objects(root, local["intent"].get("shared_objects", [])),
                            previous, helper._digest(previous / RECEIPT)["sha256"])
        _check(receipt.get("phase") == "complete" and receipt.get("intent_sha256") == helper._digest(prior_intent)["sha256"],
               "prior GC completion binding differs")
        if any({key: candidate[key] for key in ("path", "sha256", "size")} == item for candidate in intent["files"]):
            path = previous / "terminal-object-gc.json"
            return {"receipt_path": str(path.relative_to(root)), "receipt_sha256": helper._digest(path)["sha256"]}
    return None


PROJECTION_INTENT = "projection-retirement.intent.json"
PROJECTION_RECEIPT = "projection-retirement.json"


def _retire_published_projection(run_dir, *, scratch_dir, budget, owner_fd, shared_lock,
                                 deadline_at=None, selected_runs=(), selected_generations=(), _closure=None):
    """Retire one obsolete accepted projection under registered work authority.

    Terminal classification alone does not authorize an external path. The
    configured scratch namespace, run identity and current ledger registration
    are independently checked before the exact source can be unlinked.
    """
    root, run, kind = _layout(run_dir)
    _check(kind == "run", "projection subject is not a dispatcher run")
    helper = _retention()
    helper._check_owner(owner_fd, shared_lock)
    _check(budget is not None, "projection retirement requires admitted budget")
    scratch = Path(scratch_dir).absolute()
    _check(scratch.resolve() == scratch and not scratch.is_symlink()
           and budget.category_for(scratch) == "work", "scratch root is not registered work")
    target = scratch / run.name / "publication_snapshot.sqlite3"
    _check(target.resolve() == target and budget.category_for(target) == "work",
           "projection is not direct registered work")
    options = {"selected_runs": selected_runs, "selected_generations": selected_generations}
    closure = _closure if _closure is not None else operational_closure(root, deadline_at=deadline_at, **options)
    terminal = _module("terminal_publication").classify_run(run, deadline_at=deadline_at)
    if (run.name in closure["runs"] or terminal["disposition"] != "terminal"
            or terminal["terminal_kind"] not in {"published", "noop", "recovered_published"}
            or set(terminal["generation_ids"]) & set(closure["generations"])):
        return {"status": "deferred", "subject_id": run.name, "reason": "operational_closure",
                "deleted_bytes": 0}
    request = helper._json(run / "publication_request.json")
    snapshot = request.get("publication_snapshot")
    _check(isinstance(snapshot, dict) and snapshot.get("path") == str(target)
           and request.get("run_id") == run.name
           and snapshot.get("source_database") != str(target), "projection target differs from registered cycle")
    for suffix in ("-wal", "-shm", "-journal"):
        sidecar = Path(str(target) + suffix)
        _check(not sidecar.exists() and not sidecar.is_symlink(), "projection has unsealed SQLite sidecars")
    source = Path(snapshot["source_database"])
    if target.exists() and source.exists():
        _check(not os.path.samefile(target, source), "projection aliases its source database")
    intent_path, receipt_path = run / PROJECTION_INTENT, run / PROJECTION_RECEIPT
    controls = _controls(root, terminal["controls"], deadline_at)
    binding = {"schema_version": 1, "subject_id": run.name, "run_path": str(run),
               "scratch_dir": str(scratch), "target": str(target), "snapshot": snapshot,
               "terminal_kind": terminal["terminal_kind"], "controls": controls,
               "scope_hash": budget.scope_hash, "automatic_replay_allowed": False}
    if intent_path.exists() or intent_path.is_symlink():
        intent = helper._json(intent_path)
        _check(all(intent.get(key) == value for key, value in binding.items())
               and intent.get("phase") == "intent", "projection intent binding changed")
        proof = intent.get("file", {})
        _check(proof.get("sha256") == snapshot["sha256"] and proof.get("size") == snapshot["size_bytes"]
               and isinstance(proof.get("identity"), list) and len(proof["identity"]) == 5
               and all(type(value) is int for value in proof["identity"])
               and proof["identity"][2] == proof["size"], "projection intent file differs")
    else:
        _check(not receipt_path.exists() and not receipt_path.is_symlink(), "projection completion lacks intent")
        info = helper._regular(target)
        _check(info.st_nlink == 1, "projection payload has filesystem aliases")
        proof = helper._digest(target, deadline_at=deadline_at, include_identity=True)
        _check({key: proof[key] for key in ("sha256", "size")} ==
               {"sha256": snapshot["sha256"], "size": snapshot["size_bytes"]}, "projection bytes changed")
        intent = {**binding, "phase": "intent", "file": proof,
                  "created_at": datetime.now(UTC).isoformat()}
    if receipt_path.exists() or receipt_path.is_symlink():
        receipt = helper._json(receipt_path)
        _check(receipt.get("schema_version") == 1 and receipt.get("phase") == "complete"
               and receipt.get("intent_sha256") == helper._digest(intent_path, deadline_at=deadline_at)["sha256"]
               and not target.exists() and not target.is_symlink(), "projection completion differs")
        return {"status": "already_retired", "subject_id": run.name, "deleted_bytes": 0}
    if not intent_path.exists():
        helper._unchanged(target, proof["identity"])
        _seal_budgeted(intent_path, intent, budget)
    deleted = 0
    if target.exists() or target.is_symlink():
        _check(helper._regular(target).st_nlink == 1, "projection payload acquired aliases")
        helper._unchanged(target, proof["identity"])
        helper._check_deadline(deadline_at)
        target.unlink()
        helper._sync_dir(target.parent)
        deleted = proof["size"]
    _check(_controls(root, terminal["controls"], deadline_at) == controls,
           "projection terminal controls changed")
    _check(operational_closure(root, deadline_at=deadline_at, **options) == closure,
           "projection operational closure changed")
    _seal_budgeted(receipt_path, {"schema_version": 1, "phase": "complete",
                    "intent_sha256": helper._digest(intent_path, deadline_at=deadline_at)["sha256"],
                    "completed_at": datetime.now(UTC).isoformat()}, budget)
    return {"status": "retired", "subject_id": run.name, "deleted_bytes": deleted}


def retire_published_projections(root, *, scratch_dir, budget, owner_fd, shared_lock,
                                 deadline_at=None, max_groups=1, selected_runs=(), selected_generations=()):
    """Bounded catch-up for external projections; current/recovery inputs stay."""
    root = Path(root).absolute()
    _check(type(max_groups) is int and max_groups > 0, "invalid projection maintenance bound")
    helper = _retention()
    helper._check_owner(owner_fd, shared_lock)
    selected, deferred = [], []
    closure = operational_closure(root, deadline_at=deadline_at, selected_runs=selected_runs,
                                  selected_generations=selected_generations)
    for run in sorted((root / "deterministic-dispatcher-proposal" / "runs").iterdir()):
        helper._check_deadline(deadline_at)
        if run.name == ".DS_Store":
            continue
        helper._directory(run)
        request_path = run / "publication_request.json"
        if not request_path.exists():
            continue
        request = helper._json(request_path)
        snapshot = request.get("publication_snapshot", {})
        if not isinstance(snapshot, dict) or snapshot.get("path") == str(run / "publication_snapshot.sqlite3"):
            continue
        expected = Path(scratch_dir).absolute() / run.name / "publication_snapshot.sqlite3"
        if snapshot.get("path") != str(expected):
            deferred.append({"subject_id": run.name, "reason": "outside_configured_scratch"})
            continue
        result = _retire_published_projection(run, scratch_dir=scratch_dir, budget=budget, owner_fd=owner_fd,
                    shared_lock=shared_lock, deadline_at=deadline_at, selected_runs=selected_runs,
                    selected_generations=selected_generations, _closure=closure)
        if result["status"] == "deferred":
            deferred.append(result)
        elif result["status"] != "already_retired":
            selected.append(result)
        if len(selected) >= max_groups:
            break
    return {"status": "complete", "selected": selected, "deferred": deferred,
            "deleted_bytes": sum(item["deleted_bytes"] for item in selected)}


def retire_published_projection(run_dir, *, scratch_dir, budget, owner_fd, shared_lock,
                                deadline_at=None, selected_runs=(), selected_generations=()):
    """Retire one external projection with a freshly verified operational closure."""
    return _retire_published_projection(run_dir, scratch_dir=scratch_dir, budget=budget,
                owner_fd=owner_fd, shared_lock=shared_lock, deadline_at=deadline_at,
                selected_runs=selected_runs, selected_generations=selected_generations)
