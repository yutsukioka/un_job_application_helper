"""Resolve accepted detail evidence independently of a mutable refresh task.

The successful attempt and its immutable artifact establish acceptance. A task
may already be pending, blocked or failed for a later refresh. This helper does
not certify current public availability, freshness, or whole-page fidelity.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sqlite3


def _sha(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def matching_detail_attempts(conn, row, proof):
    """Return only exact successful lineage, never evidence from a failed attempt.

    Receipt/artifact failures are non-matches; the caller rejects acceptance when no
    matching lineage exists. Keeping only these rows in a publication projection
    therefore preserves both successful binding and the missing-lineage rejection.
    """
    cursor = conn.cursor()
    cursor.row_factory = sqlite3.Row
    attempts = cursor.execute(
        "SELECT a.* FROM remediation_attempts a JOIN remediation_tasks t "
        "ON t.task_id=a.task_id WHERE t.source_id=? AND t.kind='detail' "
        "AND t.external_id=? AND a.source_id=t.source_id AND a.kind=t.kind "
        "AND a.status='done' AND a.finished_at IS NOT NULL "
        "ORDER BY a.finished_at DESC,a.attempt_id",
        (row["source_id"], str(row["external_id"])),
    )
    matching = []
    for attempt in attempts:
        try:
            receipt = json.loads(attempt["evidence"] or "{}")
            path = Path(receipt["detail_path"])
            if not path.is_file() or _sha(path) != receipt.get("detail_sha256"):
                continue
            artifact = json.loads(path.read_text())
            parsed = artifact["job"]
            if (
                artifact.get("proof") == proof
                and parsed.get("source_id") == row["source_id"]
                and str(parsed.get("external_id")) == str(row["external_id"])
                and parsed.get("description") == row["description"]
            ):
                matching.append({"attempt": dict(attempt), "artifact": artifact})
        except (KeyError, TypeError, ValueError, AttributeError, OSError):
            continue
    return matching
