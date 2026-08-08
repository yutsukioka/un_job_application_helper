from __future__ import annotations

import importlib
import json
import sys
import threading
from copy import deepcopy
from dataclasses import fields
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages" / "jobagg"))
sys.path.insert(0, str(ROOT / "services" / "job-api"))

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402
from job_api import tracker as tracker_store  # noqa: E402
from job_api.app import create_app  # noqa: E402
from job_api.config import ApiSettings  # noqa: E402
from job_api.models import (  # noqa: E402
    ApplicationRecord,
    SavedSearchStoredRequest,
    StrictApplicationRecord,
)
from jobagg.classification import classify_database  # noqa: E402
from jobagg.db import JobDatabase  # noqa: E402
from jobagg.filters.saved_searches import save_search  # noqa: E402
from jobagg.filters.schemas import VacancySearchRequest  # noqa: E402
from jobagg.models import OrganizationSource  # noqa: E402
from jobagg.normalize import build_job  # noqa: E402


def _source() -> OrganizationSource:
    return OrganizationSource(
        id="un_inspira",
        name="UN Inspira",
        ats_family="inspira",
        base_url="https://careers.un.org",
    )


def _settings(tmp_path: Path) -> ApiSettings:
    return ApiSettings(
        repo_root=ROOT,
        db_path=tmp_path / "all_jobs.sqlite3",
        saved_searches_path=tmp_path / "saved_searches.json",
        tracker_path=tmp_path / "tracker.json",
    )


def _client(tmp_path: Path) -> TestClient:
    settings = _settings(tmp_path)
    db = JobDatabase(settings.db_path)
    db.initialize()
    db.upsert_job(
        build_job(
            _source(),
            title="Programme Management Officer, P-3",
            external_id="123",
            location="Nairobi",
            apply_url="https://careers.un.org/jobSearchDescription/123?language=en",
            raw={"jl": {"name": "P-3"}, "dutyStation": [{"description": "Nairobi"}]},
        )
    )
    classify_database(db, force=True)
    return TestClient(create_app(settings))


def test_job_api_health_search_detail_saved_search_and_tracker(tmp_path: Path) -> None:
    client = _client(tmp_path)

    health = client.get("/api/health")
    assert health.status_code == 200
    assert health.json()["status"] == "ok"

    search = client.post("/api/search", json={"text": "Programme", "limit": 10})
    assert search.status_code == 200
    payload = search.json()
    assert payload["total"] == 1
    job_key = payload["results"][0]["job_key"]

    detail = client.get(f"/api/jobs/{job_key}")
    assert detail.status_code == 200
    assert detail.json()["title"] == "Programme Management Officer, P-3"
    assert "deadline_info" in detail.json()

    saved = client.post(
        "/api/saved-searches",
        json={
            "name": "programme",
            "summary": "Programme roles",
            "request": {"text": "Programme"},
        },
    )
    assert saved.status_code == 200
    assert client.post("/api/saved-searches/programme/run").json()["total"] == 1
    assert client.delete("/api/saved-searches/programme").json()["deleted"] is True

    tracker = client.post(f"/api/tracker/jobs/{job_key}")
    assert tracker.status_code == 200
    tracker_payload = tracker.json()
    assert tracker_payload["job_key"] == job_key
    assert tracker_payload["status"] == "saved"
    tracker_payload["status"] = "applied"
    applied = client.post("/api/tracker", json=tracker_payload)
    assert applied.status_code == 200
    assert applied.json()["status"] == "applied"
    saved_again = client.post(f"/api/tracker/jobs/{job_key}")
    assert saved_again.status_code == 200
    assert saved_again.json()["status"] == "applied"
    assert len(client.get("/api/tracker").json()) == 1


def _saved_search_snapshot(
    client: TestClient, *, name: str = "programme"
) -> dict[str, object]:
    response = client.post(
        "/api/saved-searches",
        json={
            "name": name,
            "summary": "PRIVATE_DESCRIPTION_SENTINEL",
            "request": {"text": "PRIVATE_QUERY_SENTINEL"},
        },
    )
    assert response.status_code == 200
    return response.json()


def _tracker_snapshot(
    client: TestClient,
    *,
    record_id: str = "record-1",
    notes: str = "PRIVATE_NOTES_SENTINEL",
) -> dict[str, object]:
    response = client.post(
        "/api/tracker",
        json={
            "id": record_id,
            "job_key": "PRIVATE_JOB_KEY_SENTINEL",
            "status": "saved",
            "notes": notes,
        },
    )
    assert response.status_code == 200
    return response.json()


