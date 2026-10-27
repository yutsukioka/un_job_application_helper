"""Immutable accepted observation versions, bound in the acceptance transaction.

The pathname is a locator; the exact artifact/proof/public fields are identity.
Legacy backfill is an explicit checkpointed operation using the existing exact
lineage resolver; conflicting matches are retained, never chosen by recency.
"""

import hashlib
import json
import math
import sqlite3
import time

CONTRACT = "accepted-detail-evidence-2"
TABLE = "jobagg_accepted_evidence"


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


def exists(conn):
    return bool(conn.execute("SELECT 1 FROM sqlite_master WHERE name=?", (TABLE,)).fetchone())


def prepare(conn):
    from jobagg.acceptance_catalog import prepare as prepare_exits

    prepare_exits(conn)
    if conn.execute("SELECT 1 FROM sqlite_master WHERE name='remediation_sources'").fetchone():
        columns = {row[1] for row in conn.execute("PRAGMA table_info(remediation_sources)")}
        if "listing_event_version" not in columns:
            conn.execute("ALTER TABLE remediation_sources ADD COLUMN listing_event_version TEXT")
    conn.execute("""CREATE TABLE IF NOT EXISTS jobagg_accepted_events(version TEXT PRIMARY KEY,
        run_id TEXT NOT NULL,attempt_id TEXT NOT NULL UNIQUE,task_id TEXT NOT NULL,
        source_id TEXT NOT NULL,kind TEXT NOT NULL,reference_json TEXT NOT NULL)""")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS jobagg_accepted_event_run ON jobagg_accepted_events(run_id,attempt_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS jobagg_event_identity ON jobagg_accepted_events(kind,source_id,json_extract(reference_json,'$.evidence_key'),task_id)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS jobagg_event_backfill(attempt_id TEXT PRIMARY KEY,disposition TEXT)"
    )
    conn.execute(f"""CREATE TABLE IF NOT EXISTS {TABLE}(version TEXT PRIMARY KEY,
        job_key TEXT NOT NULL,source_id TEXT NOT NULL,external_id TEXT NOT NULL,
        task_id TEXT NOT NULL,attempt_id TEXT NOT NULL,artifact_id TEXT NOT NULL,
        artifact_path TEXT NOT NULL,artifact_sha256 TEXT NOT NULL,proof_sha256 TEXT NOT NULL,
        body_sha256 TEXT NOT NULL,public_fields_sha256 TEXT NOT NULL,
        contract TEXT NOT NULL,accepted_at REAL NOT NULL,reference_json TEXT NOT NULL)""")
    conn.execute(
        f"CREATE INDEX IF NOT EXISTS jobagg_evidence_identity ON {TABLE}(job_key,proof_sha256,attempt_id)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS jobagg_evidence_backfill(job_key TEXT PRIMARY KEY,observation_sha256 TEXT,disposition TEXT)"
    )


