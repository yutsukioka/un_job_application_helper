"""D087: independent P5/P6 stores and an authenticated backend commit point."""

import copy
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from vaultsync.epoch_rotation import EpochVault, RotationError
from vaultsync.sync_queue import EncryptedPatchOperation
from vaultsync.sync_recovery import GuardedSyncState

REPOSITORY_TESTS = Path(__file__).resolve().parents[3] / "tests"
sys.path.insert(0, str(REPOSITORY_TESTS))

from test_atlasvault_activation_c26 import (  # noqa: E402
    environment,
    history_registry,
    initial_body,
    initial_collection,
)


def test_shared_backend_record_schema_and_recipient_binding():
    import base64
    import hashlib
    import jsonschema
    from vaultsync.epoch_rotation import verify_epoch_rotation, delivery_context
    from vaultsync.key_epochs import open_epoch_hpke_v2, EpochHPKESealedVaultKeyV2

    root = Path(__file__).resolve().parents[3] / "contracts/sync"
    vector = json.loads((root / "test_vectors/atlasvault_activation_v1.json").read_text())
    record = vector["record"]
    proof = record["proof"]
    plan = proof["plan"]
    jsonschema.validate(
        record, json.loads((root / "atlasvault_activation_v1.schema.json").read_text())
    )
    result = verify_epoch_rotation(
        proof,
        registry=proof["registry"],
        account_id=plan["account_id"],
        vault_id=plan["vault_id"],
        previous_epoch=3,
        state_root=plan["state_root"],
    )
    assert (
        hashlib.sha256(
            b"atlasvault-active-recipients-v1\n"
            + json.dumps(
                {"recipients": result["recipients"]}, sort_keys=True, separators=(",", ":")
            ).encode()
        ).hexdigest()
        == vector["recipient_commitment"]
    )
    opened = []
    for i, device_id in enumerate(vector["device_ids"][:2]):
        d = next(x for x in proof["deliveries"] if x["device_id"] == device_id)
        item = open_epoch_hpke_v2(
            recipient_private_key=bytes([20 + i]) * 32,
            sealed=EpochHPKESealedVaultKeyV2(
                key_epoch=4,
                encapsulated_key=base64.b64decode(d["encapsulated_key_b64"]),
                ciphertext=base64.b64decode(d["ciphertext_b64"]),
            ),
            context=delivery_context(plan, device_id),
            minimum_key_epoch=4,
        )
        opened.append(hashlib.sha256(item.vault_key).hexdigest())
    assert opened[0] == opened[1]


def test_p6_equivocation_fence_survives_inside_activation_owner(tmp_path):
    import base64
    from vaultsync.authenticated_state_view import _root, _message

    (a, _, _), env = initialize(tmp_path)
    proof = env[4]
    bad = copy.deepcopy(env[5])
    bad["collection_root"] = "de" * 32
    bad["root"] = _root({k: v for k, v in bad.items() if k not in ("root", "signature_b64")})
    bad["signature_b64"] = base64.b64encode(env[2][0].sign(_message(bad["root"]))).decode()
    with pytest.raises(RotationError):
        a.compare_evidence([bad])
    reopened = device(tmp_path, 0, proof)
    assert reopened.observation()["status"] == "RECOVERY_PENDING"
    with pytest.raises(RotationError, match="ATLAS_RECOVERY_PENDING"):
        reopened.begin_activation(proof)
    with pytest.raises(RotationError):
        accept(reopened, 0, proof, backend_accept(env))
    assert len(reopened.recovery()["peer"]) == 1
    assert len(reopened.recovery()["local"]) == 1


def device(root, index, proof):
    plan = proof["plan"]
    return EpochVault(
        root / str(index),
        storage_key=bytes([50 + index]) * 32,
        device_id=proof["registry"][index]["device_id"],
        registry=proof["registry"],
        account_id=plan["account_id"],
        vault_id=plan["vault_id"],
        key_epoch=3,
        state_root=plan["state_root"],
    )


