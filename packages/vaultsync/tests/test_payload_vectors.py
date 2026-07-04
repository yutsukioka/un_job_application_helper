from __future__ import annotations

import json
import re
from copy import deepcopy
from pathlib import Path
from typing import Any, Mapping

import pytest

from vaultsync.crypto import decrypt_record_payload, encrypt_record_payload
from vaultsync.format import VaultMetadata
from vaultsync.records import PlaintextRecord, serialize_encrypted_record

REPO_ROOT = Path(__file__).resolve().parents[3]
VECTOR_PATH = REPO_ROOT / "contracts/sync/test_vectors/atlasvault_payload_vectors_v1.json"

REQUIRED_RECORD_TYPES = (
    "saved_search",
    "saved_job",
    "application_note",
    "profile_snippet",
    "draft_metadata",
)
COMMON_ENVELOPE_KEYS = {
    "type",
    "payload_schema",
    "payload",
    "client_created_at",
    "client_updated_at",
}
ISO_8601_UTC_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")

TEST_ONLY_VAULT_KEY = bytes(range(32))
TEST_ONLY_VAULT_ID = "00000000-0000-4000-8000-000000000002"
TEST_ONLY_RECORD_IDS = {
    record_type: f"00000000-0000-4000-8000-{index:012d}"
    for index, record_type in enumerate(REQUIRED_RECORD_TYPES, start=100)
}
TEST_ONLY_REVISION_IDS = {
    record_type: f"00000000-0000-4000-8001-{index:012d}"
    for index, record_type in enumerate(REQUIRED_RECORD_TYPES, start=100)
}
TEST_ONLY_NONCES = {
    record_type: f"nonce-{index:06d}".encode("ascii")
    for index, record_type in enumerate(REQUIRED_RECORD_TYPES, start=100)
}


class VectorValidationError(ValueError):
    """Raised when a shared compatibility vector file is malformed."""


def load_vectors() -> dict[str, Any]:
    return json.loads(VECTOR_PATH.read_text(encoding="utf-8"))


def validate_vector_file(data: Mapping[str, Any]) -> None:
    if data.get("format") != "atlasvault-payload-vectors":
        raise VectorValidationError("unsupported vector format")
    if data.get("version") != 1:
        raise VectorValidationError("unsupported vector version")
    if tuple(data.get("record_types", ())) != REQUIRED_RECORD_TYPES:
        raise VectorValidationError("record type order changed")
    if set(data.get("common_envelope_keys", ())) != COMMON_ENVELOPE_KEYS:
        raise VectorValidationError("common envelope keys changed")
    if data.get("optional_field_convention") != "omit_absent_optional_fields":
        raise VectorValidationError("unsupported optional field convention")
    payloads = data.get("payloads")
    if not isinstance(payloads, dict):
        raise VectorValidationError("payloads must be an object")
    if set(payloads) != set(REQUIRED_RECORD_TYPES):
        raise VectorValidationError("payload vectors are incomplete")


def timestamp_values(value: Any) -> list[str]:
    if isinstance(value, dict):
        values: list[str] = []
        for key, child in value.items():
            if key.endswith("_at") or key == "closing_date_to":
                if not isinstance(child, str):
                    raise VectorValidationError(f"{key} must be text")
                values.append(child)
            values.extend(timestamp_values(child))
        return values
    if isinstance(value, list):
        values = []
        for child in value:
            values.extend(timestamp_values(child))
        return values
    return []


def assert_no_nulls(value: Any) -> None:
    if value is None:
        raise AssertionError("optional fields must be omitted, not encoded as null")
    if isinstance(value, dict):
        for child in value.values():
            assert_no_nulls(child)
    elif isinstance(value, list):
        for child in value:
            assert_no_nulls(child)


def plaintext_records(data: Mapping[str, Any]) -> dict[str, PlaintextRecord]:
    validate_vector_file(data)
    records = {}
    for record_type, vector in data["payloads"].items():
        assert set(vector) == COMMON_ENVELOPE_KEYS
        assert vector["type"] == record_type
        assert vector["payload_schema"] == 1
        assert isinstance(vector["payload"], dict)
        assert_no_nulls(vector)
        for timestamp in timestamp_values(vector):
            assert ISO_8601_UTC_RE.match(timestamp), timestamp
        records[record_type] = PlaintextRecord.from_dict(vector)
    return records


def test_payload_vector_file_format_and_required_payloads() -> None:
    data = load_vectors()

    validate_vector_file(data)

    assert set(data["payloads"]) == set(REQUIRED_RECORD_TYPES)
    assert data["description"].startswith("Fake pre-encryption")
    assert data["timestamp_convention"].endswith("ending in Z.")
    assert data["optional_field_convention"] == "omit_absent_optional_fields"


def test_payload_vectors_convert_to_plaintext_records() -> None:
    records = plaintext_records(load_vectors())

    assert tuple(records) == REQUIRED_RECORD_TYPES
    for record_type, record in records.items():
        assert record.type == record_type
        assert record.payload_schema == 1
        assert record.payload


def test_payload_vectors_encrypt_and_decrypt_round_trip() -> None:
    data = load_vectors()
    records = plaintext_records(data)
    metadata = VaultMetadata.new(vault_id=TEST_ONLY_VAULT_ID)

    for record_type, plaintext in records.items():
        encrypted = encrypt_record_payload(
            TEST_ONLY_VAULT_KEY,
            metadata,
            plaintext,
            record_id=TEST_ONLY_RECORD_IDS[record_type],
            revision=TEST_ONLY_REVISION_IDS[record_type],
            nonce=TEST_ONLY_NONCES[record_type],
        )

        decrypted = decrypt_record_payload(TEST_ONLY_VAULT_KEY, metadata, encrypted)

        assert decrypted.to_dict() == plaintext.to_dict()


def test_encrypted_records_match_metadata_allowlist_and_do_not_leak_plaintext() -> None:
    data = load_vectors()
    records = plaintext_records(data)
    metadata = VaultMetadata.new(vault_id=TEST_ONLY_VAULT_ID)
    allowlist = set(data["encrypted_record_expectations"]["plaintext_metadata_allowlist"])
    forbidden_strings = data["encrypted_record_expectations"]["forbidden_plaintext_strings"]

    for record_type, plaintext in records.items():
        encrypted = encrypt_record_payload(
            TEST_ONLY_VAULT_KEY,
            metadata,
            plaintext,
            record_id=TEST_ONLY_RECORD_IDS[record_type],
            revision=TEST_ONLY_REVISION_IDS[record_type],
            nonce=TEST_ONLY_NONCES[record_type],
        )
        serialized = serialize_encrypted_record(encrypted)
        envelope = json.loads(serialized)

        assert set(envelope) == allowlist
        assert envelope["id"] == TEST_ONLY_RECORD_IDS[record_type]
        assert envelope["schema_version"] == 1
        for forbidden in forbidden_strings:
            assert forbidden not in serialized


def test_unsupported_vector_format_and_version_are_rejected() -> None:
    data = load_vectors()

    bad_format = deepcopy(data)
    bad_format["format"] = "unexpected"
    with pytest.raises(VectorValidationError):
        validate_vector_file(bad_format)

    bad_version = deepcopy(data)
    bad_version["version"] = 2
    with pytest.raises(VectorValidationError):
        validate_vector_file(bad_version)


def test_vectors_do_not_reference_real_private_paths() -> None:
    serialized = json.dumps(load_vectors(), sort_keys=True)

    assert "private/" not in serialized
    assert "/private" not in serialized
    assert "private\\" not in serialized