def record(
    conn,
    *,
    job_key,
    task_id,
    attempt_id,
    artifact_path,
    artifact_sha256,
    job,
    proof,
    body_sha256,
    acceptance_proof=None,
):
    if not exists(conn):
        return None  # Old workspaces keep their original exact resolver contract.
    from jobagg.remediation_worker import PUBLIC_FIELDS

    if not acceptance_proof:
        return None
    if hashlib.sha256(job["description"].encode()).hexdigest() != body_sha256:
        raise ValueError("accepted_reference_body_hash_differs")
    cursor = conn.cursor()
    cursor.row_factory = sqlite3.Row
    observed = cursor.execute(
        "SELECT * FROM remediation_observations WHERE job_key=?", (job_key,)
    ).fetchone()
    if observed is None or json.loads(observed["proof"]) != proof:
        raise ValueError("accepted_reference_observation_missing_or_changed")
    value = {
        "job_key": job_key,
        "source_id": job["source_id"],
        "external_id": str(job["external_id"]),
        "task_id": task_id,
        "attempt_id": attempt_id,
        "artifact_id": "sha256:" + artifact_sha256,
        "artifact_path": str(artifact_path),
        "artifact_sha256": artifact_sha256,
        "proof_sha256": digest(proof),
        "body_sha256": body_sha256,
        "public_fields_sha256": digest({key: job.get(key) for key in PUBLIC_FIELDS}),
        "contract": CONTRACT,
        "observation": dict(observed),
        "acceptance_proof": acceptance_proof,
    }
    version = digest({key: item for key, item in value.items() if key != "artifact_path"})
    conn.execute(
        f"INSERT OR IGNORE INTO {TABLE} VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            version,
            job_key,
            job["source_id"],
            str(job["external_id"]),
            task_id,
            attempt_id,
            value["artifact_id"],
            str(artifact_path),
            artifact_sha256,
            value["proof_sha256"],
            body_sha256,
            value["public_fields_sha256"],
            CONTRACT,
            time.time(),
            json.dumps(value, sort_keys=True),
        ),
    )
    return version


def selected_attempts(conn, row, proof):
    """Return bound attempts, or None for the original legacy lookup path."""
    if not exists(conn):
        return None
    result = []
    cursor = conn.cursor()
    cursor.row_factory = sqlite3.Row
    for ref in cursor.execute(
        f"SELECT * FROM {TABLE} WHERE job_key=? AND proof_sha256=? ORDER BY attempt_id",
        (row["job_key"], digest(proof)),
    ).fetchall():
        if (
            ref["contract"] != CONTRACT
            or ref["source_id"] != row["source_id"]
            or ref["external_id"] != str(row["external_id"])
        ):
            raise ValueError("accepted_reference_identity_or_contract_changed")
        reference = json.loads(ref["reference_json"])
        if digest({key: item for key, item in reference.items() if key != "artifact_path"}) != ref[
            "version"
        ] or any(
            reference.get(key) != ref[key]
            for key in (
                "job_key",
                "source_id",
                "external_id",
                "task_id",
                "attempt_id",
                "artifact_id",
                "artifact_path",
                "artifact_sha256",
                "proof_sha256",
                "body_sha256",
                "public_fields_sha256",
                "contract",
            )
        ):
            raise ValueError("accepted_reference_version_or_columns_changed")
        current = cursor.execute(
            "SELECT * FROM remediation_observations WHERE job_key=?", (row["job_key"],)
        ).fetchone()
        if current is None or reference.get("observation") != dict(current):
            continue
        attempt = cursor.execute(
            "SELECT * FROM remediation_attempts WHERE attempt_id=?", (ref["attempt_id"],)
        ).fetchone()
        if (
            attempt is None
            or attempt["status"] != "done"
            or attempt["finished_at"] is None
            or attempt["task_id"] != ref["task_id"]
            or attempt["source_id"] != ref["source_id"]
            or attempt["kind"] != "detail"
        ):
            raise ValueError("accepted_reference_attempt_changed")
        evidence = json.loads(attempt["evidence"])
        if (
            evidence.get("detail_path") != ref["artifact_path"]
            or evidence.get("detail_sha256") != ref["artifact_sha256"]
        ):
            raise ValueError("accepted_reference_locator_or_hash_changed")
        result.append(dict(attempt))
    return result


def _backfill_context(conn, *, limit, deadline_at, owner_fd):
    if type(limit) is not int or not 0 < limit <= 256 or not math.isfinite(deadline_at):
        raise ValueError("finite_accepted_backfill_page_required")
    if time.monotonic() >= deadline_at:
        raise TimeoutError("accepted_backfill_page_deadline")
    from jobagg import runtime_authority

    active = runtime_authority.state(conn)
    if active is not None:
        active = runtime_authority.validate(conn)
        from jobagg.runtime_source_head import owner

        if owner_fd is None or active["phase"] != "idle":
            raise ValueError("accepted_backfill_requires_idle_owner_review")
        owner(owner_fd, active["owner_path"])
    return active