def test_conditional_snapshot_models_require_exact_stored_fields() -> None:
    saved_request_fields = {field.name for field in fields(VacancySearchRequest)}

    assert set(SavedSearchStoredRequest.model_fields) == saved_request_fields
    assert all(
        field.is_required() for field in SavedSearchStoredRequest.model_fields.values()
    )
    assert set(StrictApplicationRecord.model_fields) == set(
        ApplicationRecord.model_fields
    )
    assert all(
        field.is_required() for field in StrictApplicationRecord.model_fields.values()
    )


def test_saved_search_conditional_delete_exact_absent_and_legacy(
    tmp_path: Path,
) -> None:
    client = _client(tmp_path)
    expected = _saved_search_snapshot(client)

    deleted = client.post(
        "/api/saved-searches/programme/conditional-delete",
        json={"expected": expected},
    )
    absent = client.post(
        "/api/saved-searches/programme/conditional-delete",
        json={"expected": expected},
    )

    assert deleted.status_code == 200
    assert deleted.json() == {"outcome": "deleted"}
    assert absent.status_code == 200
    assert absent.json() == {"outcome": "absent"}

    _saved_search_snapshot(client)
    assert client.delete("/api/saved-searches/programme").json() == {"deleted": True}


def test_saved_search_conditional_delete_preserves_integer_confidence(
    tmp_path: Path,
) -> None:
    client = _client(tmp_path)
    save_search(
        _settings(tmp_path).saved_searches_path,
        name="programme",
        request=VacancySearchRequest(
            text="PRIVATE_QUERY_SENTINEL",
            min_location_confidence=1,
        ),
        description="PRIVATE_DESCRIPTION_SENTINEL",
    )
    expected = client.get("/api/saved-searches").json()[0]

    assert type(expected["request"]["min_location_confidence"]) is int
    response = client.post(
        "/api/saved-searches/programme/conditional-delete",
        json={"expected": expected},
    )

    assert response.status_code == 200
    assert response.json() == {"outcome": "deleted"}


def test_saved_search_conditional_delete_handles_large_integer_confidence(
    tmp_path: Path,
) -> None:
    client = _client(tmp_path)
    large_confidence = 10**400
    save_search(
        _settings(tmp_path).saved_searches_path,
        name="programme",
        request=VacancySearchRequest(
            text="PRIVATE_QUERY_SENTINEL",
            min_location_confidence=large_confidence,
        ),
        description="PRIVATE_DESCRIPTION_SENTINEL",
    )
    expected = client.get("/api/saved-searches").json()[0]

    assert expected["request"]["min_location_confidence"] == large_confidence
    response = client.post(
        "/api/saved-searches/programme/conditional-delete",
        json={"expected": expected},
    )

    assert response.status_code == 200
    assert response.json() == {"outcome": "deleted"}


@pytest.mark.parametrize(
    "field,value",
    [
        ("min_location_confidence", "NaN"),
        ("min_grade_confidence", "Infinity"),
        ("min_location_confidence", True),
        ("min_grade_confidence", "1"),
    ],
    ids=["nan", "infinity", "boolean", "numeric-string"],
)
def test_saved_search_conditional_delete_rejects_non_strict_confidence(
    tmp_path: Path,
    field: str,
    value: object,
) -> None:
    client = _client(tmp_path)
    current = _saved_search_snapshot(client)
    invalid = deepcopy(current)
    invalid["request"][field] = value

    response = client.post(
        "/api/saved-searches/programme/conditional-delete",
        json={"expected": invalid},
    )

    assert response.status_code == 422
    assert response.json() == {"detail": "Conditional delete request is invalid."}
    assert "PRIVATE_QUERY_SENTINEL" not in response.text
    assert client.get("/api/saved-searches").json() == [current]


