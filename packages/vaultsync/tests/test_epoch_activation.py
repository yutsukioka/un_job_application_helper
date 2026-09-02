"""Three independent C26 devices; no real identity or vault material."""

import copy
import json
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from vaultsync.epoch_rotation import EpochVault, RotationError

V = json.loads((Path(__file__).resolve().parents[3] / "contracts/sync/test_vectors/atlasvault_epoch_rotation_v1.json").read_text())


def device(root, index):
    return EpochVault(root / str(index), storage_key=bytes([50 + index]) * 32,
        device_id=V["device_ids"][index], registry=V["proof"]["registry"],
        account_id=V["proof"]["plan"]["account_id"], vault_id="vault-c26", key_epoch=3,
        state_root="ab" * 32)


def initialize(root):
    clients = [device(root, i) for i in range(3)]
    for c in clients:
        c.initialize({3: bytes([30]) * 32})
    return clients


def accept(client, index, proof=None):
    return client.accept_rotation(proof or V["proof"], agreement_private_key=bytes([20 + index]) * 32)


def test_three_devices_activate_and_offline_target_is_durably_revoked(tmp_path):
    a, b, offline = initialize(tmp_path)
    before = offline.observation()
    for i, c in enumerate((a, b)):
        assert accept(c, i)
        assert c.observation()["key_epoch"] == 4
        assert c.observation()["recipients"] == sorted(V["device_ids"][:2])
        assert not accept(c, i)
    assert offline.observation() == before
    with pytest.raises(RotationError, match="ATLAS_DEVICE_REVOKED"):
        accept(offline, 2)
    assert device(tmp_path, 2).observation()["status"] == "REVOKED"
    for i in (0, 1):
        assert device(tmp_path, i).observation() == (a, b)[i].observation()


@pytest.mark.parametrize("kind", ["patch", "snapshot"])
def test_future_ciphertext_uses_new_epoch_and_excludes_offline_key(tmp_path, kind):
    a, b, offline = initialize(tmp_path)
    historical = a.seal(kind, b"synthetic-test-payload", object_id="old", revision="r1", signing_key=Ed25519PrivateKey.from_private_bytes(bytes([10])*32))
    assert offline.open(historical) == b"synthetic-test-payload"
    accept(a, 0); accept(b, 1)
    future = a.seal(kind, b"synthetic-test-payload", object_id="new", revision="r2", signing_key=Ed25519PrivateKey.from_private_bytes(bytes([10])*32))
    assert future.key_epoch == 4
    assert b.open(future) == b"synthetic-test-payload"
    assert b.open(historical) == b"synthetic-test-payload"
    with pytest.raises(RotationError):
        offline.open(future)
    with pytest.raises(RotationError):
        accept(offline, 2)
    with pytest.raises(RotationError, match="ATLAS_DEVICE_REVOKED"):
        offline.seal(kind, b"synthetic-test-payload", object_id="stale", revision="r3", signing_key=Ed25519PrivateKey.from_private_bytes(bytes([12])*32))


def test_activation_before_publish_failure_preserves_prior_generation(tmp_path):
    a, _, _ = initialize(tmp_path)
    before = a.observation()
    def interrupt():
        raise OSError("synthetic interruption")
    with pytest.raises(RotationError):
        a._accept_rotation_for_testing(V["proof"], agreement_private_key=bytes([20])*32, before_publish=interrupt)
    assert device(tmp_path, 0).observation() == before
    assert accept(a, 0)
    assert device(tmp_path, 0).observation()["key_epoch"] == 4


def test_replayed_or_substituted_activation_never_changes_durable_state(tmp_path):
    a, _, _ = initialize(tmp_path)
    accept(a, 0)
    before = a.observation()
    for field in V["proof"]["plan"]:
        proof = copy.deepcopy(V["proof"])
        proof["plan"][field] = "substitution"
        with pytest.raises(RotationError):
            accept(a, 0, proof)
        assert device(tmp_path, 0).observation() == before
