from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

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
        keys={
            entry["key_epoch"]: _seed(entry["vault_key_label"])
            for entry in vector["entries"]
        },
    )


def test_epoch_ring_matches_shared_metadata_and_record_derivation() -> None:
    root = _root()
    ring = _ring()
    derivation = root["record_derivation"]

    assert MAXIMUM_KEY_RING_ENTRIES == root["maximum_ring_entries"]
    assert ring.metadata.to_dict() == root["ring"]["metadata"]
    assert ring.current_key_epoch == 3
    assert hashlib.sha256(
        ring.derive_record_key(
            key_epoch=1,
            vault_id=derivation["vault_id"],
            record_id=derivation["record_id"],
        )
    ).hexdigest() == derivation["expected_epoch_1_key_sha256"]
    assert hashlib.sha256(
        ring.derive_record_key(
            key_epoch=3,
            vault_id=derivation["vault_id"],
            record_id=derivation["record_id"],
        )
    ).hexdigest() == derivation["expected_epoch_3_key_sha256"]


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
