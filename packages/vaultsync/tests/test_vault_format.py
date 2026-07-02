from __future__ import annotations

import json

import pytest

from vaultsync import (
    Argon2idParams,
    UnsupportedRecordVersion,
    UnsupportedVaultVersion,
    VaultMetadata,
    create_vault_metadata,
    deserialize_encrypted_record,
    deserialize_vault_metadata,
    encrypt_record_payload,
    serialize_encrypted_record,
    serialize_vault_metadata,
)
from vaultsync.records import PlaintextRecord

PASSPHRASE = "fake test passphrase only"
FIXED_VAULT_ID = "10000000-0000-4000-8000-000000000001"
FIXED_VAULT_KEY = bytes.fromhex("55" * 32)
FIXED_SALT = bytes.fromhex("66" * 16)
FIXED_WRAP_NONCE = bytes.fromhex("77" * 12)
FIXED_RECORD_NONCE = bytes.fromhex("88" * 12)
FIXED_RECORD_ID = "10000000-0000-4000-8000-000000000002"
FIXED_REVISION = "10000000-0000-4000-8000-000000000003"


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


def record():
    return encrypt_record_payload(
        FIXED_VAULT_KEY,
        metadata(),
        PlaintextRecord.saved_text(
            "fake saved text",
            client_created_at="2026-01-01T00:00:00Z",
            client_updated_at="2026-01-01T00:00:00Z",
        ),
        record_id=FIXED_RECORD_ID,
        revision=FIXED_REVISION,
        nonce=FIXED_RECORD_NONCE,
    )


def test_vault_metadata_object_can_be_created_and_serialized() -> None:
    serialized = serialize_vault_metadata(metadata())
    payload = json.loads(serialized)

    assert payload["format"] == "atlas-vault"
    assert payload["version"] == 1
    assert payload["vault_id"] == FIXED_VAULT_ID
    assert payload["crypto"]["record_aead"] == "AES-256-GCM"
    assert payload["key_wraps"][0]["id"] == "primary-passphrase"


def test_vault_metadata_round_trips() -> None:
    original = metadata()

    restored = deserialize_vault_metadata(serialize_vault_metadata(original))

    assert restored == original


def test_encrypted_record_round_trips() -> None:
    original = record()

    restored = deserialize_encrypted_record(serialize_encrypted_record(original))

    assert restored == original


def test_unsupported_vault_version_fails_safely() -> None:
    payload = json.loads(serialize_vault_metadata(metadata()))
    payload["version"] = 2

    with pytest.raises(UnsupportedVaultVersion):
        deserialize_vault_metadata(json.dumps(payload))


def test_unsupported_record_schema_version_fails_safely() -> None:
    payload = json.loads(serialize_encrypted_record(record()))
    payload["schema_version"] = 2

    with pytest.raises(UnsupportedRecordVersion):
        deserialize_encrypted_record(json.dumps(payload))
