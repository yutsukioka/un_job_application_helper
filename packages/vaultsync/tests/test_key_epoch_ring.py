from __future__ import annotations

import hashlib
import inspect
import json
from pathlib import Path

import pytest

from vaultsync.crypto import derive_record_key as derive_legacy_record_key
from vaultsync.key_epochs import (
    MAXIMUM_KEY_RING_ENTRIES,
    EpochHPKESealedVaultKeyV2,
    KeyEpochError,
    KeyRingMetadata,
    VaultKeyEpochRing,
    open_epoch_hpke_v2,
)


VECTOR_PATH = (
    Path(__file__).resolve().parents[3]
    / "contracts"
    / "sync"
    / "test_vectors"
    / "atlasvault_key_epoch_vectors_v1.json"
)


def _root() -> dict:
    root = json.loads(VECTOR_PATH.read_text(encoding="utf-8"))
    assert root["format"] == "atlasvault-key-epoch-vectors"
    assert root["version"] == 1
    return root


def _seed(label: str) -> bytes:
    return hashlib.sha256(label.encode("ascii")).digest()


def _bytes(value: str) -> bytes:
    return bytes.fromhex(value)


def _ring() -> VaultKeyEpochRing:
    vector = _root()["ring"]
    return VaultKeyEpochRing(
        metadata=KeyRingMetadata.from_dict(vector["metadata"]),
        keys={entry["key_epoch"]: _seed(entry["vault_key_label"]) for entry in vector["entries"]},
    )


def test_epoch_ring_matches_shared_metadata_and_record_derivation() -> None:
    root = _root()
    ring = _ring()
    derivation = root["record_derivation"]

    assert MAXIMUM_KEY_RING_ENTRIES == root["maximum_ring_entries"]
    assert ring.metadata.to_dict() == root["ring"]["metadata"]
    assert ring.current_key_epoch == 3
    assert (
        hashlib.sha256(
            ring.derive_record_key(
                key_epoch=1,
                vault_id=derivation["vault_id"],
                record_id=derivation["record_id"],
            )
        ).hexdigest()
        == derivation["expected_epoch_1_key_sha256"]
    )
    assert (
        hashlib.sha256(
            ring.derive_record_key(
                key_epoch=3,
                vault_id=derivation["vault_id"],
                record_id=derivation["record_id"],
            )
        ).hexdigest()
        == derivation["expected_epoch_3_key_sha256"]
    )


def test_epoch_ring_migrates_legacy_and_recovers_retained_key() -> None:
    legacy = _root()["legacy_migration"]
    legacy_key = _seed(legacy["vault_key_label"])
    migrated = VaultKeyEpochRing.from_legacy(
        legacy_key,
        key_epoch=legacy["key_epoch"],
    )

    assert migrated.metadata.to_dict() == legacy["expected_metadata"]
    assert migrated.current_vault_key == legacy_key
    assert _ring().vault_key_for_epoch(1) == legacy_key
    with pytest.raises(KeyEpochError):
        _ring().vault_key_for_epoch(4)


def test_migrated_epoch_one_preserves_legacy_record_key_derivation() -> None:
    vault_key = _seed("legacy-record-key")
    migrated = VaultKeyEpochRing.from_legacy(vault_key)

    assert migrated.derive_record_key(
        key_epoch=1,
        vault_id="legacy-vault",
        record_id="legacy-record",
    ) == derive_legacy_record_key(vault_key, "legacy-vault", "legacy-record")


def test_migrated_epoch_one_preserves_long_legacy_identifiers() -> None:
    vault_key = _seed("legacy-long-record-key")
    migrated = VaultKeyEpochRing.from_legacy(vault_key)
    vault_id = "v" * 1025
    record_id = "r" * 1025

    assert migrated.derive_record_key(
        key_epoch=1,
        vault_id=vault_id,
        record_id=record_id,
    ) == derive_legacy_record_key(vault_key, vault_id, record_id)


def test_legacy_migration_rejects_non_initial_epoch() -> None:
    with pytest.raises(KeyEpochError):
        VaultKeyEpochRing.from_legacy(_seed("legacy-record-key"), key_epoch=2)


def test_epoch_delivery_open_requires_a_trusted_monotonic_floor() -> None:
    parameter = inspect.signature(open_epoch_hpke_v2).parameters["minimum_key_epoch"]

    assert parameter.default is inspect.Parameter.empty


def test_current_epoch_hpke_delivery_matches_vector_and_rejects_epoch_tamper() -> None:
    vector = _root()["hpke_v2_epoch_delivery"]
    sealed = _ring()._seal_current_hpke_v2_for_testing(
        recipient_public_key=_bytes(vector["recipient_public_key_hex"]),
        context=_bytes(vector["context_hex"]),
        ephemeral_private_key=_seed(vector["sender_seed_label"]),
    )

    assert sealed.key_epoch == vector["key_epoch"]
    assert sealed.encapsulated_key == _bytes(vector["encapsulated_key_hex"])
    assert sealed.ciphertext == _bytes(vector["ciphertext_hex"])
    opened = open_epoch_hpke_v2(
        recipient_private_key=_seed(vector["recipient_seed_label"]),
        sealed=sealed,
        context=_bytes(vector["context_hex"]),
        minimum_key_epoch=3,
    )
    assert opened.key_epoch == 3
    assert opened.vault_key == _ring().current_vault_key

    with pytest.raises(KeyEpochError):
        open_epoch_hpke_v2(
            recipient_private_key=_seed(vector["recipient_seed_label"]),
            sealed=EpochHPKESealedVaultKeyV2(
                key_epoch=2,
                encapsulated_key=sealed.encapsulated_key,
                ciphertext=sealed.ciphertext,
            ),
            context=_bytes(vector["context_hex"]),
            minimum_key_epoch=1,
        )


