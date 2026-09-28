"""Real Taleo parser/capture/publication integration for date-header regressions."""

from dataclasses import asdict
from datetime import UTC, datetime
import gzip
import hashlib
import json
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import pytest

from jobagg.adapters.base import AdapterContext
from jobagg.adapters.taleo import TaleoAdapter
from jobagg.db import JobDatabase
from jobagg.models import OrganizationSource
from test_live_publication import run, sha, write
from test_live_publication import setup as base_setup


@pytest.fixture
def setup(tmp_path):
    return base_setup.__wrapped__(tmp_path)


def _captured_taleo(setup, *, omit_prose=False, extend_deadline=False):
    fixture = (
        Path(__file__).parent / "fixtures/taleo_public_bindings/who_taleo_2603964_20260913.html"
    )
    original_html = fixture.read_text()
    original_url = json.loads(fixture.with_suffix(".html.provenance.json").read_text())[
        "original_url"
    ]
    source = OrganizationSource("who_taleo", "who_taleo", "taleo", original_url)
    adapter = TaleoAdapter(AdapterContext(source, None))
    original = adapter.parse_detail_html(original_html, original_url)
    original.first_seen_at = datetime(2020, 1, 1, tzinfo=UTC)
    original.last_seen_at = datetime(2020, 1, 1, tzinfo=UTC)
    closing = "Oct 23, 2026, 9:59:00 PM" if extend_deadline else "Sep 30, 2026, 9:59:00 PM"
    updated_html = original_html.replace(
        "Sep 9, 2026, 5:09:40 PM", "Sep 9, 2026, 3:09:40 PM"
    ).replace("Sep 30, 2026, 11:59:00 PM", closing)
    parts = urlsplit(original_url)
    query = {**dict(parse_qsl(parts.query)), "tz": "GMT+00:00", "tzname": "Etc/UTC"}
    updated_url = urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), ""))
    updated = adapter.parse_detail_html(updated_html, updated_url)
    if omit_prose:
        paragraph = next(value for value in updated.description.split("\n\n") if len(value) > 150)
        updated.description = updated.description.replace(paragraph, "")
    observed = "2026-09-14T10:00:00+00:00"
    updated.first_seen_at = datetime.fromisoformat(observed)
    updated.last_seen_at = datetime.fromisoformat(observed)
    setup["registry"].write_text(
        "sources:\n- id: who_taleo\n  name: WHO\n  ats_family: taleo\n  base_url: https://careers.who.int/careersection/ex/jobsearch.ftl\n  enabled: true\n"
    )
    for path in (setup["output"] / "who_jobs.sqlite3", setup["output"] / "all_jobs.sqlite3"):
        db = JobDatabase(path)
        db.initialize()
        db.upsert_job(original)
    setup["worker"].upsert_job(updated)
    root = setup["root"] / "taleo-capture"
    capture = root / "http" / "1.json"
    artifact = capture.with_suffix(".body.gz")
    artifact.parent.mkdir(parents=True)
    data = updated_html.encode()
    artifact.write_bytes(gzip.compress(data))
    write(
        capture,
        {
            "phase": {"kind": "detail", "job_id": updated.external_id},
            "status_code": 200,
            "body_captured": True,
            "artifact": str(artifact),
            "body_sha256": hashlib.sha256(data).hexdigest(),
            "body_bytes": len(data),
        },
    )
    body_sha = hashlib.sha256(updated.description.encode()).hexdigest()
    proof = {
        "source_id": updated.source_id,
        "external_id": updated.external_id,
        "observed_at": observed,
        "parsed_source_text_sha256": body_sha,
        "captures": [{"path": str(capture), "sha256": sha(capture)}],
        "completeness_certified": False,
    }
    detail_path = root / "detail.json"
    write(detail_path, {"job": asdict(updated), "proof": proof})
    with setup["worker"].connect() as conn:
        conn.execute(
            "INSERT INTO remediation_observations VALUES(?,?,?,?,?,?)",
            (
                updated.identity_key(),
                updated.source_id,
                updated.last_seen_at.timestamp(),
                body_sha,
                body_sha,
                json.dumps(proof),
            ),
        )
        conn.execute(
            "INSERT INTO remediation_tasks VALUES(?,?,?,?,?,?,?)",
            (
                "taleo-detail",
                updated.source_id,
                "detail",
                updated.external_id,
                "done",
                json.dumps({"detail_path": str(detail_path), "detail_sha256": sha(detail_path)}),
                "{}",
            ),
        )
        conn.execute(
            "INSERT INTO remediation_attempts VALUES(?,?,?,?,?,?,?,?)",
            (
                "accepted-taleo",
                "taleo-detail",
                updated.source_id,
                "detail",
                updated.last_seen_at.timestamp(),
                updated.last_seen_at.timestamp(),
                "done",
                json.dumps({"detail_path": str(detail_path), "detail_sha256": sha(detail_path)}),
            ),
        )
    assert len(" ".join(updated.description.split())) < len(" ".join(original.description.split()))
    return original, updated


@pytest.mark.parametrize("extend_deadline", [False, True])
def test_taleo_date_header_shortening_publishes_exact_new_capture(setup, extend_deadline):
    old, new = _captured_taleo(setup, extend_deadline=extend_deadline)
    result = run(setup, execute=True)
    assert result["status"] == "published"
    for filename in ("who_jobs.sqlite3", "all_jobs.sqlite3"):
        actual = JobDatabase(setup["output"] / filename).get_job(new.identity_key())
        assert actual["description"] == new.description
        assert actual["closes_at"] == new.closes_at.isoformat()
        assert actual["posted_at"] == new.posted_at.isoformat()
        assert not actual["raw"]["_jobagg_main_text_verification"]["complete"]
    if extend_deadline:
        assert (new.closes_at - old.closes_at).days == 23
    else:
        assert new.closes_at == old.closes_at


def test_taleo_date_header_change_does_not_allow_missing_prose(setup):
    old, new = _captured_taleo(setup, omit_prose=True)
    result = run(setup, execute=True)
    assert result["status"] == "no_changes"
    assert (
        result["rejected"][0]["reason"]
        == "shorter_than_retained_live_text_requires_full_source_contract"
    )
    actual = JobDatabase(setup["output"] / "all_jobs.sqlite3").get_job(old.identity_key())
    assert actual["description"] == old.description
