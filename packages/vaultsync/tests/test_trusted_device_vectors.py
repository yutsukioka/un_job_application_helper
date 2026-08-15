from __future__ import annotations

import base64
import json
from dataclasses import replace
from pathlib import Path

import pytest

from vaultsync.device_identity import SignedDeviceDescriptor
from vaultsync.trusted_devices import (
    PairingReplayEntry,
    PairingReplayStore,
    ReplayConsumeOutcome,
    TrustedDeviceCommitOutcome,
    TrustedDevicePeer,
    TrustedDeviceRegistry,
    TrustedDeviceStateError,
    commit_trusted_device,
    consume_pairing_replay,
)


REPO_ROOT = Path(__file__).resolve().parents[3]
VECTOR_PATH = (
    REPO_ROOT
    / "contracts"
    / "sync"
    / "test_vectors"
    / "atlasvault_trusted_pairing_delivery_vectors_v1.json"
)


def _root() -> dict[str, object]:
    return json.loads(VECTOR_PATH.read_text(encoding="utf-8"))


def _bytes(value: object) -> bytes:
    assert isinstance(value, str)
    return base64.b64decode(value.encode("ascii"), validate=True)


def test_registry_and_replay_store_match_reference_bytes() -> None:
    root = _root()
    registry = TrustedDeviceRegistry.from_dict(root["trusted_registry"])
    replay = PairingReplayStore.from_dict(root["replay_store"])

    assert registry.canonical_bytes() == _bytes(root["trusted_registry_canonical_b64"])
    assert replay.canonical_bytes() == _bytes(root["replay_store_canonical_b64"])
    assert TrustedDeviceRegistry.from_canonical_bytes(registry.canonical_bytes()) == registry
    assert PairingReplayStore.from_canonical_bytes(replay.canonical_bytes()) == replay


def test_trust_commit_is_create_only_idempotent_and_conflict_safe() -> None:
    root = _root()
    empty = TrustedDeviceRegistry.from_dict(root["empty_trusted_registry"])
    peer = TrustedDevicePeer.from_dict(root["trusted_peer"])
    committed, outcome = commit_trusted_device(
        empty,
        peer,
        revision=root["registry_commit_revision"],
        updated_at=root["registry_commit_timestamp"],
    )
    assert outcome is TrustedDeviceCommitOutcome.committed
    assert committed.to_dict() == root["trusted_registry"]

    duplicate, duplicate_outcome = commit_trusted_device(
        committed,
        peer,
        revision=root["unused_revision"],
        updated_at=root["later_timestamp"],
    )
    assert duplicate_outcome is TrustedDeviceCommitOutcome.already_trusted
    assert duplicate == committed

    conflict = replace(peer, delivery_id=root["conflicting_delivery_id"])
    with pytest.raises(TrustedDeviceStateError, match="trusted-device state is invalid"):
        commit_trusted_device(
            committed,
            conflict,
            revision=root["unused_revision"],
            updated_at=root["later_timestamp"],
        )


def test_registry_verifies_descriptors_orders_peers_and_caps_at_64() -> None:
    root = _root()
    registry = TrustedDeviceRegistry.from_dict(root["trusted_registry"])
    assert list(registry.devices) == sorted(
        registry.devices,
        key=lambda value: value.peer_device_id,
    )
    for peer in registry.devices:
        assert SignedDeviceDescriptor.from_dict(peer.peer_descriptor.to_dict())

    malformed = dict(root["trusted_registry"])
    malformed["devices"] = list(malformed["devices"]) * 65
    with pytest.raises(TrustedDeviceStateError):
        TrustedDeviceRegistry.from_dict(malformed)


def test_replay_consumption_rejects_conflict_and_prunes_deterministically() -> None:
    root = _root()
    empty = PairingReplayStore.from_dict(root["empty_replay_store"])
    entry = PairingReplayEntry.from_dict(root["offer_replay_entry"])
    consumed, outcome = consume_pairing_replay(
        empty,
        entry,
        revision=root["replay_commit_revision"],
        updated_at=root["replay_commit_timestamp"],
        current_time=root["verification_time"],
    )
    assert outcome is ReplayConsumeOutcome.consumed

    duplicate, duplicate_outcome = consume_pairing_replay(
        consumed,
        entry,
        revision=root["unused_revision"],
        updated_at=root["later_timestamp"],
        current_time=root["verification_time"],
    )
    assert duplicate_outcome is ReplayConsumeOutcome.already_consumed
    assert duplicate == consumed

    conflict = replace(entry, transcript_sha256="00" * 32)
    with pytest.raises(TrustedDeviceStateError):
        consume_pairing_replay(
            consumed,
            conflict,
            revision=root["unused_revision"],
            updated_at=root["later_timestamp"],
            current_time=root["verification_time"],
        )


def test_state_errors_never_echo_private_or_identifier_values() -> None:
    secret = "FAKE_SECRET_MUST_NOT_APPEAR"
    with pytest.raises(TrustedDeviceStateError) as raised:
        TrustedDeviceRegistry.from_dict({"format": secret})
    assert secret not in str(raised.value)
