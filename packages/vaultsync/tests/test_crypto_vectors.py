from __future__ import annotations

import base64
import json
from copy import deepcopy
from pathlib import Path
from typing import Any, Mapping

import pytest

from vaultsync.crypto import (
    _record_aad,
    decrypt_record_payload,
    derive_record_key,
    encrypt_record_payload,
)
from vaultsync.format import VaultAuthenticationError, VaultMetadata, _stable_json_bytes
from vaultsync.records import EncryptedRecord, PlaintextRecord, serialize_encrypted_record

REPO_ROOT = Path(__file__).resolve().parents[3]
PAYLOAD_VECTOR_PATH = REPO_ROOT / "contracts/sync/test_vectors/atlasvault_payload_vectors_v1.json"
CRYPTO_VECTOR_PATH = REPO_ROOT / "contracts/sync/test_vectors/atlasvault_crypto_vectors_v1.json"

CRYPTO_FORMAT = "atlasvault-crypto-vectors"
SUPPORTED_VERSION = 1
ENCRYPTED_RECORD_ALLOWLIST = {
    "id",
    "schema_version",
    "revision",
    "parent_revision",
    "deleted",
    "key_id",
    "nonce",
    "ciphertext",
}
RECORD_TYPE_STRINGS = {
    "saved_search",
    "saved_job",
    "application_note",
    "profile_snippet",
    "draft_metadata",
}


class CryptoVectorValidationError(ValueError):
    """Raised when an AtlasVault crypto vector is malformed."""


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def b64decode(value: Any, context: str) -> bytes:
    if not isinstance(value, str):
        raise CryptoVectorValidationError(f"{context} must be base64 text")
    try:
        return base64.b64decode(value.encode("ascii"), validate=True)
    except ValueError as exc:
        raise CryptoVectorValidationError(f"{context} must be valid base64") from exc


def validate_crypto_vectors(data: Mapping[str, Any]) -> None:
    if data.get("format") != CRYPTO_FORMAT:
        raise CryptoVectorValidationError("unsupported crypto vector format")
    if data.get("version") != SUPPORTED_VERSION:
        raise CryptoVectorValidationError("unsupported crypto vector version")
    if "Fake test-only" not in str(data.get("description", "")):
        raise CryptoVectorValidationError("crypto vector description must mark fake data")
    if "TEST ONLY" not in str(data.get("warning", "")):
        raise CryptoVectorValidationError("crypto vector warning must mark test-only data")
    suite = data.get("suite")
    if not isinstance(suite, dict):
        raise CryptoVectorValidationError("suite must be an object")
    if suite.get("record_aead") != "AES-256-GCM":
        raise CryptoVectorValidationError("unsupported record AEAD")
    if suite.get("subkey_kdf") != "HKDF-SHA256":
        raise CryptoVectorValidationError("unsupported record KDF")
    if set(data.get("encrypted_record_plaintext_metadata_allowlist", ())) != ENCRYPTED_RECORD_ALLOWLIST:
        raise CryptoVectorValidationError("encrypted record metadata allowlist changed")
    vectors = data.get("vectors")
    if not isinstance(vectors, list) or not vectors:
        raise CryptoVectorValidationError("vectors must be a non-empty list")
    for vector in vectors:
        validate_vector(vector)


def validate_vector(vector: Mapping[str, Any]) -> None:
    if not str(vector.get("name", "")).endswith("_v1"):
        raise CryptoVectorValidationError("vector name must be versioned")
    if vector.get("source_payload_vector") not in RECORD_TYPE_STRINGS:
        raise CryptoVectorValidationError("source payload vector is unsupported")
    if len(b64decode(vector.get("test_only_vault_key_b64"), "test_only_vault_key_b64")) != 32:
        raise CryptoVectorValidationError("test-only vault key must be 32 bytes")
    if not b64decode(vector.get("plaintext_json_b64"), "plaintext_json_b64"):
        raise CryptoVectorValidationError("plaintext JSON bytes must be present")
    if len(b64decode(vector.get("record", {}).get("nonce"), "record.nonce")) != 12:
        raise CryptoVectorValidationError("record nonce must be 12 bytes")
    if len(b64decode(vector.get("record", {}).get("ciphertext"), "record.ciphertext")) <= 16:
        raise CryptoVectorValidationError("record ciphertext must include ciphertext and tag")
    if not isinstance(vector.get("forbidden_plaintext_strings"), list):
        raise CryptoVectorValidationError("forbidden plaintext strings must be a list")


