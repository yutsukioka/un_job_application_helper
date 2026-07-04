from __future__ import annotations

import json

import pytest

from vaultsync import (
    Argon2idParams,
    AtlasVaultExport,
    PlaintextRecord,
    UnsupportedExportVersion,
    VaultExportError,
    create_vault_metadata,
    decrypt_record_payload,
    deserialize_vault_export,
    encrypt_record_payload,
    read_atlasvault_export,
    serialize_vault_export,
    unwrap_vault_key,
    write_atlasvault_export,
)
from vaultsync.format import VaultKeyUnwrapError

PASSPHRASE = "PHASE2A_EXPORT_FAKE_PASSPHRASE_DO_NOT_LEAK"
WRONG_PASSPHRASE = "wrong fake export passphrase"
FIXED_VAULT_ID = "30000000-0000-4000-8000-000000000001"
FIXED_VAULT_KEY = bytes.fromhex("bb" * 32)
FIXED_SALT = bytes.fromhex("bc" * 16)
FIXED_WRAP_NONCE = bytes.fromhex("bd" * 12)
EXPORT_ID = "30000000-0000-4000-8000-000000000010"
CREATED_AT = "2026-01-01T00:00:00Z"
UPDATED_AT = "2026-01-02T00:00:00Z"
SENTINEL = "TOP_SECRET_SENTINEL_DO_NOT_LEAK"
FAKE_JOB_KEY = "FAKE_EXPORT_JOB_KEY_DO_NOT_LEAK_123"
FAKE_NOTE_BODY = "FAKE_EXPORT_NOTE_BODY_DO_NOT_LEAK"
FAKE_PROFILE_TEXT = "FAKE_EXPORT_PROFILE_TEXT_DO_NOT_LEAK"
FAKE_SEARCH_TEXT = "FAKE_EXPORT_SEARCH_TEXT_DO_NOT_LEAK"
FAKE_FILTER_VALUE = "FAKE_EXPORT_FILTER_VALUE_DO_NOT_LEAK"
FAKE_GENERATED_DOC_REF = "FAKE_EXPORT_GENERATED_DOC_REF_DO_NOT_LEAK"
FAKE_STATUS = "FAKE_EXPORT_STATUS_DO_NOT_LEAK"
RECORD_TYPES = (
    "saved_search",
    "saved_job",
    "application_note",
    "profile_snippet",
    "draft_metadata",
)
FORBIDDEN_PLAINTEXT = (
    SENTINEL,
    PASSPHRASE,
    *RECORD_TYPES,
    FAKE_JOB_KEY,
    FAKE_NOTE_BODY,
    FAKE_PROFILE_TEXT,
    FAKE_SEARCH_TEXT,
    FAKE_FILTER_VALUE,
    FAKE_GENERATED_DOC_REF,
    FAKE_STATUS,
)


def fast_argon2_params() -> Argon2idParams:
    return Argon2idParams(
        salt=FIXED_SALT,
        memory_kib=1024,
        iterations=2,
        parallelism=1,
    )


def metadata():
    return create_vault_metadata(
        FIXED_VAULT_KEY,
        PASSPHRASE,
        vault_id=FIXED_VAULT_ID,
        params=fast_argon2_params(),
        nonce=FIXED_WRAP_NONCE,
    )


def payload(record_type: str) -> dict[str, object]:
    payloads: dict[str, dict[str, object]] = {
        "saved_search": {
            "name": SENTINEL,
            "summary": "fake export saved search summary",
            "request": {
                "text": FAKE_SEARCH_TEXT,
                "organizations": [FAKE_FILTER_VALUE],
            },
        },
        "saved_job": {
            "job_key": FAKE_JOB_KEY,
            "status": FAKE_STATUS,
            "notes": FAKE_NOTE_BODY,
        },
        "application_note": {
            "job_key": FAKE_JOB_KEY,
            "body": FAKE_NOTE_BODY,
            "kind": "general",
        },
        "profile_snippet": {
            "title": "fake export snippet",
            "body": FAKE_PROFILE_TEXT,
        },
        "draft_metadata": {
            "job_key": FAKE_JOB_KEY,
            "document_ref": FAKE_GENERATED_DOC_REF,
            "status": "draft",
        },
    }
    return payloads[record_type]


