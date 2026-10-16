"""Read-only classification of publication control evidence.

This module deliberately uses only the standard library. It does not open a
database, hash snapshot payloads, inspect processes, recover a publication, or
grant deletion authority. A terminal publication is not a completeness claim.
Missing evidence protects a run; contradictory or unsafe evidence raises.
"""

import hashlib
import json
import os
import re
import stat
import time
from datetime import UTC, datetime
from pathlib import Path

PUBLICATION_BINDINGS = (
    "run_id", "source_manifest_sha256", "registry_sha256", "expected_source_ids",
    "worker_acceptance_path", "worker_acceptance_sha256", "worker_database",
    "worker_database_files",
)
_REQUEST_BINDINGS = (*PUBLICATION_BINDINGS, "publication_snapshot", "publication_policy")
_OPTIONAL_BINDINGS = ("publication_snapshot", "publication_policy", "recovery_generation_binding")
_SHA = re.compile(r"[0-9a-f]{64}")
_GENERATION = re.compile(r"[0-9a-f]{32}")


def _require(condition, message):
    if not condition:
        raise ValueError("Publication classification: " + message)


def _sha(value):
    _require(isinstance(value, str) and _SHA.fullmatch(value), "invalid SHA-256")
    return value


def _clock(value):
    try:
        parsed = datetime.fromisoformat(value)
    except (AttributeError, TypeError, ValueError) as exc:
        raise ValueError("Publication classification: invalid timestamp") from exc
    _require(parsed.tzinfo is not None, "timestamp has no timezone")
    return parsed


def _path(value):
    _require(isinstance(value, (str, Path)) and bool(str(value)), "missing path")
    path = Path(value)
    _require(path.is_absolute() and ".." not in path.parts, "path must be absolute")
    return path


def _same_path(value, expected):
    # Do not follow evidence-controlled symlinks to establish identity.
    return _path(value) == expected


def _snapshot_path(value, run):
    """Validate metadata shape only; external deletion needs registered authority."""
    path = _path(value)
    return (path == run / "publication_snapshot.sqlite3" or
            (path.name == "publication_snapshot.sqlite3" and path.parent.name == run.name))


def _identity(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _check_deadline(deadline_at):
    if deadline_at is not None and time.monotonic() >= deadline_at:
        raise TimeoutError("Publication classification deadline exhausted")


def _request_identity(request):
    # Match publication_snapshot's immutable recovery identity, including the
    # optional activated policy without changing identities of legacy requests.
    payload = {key: request[key] for key in _REQUEST_BINDINGS if key in request}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"))
                          .encode()).hexdigest()


class _Missing(Exception):
    def __init__(self, path):
        self.path = path


