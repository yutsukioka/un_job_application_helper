from copy import deepcopy
from dataclasses import asdict
from datetime import datetime, UTC
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import jobagg

from jobagg.adapters.base import AdapterContext
from jobagg.adapters.imo import IMOAPIAdapter
from jobagg.adapters.imo_public import (
    MARKER,
    apply_public_date_precision,
    bound_public_date_fields,
)
from jobagg.db import JobDatabase
from jobagg.models import OrganizationSource
from jobagg.normalize import parse_datetime
from jobagg.pipelines.live_publication import _merged_model

ROOT = Path(__file__).parent
ITEMS = json.loads((ROOT / "fixtures/imo/current_vacancies_20260915.json").read_text())
SOURCE = OrganizationSource(
    "imo_api", "IMO", "imo_api", "https://recruit.imo.org", extra={"date_locale": "EU"}
)


def parsed(item):
    return IMOAPIAdapter(AdapterContext(SOURCE, http=SimpleNamespace())).parse_jobs(
        [deepcopy(item)]
    )[0]


def legacy(item):
    job = parsed(item)
    job.raw.pop(MARKER)
    job.posted_at = parse_datetime(item["dateofissue"], date_locale="EU")
    job.closes_at = parse_datetime(item["deadlineforapplications"], date_locale="EU")
    job.closes_at_local = None
    job.closes_tz = None
    return job


def test_loaded_native_package():
    assert Path(jobagg.__file__).resolve() == ROOT.parent / "jobagg/__init__.py"


@pytest.mark.parametrize("item", ITEMS, ids=lambda x: str(x["jobVacancyId"]))
def test_actual_all_current_api_calendars_and_merge_clear_old_midnight(tmp_path, item):
    job = parsed(item)
    old = legacy(item)
    assert old.posted_at is not None and old.closes_at is not None
    assert job.posted_at is None and job.closes_at is None and job.closes_tz is None
    assert (
        job.closes_at_local
        == datetime.strptime(item["deadlineforapplications"], "%d/%m/%Y")
        .date()
        .isoformat()
    )
    assert job.description == old.description
    assert all(job.raw[key] == value for key, value in item.items())
    db = JobDatabase(tmp_path / "jobs.sqlite3")
    db.initialize()
    db.upsert_job(old)
    with db.connect() as conn:
        before = dict(conn.execute("SELECT * FROM jobs").fetchone())
    db.upsert_job(job)
    row = db.get_job(job.identity_key())
    assert row["posted_at"] is row["closes_at"] is row["closes_tz"] is None
    assert row["closes_at_local"] == job.closes_at_local
    assert row["description"] == old.description
    # Publisher's missing-field fill is a separate retention layer.
    with db.connect() as conn:
        incoming = dict(conn.execute("SELECT * FROM jobs").fetchone())
    change = {
        "worker_row": incoming,
        "proof": {
            "observed_at": datetime.now(UTC).isoformat(),
            "parsed_source_text_sha256": hashlib.sha256(
                job.description.encode()
            ).hexdigest(),
        },
        "publication_key": "test",
    }
    public = _merged_model(change, before, "test-generation")
    assert public.posted_at is public.closes_at is public.closes_tz is None
    assert public.closes_at_local == job.closes_at_local
    destination = JobDatabase(tmp_path / "live.sqlite3")
    destination.initialize()
    destination.upsert_job(legacy(item))
    destination.upsert_job(public)
    live = destination.get_job(job.identity_key())
    assert live["posted_at"] is live["closes_at"] is live["closes_tz"] is None
    assert live["description"] == row["description"]


@pytest.mark.parametrize(
    "field,value",
    [
        ("source_id", "other"),
        ("external_id", "99999"),
        ("source_url", "https://wrong.example/940"),
        ("apply_url", "https://wrong.example/940"),
        ("description", "Other body"),
        ("posted_at", "2026-09-04T00:00:00Z"),
        ("closes_at_local", "2026-01-01"),
    ],
)
def test_tampered_canonical_marker_never_clears_dates(field, value):
    job = parsed(ITEMS[0])
    row = asdict(job)
    row[field] = value
    with pytest.raises(ValueError):
        bound_public_date_fields(job.raw, row)


@pytest.mark.parametrize(
    "mutation", ["raw_date", "body", "marker", "identity", "utc_claim"]
)
def test_tampered_source_or_marker_rejected_by_db(tmp_path, mutation):
    item = ITEMS[0]
    job = parsed(item)
    db = JobDatabase(tmp_path / "jobs.sqlite3")
    db.initialize()
    db.upsert_job(legacy(item))
    if mutation == "raw_date":
        job.raw["deadlineforapplications"] = "01/01/2027"
    elif mutation == "body":
        job.raw["education"] = "Changed requirement"
    elif mutation == "marker":
        job.raw[MARKER]["whole_job_certified"] = True
    elif mutation == "identity":
        job.raw["jobVacancyId"] = 99999
    else:
        job.closes_at = datetime(2026, 1, 1, tzinfo=UTC)
    with pytest.raises(ValueError):
        db.upsert_job(job)
    assert db.get_job(job.identity_key())["closes_at"] is not None