def plaintext(record_type: str) -> PlaintextRecord:
    return PlaintextRecord(
        type=record_type,
        payload_schema=1,
        payload=payload(record_type),
        client_created_at=CREATED_AT,
        client_updated_at=UPDATED_AT,
    )


def encrypted_records():
    vault_metadata = metadata()
    records = []
    for index, record_type in enumerate(RECORD_TYPES, start=1):
        records.append(
            encrypt_record_payload(
                FIXED_VAULT_KEY,
                vault_metadata,
                plaintext(record_type),
                record_id=f"30000000-0000-4000-8000-00000000010{index}",
                revision=f"30000000-0000-4000-8000-00000000020{index}",
                nonce=bytes([index + 10]) * 12,
            )
        )
    return tuple(records)


def tombstone_record():
    return encrypt_record_payload(
        FIXED_VAULT_KEY,
        metadata(),
        plaintext("saved_job"),
        record_id="30000000-0000-4000-8000-000000000199",
        revision="30000000-0000-4000-8000-000000000299",
        parent_revision="30000000-0000-4000-8000-000000000198",
        deleted=True,
        nonce=bytes([109]) * 12,
    )


def vault_export(records=None) -> AtlasVaultExport:
    return AtlasVaultExport.new(
        metadata(),
        encrypted_records() if records is None else records,
        export_id=EXPORT_ID,
        created_at=CREATED_AT,
    )


def assert_no_private_plaintext(serialized: str) -> None:
    for private_value in FORBIDDEN_PLAINTEXT:
        assert private_value not in serialized


def test_atlasvault_export_serializes_and_deserializes_successfully() -> None:
    original = vault_export()

    restored = deserialize_vault_export(serialize_vault_export(original))

    assert restored == original


def test_atlasvault_export_writes_and_reads_from_tmp_path(tmp_path) -> None:
    original = vault_export()
    path = tmp_path / "fake-export.atlasvault"

    write_atlasvault_export(original, path)

    assert read_atlasvault_export(path) == original


def test_export_decrypt_round_trip_succeeds_for_all_phase2_record_types() -> None:
    vault_metadata = metadata()
    restored = deserialize_vault_export(serialize_vault_export(vault_export()))

    decrypted = [
        decrypt_record_payload(FIXED_VAULT_KEY, vault_metadata, record)
        for record in restored.records
    ]

    assert [record.type for record in decrypted] == list(RECORD_TYPES)
    for record in decrypted:
        assert record.payload == payload(record.type)


def test_export_wrong_passphrase_fails_without_plaintext_output() -> None:
    export = vault_export()
    serialized = serialize_vault_export(export)

    assert_no_private_plaintext(serialized)
    with pytest.raises(VaultKeyUnwrapError):
        unwrap_vault_key(export.vault_metadata.key_wraps[0], WRONG_PASSPHRASE)


def test_corrupt_atlasvault_export_fails_safely(tmp_path) -> None:
    path = tmp_path / "corrupt-export.atlasvault"
    path.write_bytes(b'{"format":"atlasvault-export",')

    with pytest.raises(VaultExportError):
        read_atlasvault_export(path)


def test_unsupported_export_envelope_version_fails_safely() -> None:
    payload_data = json.loads(serialize_vault_export(vault_export()))
    payload_data["version"] = 2

    with pytest.raises(UnsupportedExportVersion):
        deserialize_vault_export(json.dumps(payload_data))


def test_atlasvault_export_json_does_not_contain_private_plaintext() -> None:
    serialized = serialize_vault_export(vault_export())

    assert_no_private_plaintext(serialized)


def test_export_preserves_tombstone_envelope_without_decrypting_payload() -> None:
    tombstone = tombstone_record()
    export = vault_export(records=(tombstone,))

    restored = deserialize_vault_export(serialize_vault_export(export))

    assert restored.records == (tombstone,)
    assert restored.records[0].deleted is True
    assert restored.records[0].parent_revision == tombstone.parent_revision


def test_export_rejects_malformed_record_envelope() -> None:
    export = vault_export()
    payload_data = json.loads(serialize_vault_export(export))
    payload_data["records"][0]["ciphertext"] = "not-valid-base64"

    with pytest.raises(VaultExportError):
        deserialize_vault_export(json.dumps(payload_data))
