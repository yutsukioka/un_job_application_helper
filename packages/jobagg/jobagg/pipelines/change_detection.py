"""Change detection summaries over persisted events."""

from __future__ import annotations

from collections import Counter
from datetime import datetime

from jobagg.db import JobDatabase


def summarize_current_jobs(db: JobDatabase, source_id: str | None = None) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for job in db.iter_jobs(source_id=source_id):
        counts[job["status"]] += 1
    return dict(counts)


def open_jobs(db: JobDatabase, source_id: str | None = None) -> list[dict]:
    return [
        job
        for job in db.iter_jobs(source_id=source_id)
        if job.get("status") == "open"
    ]


def changed_jobs(
    db: JobDatabase,
    source_id: str | None = None,
    since: datetime | str | None = None,
) -> list[dict]:
    query = """
        SELECT
            change_events.id AS change_event_id,
            change_events.source_id,
            change_events.job_key,
            change_events.change_type,
            change_events.old_hash,
            change_events.new_hash,
            change_events.observed_at,
            jobs.title,
            jobs.external_id,
            jobs.apply_url,
            jobs.status
        FROM change_events
        LEFT JOIN jobs ON jobs.job_key = change_events.job_key
    """
    clauses = []
    params = []
    if source_id:
        clauses.append("change_events.source_id = ?")
        params.append(source_id)
    if since is not None:
        clauses.append("change_events.observed_at >= ?")
        params.append(since.isoformat() if isinstance(since, datetime) else str(since))
    if clauses:
        query += " WHERE " + " AND ".join(clauses)
    query += " ORDER BY change_events.observed_at, change_events.id"

    with db.connect() as conn:
        return [dict(row) for row in conn.execute(query, tuple(params)).fetchall()]