def backfill(
    conn, *, limit, deadline_at, owner_fd=None, require_retained_captures=False, review_job_key=None
):
    """Owner-held staged migration; restart skips exact checkpointed observations.

    An already fenced copy additionally requires its idle owner/controller.
    Missing/conflicting matches remain explicit rejections, never an empty set.
    """
    from jobagg.accepted_detail_lineage import matching_detail_attempts

    active = _backfill_context(conn, limit=limit, deadline_at=deadline_at, owner_fd=owner_fd)
    prior = None
    if review_job_key is not None:
        if active is None or limit != 1 or not require_retained_captures:
            raise ValueError("exact_native_current_evidence_revalidation_required")
        prior = conn.execute(
            "SELECT job_key,observation_sha256,disposition FROM jobagg_evidence_backfill WHERE job_key=?",
            (review_job_key,),
        ).fetchone()
        if prior is None:
            raise ValueError("evidence_revalidation_original_checkpoint_missing")
        prior = list(prior)

    rows = conn.execute(
        """SELECT j.*,o.proof AS accepted_proof FROM remediation_observations o
        JOIN jobs j ON j.job_key=o.job_key WHERE ((? IS NULL AND NOT EXISTS(SELECT 1 FROM jobagg_evidence_backfill b WHERE b.job_key=j.job_key)) OR j.job_key=?)
        ORDER BY j.job_key LIMIT ?""",
        (review_job_key, review_job_key, limit),
    ).fetchall()
    if review_job_key is not None and len(rows) != 1:
        raise ValueError("evidence_revalidation_current_observation_missing")
    cursor = conn.cursor()
    cursor.row_factory = sqlite3.Row
    names = [
        item[0]
        for item in conn.execute(
            "SELECT j.*,o.proof AS accepted_proof FROM jobs j JOIN remediation_observations o ON o.job_key=j.job_key LIMIT 0"
        ).description
    ]
    cache = {}
    from jobagg.acceptance_catalog import verified_attempt
    from jobagg.remediation_worker import PUBLIC_FIELDS

    records = []
    for values in rows:
        if time.monotonic() >= deadline_at:
            raise TimeoutError("accepted_evidence_backfill_deadline")
        row = dict(zip(names, values))
        proof = json.loads(row["accepted_proof"])
        if prior is not None and (prior[0] != row["job_key"] or prior[1] != digest(proof)):
            raise ValueError("evidence_revalidation_observation_changed")
        matches = matching_detail_attempts(
            conn, row, proof, deadline_at=deadline_at, legacy_only=True
        )
        disposition = "matched" if matches else "missing_exact_acceptance"
        verified = []
        retained_results = []
        for match in matches:
            retained = None
            if require_retained_captures:
                from jobagg.historical_artifacts import assess

                retained = assess(conn, match["attempt"]["attempt_id"], deadline_at=deadline_at)
                retained_results.append(retained)
            acceptance = verified_attempt(
                conn, match["attempt"], deadline_at=deadline_at, cache=cache
            )
            if acceptance is None:
                disposition = "missing_successful_exit_proof"
            else:
                if require_retained_captures:
                    if (
                        retained["disposition"]
                        != "retained_detail_bytes_and_task_crosswalk_verified"
                    ):
                        disposition = "retained_capture_review_required"
                        continue
                verified.append((match, acceptance))
            if any(
                match["artifact"]["job"].get(field) != row.get(field) for field in PUBLIC_FIELDS
            ):
                disposition = "conflicting_public_fields"
        for match, acceptance in verified:
            attempt = match["attempt"]
            artifact = match["artifact"]
            receipt = json.loads(attempt["evidence"])
            record(
                conn,
                job_key=row["job_key"],
                task_id=attempt["task_id"],
                attempt_id=attempt["attempt_id"],
                artifact_path=receipt["detail_path"],
                artifact_sha256=receipt["detail_sha256"],
                job=artifact["job"],
                proof=proof,
                body_sha256=hashlib.sha256(artifact["job"]["description"].encode()).hexdigest(),
                acceptance_proof=acceptance,
            )
        if active is not None:
            from jobagg.runtime_containment import invalidate
            invalidate(conn,'explicit accepted-evidence backfill review changed')
        conn.execute(
            "INSERT INTO jobagg_evidence_backfill VALUES(?,?,?) ON CONFLICT(job_key) DO UPDATE SET observation_sha256=excluded.observation_sha256,disposition=excluded.disposition",
            (row["job_key"], digest(proof), disposition),
        )
        if prior is not None:
            history = {
                "schema": "native-evidence-backfill-review-1",
                "previous": prior,
                "current": [row["job_key"], digest(proof), disposition],
                "epoch": active["epoch"],
            }
            conn.execute(
                "INSERT OR IGNORE INTO jobagg_legacy_review_holds VALUES(?,?)",
                (
                    "evidence-backfill-review/" + digest(history),
                    json.dumps(history, sort_keys=True),
                ),
            )
        records.append(
            {
                "job_key": row["job_key"],
                "observation_sha256": digest(proof),
                "disposition": disposition,
                "retained_evidence": retained_results,
                "previous_backfill": prior,
            }
        )
    return {
        "records": records,
        "processed": len(rows),
        "complete": not conn.execute("""SELECT 1 FROM remediation_observations o WHERE NOT EXISTS(
        SELECT 1 FROM jobagg_evidence_backfill b WHERE b.job_key=o.job_key) LIMIT 1""").fetchone(),
        "rejections": conn.execute(
            "SELECT count(*) FROM jobagg_evidence_backfill WHERE disposition!='matched'"
        ).fetchone()[0],
    }