def metadata_from_vector(vector: Mapping[str, Any]) -> VaultMetadata:
    vault = vector["vault"]
    if vault["format"] != "atlas-vault" or vault["version"] != 1:
        raise CryptoVectorValidationError("unsupported vector vault metadata")
    return VaultMetadata.new(vault_id=vault["vault_id"])


def plaintext_from_payload_vector(vector: Mapping[str, Any]) -> PlaintextRecord:
    payload_vectors = load_json(PAYLOAD_VECTOR_PATH)
    payload = payload_vectors["payloads"][vector["source_payload_vector"]]
    return PlaintextRecord.from_dict(payload)


def plaintext_bytes_from_payload_vector(vector: Mapping[str, Any]) -> bytes:
    return _stable_json_bytes(plaintext_from_payload_vector(vector).to_dict())


def serialized_vector_text() -> str:
    return CRYPTO_VECTOR_PATH.read_text(encoding="utf-8")


def test_crypto_vector_file_format_and_test_only_markers() -> None:
    data = load_json(CRYPTO_VECTOR_PATH)

    validate_crypto_vectors(data)

    serialized = serialized_vector_text()
    assert "TEST ONLY" in serialized
    assert "Not real user data" in serialized
    assert "test_only_vault_key_b64" in serialized
    assert "passphrase_b64" not in serialized
    assert "recovery_key" not in serialized


def test_crypto_vectors_recompute_python_reference_outputs() -> None:
    data = load_json(CRYPTO_VECTOR_PATH)
    validate_crypto_vectors(data)

    for vector in data["vectors"]:
        vault_key = b64decode(vector["test_only_vault_key_b64"], "test_only_vault_key_b64")
        metadata = metadata_from_vector(vector)
        plaintext = plaintext_from_payload_vector(vector)
        plaintext_bytes = plaintext_bytes_from_payload_vector(vector)
        encrypted = EncryptedRecord.from_dict(vector["record"])

        recomputed_record_key = derive_record_key(vault_key, metadata.vault_id, encrypted.id)
        recomputed_aad = _record_aad(metadata, encrypted)
        recomputed_encrypted = encrypt_record_payload(
            vault_key,
            metadata,
            plaintext,
            record_id=encrypted.id,
            revision=encrypted.revision,
            parent_revision=encrypted.parent_revision,
            deleted=encrypted.deleted,
            key_id=encrypted.key_id,
            nonce=encrypted.nonce,
        )

        assert base64.b64encode(recomputed_record_key).decode("ascii") == vector["record_key_b64"]
        assert base64.b64encode(plaintext_bytes).decode("ascii") == vector["plaintext_json_b64"]
        assert json.loads(recomputed_aad.decode("utf-8")) == vector["aad_json"]
        assert base64.b64encode(recomputed_aad).decode("ascii") == vector["aad_b64"]
        assert recomputed_encrypted.to_dict() == encrypted.to_dict()


def test_crypto_vectors_plaintext_bytes_match_source_payload_vectors() -> None:
    data = load_json(CRYPTO_VECTOR_PATH)
    validate_crypto_vectors(data)

    for vector in data["vectors"]:
        plaintext_bytes = b64decode(vector["plaintext_json_b64"], "plaintext_json_b64")
        expected_bytes = plaintext_bytes_from_payload_vector(vector)

        assert plaintext_bytes == expected_bytes
        assert json.loads(plaintext_bytes.decode("utf-8")) == plaintext_from_payload_vector(
            vector
        ).to_dict()


def test_crypto_vectors_decrypt_to_source_payload_vectors() -> None:
    data = load_json(CRYPTO_VECTOR_PATH)
    validate_crypto_vectors(data)

    for vector in data["vectors"]:
        vault_key = b64decode(vector["test_only_vault_key_b64"], "test_only_vault_key_b64")
        metadata = metadata_from_vector(vector)
        expected = plaintext_from_payload_vector(vector)
        encrypted = EncryptedRecord.from_dict(vector["record"])

        decrypted = decrypt_record_payload(vault_key, metadata, encrypted)

        assert decrypted.to_dict() == expected.to_dict()


