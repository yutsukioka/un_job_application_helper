from __future__ import annotations

import base64
import hashlib
import json
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest

from vaultsync import crypto
from vaultsync.export import (
    AtlasVaultExport,
    VaultExportError,
    deserialize_vault_export,
    serialize_vault_export_bytes,
)
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


def test_export_metadata_accepts_canonical_uuid_and_utc_seconds() -> None:
    vector = load_vector()
    export = AtlasVaultExport(
        vault_metadata=VaultMetadata.from_dict(vector["vault_metadata"]),
        records=(),
        export_id="30000000-0000-4000-8000-000000000010",
        created_at="2026-07-26T05:17:28Z",
    )

    assert export.export_id == "30000000-0000-4000-8000-000000000010"
    assert export.created_at == "2026-07-26T05:17:28Z"


@pytest.mark.parametrize(
    "export_id",
    [
        "not-a-uuid",
        "30000000-0000-4000-8000-0000000000AA",
        "30000000000040008000000000000010",
        "{30000000-0000-4000-8000-000000000010}",
        " 30000000-0000-4000-8000-000000000010",
        "30000000-0000-4000-8000-000000000010 ",
        "",
    ],
)
def test_direct_export_construction_rejects_noncanonical_export_id(
    export_id: str,
) -> None:
    vector = load_vector()

    with pytest.raises(
        VaultExportError,
        match="^export_id must be a canonical lowercase UUID$",
    ):
        AtlasVaultExport(
            vault_metadata=VaultMetadata.from_dict(vector["vault_metadata"]),
            records=(),
            export_id=export_id,
            created_at="2026-07-26T05:17:28Z",
        )


@pytest.mark.parametrize(
    "export_id",
    [
        "not-a-uuid",
        "30000000-0000-4000-8000-0000000000AA",
        "30000000000040008000000000000010",
        "{30000000-0000-4000-8000-000000000010}",
        " 30000000-0000-4000-8000-000000000010",
        "30000000-0000-4000-8000-000000000010 ",
        "",
    ],
)
def test_untrusted_export_decode_rejects_noncanonical_export_id(
    export_id: str,
) -> None:
    data = deepcopy(load_vector()["export"])
    data["export_id"] = export_id

    with pytest.raises(
        VaultExportError,
        match="^export_id must be a canonical lowercase UUID$",
    ):
        deserialize_vault_export(data)


@pytest.mark.parametrize(
    "created_at",
    [
        "not-a-date",
        "2026-07-26T05:17:28.000Z",
        "2026-07-26T05:17:28+00:00",
        "2026-07-26T05:17:28z",
        "2026-07-26T05:17:28",
        "2026-02-30T05:17:28Z",
        " 2026-07-26T05:17:28Z",
        "2026-07-26T05:17:28Z ",
        "",
    ],
)
def test_direct_export_construction_rejects_noncanonical_timestamp(
    created_at: str,
) -> None:
    vector = load_vector()

    with pytest.raises(
        VaultExportError,
        match="^created_at must be UTC ISO-8601 seconds$",
    ):
        AtlasVaultExport(
            vault_metadata=VaultMetadata.from_dict(vector["vault_metadata"]),
            records=(),
            export_id="30000000-0000-4000-8000-000000000010",
            created_at=created_at,
        )


@pytest.mark.parametrize(
    "created_at",
    [
        "not-a-date",
        "2026-07-26T05:17:28.000Z",
        "2026-07-26T05:17:28+00:00",
        "2026-07-26T05:17:28z",
        "2026-07-26T05:17:28",
        "2026-02-30T05:17:28Z",
        " 2026-07-26T05:17:28Z",
        "2026-07-26T05:17:28Z ",
        "",
    ],
)
def test_untrusted_export_decode_rejects_noncanonical_timestamp(
    created_at: str,
) -> None:
    data = deepcopy(load_vector()["export"])
    data["created_at"] = created_at

    with pytest.raises(
        VaultExportError,
        match="^created_at must be UTC ISO-8601 seconds$",
    ):
        deserialize_vault_export(data)


def test_export_new_rejects_explicit_empty_metadata_instead_of_defaulting() -> None:
    vector = load_vector()
    metadata = VaultMetadata.from_dict(vector["vault_metadata"])

    with pytest.raises(
        VaultExportError,
        match="^export_id must be a canonical lowercase UUID$",
    ):
        AtlasVaultExport.new(
            metadata,
            export_id="",
            created_at="2026-07-26T05:17:28Z",
        )
    with pytest.raises(
        VaultExportError,
        match="^created_at must be UTC ISO-8601 seconds$",
    ):
        AtlasVaultExport.new(
            metadata,
            export_id="30000000-0000-4000-8000-000000000010",
            created_at="",
        )