def legacy_rejection(conn, job_key, proof):
    if not conn.execute(
        "SELECT 1 FROM sqlite_master WHERE name='jobagg_evidence_backfill'"
    ).fetchone():
        return None
    value = conn.execute(
        "SELECT observation_sha256,disposition FROM jobagg_evidence_backfill WHERE job_key=?",
        (job_key,),
    ).fetchone()
    if value and value[0] == digest(proof) and value[1] == "conflicting_public_fields":
        return "worker_public_fields_differ_from_completed_detail_artifact"
    return None


def record_event(
    conn,
    *,
    task,
    attempt_id,
    binding,
    run_id,
    acceptance_proof=None,
    request_sha256=None,
    request_hash_kind="canonical",
    runtime_contract_sha256=None,
    legacy_owner_fd=None,
    deadline_at=None,
):
    """Successful native acceptance, including listing/document, in its own TX.

    The object was durably captured before finish(); no process exit is inferred.
    Older occurrences remain independent of the next refresh task state.
    """
    from jobagg.runtime_authority import state

    active = state(conn)
    if (active is None and acceptance_proof is None) or not conn.execute(
        "SELECT 1 FROM sqlite_master WHERE name='jobagg_accepted_events'"
    ).fetchone():
        return
    legacy_import = legacy_owner_fd is not None
    if legacy_import:
        _backfill_context(conn, limit=1, deadline_at=deadline_at, owner_fd=legacy_owner_fd)
        if acceptance_proof is None or binding is not None:
            raise ValueError("legacy_event_requires_independent_exit")
    if (
        active is not None
        and not legacy_import
        and (active["phase"] != "worker" or active["run_id"] != run_id)
    ):
        raise ValueError("atomic_acceptance_run_or_phase_differs")
    cursor = conn.cursor()
    cursor.row_factory = sqlite3.Row
    attempt = dict(
        cursor.execute(
            "SELECT * FROM remediation_attempts WHERE attempt_id=?", (attempt_id,)
        ).fetchone()
    )
    if attempt["status"] != "done" or attempt["finished_at"] is None:
        raise ValueError("atomic_acceptance_requires_successful_attempt")
    if legacy_import:
        from jobagg.acceptance_catalog import verified_attempt

        actual = verified_attempt(conn, attempt, deadline_at=deadline_at, cache={})
        if actual is None or actual != acceptance_proof or actual["run_id"] != run_id:
            raise ValueError("legacy_event_exit_proof_differs")
    if active is not None and not legacy_import:
        from jobagg.remediation_worker import implementation_hash

        if binding.get("implementation_sha256") != implementation_hash():
            raise ValueError("Code changed before atomic acceptance reference")
    receipt = json.loads(attempt["evidence"])
    path_key, hash_key = {
        "detail": ("detail_path", "detail_sha256"),
        "listing": ("frame_path", "frame_sha256"),
        "document": ("manifest", "sha256"),
    }[attempt["kind"]]
    evidence_key = receipt[hash_key]
    if task["kind"] == "document":
        document = cursor.execute(
            "SELECT manifest FROM remediation_documents WHERE task_id=?", (task["task_id"],)
        ).fetchone()
        evidence_key = digest(json.loads(document[0])) if document else None
    proof = {
        "contract": "native-atomic-acceptance-1"
        if active is not None and not legacy_import
        else "legacy-accepted-event-1",
        "evidence_key": evidence_key,
        "task_payload": json.loads(task["payload"]),
        "acceptance_proof": acceptance_proof,
        "run_id": run_id,
        "request_sha256": request_sha256,
        "request_hash_kind": request_hash_kind,
        "runtime_contract_sha256": runtime_contract_sha256,
        "runtime_epoch": active["epoch"] if active and not legacy_import else None,
        "binding": binding,
        "transaction_id": active["source_transaction_id"] if active and not legacy_import else None,
        "attempt": attempt,
        "task_identity": {
            key: task[key] for key in ("task_id", "source_id", "kind", "external_id")
        },
        "artifact": {
            "id": "sha256:" + receipt[hash_key],
            "sha256": receipt[hash_key],
            "path": receipt[path_key],
        },
    }
    version = digest(proof)
    listing_head = None
    if task["kind"] == "listing":
        from jobagg.artifact_locations import resolve
        from jobagg.publication_snapshot import _json_document
        from jobagg.remediation_worker import epoch

        frame, artifact = _json_document(
            resolve(conn, receipt["frame_path"], receipt["frame_sha256"], strict=active is not None),
            deadline_at=deadline_at,
        )
        if (
            artifact["sha256"] != receipt["frame_sha256"]
            or frame.get("source_id") != task["source_id"]
        ):
            raise ValueError("accepted_listing_frame_identity_changed")
        current = cursor.execute(
            "SELECT last_list_at,listing_proof,listing_event_version FROM remediation_sources WHERE source_id=?",
            (task["source_id"],),
        ).fetchone()
        # Native finish binds the new source head in this same transaction.
        # Legacy import may attach only the current task's exact retained frame,
        # never an older occurrence with a repeated count or timestamp.
        current_receipt = json.loads(task["receipt"] or "{}")
        matches = (
            current is not None
            and current["last_list_at"] == epoch(frame["observed_at"])
            and (json.loads(current["listing_proof"]) == receipt["enumeration"])
        )
        if active is not None and not legacy_import and not matches:
            raise ValueError("atomic_accepted_listing_head_state_differs")
        if matches and (
            (active is not None and not legacy_import)
            or legacy_import
            or current_receipt.get("frame_sha256") == receipt["frame_sha256"]
        ):
            if (active is None or legacy_import) and current["listing_event_version"] not in (
                None,
                version,
            ):
                raise ValueError("accepted_listing_legacy_head_conflicting_occurrence")
            listing_head = (version, task["source_id"])
    conn.execute(
        "INSERT INTO jobagg_accepted_events VALUES(?,?,?,?,?,?,?)",
        (
            version,
            run_id,
            attempt_id,
            task["task_id"],
            task["source_id"],
            task["kind"],
            json.dumps(proof, sort_keys=True),
        ),
    )
    if listing_head is not None:
        conn.execute(
            "UPDATE remediation_sources SET listing_event_version=? WHERE source_id=?", listing_head
        )
    return version


