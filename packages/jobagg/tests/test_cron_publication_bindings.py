"""Regressions for source observations damaged by publication's generic merge."""

from copy import deepcopy
from dataclasses import asdict
from datetime import UTC, datetime
import hashlib
import json

import pytest

from jobagg.adapters.base import AdapterContext
from jobagg.adapters.unv import UNVAdapter
from jobagg.db import JobDatabase
from jobagg.models import OrganizationSource
from jobagg.pipelines.live_publication import _merged_model, _preview_merge
from test_unv_public_fields import fixture
from test_worldbank_public_retention import detail, listing
from test_workday_public_deadlines import parse, payload


def row(job):
    value = json.loads(json.dumps(asdict(job), default=lambda x: x.isoformat()))
    value["raw_json"] = json.dumps(value.pop("raw"))
    return value


def unv():
    adapter = UNVAdapter(
        AdapterContext(OrganizationSource("unv_uvp", "UNV", "unv", "https://app.unv.org"), None)
    )
    return adapter.parse_jobs({"value": fixture(observed_category_filter=True)})[0]


@pytest.mark.parametrize("kind", ["unv", "worldbank", "workday"])
def test_source_observation_survives_real_publication_preview(tmp_path, kind):
    if kind == "unv":
        incoming = unv()
        old = deepcopy(incoming)
        old.employment_type = "Previously inferred type"
    elif kind == "worldbank":
        incoming, old = detail(), listing()
        assert incoming.raw["preferred_languages"] == ""
    else:
        incoming = parse("unhcr_workday", payload("unhcr_workday", "JR2668710"))
        old = deepcopy(incoming)
        old.raw = {}
        old.posted_at = old.closes_at = datetime(2020, 1, 1, tzinfo=UTC)
    db = JobDatabase(tmp_path / "live.sqlite3")
    db.initialize()
    db.upsert_job(old)
    with db.connect() as conn:
        before = dict(conn.execute("SELECT * FROM jobs").fetchone())
        change = {
            "worker_row": row(incoming),
            "publication_key": "regression",
            "proof": {
                "observed_at": "2026-09-21T12:00:00+00:00",
                "parsed_source_text_sha256": hashlib.sha256(
                    incoming.description.encode()
                ).hexdigest(),
            },
        }
        merged = _merged_model(change, before, "test")
        preview = _preview_merge(change, before, conn)
    assert preview["description"] == incoming.description
    if kind == "unv":
        assert (
            merged.raw["_jobagg_main_text_verification"]
            == incoming.raw["_jobagg_main_text_verification"]
        )
        assert merged.employment_type == incoming.employment_type
    elif kind == "worldbank":
        assert merged.raw["preferred_languages"] == ""
        assert preview["posted_at"] is None
        assert preview["employment_type"] is None
    else:
        assert preview["posted_at"] == row(incoming)["posted_at"]
        assert preview["closes_at"] is None
    db.upsert_job(merged)
    assert db.get_job(incoming.identity_key())["description"] == incoming.description
    # A fresh certificate must not hide an altered source body.
    change["worker_row"]["description"] += " Unobserved extra requirement."
    with db.connect() as conn, pytest.raises(ValueError):
        _preview_merge(change, before, conn)
