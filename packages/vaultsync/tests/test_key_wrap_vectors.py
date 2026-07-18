from __future__ import annotations

import base64
import binascii
import json
from copy import deepcopy
from pathlib import Path
from typing import Any, Mapping

import pytest

from vaultsync.crypto import _key_wrap_aad, unwrap_vault_key, wrap_vault_key
from vaultsync.format import VaultFormatError, VaultKeyUnwrapError, VaultMetadata

REPO_ROOT = Path(__file__).resolve().parents[3]
VECTOR_PATH = REPO_ROOT / "contracts/sync/test_vectors/atlasvault_key_wrap_vectors_v1.json"

VECTOR_FORMAT = "atlasvault-key-wrap-vectors"
SUPPORTED_VECTOR_VERSION = 1


class KeyWrapVectorValidationError(ValueError):
    """Raised when a shared fake key-wrap vector is malformed."""


def load_vectors() -> dict[str, Any]:
    return json.loads(VECTOR_PATH.read_text(encoding="utf-8"))


def decode_base64(value: Any, context: str) -> bytes:
    if not isinstance(value, str):
        raise KeyWrapVectorValidationError(f"{context} must be base64 text")
    try:
        return base64.b64decode(value.encode("ascii"), validate=True)
    except (binascii.Error, UnicodeEncodeError) as exc:
        raise KeyWrapVectorValidationError(f"{context} must be valid base64") from exc


def decode_fake_utf8(value: Any, context: str) -> str:
    if not isinstance(value, list) or not value:
        raise KeyWrapVectorValidationError(f"{context} must be a non-empty byte array")
    if any(
        isinstance(item, bool) or not isinstance(item, int) or not 0 <= item <= 255
        for item in value
    ):
        raise KeyWrapVectorValidationError(f"{context} must contain bytes")
    try:
        decoded = bytes(value).decode("utf-8")
    except UnicodeDecodeError as exc:
        raise KeyWrapVectorValidationError(f"{context} must be UTF-8") from exc
    return decoded


def validate_vectors(data: Mapping[str, Any]) -> None:
    if data.get("format") != VECTOR_FORMAT:
        raise KeyWrapVectorValidationError("unsupported key-wrap vector format")
    if data.get("version") != SUPPORTED_VECTOR_VERSION:
        raise KeyWrapVectorValidationError("unsupported key-wrap vector version")
    if "Fake test-only" not in str(data.get("description", "")):
        raise KeyWrapVectorValidationError("key-wrap vector description must mark fake data")
    warning = str(data.get("warning", ""))
    for marker in ("TEST ONLY", "Not real user data", "Not a production vault", "Not a production key"):
        if marker not in warning:
            raise KeyWrapVectorValidationError("key-wrap vector warning is incomplete")

    suite = data.get("suite")
    if not isinstance(suite, Mapping):
        raise KeyWrapVectorValidationError("suite must be an object")
    if suite.get("kdf") != "Argon2id":
        raise KeyWrapVectorValidationError("unsupported key-wrap KDF")
    if suite.get("key_wrap_aead") != "AES-256-GCM":
        raise KeyWrapVectorValidationError("unsupported key-wrap AEAD")
    if "excludes vault_id" not in str(suite.get("vault_binding", "")):
        raise KeyWrapVectorValidationError("v1 vault-binding limitation must be explicit")

    vectors = data.get("vectors")
    if not isinstance(vectors, list) or not vectors:
        raise KeyWrapVectorValidationError("vectors must be a non-empty list")
    for vector in vectors:
        validate_vector(vector)


def validate_vector(vector: Any) -> None:
    if not isinstance(vector, Mapping):
        raise KeyWrapVectorValidationError("vector must be an object")
    if not str(vector.get("name", "")).endswith("_v1"):
        raise KeyWrapVectorValidationError("vector name must be versioned")
    if vector.get("test_only") is not True:
        raise KeyWrapVectorValidationError("vector must be marked test-only")

    vault_key = decode_base64(vector.get("test_only_vault_key_b64"), "test_only_vault_key_b64")
    if len(vault_key) != 32:
        raise KeyWrapVectorValidationError("test-only vault key must be 32 bytes")
    passphrase = decode_fake_utf8(vector.get("test_only_input_utf8"), "test_only_input_utf8")
    wrong_passphrase = decode_fake_utf8(
        vector.get("wrong_test_only_input_utf8"),
        "wrong_test_only_input_utf8",
    )
    if passphrase == wrong_passphrase:
        raise KeyWrapVectorValidationError("wrong test passphrase must differ")

    metadata_data = vector.get("vault_metadata")
    if not isinstance(metadata_data, Mapping):
        raise KeyWrapVectorValidationError("vault_metadata must be an object")
    try:
        metadata = VaultMetadata.from_dict(metadata_data)
    except VaultFormatError as exc:
        raise KeyWrapVectorValidationError("invalid v1 vault metadata") from exc
    if len(metadata.key_wraps) != 1:
        raise KeyWrapVectorValidationError("vector must contain exactly one key wrap")
    wrapped = metadata.key_wraps[0]
    if len(wrapped.kdf.salt) < 16:
        raise KeyWrapVectorValidationError("key-wrap salt must be at least 16 bytes")
    if len(wrapped.nonce) != 12:
        raise KeyWrapVectorValidationError("key-wrap nonce must be 12 bytes")
    if len(wrapped.ciphertext) != 48:
        raise KeyWrapVectorValidationError("key-wrap ciphertext must contain 32 bytes plus tag")

    expected_aad = _key_wrap_aad(wrapped.id, wrapped.type, wrapped.kdf)
    if decode_base64(vector.get("key_wrap_aad_b64"), "key_wrap_aad_b64") != expected_aad:
        raise KeyWrapVectorValidationError("key-wrap AAD bytes do not match")
    aad_json = vector.get("key_wrap_aad_json")
    if not isinstance(aad_json, Mapping) or json.loads(expected_aad) != aad_json:
        raise KeyWrapVectorValidationError("key-wrap AAD JSON does not match")
    if "vault_id" in aad_json:
        raise KeyWrapVectorValidationError("v1 key-wrap AAD must not claim vault binding")