@pytest.mark.parametrize(
    "field,value",
    [
        ("limit", "50"),
        ("include_low_confidence", "false"),
        ("offset", False),
    ],
    ids=["integer-string", "boolean-string", "boolean-integer-alias"],
)
def test_saved_search_conditional_delete_requires_strict_snapshot_scalars(
    tmp_path: Path,
    field: str,
    value: object,
) -> None:
    client = _client(tmp_path)
    current = _saved_search_snapshot(client)
    invalid = deepcopy(current)
    invalid["request"][field] = value

    response = client.post(
        "/api/saved-searches/programme/conditional-delete",
        json={"expected": invalid},
    )

    assert response.status_code == 422
    assert response.json() == {"detail": "Conditional delete request is invalid."}
    assert client.get("/api/saved-searches").json() == [current]


def test_saved_search_conditional_delete_rejects_identity_and_extra_fields(
    tmp_path: Path,
) -> None:
    client = _client(tmp_path)
    expected = _saved_search_snapshot(client)

    identity = client.post(
        "/api/saved-searches/different/conditional-delete",
        json={"expected": expected},
    )
    extra = client.post(
        "/api/saved-searches/programme/conditional-delete",
        json={"expected": expected, "unexpected": True},
    )

    assert identity.status_code == 400
    assert identity.json() == {"detail": "Conditional delete identity mismatch."}
    assert extra.status_code == 422
    assert extra.json() == {"detail": "Conditional delete request is invalid."}


@pytest.mark.parametrize(
    "field,value",
    [
        ("description", "changed-description"),
        ("request.text", "changed-query"),
        ("created_at", "2099-01-01T00:00:00+00:00"),
        ("updated_at", "2099-01-01T00:00:00+00:00"),
    ],
)
def test_saved_search_conditional_delete_mismatch_is_private_free(
    tmp_path: Path,
    field: str,
    value: object,
) -> None:
    client = _client(tmp_path)
    current = _saved_search_snapshot(client)
    expected = deepcopy(current)
    if field == "request.text":
        expected["request"]["text"] = value
    else:
        expected[field] = value

    response = client.post(
        "/api/saved-searches/programme/conditional-delete",
        json={"expected": expected},
    )

    assert response.status_code == 412
    assert response.json() == {"detail": "Conditional delete precondition failed."}
    assert "PRIVATE_DESCRIPTION_SENTINEL" not in response.text
    assert "PRIVATE_QUERY_SENTINEL" not in response.text
    assert client.get("/api/saved-searches").json() == [current]


def test_saved_search_changed_after_review_survives_conditional_delete(
    tmp_path: Path,
) -> None:
    client = _client(tmp_path)
    reviewed = _saved_search_snapshot(client)
    changed = client.post(
        "/api/saved-searches",
        json={
            "name": "programme",
            "summary": "changed-after-review",
            "request": {"text": "changed-after-review"},
        },
    ).json()

    response = client.post(
        "/api/saved-searches/programme/conditional-delete",
        json={"expected": reviewed},
    )

    assert response.status_code == 412
    assert client.get("/api/saved-searches").json() == [changed]


def test_saved_search_invalid_request_error_does_not_echo_private_input(
    tmp_path: Path,
) -> None:
    client = _client(tmp_path)
    expected = _saved_search_snapshot(client)
    expected["unexpected"] = "PRIVATE_VALIDATION_SENTINEL"

    response = client.post(
        "/api/saved-searches/programme/conditional-delete",
        json={"expected": expected},
    )

    assert response.status_code == 422
    assert response.json() == {"detail": "Conditional delete request is invalid."}
    assert "PRIVATE_VALIDATION_SENTINEL" not in response.text

    wrong_shape = client.post(
        "/api/saved-searches/programme/conditional-delete",
        json=["PRIVATE_SHAPE_SENTINEL"],
    )
    assert wrong_shape.status_code == 422
    assert wrong_shape.json() == {"detail": "Conditional delete request is invalid."}
    assert "PRIVATE_SHAPE_SENTINEL" not in wrong_shape.text


@pytest.mark.parametrize(
    "mutate_request",
    [
        lambda request: request.pop("sort"),
        lambda request: request.update({"include_facets": True}),
    ],
    ids=["missing-stored-field", "nonstored-api-field"],
)
def test_saved_search_conditional_delete_requires_exact_stored_request_shape(
    tmp_path: Path,
    mutate_request: Any,
) -> None:
    client = _client(tmp_path)
    current = _saved_search_snapshot(client)
    incomplete = deepcopy(current)
    mutate_request(incomplete["request"])

    response = client.post(
        "/api/saved-searches/programme/conditional-delete",
        json={"expected": incomplete},
    )

    assert response.status_code == 422
    assert response.json() == {"detail": "Conditional delete request is invalid."}
    assert "PRIVATE_QUERY_SENTINEL" not in response.text
    assert client.get("/api/saved-searches").json() == [current]