def test_invalid_calendar_is_rejected_and_explicit_instant_is_not_erased():
    item = deepcopy(ITEMS[0])
    item["deadlineforapplications"] = "31/02/2026"
    with pytest.raises(ValueError):
        parsed(item)
    item["deadlineforapplications"] = "2026-10-01T23:59:00Z"
    job = parsed(item)
    assert job.closes_at == datetime(2026, 10, 1, 23, 59, tzinfo=UTC)
    assert "closes_at" not in job.raw[MARKER]["owned_fields"]


def test_newer_public_date_is_used_and_unsupported_sentinels_are_not_created():
    item = deepcopy(ITEMS[0])
    item["deadlineforapplications"] = "01/10/2026"
    assert parsed(item).closes_at_local == "2026-10-01"
    item["dateofissue"] = None
    item["deadlineforapplications"] = None
    job = parsed(item)
    assert MARKER not in job.raw and job.closes_at is None


def test_worker_fresh_guarded_detail_clears_existing_midnight(tmp_path, monkeypatch):
    from jobagg.http import JobAggHTTPClient, HttpResponse
    from jobagg.remediation_worker import Worker
    from jobagg.pipelines import http_checkpoint

    monkeypatch.setattr(http_checkpoint.time, "sleep", lambda _: None)
    registry = tmp_path / "registry.yaml"
    registry.write_text(
        "sources:\n  - id: imo_api\n    name: IMO\n    ats_family: imo_api\n    base_url: https://recruit.imo.org\n    extra:\n      date_locale: EU\n"
    )
    robots = tmp_path / "robots.yaml"
    robots.write_text("default:\n  honor_robots_txt: false\n  min_delay_seconds: 0\n")
    owner = tmp_path / "owner.lock"
    bootstrap = tmp_path / "bootstrap.json"
    bootstrap.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "shared_lock": str(owner),
                "reviewed_at": datetime.now(UTC).isoformat(),
                "prior_writers_reviewed": True,
                "no_unmigrated_policy_state": True,
                "scope_source_ids": ["imo_api"],
                "evidence": [],
                "detail_attempts": [],
                "host_states": {},
                "source_holds": {},
                "review_note": "Temporary isolated test",
            }
        )
    )
    calls = []

    class Client(JobAggHTTPClient):
        def __init__(self):
            super().__init__(min_delay_seconds=0, max_retries=0)

        def _request(self, url, **kwargs):
            calls.append(url)
            content = json.dumps([ITEMS[0]]).encode()
            return HttpResponse(
                url,
                200,
                {"Content-Type": "application/json"},
                content.decode(),
                content,
            )

    worker = Worker(
        registry=registry,
        robots=robots,
        workspace=tmp_path / "worker",
        shared_lock=owner,
        max_tasks=1,
        client_factory=lambda s, p: Client(),
        policy_bootstrap=bootstrap,
    )
    worker.tick(execute=True)
    with worker.db.connect() as conn:
        original = legacy(ITEMS[0])
        raw = deepcopy(original.raw)
        conn.execute(
            "UPDATE jobs SET posted_at=?,closes_at=?,closes_at_local=NULL,closes_tz=NULL,raw_json=?",
            (
                original.posted_at.isoformat(),
                original.closes_at.isoformat(),
                json.dumps(raw),
            ),
        )
    report = worker.tick(execute=True)
    row = worker.db.get_job(original.identity_key())
    assert len(calls) == 2 and row["posted_at"] is row["closes_at"] is None
    assert (
        row["closes_at_local"]
        == datetime.strptime(ITEMS[0]["deadlineforapplications"], "%d/%m/%Y")
        .date()
        .isoformat()
    )
    with worker.db.connect() as conn:
        assert (
            conn.execute(
                "SELECT status FROM remediation_tasks WHERE kind='detail'"
            ).fetchone()[0]
            == "done"
        )
        assert (
            conn.execute("SELECT count(*) FROM remediation_observations").fetchone()[0]
            == 1
        )
    assert report["completeness_certified"] is False


