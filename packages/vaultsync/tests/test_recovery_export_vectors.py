from __future__ import annotations

import base64
import hashlib
import json
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest

from vaultsync import crypto
from vaultsync.export import deserialize_vault_export, serialize_vault_export_bytes
from vaultsync.format import (
    RecoveryKeyError,
    RecoveryKeyWrapV2,
    VaultFormatError,
    VaultKeyUnwrapError,
    VaultMetadata,
)


REPO_ROOT = Path(__file__).resolve().parents[3]
VECTOR_PATH = (
    REPO_ROOT
    / "contracts"
    / "sync"
    / "test_vectors"
    / "atlasvault_recovery_export_vectors_v2.json"
)


def load_vector() -> dict[str, Any]:
    document = json.loads(VECTOR_PATH.read_text(encoding="utf-8"))
    assert document["format"] == "atlasvault-recovery-export-vectors"
    assert document["version"] == 2
    assert "Fake test-only" in document["description"]
    assert "TEST ONLY" in document["warning"]
    assert len(document["vectors"]) == 1
    vector = document["vectors"][0]
    assert vector["test_only"] is True
    return vector


def decode64(value: str) -> bytes:
    return base64.b64decode(value.encode("ascii"), validate=True)


def test_python_recomputes_recovery_code_and_wrap_vector() -> None:
    vector = load_vector()
    recovery_key = decode64(vector["test_only_recovery_key_b64"])
    vault_key = decode64(vector["test_only_vault_key_b64"])

    assert len(recovery_key) == 32
    assert len(vault_key) == 32
    assert crypto.encode_recovery_key(recovery_key) == vector["canonical_recovery_text"]
    assert crypto.parse_recovery_key(vector["canonical_recovery_text"]) == recovery_key

    recomputed = crypto.wrap_vault_key_with_recovery(
        vault_key,
        recovery_key,
        vault_id=vector["vault_id"],
        salt=decode64(vector["salt_b64"]),
        nonce=decode64(vector["nonce_b64"]),
    )
    assert recomputed.to_dict() == vector["recovery_wrap"]
    aad = crypto.recovery_wrap_v2_aad(vector["vault_id"], recomputed)
    assert aad == decode64(vector["key_wrap_aad_b64"])
    assert json.loads(aad) == vector["key_wrap_aad_json"]
    assert crypto.unwrap_vault_key_with_recovery(
        recomputed,
        recovery_key,
        vault_id=vector["vault_id"],
    ) == vault_key


def test_python_recomputes_canonical_export_vector() -> None:
    vector = load_vector()
    export = deserialize_vault_export(vector["export"])
    canonical = serialize_vault_export_bytes(export)

    assert export.vault_metadata.to_dict() == vector["vault_metadata"]
    assert export.records == ()
    assert canonical == decode64(vector["canonical_export_json_b64"])
    assert hashlib.sha256(canonical).hexdigest() == vector["canonical_export_sha256"]
    assert b"store_id" not in canonical
    assert b"selected_vault" not in canonical
    assert vector["test_only_recovery_key_b64"].encode() not in canonical
    assert vector["test_only_vault_key_b64"].encode() not in canonical
    assert vector["canonical_recovery_text"].encode() not in canonical


def test_recovery_key_generation_requests_exactly_32_random_bytes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requested: list[int] = []

    def fake_token_bytes(count: int) -> bytes:
        requested.append(count)
        return b"\xa5" * count

    monkeypatch.setattr(crypto.secrets, "token_bytes", fake_token_bytes)

    assert crypto.generate_recovery_key() == b"\xa5" * 32
    assert requested == [32]


@pytest.mark.parametrize(
    "value",
    [
        "AVRK2-AAAQ-EAYE-AUDA-OCAJ-BIFQ-YDIO-B4IB-CEQT-CQKR-MFYY-DENB-WHA5-DYPS-3FKD-2KZQ",
        "AVRK1-AAAQ-EAYE-AUDA-OCAJ-BIFQ-YDIO-B4IB-CEQT-CQKR-MFYY-DENB-WHA5-DYPS-3FKD-2KZ=",
        "AVRK1-AAA0-EAYE-AUDA-OCAJ-BIFQ-YDIO-B4IB-CEQT-CQKR-MFYY-DENB-WHA5-DYPS-3FKD-2KZQ",
        "AVRK1-AAA1-EAYE-AUDA-OCAJ-BIFQ-YDIO-B4IB-CEQT-CQKR-MFYY-DENB-WHA5-DYPS-3FKD-2KZQ",
        "AVRK1-AAA8-EAYE-AUDA-OCAJ-BIFQ-YDIO-B4IB-CEQT-CQKR-MFYY-DENB-WHA5-DYPS-3FKD-2KZQ",
        "AVRK1-AAAQ-EAYE-AUDA-OCAJ-BIFQ-YDIO-B4IB-CEQT-CQKR-MFYY-DENB-WHA5-DYPS-3FKD",
        "AVRK1-AAAQ-EAYE-AUDA-OCAJ-BIFQ-YDIO-B4IB-CEQT-CQKR-MFYY-DENB-WHA5-DYPS-3FKD-2KZA",
        "AVRK1-AAAQ-EAYE-AUDA-OCAJ-BIFQ-YDIO-B4IB-CEQT-CQKR-MFYY-DENB-WHA5-DYPS-3FKD-2KZ\u041e",
    ],
)
def test_recovery_key_parser_rejects_malformed_input_without_echo(value: str) -> None:
    with pytest.raises(RecoveryKeyError) as raised:
        crypto.parse_recovery_key(value)

    assert str(raised.value) == "invalid recovery key"
    assert value not in str(raised.value)


