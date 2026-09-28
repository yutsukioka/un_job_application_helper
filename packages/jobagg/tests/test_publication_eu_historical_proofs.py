"""Fresh EU bodies must not inherit an older body certificate as active proof."""

from copy import deepcopy
from dataclasses import asdict
import hashlib
import json

import pytest

from jobagg.adapters.eu_primary_metadata import MARKER, PREVIOUS, apply_public_fields
from jobagg.db import JobDatabase
from jobagg.eu_primary_metadata_observation import bound_public_metadata
from jobagg.pipelines.live_publication import _merged_model
from test_eu_primary_metadata import sample


@pytest.mark.parametrize("index", [3, 4, 7])
def test_euaa_fresh_metadata_retains_old_certificate_only_as_history(tmp_path, index):
    original = sample(index)
    apply_public_fields(original, original.raw["official_notice_text"])
    db = JobDatabase(tmp_path / "live.sqlite3")
    db.initialize()
    db.upsert_job(original)
    with db.connect() as conn:
        before = dict(conn.execute("SELECT * FROM jobs").fetchone())
    new = deepcopy(original)
    new.raw.pop(PREVIOUS)
    new.description += "\n\n"
    apply_public_fields(new, new.raw["official_notice_text"])
    incoming = asdict(new)
    incoming["raw_json"] = json.dumps(incoming.pop("raw"), default=str)
    for field in ("first_seen_at", "last_seen_at", "posted_at", "closes_at"):
        if incoming.get(field):
            incoming[field] = incoming[field].isoformat()
    model = _merged_model(
        {
            "worker_row": incoming,
            "proof": {
                "observed_at": "2026-09-16T12:00:00+00:00",
                "parsed_source_text_sha256": hashlib.sha256(
                    incoming["description"].encode()
                ).hexdigest(),
            },
            "publication_key": "new-proof",
        },
        before,
        "generation",
    )
    assert PREVIOUS not in model.raw
    assert model.raw[MARKER] == new.raw[MARKER]
    assert bound_public_metadata(model.raw, asdict(model))
    history = model.raw["_deterministic_publication_historical_proofs"]
    assert history[-1]["proofs"][PREVIOUS] == original.raw[PREVIOUS]
    assert (
        history[-1]["description_sha256"]
        == hashlib.sha256(original.description.encode()).hexdigest()
    )
    db.upsert_job(model)  # The production guard must still validate the result.
    assert db.get_job(original.identity_key())["description"] == new.description


def test_invalid_incoming_eu_claims_are_not_repaired_by_archiving_proof(tmp_path):
    original = sample(7)
    apply_public_fields(original, original.raw["official_notice_text"])
    db = JobDatabase(tmp_path / "live.sqlite3")
    db.initialize()
    db.upsert_job(original)
    with db.connect() as conn:
        before = dict(conn.execute("SELECT * FROM jobs").fetchone())
    incoming = dict(before)
    raw = json.loads(incoming["raw_json"])
    raw.pop(PREVIOUS)
    raw[MARKER]["description_sha256"] = "unbound"
    incoming["raw_json"] = json.dumps(raw)
    model = _merged_model(
        {
            "worker_row": incoming,
            "proof": {
                "observed_at": "2026-09-16T12:00:00+00:00",
                "parsed_source_text_sha256": hashlib.sha256(
                    incoming["description"].encode()
                ).hexdigest(),
            },
            "publication_key": "invalid",
        },
        before,
        "generation",
    )
    assert PREVIOUS in model.raw
    with pytest.raises(ValueError, match="exact source/body/provenance binding"):
        db.upsert_job(model)
