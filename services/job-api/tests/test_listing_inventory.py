"""Read-only listing visibility is separate from fully verified job text."""

from datetime import datetime, timezone
import hashlib
import json
import sqlite3

from fastapi.testclient import TestClient
import pytest

from job_api.app import create_app
from job_api.config import ApiSettings
from job_api.listing_inventory import listing_inventory


@pytest.fixture
def inventory(tmp_path):
    path = tmp_path / "jobs.sqlite3"
    with sqlite3.connect(path) as conn:
        conn.executescript("""
        CREATE TABLE jobs(job_key TEXT PRIMARY KEY,source_id TEXT,external_id TEXT,description TEXT,org_id TEXT);
        CREATE TABLE live_listing_frames(source_id TEXT PRIMARY KEY,observed_at TEXT,frame_sha256 TEXT,
            inventory_complete INTEGER,observed_count INTEGER,proof_json TEXT,generation_id TEXT);
        CREATE TABLE live_listing_inventory(source_id TEXT,external_id TEXT,worker_job_key TEXT,canonical_job_key TEXT,
            title TEXT,apply_url TEXT,observed_at TEXT,observed_in_latest_listing INTEGER,inventory_complete INTEGER,
            published_detail INTEGER,listing_json TEXT,generation_id TEXT,PRIMARY KEY(source_id,external_id));
        """)
        conn.executemany(
            "INSERT INTO jobs VALUES(?,?,?,?,?)",
            [
                (
                    "canonical:one%2D",
                    "source_a",
                    "one%2D",
                    "Retained full public text",
                    "Organization A",
                ),
                (
                    "canonical:one-",
                    "source_a",
                    "one-",
                    "Different identity",
                    "Organization A",
                ),
                (
                    "foreign:two",
                    "source_b",
                    "two",
                    "Other source public text",
                    "Organization B",
                ),
            ],
        )
        for source, complete, count in (("source_a", 1, 3), ("source_b", 0, 1)):
            conn.execute(
                "INSERT INTO live_listing_frames VALUES(?,?,?,?,?,?,?)",
                (
                    source,
                    "2026-09-14T12:00:00+00:00",
                    "frame-" + source,
                    complete,
                    count,
                    json.dumps(
                        {
                            "complete": bool(complete),
                            "reason": "captured configured scope",
                        }
                    ),
                    "generation-one",
                ),
            )
        rows = [
            ("source_a", "one%2D", "canonical:one%2D", 1, 1, 1),
            ("source_a", "pending", None, 1, 1, 0),
            ("source_a", "two", "foreign:two", 1, 1, 1),
            ("source_a", "gone", None, 0, 1, 0),
            ("source_b", "pending-b", None, 1, 0, 0),
            ("source_b", "unknown", None, None, 0, 0),
        ]
        for source, external, canonical, present, complete, detail in rows:
            conn.execute(
                "INSERT INTO live_listing_inventory VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    source,
                    external,
                    source + ":" + external,
                    canonical,
                    "Listing " + external,
                    "https://example.org/jobs/" + external,
                    "2026-09-14T12:00:00+00:00",
                    present,
                    complete,
                    detail,
                    json.dumps({"org_id": "Organization " + source}),
                    "generation-one",
                ),
            )
    return path


@pytest.fixture
def client(inventory):
    directory = inventory.parent
    settings = ApiSettings(
        repo_root=directory,
        db_path=inventory,
        saved_searches_path=directory / "saved.json",
        tracker_path=directory / "tracker.json",
    )
    with TestClient(create_app(settings)) as value:
        yield value


def test_legacy_or_missing_database_is_explicitly_unavailable_without_creating_files(
    tmp_path,
):
    path = tmp_path / "missing.sqlite3"
    assert listing_inventory(path)["available"] is False
    assert not path.exists()
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE jobs(job_key TEXT)")
    before = path.read_bytes()
    result = listing_inventory(path)
    assert result["available"] is False
    assert result["counts"] is None and result["total"] is None
    assert before == path.read_bytes()


def test_current_observations_and_absence_unknown_have_distinct_counts(inventory):
    result = listing_inventory(inventory)
    assert result["total"] == 4
    assert result["counts"] == {
        "tracked": 6,
        "observed_current": 4,
        "detail_pending": 2,
        "detail_published": 2,
        "absent_from_complete_frame": 1,
        "presence_unknown": 1,
    }
    assert len(result["listings"]) == 4
    assert all(row["observed_in_latest_listing"] is True for row in result["listings"])
    assert not result["completeness_certified"]