def atomic_run(conn, run_id, *, limit, deadline_at):
    """Bounded independent proof after a dead worker; never fabricate its exit."""
    from pathlib import Path
    from jobagg.accepted_detail_lineage import _sha

    cursor = conn.cursor()
    cursor.row_factory = sqlite3.Row
    if not cursor.execute(
        "SELECT 1 FROM sqlite_master WHERE name='jobagg_accepted_events'"
    ).fetchone():
        return None
    rows = cursor.execute(
        "SELECT * FROM jobagg_accepted_events WHERE run_id=? ORDER BY attempt_id LIMIT ?",
        (run_id, limit + 1),
    ).fetchall()
    if not rows:
        return None
    if len(rows) > limit:
        raise ValueError("atomic_acceptance_recovery_requires_larger_reviewed_bound")
    refs = []
    request_sha = None
    request_kind = None
    for row in rows:
        if time.monotonic() >= deadline_at:
            raise TimeoutError("atomic_acceptance_verification_deadline")
        proof = json.loads(row["reference_json"])
        if request_sha is not None and request_sha != proof.get("request_sha256"):
            raise ValueError("atomic_acceptance_request_conflict")
        request_sha = proof.get("request_sha256")
        if request_kind is not None and request_kind != proof.get("request_hash_kind", "canonical"):
            raise ValueError("atomic_acceptance_request_hash_contract_conflict")
        request_kind = proof.get("request_hash_kind", "canonical")
        attempt = cursor.execute(
            "SELECT * FROM remediation_attempts WHERE attempt_id=?", (row["attempt_id"],)
        ).fetchone()
        if (
            digest(proof) != row["version"]
            or proof.get("contract") != "native-atomic-acceptance-1"
            or proof.get("run_id") != run_id
            or attempt is None
            or attempt["status"] != "done"
            or attempt["finished_at"] is None
            or not proof.get("transaction_id")
            or proof["artifact"].get("id") != "sha256:" + proof["artifact"]["sha256"]
            or dict(attempt) != proof["attempt"]
            or any(
                attempt[key] != row[key] for key in ("attempt_id", "task_id", "source_id", "kind")
            )
            or _sha(Path(proof["artifact"]["path"]), deadline_at=deadline_at)
            != proof["artifact"]["sha256"]
        ):
            raise ValueError("atomic_acceptance_reference_or_artifact_changed")
        refs.append(row["version"])
    return {
        "kind": "verified_native_atomic_acceptance",
        "run_id": run_id,
        "versions": refs,
        "sha256": digest(refs),
        "worker_exit_verified": False,
        "request_sha256": request_sha,
        "request_hash_kind": request_kind,
    }