def test_recovery_key_parser_accepts_ascii_case_and_group_spacing() -> None:
    vector = load_vector()
    canonical = vector["canonical_recovery_text"]
    spaced = canonical.lower().replace("-", " ")

    assert crypto.parse_recovery_key(f" \t{spaced}\r\n") == decode64(
        vector["test_only_recovery_key_b64"]
    )


def test_checksum_mismatch_and_wrong_recovery_key_fail_safely() -> None:
    vector = load_vector()
    canonical = vector["canonical_recovery_text"]
    replacement = "A" if canonical[-1] != "A" else "B"
    bad_checksum = canonical[:-1] + replacement
    with pytest.raises(RecoveryKeyError, match="^invalid recovery key$"):
        crypto.parse_recovery_key(bad_checksum)

    wrapped = RecoveryKeyWrapV2.from_dict(vector["recovery_wrap"])
    wrong = crypto.parse_recovery_key(vector["wrong_canonical_recovery_text"])
    with pytest.raises(VaultKeyUnwrapError) as raised:
        crypto.unwrap_vault_key_with_recovery(
            wrapped,
            wrong,
            vault_id=vector["vault_id"],
        )
    assert str(raised.value) == "failed to unwrap vault key"
    assert vector["wrong_canonical_recovery_text"] not in str(raised.value)


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("id",), "other"),
        (("type",), "passphrase"),
        (("wrap_version",), 1),
        (("kdf", "algorithm"), "PBKDF2"),
        (("kdf", "info"), "other"),
        (("kdf", "salt"), base64.b64encode(b"x" * 31).decode()),
        (("nonce",), base64.b64encode(b"x" * 11).decode()),
        (("ciphertext",), base64.b64encode(b"x" * 47).decode()),
    ],
)
def test_strict_v2_model_rejects_malformed_fields(
    path: tuple[str, ...],
    value: Any,
) -> None:
    data = deepcopy(load_vector()["recovery_wrap"])
    target = data
    for part in path[:-1]:
        target = target[part]
    target[path[-1]] = value

    with pytest.raises(VaultFormatError):
        RecoveryKeyWrapV2.from_dict(data)


def test_v2_model_rejects_unknown_keys_and_duplicate_ids() -> None:
    vector = load_vector()
    data = deepcopy(vector["recovery_wrap"])
    data["unexpected"] = True
    with pytest.raises(VaultFormatError):
        RecoveryKeyWrapV2.from_dict(data)

    wrap = RecoveryKeyWrapV2.from_dict(vector["recovery_wrap"])
    with pytest.raises(VaultFormatError, match="duplicate"):
        VaultMetadata.new(
            vault_id=vector["vault_id"],
            key_wraps=(wrap, wrap),
        )


@pytest.mark.parametrize("field", ["salt", "nonce", "ciphertext"])
def test_valid_length_v2_crypto_mutation_fails_authentication(
    field: str,
) -> None:
    vector = load_vector()
    data = deepcopy(vector["recovery_wrap"])
    if field == "salt":
        original = bytearray(decode64(data["kdf"]["salt"]))
        original[0] ^= 0x01
        data["kdf"]["salt"] = base64.b64encode(original).decode()
    else:
        original = bytearray(decode64(data[field]))
        original[0] ^= 0x01
        data[field] = base64.b64encode(original).decode()
    wrapped = RecoveryKeyWrapV2.from_dict(data)

    with pytest.raises(VaultKeyUnwrapError, match="^failed to unwrap vault key$"):
        crypto.unwrap_vault_key_with_recovery(
            wrapped,
            decode64(vector["test_only_recovery_key_b64"]),
            vault_id=vector["vault_id"],
        )


@pytest.mark.parametrize(
    "mutated_vault_id",
    [
        "21111111-2222-3333-4444-555555555555",
        "11111111-2222-3333-4444-555555555556",
    ],
)
def test_v2_aad_binds_vault_id(mutated_vault_id: str) -> None:
    vector = load_vector()
    wrapped = RecoveryKeyWrapV2.from_dict(vector["recovery_wrap"])
    recovery_key = decode64(vector["test_only_recovery_key_b64"])

    with pytest.raises(VaultKeyUnwrapError, match="^failed to unwrap vault key$"):
        crypto.unwrap_vault_key_with_recovery(
            wrapped,
            recovery_key,
            vault_id=mutated_vault_id,
        )
