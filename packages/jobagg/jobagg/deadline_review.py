"""Preserve explicitly reviewed past-deadline classifications across refreshes.

This is not a global closure rule. Only identity-bound user review markers are
honoured; future deadlines release the classification for a possible extension.
"""
from datetime import UTC, datetime
import hashlib
import json
import uuid

MARKER = '_past_deadline_review'


def instant(value):
    if isinstance(value, datetime):
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed
    except ValueError:
        return None


def marker_for(raw, source_id, external_id):
    review = raw.get(MARKER)
    if (not isinstance(review, dict) or review.get('authority') != 'user_requested_deadline_classification'
            or review.get('source_id') != source_id or str(review.get('external_id')) != str(external_id)
            or not review.get('review_id') or instant(review.get('reviewed_at')) is None):
        return None
    return review


def apply_review(job, current, *, now=None):
    if current is None:
        return
    previous = json.loads(current['raw_json'] or '{}')
    review = marker_for(previous, job.source_id, job.external_id)
    if not review:
        return
    now = now or datetime.now(UTC)
    deadline = instant(job.closes_at) or instant(current['closes_at']) or instant(review.get('deadline_utc'))
    # An unknown deadline cannot withdraw a previously active reviewed hold.
    active = deadline < now if deadline and deadline != now else review.get('active') is True
    review = {**review, 'active': active}
    if deadline and deadline > now:
        review['release_reason'] = 'Incoming deadline is future'
    elif active:
        review.pop('release_reason', None)
    job.raw[MARKER] = review
    if active and job.status == 'open':
        job.status = 'expired'


def blocks_detail(current, proposed_deadline=None, *, now=None):
    if current is None:
        return False
    review = marker_for(json.loads(current['raw_json'] or '{}'), current['source_id'], current['external_id'])
    if not review or review.get('active') is not True:
        return False
    deadline = instant(proposed_deadline) or instant(current['closes_at']) or instant(review.get('deadline_utc'))
    return bool(deadline and deadline < (now or datetime.now(UTC)))


def reviewed_future_extension(current, proposed_deadline, *, now=None):
    """Only a positive future deadline releases the deadline-specific queue hold."""
    if current is None:
        return False
    review = marker_for(json.loads(current['raw_json'] or '{}'), current['source_id'], current['external_id'])
    deadline = instant(proposed_deadline)
    return bool(review and deadline and deadline > (now or datetime.now(UTC)))


def active_hold(conn, task_id):
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='remediation_deadline_holds'").fetchone():
        return None
    row = conn.execute(
        "SELECT * FROM remediation_deadline_holds WHERE task_id=? AND released_at IS NULL", (task_id,)
    ).fetchone()
    return dict(row) if row else None


def record_hold(conn, task_id, source_id, external_id, current, proposed_deadline=None):
    """Retain deadline evidence separately; never overwrite task accounting."""
    review = marker_for(json.loads(current['raw_json'] or '{}'), source_id, external_id)
    if not review or not blocks_detail(current, proposed_deadline):
        raise ValueError('Deadline hold requires identity-bound review evidence')
    deadline = instant(proposed_deadline) or instant(current['closes_at']) or instant(review.get('deadline_utc'))
    conn.execute("""CREATE TABLE IF NOT EXISTS remediation_deadline_holds(
        hold_id TEXT PRIMARY KEY, task_id TEXT NOT NULL, source_id TEXT NOT NULL,
        external_id TEXT NOT NULL, review_json TEXT NOT NULL, deadline_utc TEXT NOT NULL, recorded_at TEXT NOT NULL,
        released_at TEXT, release_json TEXT)""")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS active_deadline_hold ON remediation_deadline_holds(task_id) WHERE released_at IS NULL")
    if active_hold(conn, task_id):
        return
    conn.execute(
        "INSERT INTO remediation_deadline_holds(hold_id,task_id,source_id,external_id,review_json,deadline_utc,recorded_at) VALUES(?,?,?,?,?,?,?)",
        (uuid.uuid4().hex, task_id, source_id, str(external_id), json.dumps(review, sort_keys=True), deadline.isoformat(), datetime.now(UTC).isoformat()),
    )


def matches_hold_review(hold, current):
    if hold is None:
        return False  # A current marker cannot prove which review created a legacy hold.
    review = marker_for(json.loads(current['raw_json'] or '{}'), hold['source_id'], hold['external_id'])
    prior = json.loads(hold['review_json'])
    return bool(review and all(review.get(k) == prior.get(k) for k in
                               ('authority', 'source_id', 'external_id', 'review_id', 'reviewed_at')))


