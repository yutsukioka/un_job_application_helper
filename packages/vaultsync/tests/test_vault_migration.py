from __future__ import annotations

import json

import pytest

from vaultsync import (
    Argon2idParams,
    AtlasVaultExport,
    LocalVaultStore,
    MigrationSafetyError,
    MigrationSourceError,
    VaultMetadata,
    build_encrypted_records_from_sources,
    create_vault_metadata,
    decrypt_record_payload,
    dry_run_sources,
    load_saved_searches_source,
    load_tracker_source,
    serialize_local_store,
    serialize_vault_export,
    write_staged_export,
    write_staged_local_store,
)

PASSPHRASE = "PHASE2B_FAKE_PASSPHRASE_DO_NOT_LEAK"
FIXED_VAULT_ID = "40000000-0000-4000-8000-000000000001"
FIXED_VAULT_KEY = bytes.fromhex("cc" * 32)
FIXED_SALT = bytes.fromhex("cd" * 16)
FIXED_WRAP_NONCE = bytes.fromhex("ce" * 12)
SENTINEL = "TOP_SECRET_SENTINEL_DO_NOT_LEAK"
FAKE_SEARCH_NAME = "FAKE_PRIVATE_SEARCH_NAME_DO_NOT_LEAK"
FAKE_SEARCH_TEXT = "FAKE_PRIVATE_SEARCH_TEXT_DO_NOT_LEAK"
FAKE_FILTER_VALUE = "FAKE_PRIVATE_FILTER_DO_NOT_LEAK"
FAKE_JOB_KEY = "FAKE_JOB_KEY_DO_NOT_LEAK"
FAKE_STATUS = "FAKE_STATUS_DO_NOT_LEAK"
FAKE_NOTES = "FAKE_PRIVATE_NOTE_DO_NOT_LEAK"
FAKE_GENERATED_DOC_REFERENCE = "FAKE_GENERATED_DOC_REFERENCE_DO_NOT_LEAK"
CREATED_AT = "2026-01-01T00:00:00Z"
UPDATED_AT = "2026-01-02T00:00:00Z"
PRIVATE_PAYLOAD_VALUES = (
    PASSPHRASE,
    SENTINEL,
    FAKE_SEARCH_NAME,
    FAKE_SEARCH_TEXT,
    FAKE_FILTER_VALUE,
    FAKE_JOB_KEY,
    FAKE_STATUS,
    FAKE_NOTES,
    FAKE_GENERATED_DOC_REFERENCE,
)
FORBIDDEN_OUTPUT_VALUES = (
    *PRIVATE_PAYLOAD_VALUES,
    "saved_search",
    "saved_job",
)


def fast_argon2_params() -> Argon2idParams:
    return Argon2idParams(
        salt=FIXED_SALT,
        memory_kib=1024,
        iterations=2,
        parallelism=1,
    )


def metadata() -> VaultMetadata:
    return create_vault_metadata(
        FIXED_VAULT_KEY,
        PASSPHRASE,
        vault_id=FIXED_VAULT_ID,
        params=fast_argon2_params(),
        nonce=FIXED_WRAP_NONCE,
    )


def fake_saved_searches_source() -> dict[str, object]:
    return {
        "version": 1,
        "saved_searches": {
            FAKE_SEARCH_NAME: {
                "description": SENTINEL,
                "request": {
                    "text": FAKE_SEARCH_TEXT,
                    "organizations": [FAKE_FILTER_VALUE],
                    "generated_document_ref": FAKE_GENERATED_DOC_REFERENCE,
                },
                "created_at": CREATED_AT,
                "updated_at": UPDATED_AT,
            }
        },
    }


def fake_tracker_source() -> list[dict[str, object]]:
    return [
        {
            "id": "fake-tracker-record-id",
            "job_key": FAKE_JOB_KEY,
            "status": FAKE_STATUS,
            "notes": FAKE_NOTES,
            "applied_at": CREATED_AT,
            "updated_at": UPDATED_AT,
        }
    ]


def encrypted_records():
    return build_encrypted_records_from_sources(
        FIXED_VAULT_KEY,
        metadata(),
        saved_searches_source=fake_saved_searches_source(),
        tracker_source=fake_tracker_source(),
    )


def assert_no_private_payload_values(serialized: str) -> None:
    for private_value in PRIVATE_PAYLOAD_VALUES:
        assert private_value not in serialized


def assert_no_private_output_values(serialized: str) -> None:
    for private_value in FORBIDDEN_OUTPUT_VALUES:
        assert private_value not in serialized