@pytest.mark.parametrize("empty", [None, "", "missing"])
def test_raw_date_null_empty_absence_never_inherits_old_claim_in_db_or_publisher(
    tmp_path, empty
):
    old_item = deepcopy(ITEMS[0])
    old_item["jobCloseDateExternal"] = "2030-01-01T00:00:00Z"
    item = deepcopy(ITEMS[0])
    if empty == "missing":
        item.pop("jobCloseDateExternal")
    else:
        item["jobCloseDateExternal"] = empty
    job = parsed(item)
    old = legacy(old_item)
    db = JobDatabase(tmp_path / "old.sqlite3")
    db.initialize()
    db.upsert_job(old)
    with db.connect() as conn:
        before = dict(conn.execute("SELECT * FROM jobs").fetchone())
    db.upsert_job(job)
    row = db.get_job(job.identity_key())
    assert ("jobCloseDateExternal" in row["raw"]) == ("jobCloseDateExternal" in item)
    if empty != "missing":
        assert row["raw"]["jobCloseDateExternal"] == empty
    bound_public_date_fields(row["raw"], row)
    with db.connect() as conn:
        incoming = dict(conn.execute("SELECT * FROM jobs").fetchone())
    change = {
        "worker_row": incoming,
        "proof": {
            "observed_at": datetime.now(UTC).isoformat(),
            "parsed_source_text_sha256": hashlib.sha256(
                job.description.encode()
            ).hexdigest(),
        },
        "publication_key": "test",
    }
    public = _merged_model(change, before, "test")
    assert ("jobCloseDateExternal" in public.raw) == ("jobCloseDateExternal" in item)
    if empty != "missing":
        assert public.raw["jobCloseDateExternal"] == empty
    bound_public_date_fields(public.raw, public)


def test_raw_date_key_presence_tamper_is_rejected():
    job = parsed(ITEMS[0])
    job.raw["dateofissue"] = None
    job = apply_public_date_precision(job)
    del job.raw["dateofissue"]
    with pytest.raises(ValueError, match="marker"):
        bound_public_date_fields(job.raw, job)


def test_empty_body_source_row_remains_enumerated_without_detail_marker():
    item = {
        "jobVacancyId": 99999,
        "title": "Future vacancy",
        "dateofissue": "04/09/2026",
        "deadlineforapplications": "25/09/2026",
    }
    adapter = IMOAPIAdapter(AdapterContext(SOURCE, http=SimpleNamespace()))
    jobs = adapter.parse_jobs([item, deepcopy(ITEMS[0])])
    assert len(jobs) == 2 and jobs[0].external_id == "99999"
    assert not jobs[0].description and MARKER not in jobs[0].raw
    assert MARKER in jobs[1].raw


@pytest.mark.parametrize("key", [
    "jobDescription", "purposeforthepost", "maindutiesandresponsibilities",
    "requiredcompetencies", "professionalexperience", "education",
    "languageskills", "otherskills", "contractInformation", "salaryinformation",
    "essentialCompetencies", "desiredCompetencies", "salary",
    "competencyQuestions", "backgroundQuestions",
])
@pytest.mark.parametrize("mode", ["null", "empty", "absent"])
def test_current_body_input_presence_survives_db_and_publisher(tmp_path, key, mode):
    old_item = deepcopy(ITEMS[0])
    old_text = "Superseded public source wording."
    old_item[key] = [old_text] if key.endswith("Questions") else old_text
    current_item = deepcopy(ITEMS[0])
    present = mode != "absent"
    current_value = None if mode == "null" else ([] if key.endswith("Questions") else "")
    if present:
        current_item[key] = current_value
    else:
        current_item.pop(key, None)
    old = legacy(old_item)
    old.raw["historical_observation"] = {"preserve": True}
    job = parsed(current_item)
    assert old_text in old.description and old_text not in job.description
    db = JobDatabase(tmp_path / "worker.sqlite3")
    db.initialize()
    db.upsert_job(old)
    with db.connect() as conn:
        before = dict(conn.execute("SELECT * FROM jobs").fetchone())
    db.upsert_job(job)
    stored = db.get_job(job.identity_key())
    assert (key in stored["raw"]) is present
    if present:
        assert stored["raw"][key] == current_value
    assert old_text not in stored["description"]
    bound_public_date_fields(stored["raw"], stored)
    with db.connect() as conn:
        incoming = dict(conn.execute("SELECT * FROM jobs").fetchone())
    public = _merged_model({
        "worker_row": incoming,
        "proof": {"observed_at": datetime.now(UTC).isoformat(),
                  "parsed_source_text_sha256": hashlib.sha256(job.description.encode()).hexdigest()},
        "publication_key": "test",
    }, before, "test")
    assert (key in public.raw) is present
    if present:
        assert public.raw[key] == current_value
    assert public.raw["historical_observation"] == {"preserve": True}
    assert public.description == job.description
    bound_public_date_fields(public.raw, public)
    destination = JobDatabase(tmp_path / "live.sqlite3")
    destination.initialize()
    destination.upsert_job(legacy(old_item))
    destination.upsert_job(public)
    published = destination.get_job(job.identity_key())
    assert (key in published["raw"]) is present
    bound_public_date_fields(published["raw"], published)


@pytest.mark.parametrize("key", ["salaryinformation", "competencyQuestions"])
def test_body_null_presence_is_bound_even_when_rendered_text_is_identical(key):
    item = deepcopy(ITEMS[0])
    item[key] = None
    job = parsed(item)
    del job.raw[key]
    with pytest.raises(ValueError, match="marker"):
        bound_public_date_fields(job.raw, job)