def test_current_epoch_is_the_only_writable_epoch_and_old_delivery_is_rejected() -> None:
    vector = _root()["hpke_v2_epoch_delivery"]
    sealed = _ring().seal_current_hpke_v2(
        recipient_public_key=_bytes(vector["recipient_public_key_hex"]),
        context=_bytes(vector["context_hex"]),
    )
    assert sealed.key_epoch == 3
    with pytest.raises(KeyEpochError):
        open_epoch_hpke_v2(
            recipient_private_key=_seed(vector["recipient_seed_label"]),
            sealed=sealed,
            context=_bytes(vector["context_hex"]),
            minimum_key_epoch=4,
        )


def test_epoch_ring_rejects_ambiguous_or_unbounded_state() -> None:
    keys = {epoch: _seed(f"epoch-{epoch}") for epoch in range(1, 34)}
    with pytest.raises(KeyEpochError):
        VaultKeyEpochRing(
            metadata=KeyRingMetadata.from_dict(_root()["ring"]["metadata"]),
            keys={1: keys[1], 3: keys[3]},
        )
    with pytest.raises(KeyEpochError):
        VaultKeyEpochRing.from_entries(
            current_key_epoch=3,
            keys={1: keys[1], 2: keys[1], 3: keys[3]},
        )
    with pytest.raises(KeyEpochError):
        VaultKeyEpochRing.from_entries(
            current_key_epoch=MAXIMUM_KEY_RING_ENTRIES + 1,
            keys=keys,
        )


def test_epoch_metadata_rejects_non_integer_json_numbers() -> None:
    base = {
        "format": "atlasvault-vault-key-ring",
        "version": 1,
        "current_key_epoch": 3,
        "retained_key_epochs": [1, 2],
    }
    malformed = (
        {**base, "version": True},
        {**base, "version": 1.0},
        {**base, "current_key_epoch": True, "retained_key_epochs": []},
        {**base, "current_key_epoch": 3.0},
        {**base, "retained_key_epochs": [1, 2.0]},
    )
    for value in malformed:
        with pytest.raises(KeyEpochError):
            KeyRingMetadata.from_dict(value)


def test_epoch_ring_properties_hold_across_bounded_sizes() -> None:
    for current_epoch in range(1, MAXIMUM_KEY_RING_ENTRIES + 1):
        keys = {
            epoch: _seed(f"property-{current_epoch}-{epoch}")
            for epoch in range(1, current_epoch + 1)
        }
        ring = VaultKeyEpochRing.from_entries(
            current_key_epoch=current_epoch,
            keys=keys,
        )
        assert ring.metadata.retained_key_epochs == tuple(range(1, current_epoch))
        assert ring.current_vault_key == keys[current_epoch]
        derived = {
            ring.derive_record_key(
                key_epoch=epoch,
                vault_id=f"vault-{current_epoch}",
                record_id=f"record-{epoch}",
            )
            for epoch in keys
        }
        assert len(derived) == len(keys)


def test_epoch_metadata_and_delivery_tamper_fail_closed() -> None:
    base = {
        "format": "atlasvault-vault-key-ring",
        "version": 1,
        "current_key_epoch": 3,
        "retained_key_epochs": [1, 2],
    }
    malformed = (
        {key: value for key, value in base.items() if key != "format"},
        {**base, "unexpected": 1},
        {**base, "retained_key_epochs": [2, 1]},
        {**base, "retained_key_epochs": [1, 1]},
        {**base, "retained_key_epochs": [1, 3]},
    )
    for value in malformed:
        with pytest.raises(KeyEpochError):
            KeyRingMetadata.from_dict(value)

    vector = _root()["hpke_v2_epoch_delivery"]
    sealed = _ring()._seal_current_hpke_v2_for_testing(
        recipient_public_key=_bytes(vector["recipient_public_key_hex"]),
        context=_bytes(vector["context_hex"]),
        ephemeral_private_key=_seed(vector["sender_seed_label"]),
    )
    recipient = _seed(vector["recipient_seed_label"])
    context = _bytes(vector["context_hex"])
    variants = (
        EpochHPKESealedVaultKeyV2(
            key_epoch=sealed.key_epoch,
            encapsulated_key=bytes([sealed.encapsulated_key[0] ^ 1]) + sealed.encapsulated_key[1:],
            ciphertext=sealed.ciphertext,
        ),
        EpochHPKESealedVaultKeyV2(
            key_epoch=sealed.key_epoch,
            encapsulated_key=sealed.encapsulated_key,
            ciphertext=sealed.ciphertext[:-1] + bytes([sealed.ciphertext[-1] ^ 1]),
        ),
    )
    for value in variants:
        with pytest.raises(KeyEpochError):
            open_epoch_hpke_v2(
                recipient_private_key=recipient,
                sealed=value,
                context=context,
                minimum_key_epoch=sealed.key_epoch,
            )
    with pytest.raises(KeyEpochError):
        open_epoch_hpke_v2(
            recipient_private_key=_seed("wrong-recipient"),
            sealed=sealed,
            context=context,
            minimum_key_epoch=sealed.key_epoch,
        )