def test_tracker_conditional_delete_exact_absent_and_legacy(tmp_path: Path) -> None:
    client = _client(tmp_path)
    expected = _tracker_snapshot(client)

    deleted = client.post(
        "/api/tracker/record-1/conditional-delete",
        json={"expected": expected},
    )
    absent = client.post(
        "/api/tracker/record-1/conditional-delete",
        json={"expected": expected},
    )

    assert deleted.status_code == 200
    assert deleted.json() == {"outcome": "deleted"}
    assert absent.status_code == 200
    assert absent.json() == {"outcome": "absent"}

    _tracker_snapshot(client)
    assert client.delete("/api/tracker/record-1").json() == {"deleted": True}


def test_tracker_conditional_delete_rejects_normalized_timestamp_text(
    tmp_path: Path,
) -> None:
    client = _client(tmp_path)
    current = _tracker_snapshot(client)
    invalid = deepcopy(current)
    invalid["updated_at"] = current["updated_at"].replace("T", " ", 1)

    response = client.post(
        "/api/tracker/record-1/conditional-delete",
        json={"expected": invalid},
    )

    assert response.status_code == 422
    assert response.json() == {"detail": "Conditional delete request is invalid."}
    assert client.get("/api/tracker").json() == [current]


def test_tracker_conditional_delete_rejects_numeric_timestamp_alias(
    tmp_path: Path,
) -> None:
    client = _client(tmp_path)
    current = client.post(
        "/api/tracker",
        json={
            "id": "record-1",
            "job_key": "PRIVATE_JOB_KEY_SENTINEL",
            "status": "saved",
            "notes": "PRIVATE_NOTES_SENTINEL",
            "applied_at": "1970-01-01T00:00:00Z",
        },
    ).json()
    invalid = deepcopy(current)
    invalid["applied_at"] = 0

    response = client.post(
        "/api/tracker/record-1/conditional-delete",
        json={"expected": invalid},
    )

    assert response.status_code == 422
    assert response.json() == {"detail": "Conditional delete request is invalid."}
    assert client.get("/api/tracker").json() == [current]


def test_tracker_conditional_delete_rejects_identity_and_extra_fields(
    tmp_path: Path,
) -> None:
    client = _client(tmp_path)
    expected = _tracker_snapshot(client)

    identity = client.post(
        "/api/tracker/different/conditional-delete",
        json={"expected": expected},
    )
    extra = client.post(
        "/api/tracker/record-1/conditional-delete",
        json={"expected": expected, "unexpected": True},
    )

    assert identity.status_code == 400
    assert identity.json() == {"detail": "Conditional delete identity mismatch."}
    assert extra.status_code == 422
    assert extra.json() == {"detail": "Conditional delete request is invalid."}


@pytest.mark.parametrize(
    "field,value",
    [
        ("job_key", "changed-job-key"),
        ("status", "applied"),
        ("notes", "changed-notes"),
        ("applied_at", "2099-01-01T00:00:00Z"),
        ("updated_at", "2099-01-01T00:00:00Z"),
    ],
)
def test_tracker_conditional_delete_mismatch_is_private_free(
    tmp_path: Path,
    field: str,
    value: object,
) -> None:
    client = _client(tmp_path)
    current = _tracker_snapshot(client)
    expected = deepcopy(current)
    expected[field] = value

    response = client.post(
        "/api/tracker/record-1/conditional-delete",
        json={"expected": expected},
    )

    assert response.status_code == 412
    assert response.json() == {"detail": "Conditional delete precondition failed."}
    assert "PRIVATE_JOB_KEY_SENTINEL" not in response.text
    assert "PRIVATE_NOTES_SENTINEL" not in response.text
    assert client.get("/api/tracker").json() == [current]


def test_tracker_changed_after_review_survives_conditional_delete(
    tmp_path: Path,
) -> None:
    client = _client(tmp_path)
    reviewed = _tracker_snapshot(client)
    changed_request = deepcopy(reviewed)
    changed_request["notes"] = "changed-after-review"
    changed = client.post("/api/tracker", json=changed_request).json()

    response = client.post(
        "/api/tracker/record-1/conditional-delete",
        json={"expected": reviewed},
    )

    assert response.status_code == 412
    assert client.get("/api/tracker").json() == [changed]