def initialize(root):
    backend, http, identities, headers, proof, view = environment(root)
    clients = []
    for i in range(3):
        history = GuardedSyncState(
            root / str(i) / "prior-history",
            encryption_key=bytes([50 + i]) * 32,
            account_id=proof["plan"]["account_id"],
            vault_id="vault-c26",
            collection_id="collection-c26",
            key_epoch=3,
            trusted_signer=identities[0].signing_public_key,
        )
        history.initialize()
        history.ingest(
            view,
            history_registry(identities),
            initial_collection(identities[0]).to_dict(),
            initial_body(),
        )
        c = device(root, i, proof)
        c.initialize({3: bytes([30]) * 32}, history=history)
        clients.append(c)
    return clients, (backend, http, identities, headers, proof, view)


def backend_accept(env):
    backend, http, _, headers, proof, _ = env
    response = http.post("/v1/vaults/vault-c26/activations", json=proof, headers=headers[0])
    assert response.status_code == 200
    record = backend.commitments.activation(proof["plan"]["account_id"], "vault-c26")
    assert response.json() == {
        "format": "atlasvault-activation-receipt",
        "version": 2,
        "status": "ACTIVATION_ACCEPTED",
        "transition_id": record["transition_id"],
        "key_epoch": record["proof"]["plan"]["new_epoch"],
    }
    return record


def accept(client, index, proof, record):
    return client.accept_rotation(
        proof, accepted_record=record, agreement_private_key=bytes([20 + index]) * 32
    )


def test_three_devices_activate_and_offline_target_is_durably_revoked(tmp_path):
    (a, b, offline), env = initialize(tmp_path)
    proof = env[4]
    before = offline.observation()
    a.begin_activation(proof)
    record = backend_accept(env)
    for i, c in enumerate((a, b)):
        assert accept(c, i, proof, record)
        assert c.observation()["key_epoch"] == 4
        assert c.observation()["recipients"] == proof["plan"]["recipients"]
        assert not accept(c, i, proof, record)
        assert device(tmp_path, i, proof).observation() == c.observation()
    assert offline.observation() == before
    assert env[1].get("/v1/vaults/vault-c26/activations", headers=env[3][2]).status_code == 403
    # Even a stolen public activation response cannot admit the excluded recipient.
    with pytest.raises(RotationError, match="ATLAS_DEVICE_REVOKED"):
        accept(offline, 2, proof, record)
    assert device(tmp_path, 2, proof).observation()["status"] == "REVOKED"


@pytest.mark.parametrize("kind", ["patch", "snapshot"])
def test_future_ciphertext_uses_new_epoch_and_excludes_offline_key(tmp_path, kind):
    (a, b, offline), env = initialize(tmp_path)
    proof = env[4]
    signer = env[2][0]
    historical = a.seal(
        kind, b"synthetic-test-payload", object_id="old", revision="r1", signing_key=signer
    )
    assert offline.open(historical) == b"synthetic-test-payload"
    record = backend_accept(env)
    accept(a, 0, proof, record)
    accept(b, 1, proof, record)
    future = a.seal(
        kind, b"synthetic-test-payload", object_id="new", revision="r2", signing_key=signer
    )
    assert future.key_epoch == 4
    assert b.open(future) == b"synthetic-test-payload"
    assert b.open(historical) == b"synthetic-test-payload"
    with pytest.raises(RotationError):
        offline.open(future)
    with pytest.raises(RotationError):
        accept(offline, 2, proof, record)
    with pytest.raises(RotationError, match="ATLAS_DEVICE_REVOKED"):
        offline.seal(
            kind, b"synthetic-test-payload", object_id="stale", revision="r3", signing_key=env[2][2]
        )
    operation = EncryptedPatchOperation(
        operation_id="10000000-0000-4000-8000-000000000001",
        operation_type="upsert",
        author_device_id=env[2][0].device_id,
        author_sequence=1,
        lamport=1,
        envelope=future,
    )
    a.queue_operation(operation)
    assert device(tmp_path, 0, proof).pending_operations()[0].envelope.key_epoch == 4
    for recipient in proof["plan"]["recipients"]:
        assert a.delivery(recipient)["key_epoch"] == 4
    with pytest.raises(RotationError):
        a.delivery(env[2][2].device_id)
    commitment = a.create_commitment(initial_body(), signing_key=signer)
    assert commitment["view"]["key_epoch"] == 4
    response = env[1].post(
        "/v1/vaults/vault-c26/commitments", json=commitment["view"], headers=env[3][0]
    )
    assert response.status_code == 200