def test_dry_run_counts_fake_saved_searches_and_tracker_records() -> None:
    report = dry_run_sources(
        saved_searches_source=fake_saved_searches_source(),
        tracker_source=fake_tracker_source(),
    )

    assert report.saved_search_count == 1
    assert report.saved_job_count == 1
    assert report.total_records == 2
    assert report.skipped_saved_searches == 0
    assert report.skipped_saved_jobs == 0


def test_dry_run_report_does_not_contain_private_payload_values() -> None:
    report = dry_run_sources(
        saved_searches_source=fake_saved_searches_source(),
        tracker_source=fake_tracker_source(),
    )

    serialized = json.dumps(report.to_dict(), sort_keys=True)

    assert_no_private_payload_values(serialized)


def test_saved_search_migration_creates_encrypted_record_with_expected_payload() -> None:
    records = build_encrypted_records_from_sources(
        FIXED_VAULT_KEY,
        metadata(),
        saved_searches_source=fake_saved_searches_source(),
    )

    assert len(records) == 1
    decrypted = decrypt_record_payload(FIXED_VAULT_KEY, metadata(), records[0])

    assert decrypted.type == "saved_search"
    assert decrypted.payload["name"] == FAKE_SEARCH_NAME
    assert decrypted.payload["summary"] == SENTINEL
    assert decrypted.payload["description"] == SENTINEL
    assert decrypted.payload["request"]["text"] == FAKE_SEARCH_TEXT
    assert decrypted.payload["request"]["organizations"] == [FAKE_FILTER_VALUE]


def test_saved_job_migration_creates_encrypted_record_with_expected_payload() -> None:
    records = build_encrypted_records_from_sources(
        FIXED_VAULT_KEY,
        metadata(),
        tracker_source={"records": fake_tracker_source()},
    )

    assert len(records) == 1
    decrypted = decrypt_record_payload(FIXED_VAULT_KEY, metadata(), records[0])

    assert decrypted.type == "saved_job"
    assert decrypted.payload["job_key"] == FAKE_JOB_KEY
    assert decrypted.payload["status"] == FAKE_STATUS
    assert decrypted.payload["notes"] == FAKE_NOTES
    assert decrypted.payload["applied_at"] == CREATED_AT
    assert decrypted.payload["updated_at"] == UPDATED_AT


def test_serialized_encrypted_migration_outputs_do_not_contain_private_values() -> None:
    records = encrypted_records()
    vault_metadata = metadata()
    local_store = LocalVaultStore.new(vault_metadata, records)
    export = AtlasVaultExport.new(vault_metadata, records)

    assert_no_private_output_values(serialize_local_store(local_store))
    assert_no_private_output_values(serialize_vault_export(export))


def test_migration_uses_random_record_ids_not_saved_search_names_or_job_keys() -> None:
    records = encrypted_records()

    assert len(records) == 2
    for record in records:
        assert record.id not in {FAKE_SEARCH_NAME, FAKE_JOB_KEY}


def test_migration_dry_run_does_not_modify_source_files(tmp_path) -> None:
    saved_path = tmp_path / "saved_searches.json"
    tracker_path = tmp_path / "tracker.json"
    saved_path.write_text(json.dumps(fake_saved_searches_source()), encoding="utf-8")
    tracker_path.write_text(json.dumps(fake_tracker_source()), encoding="utf-8")
    original_saved = saved_path.read_text(encoding="utf-8")
    original_tracker = tracker_path.read_text(encoding="utf-8")

    report = dry_run_sources(
        saved_searches_source=load_saved_searches_source(saved_path),
        tracker_source=load_tracker_source(tracker_path),
    )

    assert report.total_records == 2
    assert saved_path.read_text(encoding="utf-8") == original_saved
    assert tracker_path.read_text(encoding="utf-8") == original_tracker


def test_staged_local_store_write_does_not_modify_source_files(tmp_path) -> None:
    saved_path, tracker_path, original_saved, original_tracker = _write_fake_sources(tmp_path)
    records = build_encrypted_records_from_sources(
        FIXED_VAULT_KEY,
        metadata(),
        saved_searches_source=load_saved_searches_source(saved_path),
        tracker_source=load_tracker_source(tracker_path),
    )

    write_staged_local_store(tmp_path / "staged-local-vault.json", metadata(), records)

    assert saved_path.read_text(encoding="utf-8") == original_saved
    assert tracker_path.read_text(encoding="utf-8") == original_tracker


