"""Preserve explicitly reviewed past-deadline classifications across refreshes.

This is not a global closure rule. Only identity-bound user review markers are
honoured; future deadlines release the classification for a possible extension.
"""
from datetime import UTC, datetime
import json

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
    active = bool(deadline and deadline < now)
    review = {**review, 'active': active}
    if not active:
        review['release_reason'] = 'Incoming deadline is future or cannot be established'
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