def test_tracker_invalid_request_error_does_not_echo_private_input(
    tmp_path: Path,
) -> None:
    client = _client(tmp_path)
    expected = _tracker_snapshot(client)
    expected["unexpected"] = "PRIVATE_VALIDATION_SENTINEL"

    response = client.post(
        "/api/tracker/record-1/conditional-delete",
        json={"expected": expected},
    )

    assert response.status_code == 422
    assert response.json() == {"detail": "Conditional delete request is invalid."}
    assert "PRIVATE_VALIDATION_SENTINEL" not in response.text

    wrong_shape = client.post(
        "/api/tracker/record-1/conditional-delete",
        json=["PRIVATE_SHAPE_SENTINEL"],
    )
    assert wrong_shape.status_code == 422
    assert wrong_shape.json() == {"detail": "Conditional delete request is invalid."}
    assert "PRIVATE_SHAPE_SENTINEL" not in wrong_shape.text


@pytest.mark.parametrize(
    "missing_field",
    ["status", "notes", "applied_at", "updated_at"],
)
def test_tracker_conditional_delete_requires_complete_stored_snapshot(
    tmp_path: Path,
    missing_field: str,
) -> None:
    client = _client(tmp_path)
    current = _tracker_snapshot(client, notes="")
    incomplete = deepcopy(current)
    incomplete.pop(missing_field)

    response = client.post(
        "/api/tracker/record-1/conditional-delete",
        json={"expected": incomplete},
    )

    assert response.status_code == 422
    assert response.json() == {"detail": "Conditional delete request is invalid."}
    assert "PRIVATE_JOB_KEY_SENTINEL" not in response.text
    assert client.get("/api/tracker").json() == [current]


def test_tracker_upsert_and_conditional_delete_share_one_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "tracker.json"
    current = tracker_store.upsert_record(
        path,
        ApplicationRecord(id="record-1", job_key="job-1", notes="version-a"),
    ).model_copy(deep=True)
    atomic_store: Any = importlib.import_module("jobagg.atomic_json_store")
    update_loaded = threading.Event()
    delete_attempted = threading.Event()
    real_read = atomic_store.AtomicJsonStore._read_unlocked
    real_acquire = atomic_store._acquire_platform_lock

    def gated_read(store: object) -> object:
        data = real_read(store)
        if threading.current_thread().name == "tracker-update":
            update_loaded.set()
            assert delete_attempted.wait(5)
        return data

    def observed_acquire(lock_file: object) -> None:
        if threading.current_thread().name == "tracker-delete":
            delete_attempted.set()
        real_acquire(lock_file)

    monkeypatch.setattr(atomic_store.AtomicJsonStore, "_read_unlocked", gated_read)
    monkeypatch.setattr(atomic_store, "_acquire_platform_lock", observed_acquire)
    outcomes: list[str] = []

    update = threading.Thread(
        name="tracker-update",
        target=lambda: tracker_store.upsert_record(
            path,
            ApplicationRecord(
                id="record-1",
                job_key="job-1",
                status="applied",
                notes="version-b",
            ),
        ),
    )
    delete = threading.Thread(
        name="tracker-delete",
        target=lambda: outcomes.append(
            tracker_store.compare_and_delete_record(
                path,
                record_id=current.id,
                expected=current,
            )
        ),
    )
    update.start()
    assert update_loaded.wait(5)
    delete.start()
    update.join(5)
    delete.join(5)

    assert not update.is_alive()
    assert not delete.is_alive()
    assert outcomes == ["mismatch"]
    records = tracker_store.list_records(path)
    assert len(records) == 1
    assert records[0].notes == "version-b"
    json.loads(path.read_text(encoding="utf-8"))


def test_tracker_duplicate_ids_are_rejected_without_deletion(tmp_path: Path) -> None:
    path = tmp_path / "tracker.json"
    expected = tracker_store.upsert_record(
        path,
        ApplicationRecord(id="record-1", job_key="job-1", notes="reviewed"),
    ).model_copy(deep=True)
    document = json.loads(path.read_text(encoding="utf-8"))
    duplicate = deepcopy(document[0])
    duplicate["notes"] = "PRIVATE_CHANGED_DUPLICATE"
    document.append(duplicate)
    path.write_text(json.dumps(document), encoding="utf-8")
    changed_bytes = path.read_bytes()

    with pytest.raises(ValueError, match="invalid") as failure:
        tracker_store.compare_and_delete_record(
            path,
            record_id=expected.id,
            expected=expected,
        )

    assert "PRIVATE_CHANGED_DUPLICATE" not in str(failure.value)
    assert path.read_bytes() == changed_bytes


