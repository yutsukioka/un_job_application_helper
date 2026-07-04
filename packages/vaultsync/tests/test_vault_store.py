from __future__ import annotations

import json
from dataclasses import replace

import pytest

from vaultsync import (
    Argon2idParams,
    LocalVaultStore,
    PlaintextRecord,
    RecordFormatError,
    UnsupportedStoreVersion,
    VaultKeyUnwrapError,
    VaultStoreError,
    create_vault_metadata,
    decrypt_record_payload,
    deserialize_local_store,
    encrypt_record_payload,
    read_local_store,
    serialize_local_store,
    unwrap_vault_key,
    write_local_store,
)

PASSPHRASE = "PHASE2A_FAKE_PASSPHRASE_DO_NOT_LEAK"
WRONG_PASSPHRASE = "wrong fake phase2a passphrase"
FIXED_VAULT_ID = "20000000-0000-4000-8000-000000000001"
FIXED_VAULT_KEY = bytes.fromhex("99" * 32)
FIXED_SALT = bytes.fromhex("aa" * 16)
FIXED_WRAP_NONCE = bytes.fromhex("ab" * 12)
STORE_ID = "20000000-0000-4000-8000-000000000010"
CREATED_AT = "2026-01-01T00:00:00Z"
UPDATED_AT = "2026-01-02T00:00:00Z"
SENTINEL = "TOP_SECRET_SENTINEL_DO_NOT_LEAK"
FAKE_JOB_KEY = "FAKE_JOB_KEY_DO_NOT_LEAK_123"
FAKE_NOTE_BODY = "FAKE_NOTE_BODY_DO_NOT_LEAK"
FAKE_PROFILE_TEXT = "FAKE_PROFILE_TEXT_DO_NOT_LEAK"
FAKE_SEARCH_TEXT = "FAKE_SEARCH_TEXT_DO_NOT_LEAK"
FAKE_FILTER_VALUE = "FAKE_FILTER_VALUE_DO_NOT_LEAK"
FAKE_GENERATED_DOC_REF = "FAKE_GENERATED_DOC_REF_DO_NOT_LEAK"
FAKE_STATUS = "FAKE_STATUS_DO_NOT_LEAK"
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
            "summary": "fake saved search summary",
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
            "title": "fake snippet",
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
                record_id=f"20000000-0000-4000-8000-00000000010{index}",
                revision=f"20000000-0000-4000-8000-00000000020{index}",
                nonce=bytes([index]) * 12,
            )
        )
    return tuple(records)


def tombstone_record():
    return encrypt_record_payload(
        FIXED_VAULT_KEY,
        metadata(),
        plaintext("saved_job"),
        record_id="20000000-0000-4000-8000-000000000199",
        revision="20000000-0000-4000-8000-000000000299",
        parent_revision="20000000-0000-4000-8000-000000000198",
        deleted=True,
        nonce=bytes([99]) * 12,
    )


def local_store(records=None) -> LocalVaultStore:
    return LocalVaultStore.new(
        metadata(),
        encrypted_records() if records is None else records,
        store_id=STORE_ID,
        created_at=CREATED_AT,
        updated_at=UPDATED_AT,
    )


def assert_no_private_plaintext(serialized: str) -> None:
    for private_value in FORBIDDEN_PLAINTEXT:
        assert private_value not in serialized


def test_local_vault_store_serializes_and_deserializes_successfully() -> None:
    original = local_store()

    restored = deserialize_local_store(serialize_local_store(original))

    assert restored == original


def test_local_vault_store_writes_and_reads_from_tmp_path(tmp_path) -> None:
    original = local_store()
    path = tmp_path / "local-vault.json"

    write_local_store(original, path)

    assert read_local_store(path) == original


def test_decrypt_round_trip_succeeds_for_all_phase2_record_types() -> None:
    vault_metadata = metadata()
    restored = deserialize_local_store(serialize_local_store(local_store()))

    decrypted = [
        decrypt_record_payload(FIXED_VAULT_KEY, vault_metadata, record)
        for record in restored.records
    ]

    assert [record.type for record in decrypted] == list(RECORD_TYPES)
    for record in decrypted:
        assert record.payload == payload(record.type)


def test_wrong_passphrase_fails_without_plaintext_output() -> None:
    store = local_store()
    serialized = serialize_local_store(store)

    assert_no_private_plaintext(serialized)
    with pytest.raises(VaultKeyUnwrapError):
        unwrap_vault_key(store.vault_metadata.key_wraps[0], WRONG_PASSPHRASE)


def test_corrupt_local_store_fails_safely(tmp_path) -> None:
    path = tmp_path / "corrupt-vault.json"
    path.write_bytes(b'{"format":"atlasvault-local-store",')

    with pytest.raises(VaultStoreError):
        read_local_store(path)


def test_unsupported_local_store_envelope_version_fails_safely() -> None:
    payload_data = json.loads(serialize_local_store(local_store()))
    payload_data["version"] = 2

    with pytest.raises(UnsupportedStoreVersion):
        deserialize_local_store(json.dumps(payload_data))


def test_local_vault_store_json_does_not_contain_private_plaintext() -> None:
    serialized = serialize_local_store(local_store())

    assert_no_private_plaintext(serialized)


def test_tombstone_encrypted_record_envelope_is_preserved_without_decrypting_payload() -> None:
    tombstone = tombstone_record()
    store = local_store(records=(tombstone,))

    restored = deserialize_local_store(serialize_local_store(store))

    assert restored.records == (tombstone,)
    assert restored.records[0].deleted is True
    assert restored.records[0].parent_revision == tombstone.parent_revision


def test_local_store_rejects_malformed_record_envelope() -> None:
    store = local_store()
    payload_data = json.loads(serialize_local_store(store))
    payload_data["records"][0]["nonce"] = "not-valid-base64"

    with pytest.raises(VaultStoreError):
        deserialize_local_store(json.dumps(payload_data))


def test_plaintext_record_accepts_phase2_types_but_rejects_unknown_type() -> None:
    for record_type in RECORD_TYPES:
        assert plaintext(record_type).type == record_type

    with pytest.raises(RecordFormatError):
        replace(plaintext("saved_job"), type="unknown_private_type")