def first_vector(data: Mapping[str, Any]) -> Mapping[str, Any]:
    return data["vectors"][0]


def metadata_from(vector: Mapping[str, Any]) -> VaultMetadata:
    return VaultMetadata.from_dict(vector["vault_metadata"])


def test_vector_file_is_fake_test_only_and_valid() -> None:
    data = load_vectors()

    validate_vectors(data)

    assert data["format"] == VECTOR_FORMAT
    assert data["version"] == SUPPORTED_VECTOR_VERSION
    assert len(data["vectors"]) == 1


def test_python_recomputes_and_unwraps_vector() -> None:
    data = load_vectors()
    validate_vectors(data)
    vector = first_vector(data)
    metadata = metadata_from(vector)
    wrapped = metadata.key_wraps[0]
    passphrase = decode_fake_utf8(vector["test_only_input_utf8"], "test_only_input_utf8")
    expected_key = decode_base64(vector["test_only_vault_key_b64"], "test_only_vault_key_b64")

    recomputed = wrap_vault_key(
        expected_key,
        passphrase,
        params=wrapped.kdf,
        nonce=wrapped.nonce,
        key_id=wrapped.id,
    )

    assert recomputed.to_dict() == wrapped.to_dict()
    assert unwrap_vault_key(wrapped, passphrase) == expected_key


def test_wrong_passphrase_fails_without_secret_output() -> None:
    data = load_vectors()
    validate_vectors(data)
    vector = first_vector(data)
    wrapped = metadata_from(vector).key_wraps[0]
    wrong = decode_fake_utf8(
        vector["wrong_test_only_input_utf8"],
        "wrong_test_only_input_utf8",
    )

    with pytest.raises(VaultKeyUnwrapError) as raised:
        unwrap_vault_key(wrapped, wrong)

    error_text = str(raised.value)
    assert error_text == "failed to unwrap vault key"
    assert wrong not in error_text
    assert vector["test_only_vault_key_b64"] not in error_text


def test_serialized_metadata_excludes_fake_key_and_passphrase() -> None:
    data = load_vectors()
    validate_vectors(data)
    vector = first_vector(data)
    serialized = json.dumps(vector["vault_metadata"], sort_keys=True)
    passphrase = decode_fake_utf8(vector["test_only_input_utf8"], "test_only_input_utf8")
    vault_key = decode_base64(vector["test_only_vault_key_b64"], "test_only_vault_key_b64")

    assert passphrase not in serialized
    assert vector["test_only_vault_key_b64"] not in serialized
    assert vault_key.hex() not in serialized


def test_v1_key_wrap_aad_excludes_vault_id() -> None:
    data = load_vectors()
    validate_vectors(data)
    vector = first_vector(data)

    assert "vault_id" not in vector["key_wrap_aad_json"]
    assert vector["vault_metadata"]["vault_id"] not in json.dumps(vector["key_wrap_aad_json"])


@pytest.mark.parametrize(
    ("path", "value", "message"),
    [
        (("version",), 2, "unsupported key-wrap vector version"),
        (("suite", "kdf"), "PBKDF2", "unsupported key-wrap KDF"),
        (("suite", "key_wrap_aead"), "AES-CBC", "unsupported key-wrap AEAD"),
        (("vectors", 0, "vault_metadata", "version"), 2, "invalid v1 vault metadata"),
        (
            ("vectors", 0, "vault_metadata", "key_wraps", 0, "type"),
            "recovery",
            "invalid v1 vault metadata",
        ),
        (
            ("vectors", 0, "vault_metadata", "key_wraps", 0, "kdf", "algorithm"),
            "PBKDF2",
            "invalid v1 vault metadata",
        ),
        (
            ("vectors", 0, "vault_metadata", "key_wraps", 0, "kdf", "salt"),
            "not-base64",
            "invalid v1 vault metadata",
        ),
        (
            ("vectors", 0, "vault_metadata", "key_wraps", 0, "kdf", "salt"),
            base64.b64encode(b"x" * 15).decode("ascii"),
            "invalid v1 vault metadata",
        ),
        (
            ("vectors", 0, "vault_metadata", "key_wraps", 0, "nonce"),
            base64.b64encode(b"x" * 11).decode("ascii"),
            "invalid v1 vault metadata",
        ),
        (
            ("vectors", 0, "test_only_vault_key_b64"),
            base64.b64encode(b"x" * 31).decode("ascii"),
            "test-only vault key must be 32 bytes",
        ),
        (
            ("vectors", 0, "test_only_input_utf8"),
            [256],
            "test_only_input_utf8 must contain bytes",
        ),
    ],
)
def test_vector_validation_rejects_malformed_inputs(
    path: tuple[str | int, ...],
    value: Any,
    message: str,
) -> None:
    data = deepcopy(load_vectors())
    target: Any = data
    for part in path[:-1]:
        target = target[part]
    target[path[-1]] = value

    with pytest.raises(KeyWrapVectorValidationError, match=message):
        validate_vectors(data)