def test_tracker_unknown_content_is_rejected_without_deletion(tmp_path: Path) -> None:
    path = tmp_path / "tracker.json"
    expected = tracker_store.upsert_record(
        path,
        ApplicationRecord(id="record-1", job_key="job-1", notes="reviewed"),
    ).model_copy(deep=True)
    document = json.loads(path.read_text(encoding="utf-8"))
    document[0]["future_private_field"] = "PRIVATE_CHANGED_FIELD"
    path.write_text(json.dumps(document), encoding="utf-8")
    changed_bytes = path.read_bytes()

    with pytest.raises(ValueError, match="invalid") as failure:
        tracker_store.compare_and_delete_record(
            path,
            record_id=expected.id,
            expected=expected,
        )

    assert "PRIVATE_CHANGED_FIELD" not in str(failure.value)
    assert path.read_bytes() == changed_bytes


@pytest.mark.parametrize(
    "path",
    [
        "/api/saved-searches/programme/conditional-delete",
        "/api/tracker/record-1/conditional-delete",
    ],
)
def test_conditional_delete_parser_failures_are_fixed_and_private_free(
    tmp_path: Path,
    path: str,
) -> None:
    client = _client(tmp_path)

    missing = client.post(path)
    malformed = client.post(
        path,
        content=b'{"expected":"PRIVATE_PARSE_SENTINEL"',
        headers={"content-type": "application/json"},
    )

    assert missing.status_code == 422
    assert missing.json() == {"detail": "Conditional delete request is invalid."}
    assert malformed.status_code == 422
    assert malformed.json() == {"detail": "Conditional delete request is invalid."}
    assert "PRIVATE_PARSE_SENTINEL" not in malformed.text


@pytest.mark.parametrize(
    "path",
    [
        "/api/saved-searches/programme/conditional-delete",
        "/api/tracker/record-1/conditional-delete",
    ],
)
def test_conditional_delete_integer_limit_parser_failure_is_fixed_and_private_free(
    tmp_path: Path,
    path: str,
) -> None:
    client = _client(tmp_path)
    previous_limit = sys.get_int_max_str_digits()
    active_limit = previous_limit or 4300
    malformed = (
        b'{"expected":{"private":"PRIVATE_INTEGER_SENTINEL","value":'
        + (b"9" * (active_limit + 1))
        + b"}}"
    )
    if previous_limit == 0:
        sys.set_int_max_str_digits(active_limit)

    try:
        response = client.post(
            path,
            content=malformed,
            headers={"content-type": "application/json"},
        )
    finally:
        if previous_limit == 0:
            sys.set_int_max_str_digits(0)

    assert response.status_code == 422
    assert response.json() == {"detail": "Conditional delete request is invalid."}
    assert "PRIVATE_INTEGER_SENTINEL" not in response.text


def test_job_api_open_search_excludes_expired_open_rows(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    db = JobDatabase(settings.db_path)
    db.initialize()
    source = _source()
    db.upsert_job(
        build_job(
            source,
            title="Expired Programme Management Officer, P-3",
            external_id="expired",
            location="Nairobi",
            closes_at="2020-01-01T00:00:00+00:00",
            apply_url="https://careers.un.org/jobSearchDescription/expired?language=en",
            raw={"jl": {"name": "P-3"}, "dutyStation": [{"description": "Nairobi"}]},
        )
    )
    db.upsert_job(
        build_job(
            source,
            title="Current Programme Management Officer, P-3",
            external_id="current",
            location="Nairobi",
            closes_at="2099-01-01T00:00:00+00:00",
            apply_url="https://careers.un.org/jobSearchDescription/current?language=en",
            raw={"jl": {"name": "P-3"}, "dutyStation": [{"description": "Nairobi"}]},
        )
    )
    classify_database(db, force=True)
    client = TestClient(create_app(settings))

    payload = client.post("/api/search", json={"status": ["open"], "limit": 10}).json()

    assert payload["total"] == 1
    assert payload["results"][0]["job_key"] == "un_inspira:current"