def resolve_event(conn, *, kind, source_id, evidence_key=None, task_id=None, version=None):
    """Indexed exact occurrence, independent of the next refresh task status."""
    if not conn.execute(
        "SELECT 1 FROM sqlite_master WHERE name='jobagg_accepted_events'"
    ).fetchone():
        return None  # Original legacy contract, before explicit migration.
    cursor = conn.cursor()
    cursor.row_factory = sqlite3.Row
    if evidence_key is None and version is None:
        raise ValueError("accepted_event_exact_identity_required")
    query = "SELECT * FROM jobagg_accepted_events WHERE kind=? AND source_id=?"
    args = (kind, source_id)
    if evidence_key is not None:
        query += " AND json_extract(reference_json,'$.evidence_key')=?"
        args += (evidence_key,)
    if version is not None:
        query += " AND version=?"
        args += (version,)
    if task_id is not None:
        query += " AND task_id=?"
        args += (task_id,)
    rows = cursor.execute(query + " LIMIT 2", args).fetchall()
    if len(rows) != 1:
        raise ValueError("accepted_event_missing_or_conflicting_occurrence")
    row = rows[0]
    proof = json.loads(row["reference_json"])
    attempt = cursor.execute(
        "SELECT * FROM remediation_attempts WHERE attempt_id=?", (row["attempt_id"],)
    ).fetchone()
    if (
        digest(proof) != row["version"]
        or proof["contract"] not in {"native-atomic-acceptance-1", "legacy-accepted-event-1"}
        or attempt is None
        or dict(attempt) != proof["attempt"]
        or attempt["status"] != "done"
        or any(
            proof["task_identity"].get(key) != row[key] for key in ("task_id", "source_id", "kind")
        )
    ):
        raise ValueError("accepted_event_identity_changed")
    return {
        "version": row["version"],
        "attempt_id": row["attempt_id"],
        "task_id": row["task_id"],
        "receipt": json.loads(attempt["evidence"]),
        "payload": proof["task_payload"],
    }