def release_hold(conn, task_id, evidence):
    hold = active_hold(conn, task_id)
    if hold:
        conn.execute(
            "UPDATE remediation_deadline_holds SET released_at=?,release_json=? WHERE hold_id=? AND released_at IS NULL",
            (datetime.now(UTC).isoformat(), json.dumps(evidence, sort_keys=True), hold['hold_id']),
        )


def hold_summaries(conn, source_id):
    """Read-only explanations, including legacy holds without retained proof."""
    tasks = {}
    if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='remediation_deadline_holds'").fetchone():
        for row in conn.execute(
            "SELECT h.*,t.status AS task_status,t.eligible_at FROM remediation_deadline_holds h "
            "LEFT JOIN remediation_tasks t ON t.task_id=h.task_id "
            "WHERE h.source_id=? AND h.released_at IS NULL", (source_id,)
        ):
            hold = dict(row)
            task = {'task_id': hold['task_id'], 'external_id': hold['external_id'],
                    'status': hold.pop('task_status'), 'eligible_at': hold.pop('eligible_at')}
            tasks[task['task_id']] = (task, hold)
    for task in conn.execute(
        "SELECT task_id,external_id,status,eligible_at FROM remediation_tasks WHERE source_id=? AND status='past_deadline'", (source_id,)
    ):
        tasks.setdefault(task['task_id'], (dict(task), None))
    result = []
    for task, hold in (tasks[key] for key in sorted(tasks)):
        review = json.loads(hold['review_json']) if hold else {}
        result.append({
            'task_id': task['task_id'], 'external_id': task['external_id'],
            'task_status': task['status'],
            'hold_id': hold['hold_id'] if hold else None,
            'review_id': review.get('review_id'), 'reviewed_at': review.get('reviewed_at'),
            'recorded_at': hold['recorded_at'] if hold else None,
            'deadline_utc': hold['deadline_utc'] if hold else None,
            'retained_eligible_at': task['eligible_at'],
            'reason': 'Identity-bound past-deadline review' if hold else 'Deadline hold lacks retained review evidence; reviewed reconciliation required',
            'automatic_retry': False,
            'release_conditions': (
                'A positive future deadline with the matching review, or an explicit evidenced correction bound to this hold; other task holds remain in force'
                if hold else 'Separate evidenced reconciliation is required before this proofless hold can be released'
            ),
        })
    return result


def validate_correction(hold, task, current, correction):
    """An absent/invalid marker is never itself authority to release a hold."""
    if not hold or task['kind'] != 'detail' or task['status'] != 'past_deadline':
        raise ValueError('Correction requires a retained deadline-only detail hold')
    review = marker_for({MARKER: json.loads(hold['review_json'])}, hold['source_id'], hold['external_id'])
    if not review:
        raise ValueError('Retained deadline review evidence is invalid')
    expected = {'task_id': task['task_id'], 'hold_id': hold['hold_id'],
                'source_id': task['source_id'], 'external_id': task['external_id'],
                'review_id': review['review_id']}
    if (hold['task_id'] != task['task_id'] or hold['source_id'] != task['source_id']
            or hold['external_id'] != task['external_id'] or current is None
            or current['source_id'] != task['source_id'] or current['external_id'] != task['external_id']
            or any(correction.get(k) != v for k, v in expected.items())
            or correction.get('authority') != 'user_requested_deadline_correction'
            or correction.get('action') != 'withdraw_classification'
            or not isinstance(correction.get('correction_id'), str) or not correction['correction_id'].strip()
            or not isinstance(correction.get('reason'), str) or not correction['reason'].strip()
            or instant(correction.get('reviewed_at')) is None
            or not instant(review['reviewed_at']) <= instant(correction['reviewed_at']) <= datetime.now(UTC)):
        raise ValueError('Correction does not bind the held identity, review and authority')
    current_review = marker_for(json.loads(current['raw_json'] or '{}'), task['source_id'], task['external_id'])
    active_unknown = bool(current_review and current_review.get('active') is True
                          and instant(current['closes_at']) is None
                          and instant(current_review.get('deadline_utc')) is None)
    if blocks_detail(current) or active_unknown:
        raise ValueError('Current identity-bound review still blocks the detail')
    return {'hold_id': hold['hold_id'], 'review_sha256': hashlib.sha256(hold['review_json'].encode()).hexdigest(),
            'correction': correction}
