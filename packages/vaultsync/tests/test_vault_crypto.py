from __future__ import annotations

import base64
from dataclasses import replace

import pytest

from vaultsync import (
    Argon2idParams,
    PlaintextRecord,
    VaultAuthenticationError,
    VaultKeyUnwrapError,
    create_vault_metadata,
    decrypt_record_payload,
    encrypt_record_payload,
    generate_vault_key,
    serialize_encrypted_record,
    serialize_vault_metadata,
    unwrap_vault_key,
)

PASSPHRASE = "fake test passphrase only"
WRONG_PASSPHRASE = "wrong fake test passphrase"
FIXED_VAULT_ID = "00000000-0000-4000-8000-000000000001"
FIXED_VAULT_KEY = bytes.fromhex("11" * 32)
FIXED_SALT = bytes.fromhex("22" * 16)
FIXED_WRAP_NONCE = bytes.fromhex("33" * 12)
FIXED_RECORD_NONCE = bytes.fromhex("44" * 12)
FIXED_RECORD_ID = "00000000-0000-4000-8000-000000000002"
FIXED_REVISION = "00000000-0000-4000-8000-000000000003"


def fast_argon2_params(salt: bytes = FIXED_SALT) -> Argon2idParams:
    return Argon2idParams(
        salt=salt,
        memory_kib=1024,
        iterations=2,
        parallelism=1,
    )


def plaintext(text: str = "small fake saved text") -> PlaintextRecord:
    return PlaintextRecord.saved_text(
        text,
        client_created_at="2026-01-01T00:00:00Z",
        client_updated_at="2026-01-01T00:00:00Z",
    )


def metadata(vault_key: bytes = FIXED_VAULT_KEY):
    return create_vault_metadata(
        vault_key,
        PASSPHRASE,
        vault_id=FIXED_VAULT_ID,
        params=fast_argon2_params(),
        nonce=FIXED_WRAP_NONCE,
    )


def encrypted_record(text: str = "small fake saved text"):
    return encrypt_record_payload(
        FIXED_VAULT_KEY,
        metadata(),
        plaintext(text),
        record_id=FIXED_RECORD_ID,
        revision=FIXED_REVISION,
        nonce=FIXED_RECORD_NONCE,
    )


def test_generate_vault_key_returns_32_random_bytes() -> None:
    first = generate_vault_key()
    second = generate_vault_key()

    assert len(first) == 32
    assert len(second) == 32
    assert first != second


def test_metadata_serialization_excludes_raw_vault_key_and_passphrase() -> None:
    serialized = serialize_vault_metadata(metadata())

    assert base64.b64encode(FIXED_VAULT_KEY).decode("ascii") not in serialized
    assert FIXED_VAULT_KEY.hex() not in serialized
    assert PASSPHRASE not in serialized


def test_correct_passphrase_unwraps_vault_key() -> None:
    vault_metadata = metadata()

    assert unwrap_vault_key(vault_metadata.key_wraps[0], PASSPHRASE) == FIXED_VAULT_KEY


def test_wrong_passphrase_fails_safely() -> None:
    vault_metadata = metadata()

    with pytest.raises(VaultKeyUnwrapError):
        unwrap_vault_key(vault_metadata.key_wraps[0], WRONG_PASSPHRASE)


def test_saved_text_record_encrypts_and_decrypts() -> None:
    vault_metadata = metadata()
    record = encrypt_record_payload(
        FIXED_VAULT_KEY,
        vault_metadata,
        plaintext("remember this fake search"),
        record_id=FIXED_RECORD_ID,
        revision=FIXED_REVISION,
        nonce=FIXED_RECORD_NONCE,
    )

    decrypted = decrypt_record_payload(FIXED_VAULT_KEY, vault_metadata, record)

    assert decrypted.to_dict() == plaintext("remember this fake search").to_dict()


def test_serialized_encrypted_record_excludes_known_plaintext_sentinel() -> None:
    sentinel = "TOP_SECRET_SENTINEL_DO_NOT_LEAK"
    serialized = serialize_encrypted_record(encrypted_record(sentinel))

    assert sentinel not in serialized


def test_tampered_ciphertext_fails_authentication() -> None:
    record = encrypted_record()
    tampered = replace(record, ciphertext=bytes([record.ciphertext[0] ^ 1]) + record.ciphertext[1:])

    with pytest.raises(VaultAuthenticationError):
        decrypt_record_payload(FIXED_VAULT_KEY, metadata(), tampered)


def test_tampered_nonce_fails_authentication() -> None:
    record = encrypted_record()
    tampered = replace(record, nonce=bytes([record.nonce[0] ^ 1]) + record.nonce[1:])

    with pytest.raises(VaultAuthenticationError):
        decrypt_record_payload(FIXED_VAULT_KEY, metadata(), tampered)


def test_tampered_authenticated_metadata_fails_authentication() -> None:
    record = encrypted_record()
    tampered = replace(record, revision="00000000-0000-4000-8000-000000000099")

    with pytest.raises(VaultAuthenticationError):
        decrypt_record_payload(FIXED_VAULT_KEY, metadata(), tampered)


def test_deterministic_test_vector_decrypts_with_fixed_inputs() -> None:
    vault_metadata = metadata()
    record_one = encrypted_record("deterministic fake text")
    record_two = encrypted_record("deterministic fake text")

    assert serialize_vault_metadata(vault_metadata) == serialize_vault_metadata(metadata())
    assert serialize_encrypted_record(record_one) == serialize_encrypted_record(record_two)
    assert (
        decrypt_record_payload(FIXED_VAULT_KEY, vault_metadata, record_one).payload["text"]
        == "deterministic fake text"
    )