def test_pending_is_durable_on_missing_delivery_and_never_writes_old_epoch(tmp_path):
    (a, _, _), env = initialize(tmp_path)
    proof = env[4]
    a.begin_activation(proof)
    record = backend_accept(env)
    with pytest.raises(RotationError):
        a.accept_rotation(proof, accepted_record=record, agreement_private_key=bytes([99]) * 32)
    reopened = device(tmp_path, 0, proof)
    assert reopened.observation()["status"] == "ACTIVATION_PENDING"
    with pytest.raises(RotationError, match="ATLAS_ACTIVATION_PENDING"):
        reopened.seal("patch", b"synthetic", object_id="bad", revision="r1", signing_key=env[2][0])
    assert accept(reopened, 0, proof, record)


def test_replayed_or_substituted_activation_never_changes_durable_state(tmp_path):
    (a, _, _), env = initialize(tmp_path)
    proof = env[4]
    record = backend_accept(env)
    accept(a, 0, proof, record)
    before = a.observation()
    for field in proof["plan"]:
        bad = copy.deepcopy(proof)
        bad["plan"][field] = "substitution"
        with pytest.raises(RotationError):
            accept(a, 0, bad, record)
        assert device(tmp_path, 0, proof).observation() == before


@pytest.mark.parametrize(
    "stage",
    [
        "prepared",
        "backend_accepted",
        "local_publishing",
        "before_local_commit",
        "after_local_commit",
        "missing_delivery",
    ],
)
def test_real_sigkill_at_activation_boundaries(tmp_path, stage):
    (_, _, _), env = initialize(tmp_path)
    proof = env[4]
    record = None if stage == "prepared" else backend_accept(env)
    config = tmp_path / "child.json"
    config.write_text(json.dumps({"proof": proof, "record": record, "stage": stage}))
    marker = tmp_path / "ready"
    code = """
import json,time,sys
from pathlib import Path
from vaultsync.epoch_rotation import EpochVault
c=json.loads(Path(sys.argv[1]).read_text()); p=c['proof']; q=p['plan']; root=Path(sys.argv[1]).parent
v=EpochVault(root/'0',storage_key=bytes([50])*32,device_id=p['registry'][0]['device_id'],registry=p['registry'],account_id=q['account_id'],vault_id=q['vault_id'],key_epoch=3,state_root=q['state_root'])
def stop(stage):
 if stage==c['stage']:
  (root/'ready').write_text(stage)
  while True: time.sleep(1)
if c['stage']=='prepared':
 v.prepare_rotation(p); stop('prepared')
elif c['stage']=='missing_delivery':
 try:v.accept_rotation(p,accepted_record=c['record'],agreement_private_key=bytes([99])*32)
 except Exception:stop('missing_delivery')
else: v._accept_rotation_for_testing(p,accepted_record=c['record'],agreement_private_key=bytes([20])*32,checkpoint=stop)
"""
    child = subprocess.Popen(
        [sys.executable, "-S", "-c", code, str(config)],
        env=dict(os.environ, PYTHONPATH=os.pathsep.join(sys.path)),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.monotonic() + 20
        while not marker.exists() and child.poll() is None and time.monotonic() < deadline:
            time.sleep(0.02)
        assert marker.exists(), "activation child did not reach interruption point"
        child.kill()
        assert child.wait(timeout=10) == -signal.SIGKILL
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=10)
    reopened = device(tmp_path, 0, proof)
    observation = reopened.observation()
    if stage != "after_local_commit":
        assert reopened.pending_activation() == proof
    if stage == "prepared":
        assert observation["status"] == "ACTIVE" and observation["key_epoch"] == 3
        assert env[0].commitments.activation(proof["plan"]["account_id"], "vault-c26") is None
    elif stage == "after_local_commit":
        assert observation["status"] == "ACTIVE" and observation["key_epoch"] == 4
    else:
        assert observation["status"] == "ACTIVATION_PENDING" and observation["key_epoch"] == 3
        with pytest.raises(RotationError, match="ATLAS_ACTIVATION_PENDING"):
            reopened.seal(
                "patch", b"synthetic", object_id="bad", revision="r1", signing_key=env[2][0]
            )
        if stage != "missing_delivery":
            assert accept(reopened, 0, proof, record)
            assert reopened.observation()["key_epoch"] == 4
    print(
        f"C26 D087 Python SIGKILL stage={stage} pid={child.pid} status={observation['status']} epoch={observation['key_epoch']}"
    )