def test_export_decode_requires_exact_top_level_keys() -> None:
    vector = load_vector()
    expected_keys = {
        "format",
        "version",
        "export_id",
        "created_at",
        "vault_metadata",
        "records",
    }
    assert set(vector["export"]) == expected_keys

    unknown = deepcopy(vector["export"])
    unknown["unexpected"] = True
    with pytest.raises(
        VaultExportError,
        match="^export must contain exactly the supported fields$",
    ):
        deserialize_vault_export(unknown)

    for key in expected_keys:
        missing = deepcopy(vector["export"])
        del missing[key]
        with pytest.raises(
            VaultExportError,
            match="^export must contain exactly the supported fields$",
        ):
            deserialize_vault_export(missing)


def test_export_metadata_errors_are_fixed_and_do_not_echo_input() -> None:
    vector = load_vector()
    invalid_id = "PRIVATE_INVALID_EXPORT_ID_SENTINEL"
    invalid_timestamp = "PRIVATE_INVALID_TIMESTAMP_SENTINEL"

    for field, value, expected in [
        (
            "export_id",
            invalid_id,
            "export_id must be a canonical lowercase UUID",
        ),
        (
            "created_at",
            invalid_timestamp,
            "created_at must be UTC ISO-8601 seconds",
        ),
    ]:
        data = deepcopy(vector["export"])
        data[field] = value
        with pytest.raises(VaultExportError) as raised:
            deserialize_vault_export(data)
        assert str(raised.value) == expected
        assert value not in str(raised.value)


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


@pytest.mark.parametrize("value", [None, b"not-text", 1])
def test_recovery_key_parser_rejects_non_text_with_fixed_error(value: Any) -> None:
    with pytest.raises(RecoveryKeyError, match="^invalid recovery key$"):
        crypto.parse_recovery_key(value)


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


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("wrap_version", None),
        ("wrap_version", 1),
        ("unexpected", True),
    ],
)
def test_versioned_v1_model_rejects_ambiguous_or_unknown_fields(
    field: str,
    value: Any,
) -> None:
    metadata = deepcopy(load_vector()["vault_metadata"])
    passphrase = {
        "id": "legacy-passphrase",
        "type": "passphrase",
        "kdf": {
            "algorithm": "Argon2id",
            "salt": base64.b64encode(b"s" * 16).decode(),
            "memory_kib": 65_536,
            "iterations": 3,
            "parallelism": 4,
        },
        "nonce": base64.b64encode(b"n" * 12).decode(),
        "ciphertext": base64.b64encode(b"c" * 48).decode(),
    }
    passphrase[field] = value
    metadata["key_wraps"] = [passphrase]

    with pytest.raises(VaultFormatError):
        VaultMetadata.from_dict(metadata)


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


@pytest.mark.parametrize(
    "vault_id",
    [
        "",
        " leading",
        "trailing ",
        "a" * 97,
        ".",
        "..",
        "contains/slash",
        "contains.dot",
        "unicode-\u00e9",
        "saved_search",
        "SAVED_JOB",
    ],
)
def test_v2_and_metadata_reject_vault_ids_outside_swift_path_policy(
    vault_id: str,
) -> None:
    vector = load_vector()
    wrapped = RecoveryKeyWrapV2.from_dict(vector["recovery_wrap"])

    with pytest.raises(VaultFormatError, match="^vault_id must be a valid identifier$"):
        crypto.recovery_wrap_v2_aad(vault_id, wrapped)

    metadata = deepcopy(vector["vault_metadata"])
    metadata["vault_id"] = vault_id
    with pytest.raises(VaultFormatError, match="^vault_id must be a valid identifier$"):
        VaultMetadata.from_dict(metadata)

    with pytest.raises(VaultFormatError, match="^vault_id must be a valid identifier$"):
        VaultMetadata.new(vault_id=vault_id)


def test_v2_and_metadata_accept_swift_path_policy_identifier() -> None:
    vector = load_vector()
    wrapped = RecoveryKeyWrapV2.from_dict(vector["recovery_wrap"])
    vault_id = "Valid_Vault-123"
    aad = crypto.recovery_wrap_v2_aad(vault_id, wrapped)
    metadata = deepcopy(vector["vault_metadata"])
    metadata["vault_id"] = vault_id

    assert json.loads(aad)["vault_id"] == vault_id
    assert VaultMetadata.from_dict(metadata).vault_id == vault_id
