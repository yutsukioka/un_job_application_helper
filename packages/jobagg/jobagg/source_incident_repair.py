"""Explicit, captured-evidence-only repair for the October 2026 source incidents.

Preview is read-only. Apply requires a fresh current-seal plan, the configured
maintenance marker, a complete publication gate and the actual shared owner.
Only selected task status/eligibility/receipt fields and the atomic journal may
change. This command does not fetch, reseal, change schedules or alter quotas.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime
import gzip
import hashlib
import io
import json
from pathlib import Path
import sqlite3
import time
from urllib.parse import urlsplit

from jobagg.captured_transient_repair import validate_captured_transient
from jobagg.deadline_review import active_hold, blocks_detail
from jobagg.pipelines.host_recovery import host_eligibility
from jobagg.pipelines.inventory_checks import verify_listing
from jobagg.pipelines.live_publication import GATE_NAME
from jobagg.pipelines.sync_source import load_sources
from jobagg.pipelines.worker_policy import SharedPolicy
from jobagg.remediation_repair import (
    digest_value,
    failure_artifacts,
    original_listing,
    policy_state,
)
from jobagg.remediation_worker import (
    dump,
    implementation_hash,
    record,
    sha,
    shared_owner,
    task_key,
    utc,
)
from jobagg.storage_retention import _check_owner
from jobagg.vacancy_outcomes import captured_unavailable, unv_empty_assignment_template

KIND = "reviewed_source_incident_repair_v1"
JOURNAL = "source_incident_repairs"
MAX_TASKS = 19
MAX_PLAN_AGE_SECONDS = 900
ACTIONS = {"retry_captured_transient", "unv_absent", "who_unavailable"}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def direct(path, *, exists=True):
    path = Path(path).absolute()
    require(path.resolve() == path and not path.is_symlink(), "Aliased repair path: " + str(path))
    if exists:
        require(path.is_file(), "Required repair file missing: " + str(path))
    return path


def reference(path, *, optional=False):
    path = direct(path, exists=not optional)
    return {"path": str(path), "sha256": sha(path) if path.exists() else None}


def read(path):
    return json.loads(direct(path).read_text())


def timestamp(value):
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    require(parsed.tzinfo is not None, "Evidence timestamp must be timezone-aware")
    return parsed.timestamp()


@contextmanager
def connection(workspace, *, write=False):
    path = direct(Path(workspace) / "jobs.sqlite3")
    conn = sqlite3.connect(
        path.as_uri() + ("?mode=rw" if write else "?mode=ro"), uri=True, timeout=2
    )
    conn.row_factory = sqlite3.Row
    if not write:
        conn.execute("PRAGMA query_only=ON")
    try:
        conn.execute("BEGIN IMMEDIATE" if write else "BEGIN")
        yield conn
        if write:
            conn.commit()
        else:
            conn.rollback()
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()


def argument(argv, name):
    require(
        isinstance(argv, list) and argv.count(name) == 1, "Ambiguous dispatcher argument: " + name
    )
    index = argv.index(name)
    require(index + 1 < len(argv), "Missing dispatcher argument: " + name)
    return argv[index + 1]


def package_implementation_hash(cwd):
    """Read the same sealed manifest from an explicitly configured package cwd."""
    root = Path(cwd).absolute() / "jobagg"
    require(root.resolve() == root and root.is_dir(), "Invalid configured implementation path")
    paths = sorted(root.rglob("*.py"))
    require(0 < len(paths) <= 4096, "Configured implementation exceeds repair read bound")
    return digest_value({str(path.relative_to(root)): sha(direct(path)) for path in paths})


def runtime(workspace, lock, config_path, expected_old):
    workspace, lock = Path(workspace).absolute(), direct(lock)
    require(workspace.resolve() == workspace, "Aliased repair workspace")
    config_path = direct(config_path)
    config = read(config_path)
    marker_path = workspace / "worker_workspace.json"
    marker = read(marker_path)
    worker, publisher = config["worker_argv"], config["publication_argv"]
    require(
        config["shared_lock_path"]
        == str(lock)
        == marker.get("shared_lock")
        == argument(worker, "--shared-lock")
        == argument(publisher, "--shared-lock"),
        "Dispatcher/workspace shared owner differs",
    )
    require(
        argument(worker, "--workspace") == str(workspace)
        and argument(publisher, "--worker-database") == str(workspace / "jobs.sqlite3")
        and config["publication_worker_database"] == str(workspace / "jobs.sqlite3"),
        "Dispatcher worker database differs",
    )
    registry = direct(argument(worker, "--registry"))
    robots = direct(argument(worker, "--robots"))
    require(argument(publisher, "--registry") == str(registry), "Publisher registry differs")
    require(
        marker.get("registry_sha256") == sha(registry)
        and marker.get("robots_sha256") == sha(robots),
        "Registry/robots seal differs",
    )
    candidate = implementation_hash()
    if expected_old is not None:
        require(marker.get("implementation_sha256") == expected_old, "Expected old seal differs")
    else:
        require(
            marker.get("implementation_sha256") == candidate,
            "Current code is not resealed in workspace",
        )
    configured_implementations = {}
    for role in ("worker", "publication"):
        cwd = config.get(role + "_cwd")
        require(isinstance(cwd, str) and Path(cwd).is_absolute(), "Missing configured code cwd")
        fingerprint = package_implementation_hash(cwd)
        require(
            fingerprint == marker["implementation_sha256"],
            "Configured " + role + " implementation differs from workspace seal",
        )
        configured_implementations[role] = {"cwd": cwd, "implementation_sha256": fingerprint}
    output = Path(argument(publisher, "--output-dir")).absolute()
    require(output.resolve() == output, "Aliased publication output")
    maintenance = reference(config["maintenance_file"], optional=True)
    gate = reference(output / GATE_NAME)
    gate_value = read(gate["path"])
    require(
        gate_value.get("state") == "complete"
        and gate_value.get("status") == "published"
        and gate_value.get("database_transactions_complete") is True,
        "Publication gate is not complete and published",
    )
    return {
        "workspace": str(workspace),
        "shared_lock": str(lock),
        "dispatcher_config": reference(config_path),
        "workspace_marker": reference(marker_path),
        "registry": reference(registry),
        "robots": reference(robots),
        "maintenance": maintenance,
        "publication_gate": gate,
        "candidate_implementation_sha256": candidate,
        "sealed_implementation_sha256": marker["implementation_sha256"],
        "configured_implementations": configured_implementations,
        "expected_old_implementation": expected_old,
        "executable": expected_old is None and maintenance["sha256"] is not None,
    }


def normalize_selections(selections):
    require(
        isinstance(selections, list) and 0 < len(selections) <= MAX_TASKS,
        "Select between one and nineteen exact incident tasks",
    )
    require(
        all(
            isinstance(x, dict)
            and set(x).issubset({"task_id", "action", "census_frame"})
            and isinstance(x.get("task_id"), str)
            and x.get("action") in ACTIONS
            for x in selections
        ),
        "Each selection requires an explicit task_id and supported action",
    )
    require(len({x["task_id"] for x in selections}) == len(selections), "Duplicate selected task")
    return sorted(selections, key=lambda x: x["task_id"])


def capture_refs(workspace, paths):
    """Bind metadata and bodies without following capture aliases/escapes."""
    root = (Path(workspace) / "captures").resolve()
    refs = []
    require(0 < len(paths) <= 256, "Capture sequence exceeds repair read bound")
    for value in paths:
        path = direct(value)
        require(path.is_relative_to(root), "Capture metadata escapes worker archive")
        require(path.stat().st_size <= 256 * 1024, "Capture metadata exceeds repair read bound")
        meta = read(path)
        refs.append(reference(path))
        if meta.get("artifact"):
            body = direct(meta["artifact"])
            require(
                body.parent == path.parent and body.is_relative_to(root),
                "Capture body escapes attempt",
            )
            require(
                body.stat().st_size <= 2 * 1024 * 1024
                and type(meta.get("body_bytes")) is int
                and 0 <= meta["body_bytes"] <= 2 * 1024 * 1024,
                "Capture body exceeds repair read bound",
            )
            with gzip.GzipFile(fileobj=io.BytesIO(body.read_bytes())) as stream:
                decoded = stream.read(meta["body_bytes"] + 1)
            require(
                len(decoded) == meta["body_bytes"]
                and hashlib.sha256(decoded).hexdigest() == meta.get("body_sha256"),
                "Captured body size/hash differs",
            )
            refs.append(reference(body))
    return refs


def failed_attempt(conn, task):
    require(
        task["status"] == "blocked" and task["claim"],
        "Selected task is not a claimed blocked incident",
    )
    require(
        task["task_id"] == task_key(task["source_id"], task["kind"], task["external_id"]),
        "Task native identity differs",
    )
    row = conn.execute(
        "SELECT * FROM remediation_attempts WHERE attempt_id=?", (task["claim"],)
    ).fetchone()
    require(row is not None, "Failed attempt is absent")
    attempt = dict(row)
    require(
        all(attempt[k] == task[k] for k in ("task_id", "source_id", "kind", "status"))
        and attempt["finished_at"] is not None
        and json.loads(attempt["evidence"]) == json.loads(task["receipt"]),
        "Failed attempt/claim/receipt differs",
    )
    latest = conn.execute(
        "SELECT attempt_id FROM remediation_attempts WHERE task_id=? ORDER BY started_at DESC LIMIT 1",
        (task["task_id"],),
    ).fetchone()
    require(latest[0] == task["claim"], "Selected claim is not latest task attempt")
    return attempt


def preservation(conn, task):
    detail = task["kind"] == "detail"
    query = (
        "SELECT * FROM jobs WHERE source_id=?"
        + (" AND external_id=?" if detail else "")
        + " ORDER BY job_key"
    )
    params = (task["source_id"], task["external_id"]) if detail else (task["source_id"],)
    rows = [dict(x) for x in conn.execute(query, params)]
    require(
        task["kind"] != "detail" or len(rows) == 1, "Detail job preservation identity is ambiguous"
    )
    observations = (
        [
            dict(x)
            for x in conn.execute(
                "SELECT * FROM remediation_observations WHERE "
                + ("job_key=?" if detail else "source_id=?")
                + " ORDER BY job_key",
                (rows[0]["job_key"] if detail else task["source_id"],),
            )
        ]
        if rows or not detail
        else []
    )
    attempts = [
        dict(x)
        for x in conn.execute(
            "SELECT * FROM remediation_attempts WHERE task_id=? ORDER BY attempt_id",
            (task["task_id"],),
        )
    ]
    holds = (
        [
            dict(x)
            for x in conn.execute(
                "SELECT * FROM remediation_deadline_holds WHERE task_id=? ORDER BY hold_id",
                (task["task_id"],),
            )
        ]
        if conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='remediation_deadline_holds'"
        ).fetchone()
        else []
    )
    return {
        "jobs_sha256": digest_value(rows),
        "observations_sha256": digest_value(observations),
        "attempts_sha256": digest_value(attempts),
        "attempt_count": len(attempts),
        "deadline_holds_sha256": digest_value(holds),
    }


def unv_null_evidence(workspace, task, payload):
    _, paths = failure_artifacts(workspace, task)
    require(len(paths) == 1, "Null assignment must have exactly one captured detail request")
    meta = read(paths[0])
    refs = capture_refs(workspace, paths)
    if meta.get("source_binding") is not None:
        evidence = captured_unavailable(task["source_id"], task["external_id"], paths)
        require(
            evidence and evidence.get("category") == "vacancy_detail_empty",
            "UNV capture is not typed empty assignment",
        )
        return evidence, refs
    # Explicit legacy repair route: never synthesize source_binding metadata.
    identity = task["external_id"]
    listing = payload["listing"]
    url = "https://app.unv.org/api/doa/doa/" + identity
    require(
        listing.get("source_id") == "unv_uvp"
        and str(listing.get("external_id")) == identity
        and str(listing.get("raw", {}).get("id") or listing.get("raw", {}).get("doaRequestNo"))
        == identity,
        "Legacy UNV listing has no native assignment identity",
    )
    require(
        meta.get("state") == "response_captured"
        and meta.get("status_code") == 200
        and meta.get("method") == "GET"
        and meta.get("body_captured") is True
        and meta.get("phase") == {"kind": "detail", "job_id": identity}
        and str(meta.get("external_id")) == identity
        and meta.get("url") == url == meta.get("response_url")
        and meta.get("request_url_sha256") == hashlib.sha256(url.encode()).hexdigest()
        and meta.get("response_url_sha256") == hashlib.sha256(url.encode()).hexdigest(),
        "Legacy UNV capture source/phase/URL/identity differs",
    )
    body = gzip.decompress(direct(meta["artifact"]).read_bytes())
    require(
        hashlib.sha256(body).hexdigest() == meta.get("body_sha256")
        and len(body) == meta.get("body_bytes")
        and timestamp(meta["started_at"]) <= timestamp(meta["finished_at"]),
        "Legacy UNV body/interval differs",
    )
    mime = {k.lower(): v for k, v in meta.get("response_headers", {}).items()}.get(
        "content-type", ""
    )
    require(mime.split(";", 1)[0].strip().lower() == "application/json", "Legacy UNV MIME differs")
    detected = unv_empty_assignment_template("unv_uvp", identity, url, url, body)
    require(detected is not None, "Legacy UNV body is not exact successful null assignment")
    return {
        **detected,
        "source_id": "unv_uvp",
        "external_id": identity,
        "request_url": url,
        "response_url": url,
        "body_sha256": meta["body_sha256"],
        "observed_at": meta["finished_at"],
        "captures": [reference(paths[0])],
        "closure_inferred": False,
        "detail_complete": False,
        "legacy_source_authority": "validated_original_task_listing_and_attempt_not_synthetic_metadata",
    }, refs


def latest_unv_census(conn, workspace, source, selection, failed_at, *, present=False):
    state = conn.execute(
        "SELECT * FROM remediation_sources WHERE source_id=?", (source.id,)
    ).fetchone()
    attempt = conn.execute(
        "SELECT * FROM remediation_attempts WHERE source_id=? AND kind='listing' AND status='done' ORDER BY finished_at DESC LIMIT 1",
        (source.id,),
    ).fetchone()
    require(state is not None and attempt is not None, "Latest UNV listing state/attempt missing")
    state, attempt = dict(state), dict(attempt)
    evidence = json.loads(attempt["evidence"])
    frame_path = direct(evidence["frame_path"])
    require(
        frame_path.parent == Path(workspace) / "captures" / attempt["attempt_id"]
        and frame_path.name == "listing.json"
        and sha(frame_path) == evidence["frame_sha256"],
        "Latest UNV frame differs",
    )
    if selection.get("census_frame"):
        require(
            str(frame_path) == str(direct(selection["census_frame"])),
            "Selected census is not latest successful listing",
        )
    frame = read(frame_path)
    require(frame.get("source_id") == source.id, "Latest census source differs")
    jobs = [record(x) for x in frame["jobs"]]
    require(
        len({j.identity_key() for j in jobs}) == len(jobs),
        "Latest census duplicates job identities",
    )
    ids = [j.identity_key() for j in jobs]
    require(
        json.loads(state["listing_ids"]) == ids
        and state["last_list_at"] == timestamp(frame["observed_at"])
        and json.loads(state["listing_proof"]) == evidence["enumeration"],
        "Latest source listing state/proof differs",
    )
    paths = sorted((frame_path.parent / "http").glob("*.json"))
    refs = [reference(frame_path), *capture_refs(workspace, paths)]
    proof = verify_listing(source, jobs, paths)
    require(
        proof.get("complete") is True and proof == evidence["enumeration"],
        "UNV census is partial or does not reproduce its proof",
    )
    require(
        timestamp(proof["started_at"]) > failed_at and attempt["started_at"] > failed_at,
        "UNV census is not newer than failed detail",
    )
    identity = selection["external_id"]
    require(
        any(str(j.external_id) == identity for j in jobs) is present,
        "UNV assignment is absent" if present else "UNV assignment is still listed",
    )
    return (
        {
            "status": "observed" if present else "not_observed",
            "frame_path": str(frame_path),
            "frame_sha256": sha(frame_path),
            "enumeration": proof,
            "closure_inferred": False,
        },
        refs,
        attempt,
    )


def collect(conn, context, selections, as_of, check):
    workspace, lock = Path(context["workspace"]), Path(context["shared_lock"])
    sources = {s.id: s for s in load_sources(context["registry"]["path"])}
    selected_sources = set()
    items = []
    for selection in selections:
        check()
        row = conn.execute(
            "SELECT * FROM remediation_tasks WHERE task_id=?", (selection["task_id"],)
        ).fetchone()
        require(row is not None, "Selected task is absent")
        task = dict(row)
        action = selection["action"]
        attempt = failed_attempt(conn, task)
        source_id = task["source_id"]
        require(
            source_id in sources and sources[source_id].enabled,
            "Selected source is disabled or absent",
        )
        selected_sources.add(source_id)
        source_state = [
            dict(x)
            for x in conn.execute(
                "SELECT * FROM remediation_sources WHERE source_id=?", (source_id,)
            )
        ]
        circuit = [
            dict(x)
            for x in conn.execute(
                "SELECT * FROM source_circuit_breakers WHERE source_id=?", (source_id,)
            )
        ]
        require(
            not any(x["state"] in {"open", "half_open"} for x in circuit),
            "Source circuit remains held",
        )
        refs, listing_attempt = [], None
        payload = json.loads(task["payload"])
        if task["kind"] == "detail":
            original, fixed, listing, original_refs, listing_attempt = original_listing(
                conn, workspace, task
            )
            require(original == fixed, "Queued detail differs from original source listing")
            refs.extend(original_refs)
        elif task["kind"] != "listing":
            raise ValueError("Incident action supports only listing/detail tasks")
        receipt = json.loads(task["receipt"])
        _, failed_paths = failure_artifacts(workspace, task)
        refs.extend(capture_refs(workspace, failed_paths))
        for path in failed_paths:
            meta = read(path)
            require(
                attempt["started_at"]
                <= timestamp(meta["started_at"])
                <= timestamp(meta["finished_at"])
                <= attempt["finished_at"],
                "Capture interval falls outside durable failed attempt",
            )
        after = dict(task)
        if action == "retry_captured_transient":
            outcome = validate_captured_transient(workspace, task)
            require(
                outcome is not None, "Selected task has no supported captured transient incident"
            )
            refs.extend(outcome["evidence"])
            urls = [outcome["retry_url"]]
            configured = sources[source_id]
            expected_url = (
                configured.base_url.rstrip("/")
                + "/hcmRestApi/CandidateExperience/en/siteSettings/"
                + str(configured.extra.get("site_number"))
                if source_id == "iom_oracle_hcm"
                else configured.extra.get("listing_url")
                if source_id == "ifad_peoplesoft"
                else configured.extra.get("public_category_sections_url")
            )
            require(
                outcome["retry_url"] == expected_url,
                "Current source configuration differs from captured incident route",
            )
            if task["kind"] == "detail":
                require(source_id == "unv_uvp", "Unexpected transient detail source")
                current = conn.execute(
                    "SELECT * FROM jobs WHERE source_id=? AND external_id=?",
                    (source_id, task["external_id"]),
                ).fetchone()
                require(
                    active_hold(conn, task["task_id"]) is None
                    and not blocks_detail(current, payload["listing"].get("closes_at")),
                    "Reviewed deadline hold forbids transient detail retry",
                )
                census, extra, census_attempt = latest_unv_census(
                    conn,
                    workspace,
                    sources[source_id],
                    {**selection, "external_id": task["external_id"]},
                    attempt["finished_at"],
                    present=True,
                )
                refs.extend(extra)
                outcome = {
                    **outcome,
                    "listing_reconciliation": census,
                    "listing_attempt": census_attempt,
                }
            after["status"] = "pending"
        elif action == "unv_absent":
            require(
                source_id == "unv_uvp" and task["kind"] == "detail",
                "UNV absence action source/kind differs",
            )
            outcome, extra = unv_null_evidence(workspace, task, payload)
            refs.extend(extra)
            census, extra, census_attempt = latest_unv_census(
                conn,
                workspace,
                sources[source_id],
                {**selection, "external_id": task["external_id"]},
                timestamp(outcome["observed_at"]),
            )
            refs.extend(extra)
            outcome = {
                **outcome,
                "listing_reconciliation": census,
                "listing_attempt": census_attempt,
            }
            urls = [outcome["request_url"]]
            after["status"] = "not_observed"
        else:
            require(
                source_id == "who_taleo" and task["kind"] == "detail",
                "WHO unavailable action source/kind differs",
            )
            _, paths = failure_artifacts(workspace, task)
            refs.extend(capture_refs(workspace, paths))
            outcome = captured_unavailable(source_id, task["external_id"], paths)
            require(
                outcome and outcome.get("detector") == "who_active_unavailable_template_v1",
                "WHO capture is not exact active unavailable template",
            )
            urls = [outcome["request_url"]]
            after["status"] = "unavailable_pending_inventory"
        due, policy_refs = policy_state(workspace, lock, source_id, urls)
        refs.extend(policy_refs)
        for url in urls:
            host = (urlsplit(url).hostname or "").lower()
            path = (
                Path(str(lock) + ".worker-policy")
                / "hosts"
                / ("host-" + hashlib.sha256(host.encode()).hexdigest()[:24] + ".json")
            )
            refs.append(reference(path, optional=True))
            state = read(path) if path.exists() else {}
            eligibility = host_eligibility(state, as_of)
            require(eligibility["category"] != "review", "Host requires review")
            due = max(due, eligibility["eligible_at"])
        if action == "retry_captured_transient":
            after["eligible_at"] = max(task["eligible_at"], due, as_of)
        else:
            updated = {
                **receipt,
                "vacancy_unavailable": {
                    k: v
                    for k, v in outcome.items()
                    if k not in {"listing_reconciliation", "listing_attempt"}
                },
                "frame_path": payload["frame_path"],
                "frame_sha256": payload["frame_sha256"],
                "source_incident_repair": {
                    "action": action,
                    "prior_receipt_sha256": digest_value(receipt),
                    "network_requests": 0,
                },
            }
            if action == "unv_absent":
                updated["listing_reconciliation"] = outcome["listing_reconciliation"]
            after["receipt"] = dump(updated)
        items.append(
            {
                "selection": selection,
                "task_before": task,
                "task_after": after,
                "attempt_before": attempt,
                "listing_attempt": listing_attempt,
                "preservation": preservation(conn, task),
                "source_state": source_state,
                "source_circuits": circuit,
                "outcome": outcome,
                "evidence": sorted({r["path"]: r for r in refs}.values(), key=lambda x: x["path"]),
            }
        )
    check()
    policy = SharedPolicy(lock, selected_sources)
    require(policy.inspect()["status"] == "existing", "Existing sealed quota history required")
    events = policy.event_snapshot(check_deadline=check)
    return items, digest_value(events)


def deadline_check(max_seconds):
    require(0 < max_seconds <= 300, "Repair verification budget must be 1–300 seconds")
    deadline = time.monotonic() + max_seconds

    def check():
        require(
            time.monotonic() < deadline, "Repair verification budget exhausted; no partial repair"
        )

    return check


def unchanged_refs(items):
    for item in items:
        for expected in item["evidence"]:
            require(
                reference(expected["path"], optional=expected["sha256"] is None) == expected,
                "Evidence or policy file changed: " + expected["path"],
            )


def prepare(
    workspace,
    shared_lock,
    dispatcher_config,
    selections,
    *,
    expected_old_implementation=None,
    max_seconds=120,
):
    check = deadline_check(max_seconds)
    selections = normalize_selections(selections)
    context = runtime(workspace, shared_lock, dispatcher_config, expected_old_implementation)
    as_of = time.time()
    with connection(context["workspace"]) as conn:
        items, quota_sha = collect(conn, context, selections, as_of, check)
    check()
    return {
        "schema_version": 1,
        "kind": KIND,
        "created_at": utc(),
        "as_of": as_of,
        "runtime": context,
        "selections": selections,
        "items": items,
        "quota_history_sha256": quota_sha,
        "network_requests": 0,
        "database_writes": 0,
        "schedule_changes": 0,
    }


def apply(plan, *, max_seconds=120, fault=None):
    require(
        plan.get("schema_version") == 1 and plan.get("kind") == KIND,
        "Unsupported incident repair plan",
    )
    selections = normalize_selections(plan["selections"])
    context = plan["runtime"]
    require(
        context.get("executable") is True and context.get("expected_old_implementation") is None,
        "Old-seal or maintenance-free preview cannot execute; prepare a fresh resealed plan",
    )
    require(
        0 <= time.time() - plan["as_of"] <= MAX_PLAN_AGE_SECONDS,
        "Incident repair plan is stale or future",
    )
    require(
        context
        == runtime(
            context["workspace"], context["shared_lock"], context["dispatcher_config"]["path"], None
        ),
        "Runtime seal, maintenance or publication gate changed",
    )
    check = deadline_check(max_seconds)
    plan_sha = digest_value(plan)
    lock = direct(context["shared_lock"])
    with shared_owner(lock) as owner_fd:
        _check_owner(owner_fd, lock)
        require(
            context
            == runtime(context["workspace"], lock, context["dispatcher_config"]["path"], None),
            "Runtime gate changed under owner",
        )
        with connection(context["workspace"], write=True) as conn:
            exists = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (JOURNAL,)
            ).fetchone()
            prior = (
                conn.execute(
                    "SELECT * FROM " + JOURNAL + " WHERE plan_sha256=?", (plan_sha,)
                ).fetchall()
                if exists
                else []
            )
            if prior:
                require(
                    len(prior) == len(plan["items"])
                    and {r["task_id"] for r in prior} == {x["task_id"] for x in selections},
                    "Partial incident repair journal",
                )
                by_key = {x["task_before"]["task_id"]: x for x in plan["items"]}
                for row in prior:
                    actual = conn.execute(
                        "SELECT * FROM remediation_tasks WHERE task_id=?", (row["task_id"],)
                    ).fetchone()
                    item = by_key[row["task_id"]]
                    require(
                        actual is not None
                        and dict(actual) == item["task_after"] == json.loads(row["after_json"])
                        and item["task_before"] == json.loads(row["before_json"])
                        and preservation(conn, item["task_before"]) == item["preservation"],
                        "Journal or later task/job/attempt state changed",
                    )
                unchanged_refs(plan["items"])
                return {"status": "already_applied", "plan_sha256": plan_sha, "tasks_changed": 0}
            items, quota_sha = collect(conn, context, selections, plan["as_of"], check)
            require(
                items == plan["items"] and quota_sha == plan["quota_history_sha256"],
                "Task, evidence, job, source or quota state changed since preview",
            )
            require(
                context
                == runtime(context["workspace"], lock, context["dispatcher_config"]["path"], None),
                "Runtime gate changed before commit",
            )
            conn.execute(
                "CREATE TABLE IF NOT EXISTS "
                + JOURNAL
                + "(plan_sha256 TEXT,task_id TEXT,applied_at TEXT,before_json TEXT,after_json TEXT,proof_json TEXT,PRIMARY KEY(plan_sha256,task_id))"
            )
            for index, item in enumerate(items):
                check()
                before, after = item["task_before"], item["task_after"]
                current = conn.execute(
                    "SELECT * FROM remediation_tasks WHERE task_id=?", (before["task_id"],)
                ).fetchone()
                require(
                    current is not None and dict(current) == before,
                    "Task CAS changed inside transaction",
                )
                conn.execute(
                    "UPDATE remediation_tasks SET status=?,eligible_at=?,receipt=? WHERE task_id=?",
                    (after["status"], after["eligible_at"], after["receipt"], before["task_id"]),
                )
                conn.execute(
                    "INSERT INTO " + JOURNAL + " VALUES(?,?,?,?,?,?)",
                    (plan_sha, before["task_id"], utc(), dump(before), dump(after), dump(item)),
                )
                if fault:
                    fault(index, conn)
            # Validate exact task after-images and protected rows before commit.
            for item in items:
                actual = conn.execute(
                    "SELECT * FROM remediation_tasks WHERE task_id=?",
                    (item["task_before"]["task_id"],),
                ).fetchone()
                require(
                    dict(actual) == item["task_after"]
                    and preservation(conn, item["task_before"]) == item["preservation"],
                    "Repair readback or preservation differs",
                )
            unchanged_refs(items)
            require(
                context
                == runtime(context["workspace"], lock, context["dispatcher_config"]["path"], None),
                "Runtime gate changed during repair",
            )
            _check_owner(owner_fd, lock)
            check()
    return {
        "status": "applied",
        "plan_sha256": plan_sha,
        "tasks_changed": len(items),
        "attempts_changed": 0,
        "jobs_changed": 0,
        "policy_changes": 0,
        "schedule_changes": 0,
        "network_requests": 0,
    }


def report_destination(path, context):
    report_path = direct(path, exists=False)
    protected_roots = [
        Path(context["workspace"]),
        Path(context["shared_lock"] + ".worker-policy"),
        Path(context["publication_gate"]["path"]).parent,
    ]
    protected_files = [
        Path(context[k]["path"]) for k in ("dispatcher_config", "registry", "robots", "maintenance")
    ]
    protected_files.append(Path(context["shared_lock"]))
    require(
        not any(report_path.is_relative_to(root) for root in protected_roots)
        and report_path not in protected_files,
        "Report must not overwrite production state or configuration",
    )
    return report_path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path)
    parser.add_argument("--shared-lock", type=Path)
    parser.add_argument("--dispatcher-config", type=Path)
    parser.add_argument("--selection", type=Path)
    parser.add_argument("--expected-old-implementation")
    parser.add_argument("--apply-plan", type=Path)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--max-seconds", type=float, default=120)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    if args.execute:
        require(
            args.apply_plan is not None and args.expected_old_implementation is None,
            "Execute requires a fresh current-seal plan",
        )
        plan = read(args.apply_plan)
        report_path = report_destination(args.report, plan["runtime"])
        result = apply(plan, max_seconds=args.max_seconds)
    else:
        require(
            args.apply_plan is None
            and all((args.workspace, args.shared_lock, args.dispatcher_config, args.selection)),
            "Preview requires explicit workspace/owner/config/selection",
        )
        selection = read(args.selection)
        result = prepare(
            args.workspace,
            args.shared_lock,
            args.dispatcher_config,
            selection["selections"],
            expected_old_implementation=args.expected_old_implementation,
            max_seconds=args.max_seconds,
        )
        report_path = report_destination(args.report, result["runtime"])
    report_path.write_text(json.dumps(result, indent=2) + "\n")


if __name__ == "__main__":
    main()
