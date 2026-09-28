from __future__ import annotations

import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "packages" / "jobagg"))
sys.path.insert(0, str(ROOT / "services" / "job-api"))

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402
from job_api.app import create_app  # noqa: E402
from job_api.config import ApiSettings  # noqa: E402
from jobagg.classification import classify_database  # noqa: E402
from jobagg.db import JobDatabase  # noqa: E402
from jobagg.models import OrganizationSource  # noqa: E402
from jobagg.normalize import build_job  # noqa: E402


def test_C4_job_api_annotates_apply_and_source_url_trust(tmp_path: Path) -> None:
    settings = ApiSettings(
        repo_root=ROOT,
        db_path=tmp_path / "all_jobs.sqlite3",
        saved_searches_path=tmp_path / "saved_searches.json",
        tracker_path=tmp_path / "tracker.json",
    )
    source = OrganizationSource(
        id="un_inspira",
        name="UN Inspira",
        ats_family="inspira",
        base_url="https://careers.un.org",
    )
    db = JobDatabase(settings.db_path)
    db.initialize()
    db.upsert_job(
        build_job(
            source,
            title="Programme Management Officer, P-3",
            external_id="cross-origin",
            location="Nairobi",
            apply_url="https://apply.vendor.example/jobs/cross-origin",
            source_url="https://careers.un.org/jobSearchDescription/cross-origin",
            raw={"jl": {"name": "P-3"}, "dutyStation": [{"description": "Nairobi"}]},
        )
    )
    classify_database(db, force=True)
    client = TestClient(create_app(settings))

    search = client.post("/api/search", json={"text": "Programme", "limit": 10})

    assert search.status_code == 200
    row = search.json()["results"][0]
    assert row["apply_url_trust"] == {
        "origin_host": "apply.vendor.example",
        "matches_source_org": False,
    }
    assert row["source_url_trust"] == {
        "origin_host": "careers.un.org",
        "matches_source_org": True,
    }

    detail = client.get(f"/api/jobs/{row['job_key']}")

    assert detail.status_code == 200
    payload = detail.json()
    assert payload["apply_url_trust"] == row["apply_url_trust"]
    assert payload["source_url_trust"] == row["source_url_trust"]
