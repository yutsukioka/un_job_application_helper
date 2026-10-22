"""Immutable accepted observation versions, bound in the acceptance transaction.

The pathname is a locator; the exact artifact/proof/public fields are identity.
Legacy backfill is an explicit checkpointed operation using the existing exact
lineage resolver; conflicting matches are retained, never chosen by recency.
"""

import hashlib
import json
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


def backfill(conn, *, limit, deadline_at):
    """Owner-held staged migration; restart skips exact checkpointed observations.

    This must finish before enabling the native runtime fence. Missing/conflicting
    matches keep explicit rejection dispositions and cannot certify an empty set.
    """
    from jobagg.accepted_detail_lineage import matching_detail_attempts

    rows = conn.execute(
        """SELECT j.*,o.proof AS accepted_proof FROM remediation_observations o
        JOIN jobs j ON j.job_key=o.job_key WHERE NOT EXISTS(SELECT 1 FROM jobagg_evidence_backfill b WHERE b.job_key=j.job_key)
        ORDER BY j.job_key LIMIT ?""",
        (limit,),
    ).fetchall()
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

    for values in rows:
        if time.monotonic() >= deadline_at:
            raise TimeoutError("accepted_evidence_backfill_deadline")
        row = dict(zip(names, values))
        proof = json.loads(row["accepted_proof"])
        matches = matching_detail_attempts(
            conn, row, proof, deadline_at=deadline_at, legacy_only=True
        )
        disposition = "matched" if matches else "missing_exact_acceptance"
        verified = []
        for match in matches:
            acceptance = verified_attempt(
                conn, match["attempt"], deadline_at=deadline_at, cache=cache
            )
            if acceptance is None:
                disposition = "missing_successful_exit_proof"
            else:
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
        conn.execute(
            "INSERT INTO jobagg_evidence_backfill VALUES(?,?,?)",
            (row["job_key"], digest(proof), disposition),
        )
    return {
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


def record_event(conn, *, task, attempt_id, binding, run_id, acceptance_proof=None):
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
    if active is not None and (active["phase"] != "worker" or active["run_id"] != run_id):
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
    receipt = json.loads(attempt["evidence"])
    path_key, hash_key = {
        "detail": ("detail_path", "detail_sha256"),
        "listing": ("frame_path", "frame_sha256"),
        "document": ("manifest", "sha256"),
    }[attempt["kind"]]
    evidence_key = receipt[hash_key]
    if task["kind"] == "listing":
        evidence_key = digest(receipt["enumeration"])
    if task["kind"] == "document":
        document = cursor.execute(
            "SELECT manifest FROM remediation_documents WHERE task_id=?", (task["task_id"],)
        ).fetchone()
        evidence_key = digest(json.loads(document[0])) if document else None
    proof = {
        "contract": "native-atomic-acceptance-1"
        if active is not None
        else "legacy-accepted-event-1",
        "evidence_key": evidence_key,
        "task_payload": json.loads(task["payload"]),
        "acceptance_proof": acceptance_proof,
        "run_id": run_id,
        "binding": binding,
        "transaction_id": active["source_transaction_id"] if active else None,
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
    for row in rows:
        if time.monotonic() >= deadline_at:
            raise TimeoutError("atomic_acceptance_verification_deadline")
        proof = json.loads(row["reference_json"])
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
    }


def resolve_event(conn, *, kind, source_id, evidence_key, task_id=None):
    """Indexed exact occurrence, independent of the next refresh task status."""
    if not conn.execute(
        "SELECT 1 FROM sqlite_master WHERE name='jobagg_accepted_events'"
    ).fetchone():
        return None  # Original legacy contract, before explicit migration.
    cursor = conn.cursor()
    cursor.row_factory = sqlite3.Row
    query = "SELECT * FROM jobagg_accepted_events WHERE kind=? AND source_id=? AND json_extract(reference_json,'$.evidence_key')=?"
    args = (kind, source_id, evidence_key)
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
                    resolve_event(
                        conn,
                        kind="listing",
                        source_id=source,
                        evidence_key=digest(json.loads(row[0])),
                    )
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


def backfill_events(conn, *, limit, deadline_at):
    """Explicit finite legacy occurrence import; killed done rows stay rejected."""
    from pathlib import Path
    from jobagg.acceptance_catalog import verified_attempt
    from jobagg.publication_snapshot import _json_document

    cursor = conn.cursor()
    cursor.row_factory = sqlite3.Row
    rows = cursor.execute(
        "SELECT * FROM remediation_attempts a WHERE a.kind IN('listing','document') AND a.status='done' AND a.finished_at IS NOT NULL AND NOT EXISTS(SELECT 1 FROM jobagg_event_backfill b WHERE b.attempt_id=a.attempt_id) ORDER BY a.attempt_id LIMIT ?",
        (limit,),
    ).fetchall()
    cache = {}
    for row in rows:
        if time.monotonic() >= deadline_at:
            raise TimeoutError("accepted_event_backfill_deadline")
        disposition = "missing_successful_exit_proof"
        proof = verified_attempt(conn, row, deadline_at=deadline_at, cache=cache)
        if proof:
            task = cursor.execute(
                "SELECT * FROM remediation_tasks WHERE task_id=?", (row["task_id"],)
            ).fetchone()
            receipt = json.loads(row["evidence"])
            path_key, hash_key = (
                ("frame_path", "frame_sha256")
                if row["kind"] == "listing"
                else ("manifest", "sha256")
            )
            _, record = _json_document(Path(receipt[path_key]), deadline_at=deadline_at)
            if record["sha256"] != receipt[hash_key]:
                raise ValueError("accepted_event_backfill_artifact_changed")
            record_event(
                conn,
                task=task,
                attempt_id=row["attempt_id"],
                binding=None,
                run_id=proof["run_id"],
                acceptance_proof=proof,
            )
            disposition = "matched"
        conn.execute(
            "INSERT INTO jobagg_event_backfill VALUES(?,?)", (row["attempt_id"], disposition)
        )
    return {"processed": len(rows), "complete": len(rows) < limit}