def test_pending_filter_and_source_filter_preserve_listing_without_inventing_description(
    client,
):
    response = client.get(
        "/api/listing-inventory", params={"source": "source_a", "pending_only": "true"}
    )
    assert response.status_code == 200
    result = response.json()
    assert result["total"] == 1
    row = result["listings"][0]
    assert row["external_id"] == "pending" and row["title"] == "Listing pending"
    assert row["published_detail"] is False and row["detail_available"] is False
    assert row["detail_url"] is None and "description" not in row
    assert row["detail_status"] == "pending"
    assert [frame["source_id"] for frame in result["sources"]] == ["source_a"]


def test_exact_native_identity_joins_and_foreign_canonical_mapping_is_not_exposed(
    inventory,
):
    result = listing_inventory(inventory, source="source_a")
    rows = {row["external_id"]: row for row in result["listings"]}
    assert rows["one%2D"]["canonical_job_key"] == "canonical:one%2D"
    assert (
        rows["one%2D"]["detail_url"] == "/api/job-detail?job_key=canonical%3Aone%252D"
    )
    assert rows["one%2D"]["source_name"] == "Organization A"
    assert rows["two"]["canonical_job_key"] is None
    assert (
        rows["two"]["detail_available"] is False and rows["two"]["detail_url"] is None
    )
    assert rows["two"]["detail_status"] == "publication_binding_conflict"


def test_stable_bounded_pagination_does_not_omit_or_repeat_rows(client):
    first = client.get("/api/listing-inventory", params={"limit": 2}).json()
    second = client.get(
        "/api/listing-inventory", params={"limit": 2, "offset": first["next_offset"]}
    ).json()
    keys = [
        (row["source_id"], row["external_id"])
        for row in first["listings"] + second["listings"]
    ]
    assert len(keys) == len(set(keys)) == 4
    assert keys == sorted(keys)
    assert second["next_offset"] is None


@pytest.mark.parametrize(
    "params", [{"limit": 0}, {"limit": 1001}, {"offset": -1}, {"source": ""}]
)
def test_invalid_pagination_and_source_rejected_by_route(client, params):
    assert client.get("/api/listing-inventory", params=params).status_code == 422


def test_current_frame_proof_and_age_do_not_claim_freshness_or_whole_job_completeness(
    inventory,
):
    now = datetime(2026, 9, 14, 13, tzinfo=timezone.utc)
    result = listing_inventory(inventory, now=now)
    frames = {row["source_id"]: row for row in result["sources"]}
    assert frames["source_a"]["inventory_complete"] is True
    assert frames["source_b"]["inventory_complete"] is False
    assert frames["source_a"]["age_seconds"] == 3600
    assert frames["source_a"]["freshness_certified"] is False
    assert frames["source_b"]["presence_unknown"] == 1
    assert frames["source_a"]["evidence"] == {"complete": True}
    assert all(frame["completeness_certified"] is False for frame in frames.values())


def test_route_honors_publication_gate(client, inventory):
    path = inventory.parent / ".jobagg-publication-state.json"
    for state in ("publishing", "exporting"):
        path.write_text(json.dumps({"state": state, "generation_id": "one"}))
        result = client.get("/api/listing-inventory")
        assert result.status_code == 503
        assert result.headers["cache-control"] == "no-store"
    path.write_text(json.dumps({"state": "complete", "generation_id": "one"}))
    assert client.get("/api/listing-inventory").status_code == 200


def test_every_query_is_read_only_and_unknown_source_is_an_empty_scoped_result(
    inventory,
):
    before = hashlib.sha256(inventory.read_bytes()).hexdigest()
    result = listing_inventory(inventory, source="not-configured")
    assert result["available"] is True and result["total"] == 0
    assert result["sources"] == [] and result["listings"] == []
    assert hashlib.sha256(inventory.read_bytes()).hexdigest() == before


def test_public_inventory_projects_evidence_without_local_paths(client, inventory):
    proof = {"complete": True, "method": "workday_cxs_v1", "observed_count": 3,
             "page_count": 1, "capture_paths": [{"path": "/Users/private/worker/run/http/1.json", "sha256": "a" * 64}],
             "reasons": ["Failure opening /Users/private/secret"],
             "unexpected": {"path": "/mnt/private"}, "scope": "/Users/private/source"}
    with sqlite3.connect(inventory) as conn:
        conn.execute("UPDATE live_listing_frames SET proof_json=?", (json.dumps(proof),))
    response = client.get("/api/listing-inventory")
    assert response.status_code == 200
    assert "/Users" not in response.text and "/mnt" not in response.text
    assert response.json()["sources"][0]["evidence"] == {
        "complete": True, "method": "workday_cxs_v1", "observed_count": 3,
        "page_count": 1, "capture_sha256": ["a" * 64]}
