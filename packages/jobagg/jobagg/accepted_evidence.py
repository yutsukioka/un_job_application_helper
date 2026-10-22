"""Immutable accepted observation versions, bound in the acceptance transaction.

The pathname is a locator; the exact artifact/proof/public fields are identity.
Legacy backfill is an explicit checkpointed operation using the existing exact
lineage resolver; conflicting matches are retained, never chosen by recency.
"""

import hashlib
import json
import sqlite3
import time

CONTRACT = "accepted-detail-evidence-1"
TABLE = "jobagg_accepted_evidence"


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


def exists(conn):
    return bool(conn.execute("SELECT 1 FROM sqlite_master WHERE name=?", (TABLE,)).fetchone())


def prepare(conn):
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
    conn, *, job_key, task_id, attempt_id, artifact_path, artifact_sha256, job, proof, body_sha256
):
    if not exists(conn):
        return None  # Old workspaces keep their original exact resolver contract.
    from jobagg.remediation_worker import PUBLIC_FIELDS

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
    }
    version = digest(value)
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
    for values in rows:
        if time.monotonic() >= deadline_at:
            raise TimeoutError("accepted_evidence_backfill_deadline")
        row = dict(zip(names, values))
        proof = json.loads(row["accepted_proof"])
        matches = matching_detail_attempts(
            conn, row, proof, deadline_at=deadline_at, legacy_only=True
        )
        disposition = "matched" if matches else "missing_exact_acceptance"
        for match in matches:
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
                body_sha256=proof.get("body_sha256", proof.get("response_body_sha256", "")),
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