def resolve_listing_event(conn, *, source_id):
    """Resolve the source's transactionally bound exact frame occurrence."""
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='jobagg_accepted_events'").fetchone():
        return None  # Original unmigrated legacy publisher contract.
    if "listing_event_version" not in {row[1] for row in conn.execute("PRAGMA table_info(remediation_sources)")}:
        raise ValueError("accepted_listing_head_requires_preparation")
    row = conn.execute("SELECT listing_event_version,listing_proof FROM remediation_sources WHERE source_id=?",
                       (source_id,)).fetchone()
    if row is None or row[0] is None:
        raise ValueError("accepted_listing_head_missing_exact_occurrence")
    accepted = resolve_event(conn, kind="listing", source_id=source_id, version=row[0])
    if accepted["receipt"].get("enumeration") != json.loads(row[1]):
        raise ValueError("accepted_listing_head_proof_changed")
    return accepted


def selected_events(conn, *, job_keys, listing_sources, deadline_at):
    """Exact event dependencies only; no history discovery in publication."""
    cursor = conn.cursor()
    cursor.row_factory = sqlite3.Row
    result = []
    for source in listing_sources:
        if time.monotonic() >= deadline_at:
            raise TimeoutError("selected_event_deadline")
        row = cursor.execute(
            "SELECT listing_proof FROM remediation_sources WHERE source_id=?", (source,)
        ).fetchone()
        if row and row[0]:
            try:
                result.append(
                    resolve_listing_event(conn, source_id=source)
                )
            except ValueError:
                pass  # Preserve publisher's exact rejection, never accept a substitute.
    for job in job_keys:
        for row in cursor.execute(
            "SELECT task_id,source_id,manifest FROM remediation_documents WHERE job_key=?", (job,)
        ).fetchall():
            if time.monotonic() >= deadline_at:
                raise TimeoutError("selected_event_deadline")
            try:
                result.append(
                    resolve_event(
                        conn,
                        kind="document",
                        source_id=row["source_id"],
                        task_id=row["task_id"],
                        evidence_key=digest(json.loads(row["manifest"])),
                    )
                )
            except ValueError:
                pass
    return [row for row in result if row is not None]


