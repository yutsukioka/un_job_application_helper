"""Keep primary-notice metadata separate from the reviewed PDF body certificate."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime

from jobagg.adapters.eu_primary_metadata import FIELDS, MARKER, PREVIOUS, apply_public_fields
from jobagg.eu_primary_observation import MARKER as TEXT_MARKER, bound_public_text
from jobagg.models import JobRecord


def _date(value):
    return datetime.fromisoformat(value) if isinstance(value, str) else value


def _bound(raw, row):
    if (row["source_id"] != "eu_careers_static" or TEXT_MARKER in raw
            or not isinstance(raw.get(MARKER), dict)):
        return False
    if PREVIOUS in raw:
        proof = raw[PREVIOUS]
        if not isinstance(proof, dict) or not proof:
            return False
        retained = proof.get("retained_normalized_fields")
        if not isinstance(retained, dict) or set(retained) != set(FIELDS):
            return False
        # This historical certificate owns text/pages/retrieval, not the newly
        # corrected metadata. Restore its original metadata view only for its
        # independent body validator; never publish that old view again.
        prior_raw = {**deepcopy(raw), TEXT_MARKER: deepcopy(proof)}
        prior_row = {key: row[key] for key in (
            "source_id", "external_id", "source_url", "apply_url", "description",
        )}
        prior_row.update(retained)
        if not bound_public_text(prior_raw, prior_row):
            return False
    values = {key: row[key] for key in (
        "source_id", "external_id", "source_url", "apply_url", "description", *FIELDS,
    )}
    values["posted_at"], values["closes_at"] = _date(values["posted_at"]), _date(values["closes_at"])
    expected = apply_public_fields(JobRecord(**values, org_id=values["source_id"], ats_family="static_html", raw=deepcopy(raw)), raw.get("official_notice_text"))
    if raw[MARKER] != expected.raw[MARKER]:
        return False
    # The deterministic source parser owns all eight fields, including explicit
    # None values. A prior reviewed document is optional for a fresh metadata
    # projection; absence never creates a visual/full-document certificate.
    for key in FIELDS:
        value = _date(row[key]) if key in {"posted_at", "closes_at"} else row[key]
        if value != getattr(expected, key):
            return False
    return True


def bound_public_metadata(raw, row):
    """Portable source/body/metadata binding; original capture files bind at import."""
    try:
        return _bound(raw, row)
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError):
        return False