def test_staged_atlasvault_export_write_does_not_modify_source_files(tmp_path) -> None:
    saved_path, tracker_path, original_saved, original_tracker = _write_fake_sources(tmp_path)
    records = build_encrypted_records_from_sources(
        FIXED_VAULT_KEY,
        metadata(),
        saved_searches_source=load_saved_searches_source(saved_path),
        tracker_source=load_tracker_source(tracker_path),
    )

    write_staged_export(tmp_path / "staged-export.atlasvault", metadata(), records)

    assert saved_path.read_text(encoding="utf-8") == original_saved
    assert tracker_path.read_text(encoding="utf-8") == original_tracker


def test_existing_plaintext_source_files_are_left_untouched_after_errors(tmp_path) -> None:
    saved_path = tmp_path / "bad-saved-searches.json"
    saved_path.write_text("{not valid json", encoding="utf-8")
    original_saved = saved_path.read_text(encoding="utf-8")

    with pytest.raises(MigrationSourceError):
        load_saved_searches_source(saved_path)

    assert saved_path.read_text(encoding="utf-8") == original_saved


def test_malformed_saved_search_source_reports_non_sensitive_warning() -> None:
    report = dry_run_sources(saved_searches_source=[{"name": FAKE_SEARCH_NAME}])

    serialized = json.dumps(report.to_dict(), sort_keys=True)

    assert report.saved_search_count == 0
    assert report.skipped_saved_searches == 1
    assert "saved_search.skipped" in report.warnings
    assert_no_private_payload_values(serialized)


def test_malformed_tracker_source_reports_non_sensitive_warning() -> None:
    report = dry_run_sources(tracker_source=[{"notes": FAKE_NOTES}])

    serialized = json.dumps(report.to_dict(), sort_keys=True)

    assert report.saved_job_count == 0
    assert report.skipped_saved_jobs == 1
    assert "saved_job.skipped" in report.warnings
    assert_no_private_payload_values(serialized)


def test_unsupported_source_shape_fails_safely() -> None:
    with pytest.raises(MigrationSourceError):
        dry_run_sources(saved_searches_source="not a supported source")

    with pytest.raises(MigrationSourceError):
        dry_run_sources(tracker_source="not a supported source")


def test_output_overwrite_is_refused_by_default(tmp_path) -> None:
    output_path = tmp_path / "existing-local-vault.json"
    output_path.write_text("existing", encoding="utf-8")

    with pytest.raises(MigrationSafetyError):
        write_staged_local_store(output_path, metadata(), encrypted_records())

    assert output_path.read_text(encoding="utf-8") == "existing"


def test_output_overwrite_succeeds_only_with_explicit_overwrite(tmp_path) -> None:
    local_path = tmp_path / "existing-local-vault.json"
    export_path = tmp_path / "existing-export.atlasvault"
    local_path.write_text("existing", encoding="utf-8")
    export_path.write_text("existing", encoding="utf-8")
    records = encrypted_records()

    with pytest.raises(MigrationSafetyError):
        write_staged_export(export_path, metadata(), records)

    local_store = write_staged_local_store(local_path, metadata(), records, overwrite=True)
    export = write_staged_export(export_path, metadata(), records, overwrite=True)

    assert isinstance(local_store, LocalVaultStore)
    assert isinstance(export, AtlasVaultExport)
    assert_no_private_output_values(local_path.read_text(encoding="utf-8"))
    assert_no_private_output_values(export_path.read_text(encoding="utf-8"))


def test_supported_source_shapes_are_counted() -> None:
    list_report = dry_run_sources(
        saved_searches_source=[
            {
                "name": FAKE_SEARCH_NAME,
                "request": {"text": FAKE_SEARCH_TEXT},
            }
        ],
        tracker_source={
            "fake-record-id": {
                "job_key": FAKE_JOB_KEY,
            }
        },
    )
    applications_report = dry_run_sources(
        tracker_source={
            "applications": [
                {
                    "job_key": FAKE_JOB_KEY,
                }
            ]
        }
    )

    assert list_report.saved_search_count == 1
    assert list_report.saved_job_count == 1
    assert applications_report.saved_job_count == 1


def _write_fake_sources(tmp_path):
    saved_path = tmp_path / "saved_searches.json"
    tracker_path = tmp_path / "tracker.json"
    saved_path.write_text(json.dumps(fake_saved_searches_source()), encoding="utf-8")
    tracker_path.write_text(json.dumps(fake_tracker_source()), encoding="utf-8")
    return (
        saved_path,
        tracker_path,
        saved_path.read_text(encoding="utf-8"),
        tracker_path.read_text(encoding="utf-8"),
    )