class _Controls:
    def __init__(self, deadline_at):
        self.deadline_at = deadline_at
        self.records = {}

    def read(self, path, *, optional=False):
        _check_deadline(self.deadline_at)
        path = _path(path)
        if path in self.records:
            return self.records[path][0]
        # Check each ancestor, not just the final file: O_NOFOLLOW alone does
        # not stop a recovery subdirectory from redirecting the read.
        for parent in reversed(path.parents):
            try:
                info = parent.lstat()
            except FileNotFoundError:
                if optional:
                    return None
                raise _Missing(path) from None
            _require(stat.S_ISDIR(info.st_mode), f"unsafe control parent: {parent}")
        try:
            before = path.lstat()
        except FileNotFoundError:
            if optional:
                return None
            raise _Missing(path) from None
        _require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1,
                 f"control is not a private regular file: {path}")
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        try:
            opened = os.fstat(fd)
            _require(_identity(before) == _identity(opened), f"control changed: {path}")
            chunks = []
            digest = hashlib.sha256()
            while True:
                _check_deadline(self.deadline_at)
                chunk = os.read(fd, 1024 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
                chunks.append(chunk)
            after = os.fstat(fd)
        finally:
            os.close(fd)
        _require(_identity(before) == _identity(after), f"control changed: {path}")
        try:
            value = json.loads(b"".join(chunks))
        except (UnicodeError, ValueError) as exc:
            raise ValueError(f"Publication classification: invalid JSON: {path}") from exc
        _require(isinstance(value, dict), f"control is not an object: {path}")
        self.records[path] = (value, digest.hexdigest(), _identity(after), after.st_size)
        self.verify()
        return value

    def digest(self, path):
        self.read(path)
        return self.records[path][1]

    def reference(self, reference, *, root=None, expected=None):
        _require(isinstance(reference, dict), "missing control reference")
        value = reference.get("path")
        _require(isinstance(value, str) and bool(value), "missing reference path")
        path = Path(value)
        _require(".." not in path.parts, "reference traverses parent")
        if not path.is_absolute():
            _require(root is not None, "relative reference lacks root")
            path = root / path
        if root is not None:
            _require(path.is_relative_to(root), "reference escapes run")
        if expected is not None:
            _require(path == expected, "reference points to another control")
        value = self.read(path)
        _require(self.digest(path) == _sha(reference.get("sha256")),
                 f"control reference digest differs: {path}")
        if "size_bytes" in reference:
            _require(type(reference["size_bytes"]) is int
                     and reference["size_bytes"] == self.records[path][3],
                     "control reference size differs")
        return path, value

    def verify(self):
        for path, record in self.records.items():
            _check_deadline(self.deadline_at)
            try:
                current = path.lstat()
            except FileNotFoundError as exc:
                raise ValueError(f"Publication classification: control disappeared: {path}") from exc
            _require(stat.S_ISREG(current.st_mode) and current.st_nlink == 1
                     and _identity(current) == record[2], f"control changed: {path}")


def validate_published_result(result, request, *, request_sha256, latest=None):
    """Validate the runner receipt contract, including its exact request hash.

    Accepts the four runner result statuses; callers must separately require a
    terminal status. ``latest`` is an aware datetime, defaulting to UTC now.
    No paths or snapshot bytes are opened by this function.
    """
    _require(isinstance(result, dict) and result.get("schema_version") == 1,
             "invalid publication result")
    _require(isinstance(request, dict), "invalid publication request")
    _require(result.get("publication_request_sha256") == _sha(request_sha256),
             "publication result request digest differs")
    for key in _OPTIONAL_BINDINGS:
        _require((key in result) == (key in request), f"{key} presence differs")
    for key in (*PUBLICATION_BINDINGS, *(key for key in _OPTIONAL_BINDINGS if key in request)):
        _require(key in request and result.get(key) == request[key],
                 f"publication result binding differs: {key}")
    status = result.get("status")
    _require(status in {"published", "incomplete", "noop", "deferred"},
             "unrecognized publication status")
    _require(_clock(request.get("generated_at")) <= _clock(result.get("generated_at"))
             <= (latest or datetime.now(UTC)), "publication result time differs")
    _sha(result.get("observation_set_sha256"))
    _require(result.get("whole_job_completeness_certified") is False,
             "publication cannot certify whole-job completeness")
    publication = result.get("publication")
    _require(isinstance(publication, dict), "missing inner publication receipt")
    if status == "published":
        _require(publication.get("state") == "complete"
                 and publication.get("status") == "published"
                 and publication.get("database_transactions_complete") is True
                 and _GENERATION.fullmatch(str(publication.get("generation_id", ""))),
                 "published generation is not complete")
    if status == "deferred":
        _require(publication.get("status") == "deferred"
                 and publication.get("generation_not_started") is True
                 and not publication.get("generation_id")
                 and not request.get("recover_publication")
                 and "recovery_generation_binding" not in request,
                 "deferred result lacks no-generation proof")
    if status == "noop":
        _require(publication.get("status") == "no_changes"
                 and publication.get("generation_not_started") is True
                 and not publication.get("generation_id"),
                 "noop result claims a generation")
    if "recovery_generation_binding" in request:
        binding = request["recovery_generation_binding"]
        _require(isinstance(binding, dict)
                 and result["observation_set_sha256"] == binding.get("observation_set_sha256")
                 and publication.get("generation_id") == binding.get("generation_id"),
                 "publication returned another recovery generation")


def _validate_original(run, outcome, request, controls):
    _require(request.get("schema_version") == 1 and request.get("run_id") == run.name,
             "original publication request identity differs")
    _require(not request.get("recover_publication")
             and "recovery_generation_binding" not in request,
             "original publication request is a recovery request")
    for key in ("source_manifest_sha256", "registry_sha256", "worker_acceptance_sha256"):
        _sha(request.get(key))
    sources = request.get("expected_source_ids")
    _require(isinstance(sources, list) and sources
             and all(isinstance(value, str) and value for value in sources)
             and len(set(sources)) == len(sources), "invalid publication source scope")
    _require(_clock(request.get("generated_at")) <= _clock(request.get("deadline_at")),
             "original request time window differs")
    _require(request.get("worker_status") in {"complete", "incomplete"},
             "worker status is not accepted")
    worker_request = controls.read(run / "request.json")
    _require(worker_request.get("run_id") == run.name
             and worker_request.get("source_manifest_sha256") == request["source_manifest_sha256"]
             and worker_request.get("expected_source_ids") == sources
             and worker_request.get("source_registry", {}).get("sha256")
             == request["registry_sha256"], "worker request publication binding differs")
    intent = controls.read(run / "publication_intent.json")
    _require(intent.get("run_id") == run.name
             and intent.get("request_sha256") == controls.digest(run / "publication_request.json"),
             "original publication intent binding differs")
    _sha(intent.get("config_sha256"))
    _require(isinstance(intent.get("command_template"), list) and intent["command_template"]
             and all(isinstance(value, str) and value for value in intent["command_template"]),
             "intent command binding missing")
    _path(intent.get("cwd"))
    _require(_clock(intent.get("created_at")) >= _clock(request["generated_at"]),
             "intent predates publication request")
    acceptance_path = run / "acceptance.json"
    _require(_same_path(request.get("worker_acceptance_path"), acceptance_path),
             "acceptance path differs")
    acceptance = controls.read(acceptance_path)
    acceptance_sha = controls.digest(acceptance_path)
    _require(acceptance_sha == request["worker_acceptance_sha256"]
             and outcome.get("worker_report_sha256") == acceptance_sha,
             "acceptance digest differs")
    _require(acceptance.get("schema_version") == 1 and acceptance.get("run_id") == run.name
             and acceptance.get("source_manifest_sha256") == request["source_manifest_sha256"],
             "acceptance identity differs")
    worker_database = _path(request.get("worker_database"))
    _require(_same_path(acceptance.get("database"), worker_database),
             "acceptance worker database differs")
    files = request.get("worker_database_files")
    _require(isinstance(files, list) and files, "worker database file binding missing")
    for record in files:
        _require(isinstance(record, dict), "invalid worker database file binding")
        _path(record.get("path"))
        _sha(record.get("sha256"))
    snapshot = request.get("publication_snapshot")
    if "publication_snapshot" in request:
        _require(isinstance(snapshot, dict) and snapshot.get("schema_version") == 1
                 and snapshot.get("kind") in {"sqlite_backup", "sqlite_publication_projection"}
                 and _snapshot_path(snapshot.get("path"), run)
                 and _same_path(snapshot.get("source_database"), worker_database)
                 and snapshot.get("worker_acceptance_sha256") == acceptance_sha
                 and type(snapshot.get("size_bytes")) is int and snapshot["size_bytes"] >= 0,
                 "publication snapshot metadata differs")
        _sha(snapshot.get("sha256"))
        _require(files == [{"path": snapshot["path"], "sha256": snapshot["sha256"]}],
                 "publication snapshot file binding differs")
    else:
        _require(files[0]["path"] == str(worker_database)
                 and len(files) <= 2
                 and (len(files) == 1 or files[1]["path"] == str(worker_database) + "-wal"),
                 "legacy worker database file binding differs")


def _validate_generation_binding(run, request, binding, publication):
    root = run.parents[2]
    generation = binding.get("generation_id")
    _require(_GENERATION.fullmatch(str(generation or "")), "invalid recovery generation ID")
    expected_plan = root / "deterministic-live-publication" / "generations" / generation / "plan.json"
    effective = request.get("publication_snapshot", {}).get("path", request["worker_database"])
    _require(binding.get("schema_version") == 1
             and binding.get("kind") == "existing_publication_generation"
             and binding.get("request_identity_sha256") == _request_identity(request)
             and binding.get("initial_gate_state") in {"publishing", "exporting"}
             and _same_path(binding.get("worker_database"), _path(effective))
             and _same_path(binding.get("output_dir"), root / "output")
             and _same_path(binding.get("state_dir"), root / "deterministic-live-publication")
             and _same_path(binding.get("plan_path"), expected_plan)
             and binding.get("registry_sha256") == request["registry_sha256"]
             and binding.get("original_generated_at") == request["generated_at"]
             and binding.get("original_deadline_at") == request["deadline_at"]
             and type(binding.get("request_ownership_verified")) is bool
             and "expiry_recovery_authorization" in binding,
             "recovery generation immutable binding differs")
    _sha(binding.get("plan_sha256"))
    _sha(binding.get("observation_set_sha256"))
    _require(publication.get("generation_id") == generation
             and publication.get("plan_sha256") == binding["plan_sha256"]
             and _same_path(publication.get("plan_path"), expected_plan)
             and publication.get("observation_set_sha256") == binding["observation_set_sha256"],
             "recovered generation publication binding differs")
    authorization = binding["expiry_recovery_authorization"]
    if authorization is not None:
        _require(isinstance(authorization, dict)
                 and authorization.get("schema_version") == 1
                 and authorization.get("kind") == "reviewed_expired_publication"
                 and all(authorization.get(key) == binding[key] for key in (
                     "request_identity_sha256", "generation_id", "plan_sha256",
                     "original_generated_at", "original_deadline_at")),
                 "expired recovery authorization differs")
        _clock(authorization.get("gate_started_at"))


def classify_run(run, *, allow_missing_generation_result=False, deadline_at=None,
                 allow_unfinished_no_write_state=False):
    """Return a conservative classification of a run's *present* controls.

    ``controls`` contains only existing, read and verified paths. Missing files
    are explicit ``known_deficits``. The historical-review opt-in tolerates only
    an absent recovered generation result after validating all other bindings;
    it never synthesizes that result or authorizes retirement on its own.
    """
    _require(type(allow_missing_generation_result) is bool, "invalid missing-result option")
    _require(type(allow_unfinished_no_write_state) is bool, "invalid no-write state option")
    run = Path(os.path.abspath(run))
    controls = _Controls(deadline_at)
    answer = {"disposition": "unresolved", "terminal_kind": None, "result": None,
              "controls": [], "generation_ids": [], "known_deficits": []}

    def generation(value):
        if value is not None:
            _require(_GENERATION.fullmatch(str(value)), "invalid referenced generation ID")
            if value not in answer["generation_ids"]:
                answer["generation_ids"].append(value)

    def finish():
        controls.verify()
        answer["controls"] = list(controls.records)
        return answer

    def deficit(path, code="missing_control"):
        answer["known_deficits"].append({"code": code, "path": str(path)})

    try:
        outcome = controls.read(run / "outcome.json", optional=True)
        state = controls.read(run / "state.json", optional=True)
        for document in (outcome, state):
            if document is not None:
                _require(document.get("run_id") == run.name, "run identity differs")
        original_result = controls.read(run / "publication_result.json", optional=True)
        answer["result"] = original_result
        if original_result is not None:
            publication = original_result.get("publication", {})
            _require(isinstance(publication, dict), "invalid publication reference")
            generation(publication.get("generation_id"))
        binding = controls.read(run / "publication_generation_binding.json", optional=True)
        if binding is not None:
            generation(binding.get("generation_id"))
        if any(document and document.get("status") == "running" for document in (outcome, state)):
            answer["disposition"] = "active"
            return finish()
        if outcome is None:
            raise _Missing(run / "outcome.json")
        request_path = run / "publication_request.json"
        request = controls.read(request_path)
        _validate_original(run, outcome, request, controls)
        request_sha = controls.digest(request_path)
        resolution_path = run / "publication_resolution.json"
        resolution = controls.read(resolution_path, optional=True)
        no_write_path = run / "publication_no_write_resolution.json"
        no_write = controls.read(no_write_path, optional=True)
        _require(not (resolution and no_write), "conflicting recovery and no-write resolutions")
        direct = (outcome.get("status") in {"complete", "incomplete"}
                  and outcome.get("publication_status") in {"published", "noop"}
                  and outcome.get("publication_recovery_allowed") is False)
        if direct:
            _require(resolution is None and no_write is None and binding is None,
                     "direct terminal run has recovery controls")
            if original_result is None:
                raise _Missing(run / "publication_result.json")
            _require(outcome.get("publication_result_sha256")
                     == controls.digest(run / "publication_result.json"), "outcome result hash differs")
            validate_published_result(original_result, request, request_sha256=request_sha)
            _require(original_result["status"] == outcome["publication_status"],
                     "outcome publication status differs")
            if state is not None:
                _require(all(state.get(key) == outcome.get(key) for key in (
                    "status", "publication_status", "publication_recovery_allowed",
                    "publication_result_sha256")), "terminal state differs from outcome")
            answer.update(disposition="terminal", terminal_kind=original_result["status"])
            return finish()
        if resolution is None and no_write is None:
            deficit(resolution_path, "unresolved_publication")
            return finish()
        _require(outcome.get("publication_status") in {"unknown_requires_review_no_replay", "incomplete"},
                 "resolution has no unresolved original publication")
        selected = resolution if resolution is not None else no_write
        _require(selected.get("run_id") == run.name
                 and selected.get("previous_outcome_sha256") == controls.digest(run / "outcome.json"),
                 "resolution original outcome differs")
        pending_no_write_state = (allow_unfinished_no_write_state and no_write is not None
                                  and (state is None or state == outcome))
        if not pending_no_write_state:
            if state is None:
                raise _Missing(run / "state.json")
            _require(state.get("status") in {"complete", "incomplete"}
                     and state.get("publication_recovery_allowed") is False,
                     "resolved run state is not terminal")
        if no_write is not None:
            _require(binding is None and (pending_no_write_state or (
                     state.get("publication_status") == "deferred"
                     and state.get("generation_not_started") is True
                     and state.get("worker_reexecuted") is False))
                     and no_write.get("original_request_sha256") == request_sha
                     and no_write.get("original_intent_sha256")
                     == controls.digest(run / "publication_intent.json"), "no-write resolution differs")
            _require(original_result is None
                     or not original_result.get("publication", {}).get("generation_id"),
                     "no-write resolution contradicts original generation")
            proof = no_write.get("proof")
            _require(isinstance(proof, dict) and proof.get("schema_version") == 1
                     and proof.get("kind") == "verified_unstarted_publication"
                     and proof.get("request_identity_sha256") == _request_identity(request)
                     and proof.get("generation_not_started") is True, "no-write proof differs")
            gate = proof.get("prior_gate")
            _require(isinstance(gate, dict) and gate.get("state") == "complete"
                     and gate.get("status") == "published"
                     and gate.get("database_transactions_complete") is True
                     and gate.get("request_identity_sha256") != _request_identity(request)
                     and _clock(gate.get("completed_at")) < _clock(request["generated_at"]),
                     "prior gate does not prove unstarted publication")
            _sha(proof.get("prior_gate_sha256"))
            generation(gate.get("generation_id"))
            _require(gate.get("generation_id") is not None, "prior gate lacks generation")
            prior = run.parents[2] / "deterministic-live-publication" / "generations" / gate["generation_id"]
            plan_path, _ = controls.reference(proof.get("prior_plan"), expected=prior / "plan.json")
            _, prior_result = controls.reference(proof.get("prior_result"), expected=prior / "result.json")
            _require(_same_path(gate.get("plan_path"), plan_path)
                     and gate.get("plan_sha256") == controls.digest(plan_path)
                     and prior_result == gate, "prior gate/plan/result binding differs")
            answer.update(disposition="terminal", terminal_kind="verified_unstarted")
            if pending_no_write_state:
                answer["no_write_state_pending"] = True
            return finish()
        _require(state.get("publication_status") == "published"
                 and state.get("publication_resolution_sha256") == controls.digest(resolution_path),
                 "recovered final state resolution differs")
        recovery_path, recovery = controls.reference(resolution.get("request"), root=run)
        _, result = controls.reference(resolution.get("result"), root=run)
        answer["result"] = result
        _require(recovery.get("recover_publication") is True
                 and recovery.get("original_publication_request")
                 == {"path": str(request_path), "sha256": request_sha}
                 and recovery.get("original_publication_intent_sha256")
                 == controls.digest(run / "publication_intent.json"), "recovery original intent differs")
        for key in _REQUEST_BINDINGS:
            _require((key in recovery) == (key in request)
                     and recovery.get(key) == request.get(key), f"recovery original binding differs: {key}")
        _require(_clock(recovery.get("generated_at")) >= _clock(request["generated_at"]),
                 "recovery request predates original")
        if binding is None:
            raise _Missing(run / "publication_generation_binding.json")
        _require(recovery.get("recovery_generation_binding") == binding,
                 "recovery generation control differs")
        validate_published_result(result, recovery, request_sha256=controls.digest(recovery_path))
        _require(result["status"] == "published", "recovered publication is not published")
        publication = result["publication"]
        generation(publication.get("generation_id"))
        _validate_generation_binding(run, request, binding, publication)
        generation_path = (run.parents[2] / "deterministic-live-publication" / "generations"
                           / binding["generation_id"] / "result.json")
        generation_result = controls.read(generation_path, optional=True)
        if generation_result is None:
            deficit(generation_path, "missing_generation_result")
            if not allow_missing_generation_result:
                return finish()
        else:
            _require(generation_result == publication, "recovered hot generation result differs")
        answer.update(disposition="terminal", terminal_kind="recovered_published")
        return finish()
    except _Missing as exc:
        deficit(exc.path)
        return finish()