def backfill_events(conn, *, limit, deadline_at, owner_fd=None):
    """Finite legacy import; superseded/currently unproven heads stay distinct."""
    from jobagg.acceptance_catalog import verified_attempt
    from jobagg.publication_snapshot import _json_document
    from jobagg.artifact_locations import resolve

    active = _backfill_context(conn, limit=limit, deadline_at=deadline_at, owner_fd=owner_fd)
    cursor = conn.cursor()
    cursor.row_factory = sqlite3.Row
    rows = cursor.execute(
        "SELECT * FROM remediation_attempts a WHERE a.kind IN('listing','document') AND a.status='done' AND a.finished_at IS NOT NULL AND NOT EXISTS(SELECT 1 FROM jobagg_event_backfill b WHERE b.attempt_id=a.attempt_id) ORDER BY a.attempt_id LIMIT ?",
        (limit,),
    ).fetchall()
    cache, records = {}, []
    for row in rows:
        if time.monotonic() >= deadline_at:
            raise TimeoutError("accepted_event_backfill_deadline")
        disposition, reason = "missing_successful_exit_proof", None
        try:
            proof = verified_attempt(conn, row, deadline_at=deadline_at, cache=cache)
            if cursor.execute(
                "SELECT 1 FROM jobagg_accepted_events WHERE attempt_id=?", (row["attempt_id"],)
            ).fetchone():
                disposition = "existing_accepted_occurrence"
            elif proof:
                task = cursor.execute(
                    "SELECT * FROM remediation_tasks WHERE task_id=?", (row["task_id"],)
                ).fetchone()
                receipt = json.loads(row["evidence"])
                path_key, hash_key = (
                    ("frame_path", "frame_sha256")
                    if row["kind"] == "listing"
                    else ("manifest", "sha256")
                )
                artifact, record = _json_document(
                    resolve(conn, receipt[path_key], receipt[hash_key], strict=active is not None),
                    deadline_at=deadline_at,
                )
                if record["sha256"] != receipt[hash_key]:
                    raise ValueError("accepted_event_backfill_artifact_changed")
                disposition = "ready"
                if active is not None and row["kind"] == "listing":
                    from jobagg.remediation_worker import epoch

                    current = cursor.execute(
                        "SELECT last_list_at,listing_proof FROM remediation_sources WHERE source_id=?",
                        (row["source_id"],),
                    ).fetchone()
                    if (
                        current is None
                        or current["last_list_at"] != epoch(artifact["observed_at"])
                        or json.loads(current["listing_proof"]) != receipt["enumeration"]
                    ):
                        disposition = "historical_listing_not_current_source_head"
                if disposition == "ready":
                    if active is not None and row["kind"] == "document":
                        document = cursor.execute(
                            "SELECT manifest FROM remediation_documents WHERE task_id=?",
                            (row["task_id"],),
                        ).fetchone()
                        payload = json.loads(task["payload"])
                        if document is None or json.loads(document[0]) != artifact:
                            disposition = "historical_document_not_current_manifest"
                        elif artifact.get("source_id") != row["source_id"] or any(
                            payload.get(k) != artifact.get(k) for k in ("job_key", "url")
                        ):
                            disposition = "current_document_task_scope_requires_review"
                        else:
                            disposition = "ready"
                    else:
                        disposition = "ready"
                    if disposition == "ready":
                        record_event(
                            conn,
                            task=task,
                            attempt_id=row["attempt_id"],
                            binding=None,
                            run_id=proof["run_id"],
                            acceptance_proof=proof,
                            legacy_owner_fd=owner_fd if active is not None else None,
                            deadline_at=deadline_at,
                        )
                        disposition = "matched"
        except TimeoutError:
            raise
        except (ValueError, OSError, KeyError, TypeError) as error:
            if active is None:
                raise  # Preserve the pre-activation migration contract.
            disposition, reason = "current_event_evidence_requires_review", str(error)
        if active is not None:
            from jobagg.runtime_containment import invalidate
            invalidate(conn,'explicit legacy-event backfill review changed')
        conn.execute(
            "INSERT INTO jobagg_event_backfill VALUES(?,?)", (row["attempt_id"], disposition)
        )
        records.append(
            {
                "attempt_id": row["attempt_id"],
                "source_id": row["source_id"],
                "kind": row["kind"],
                "disposition": disposition,
                "reason": reason,
            }
        )
    return {"processed": len(rows), "complete": len(rows) < limit, "records": records}