def test_crypto_vectors_encrypted_records_do_not_leak_plaintext() -> None:
    data = load_json(CRYPTO_VECTOR_PATH)
    validate_crypto_vectors(data)

    for vector in data["vectors"]:
        encrypted = EncryptedRecord.from_dict(vector["record"])
        serialized = serialize_encrypted_record(encrypted)
        envelope = json.loads(serialized)

        assert set(envelope) == ENCRYPTED_RECORD_ALLOWLIST
        assert set(envelope) == set(data["encrypted_record_plaintext_metadata_allowlist"])
        for forbidden in vector["forbidden_plaintext_strings"]:
            assert forbidden not in serialized
        for record_type in RECORD_TYPE_STRINGS:
            assert record_type not in serialized


def test_crypto_vectors_reject_aad_nonce_and_ciphertext_tampering() -> None:
    vector = load_json(CRYPTO_VECTOR_PATH)["vectors"][0]
    vault_key = b64decode(vector["test_only_vault_key_b64"], "test_only_vault_key_b64")
    metadata = metadata_from_vector(vector)
    encrypted = EncryptedRecord.from_dict(vector["record"])

    aad_tampered = EncryptedRecord(
        id=encrypted.id,
        schema_version=encrypted.schema_version,
        revision=f"{encrypted.revision}-tampered",
        parent_revision=encrypted.parent_revision,
        deleted=encrypted.deleted,
        key_id=encrypted.key_id,
        nonce=encrypted.nonce,
        ciphertext=encrypted.ciphertext,
    )
    with pytest.raises(VaultAuthenticationError):
        decrypt_record_payload(vault_key, metadata, aad_tampered)

    nonce = bytearray(encrypted.nonce)
    nonce[0] ^= 1
    nonce_tampered = EncryptedRecord(
        id=encrypted.id,
        schema_version=encrypted.schema_version,
        revision=encrypted.revision,
        parent_revision=encrypted.parent_revision,
        deleted=encrypted.deleted,
        key_id=encrypted.key_id,
        nonce=bytes(nonce),
        ciphertext=encrypted.ciphertext,
    )
    with pytest.raises(VaultAuthenticationError):
        decrypt_record_payload(vault_key, metadata, nonce_tampered)

    ciphertext = bytearray(encrypted.ciphertext)
    ciphertext[-1] ^= 1
    ciphertext_tampered = EncryptedRecord(
        id=encrypted.id,
        schema_version=encrypted.schema_version,
        revision=encrypted.revision,
        parent_revision=encrypted.parent_revision,
        deleted=encrypted.deleted,
        key_id=encrypted.key_id,
        nonce=encrypted.nonce,
        ciphertext=bytes(ciphertext),
    )
    with pytest.raises(VaultAuthenticationError):
        decrypt_record_payload(vault_key, metadata, ciphertext_tampered)


def test_crypto_vector_aad_json_uses_stable_json_bytes() -> None:
    data = load_json(CRYPTO_VECTOR_PATH)
    validate_crypto_vectors(data)

    for vector in data["vectors"]:
        expected_aad = b64decode(vector["aad_b64"], "aad_b64")

        assert _stable_json_bytes(vector["aad_json"]) == expected_aad


def test_crypto_vectors_reject_unsupported_format_and_version() -> None:
    data = load_json(CRYPTO_VECTOR_PATH)

    bad_format = deepcopy(data)
    bad_format["format"] = "unexpected"
    with pytest.raises(CryptoVectorValidationError):
        validate_crypto_vectors(bad_format)

    bad_version = deepcopy(data)
    bad_version["version"] = 2
    with pytest.raises(CryptoVectorValidationError):
        validate_crypto_vectors(bad_version)


def test_crypto_vectors_do_not_reference_real_private_paths_or_user_data() -> None:
    serialized = serialized_vector_text()

    assert "private/" not in serialized
    assert "/private" not in serialized
    assert "private\\" not in serialized
    assert ".atlasvault" not in serialized
    assert "real user" not in serialized.lower().replace("not real user", "")
