"""D087 activation through authenticated HTTP; synthetic identities only."""

import base64
import copy
import hashlib
import json
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from atlasvault_api.app import AtlasVaultBackend, create_app
from test_atlasvault_backend_c13 import (
    ACCOUNT_A, _bootstrap, _session, _signed_transition,
)
from test_atlasvault_backend_c14 import _opaque_envelope
from vaultsync.authenticated_state_view import _message, _root, registry_root
from vaultsync.device_identity import device_identity_from_private_keys
from vaultsync.epoch_rotation import create_epoch_rotation
from vaultsync.revocation import RevocationRegistry, _message as removal_message, _root as removal_root

VAULT = "vault-c26"


def environment(tmp_path, backend=None):
    devices = [device_identity_from_private_keys(
        signing_private_seed=bytes([10+i])*32,
        agreement_private_key=bytes([20+i])*32,
        created_at="2026-01-01T00:00:00Z", key_epoch=3,
    ) for i in range(3)]
    backend = backend or AtlasVaultBackend(commitments_path=tmp_path / "backend.sqlite")
    http = TestClient(create_app(backend))
    first = _bootstrap(http, devices[0])
    tokens = [_session(http, devices[0])[0]]
    parent = first["revision"]
    for i in (1, 2):
        transition = _signed_transition(account_id=ACCOUNT_A,
            revision=f"10000000-0000-4000-8000-{i+1:012d}", parent_revision=parent,
            device=devices[i], signer=devices[0])
        result = http.post(f"/v1/accounts/{ACCOUNT_A}/devices", json=transition,
            headers={"Authorization": "Bearer " + tokens[0]})
        assert result.status_code == 201
        parent = result.json()["revision"]
        tokens.append(_session(http, devices[i])[0])
    entries = []
    for device in devices:
        descriptor = device.sign_descriptor().to_dict()["descriptor"]
        entries.append({"device_id": hashlib.sha256(device.device_id.encode()).hexdigest(),
            "descriptor_sha256": hashlib.sha256(json.dumps(descriptor, sort_keys=True, separators=(",", ":")).encode()).hexdigest()})
    unsigned = dict(format="atlasvault-authenticated-state-view", version=2,
        account_id=ACCOUNT_A, vault_id=VAULT, sequence=1, previous_root="0"*64,
        collection_root="cd"*32, registry_root=registry_root(entries),
        previous_registry_root=registry_root([]), key_epoch=3)
    root = _root(unsigned)
    view = dict(unsigned, root=root, signature_b64=base64.b64encode(devices[0].sign(_message(root))).decode())
    headers = [{"Authorization": "Bearer " + t} for t in tokens]
    assert http.post(f"/v1/vaults/{VAULT}/commitments", json=view, headers=headers[0]).status_code == 200
    registry = [{"device_id": d.device_id, "signing_public_b64": base64.b64encode(d.signing_public_key).decode(),
        "agreement_public_b64": base64.b64encode(d.agreement_public_key).decode(), "state": "ACTIVE"} for d in devices]
    removal = RevocationRegistry(tmp_path / "removal", bytes([50])*32, ACCOUNT_A, VAULT, 3, registry, root)
    removal.initialize()
    proposal = removal.prepare(devices[2].device_id, devices[0].device_id)
    digest = removal_root(proposal)
    transition = dict(proposal, root=digest, signature_b64=base64.b64encode(devices[0].sign(removal_message(digest))).decode())
    removal.commit(transition)
    proof = create_epoch_rotation(transition, registry=registry, state_root=root, signing_key=devices[0])
    return backend, http, devices, headers, proof, view


def test_activation_is_durable_idempotent_and_fences_old_writes(tmp_path):
    backend, http, devices, headers, proof, _ = environment(tmp_path)
    route = f"/v1/vaults/{VAULT}/activations"
    assert http.post(route, json=proof).status_code == 401
    accepted = http.post(route, json=proof, headers=headers[0])
    assert accepted.status_code == 200
    record = accepted.json()
    assert record["status"] == "ACTIVATION_ACCEPTED"
    assert record["proof"]["plan"]["new_epoch"] == 4
    assert http.post(route, json=proof, headers=headers[0]).json() == record
    assert http.get(route, headers=headers[1]).json() == record
    assert http.get(route, headers=headers[2]).status_code == 403
    assert devices[2].device_id not in proof["plan"]["recipients"]
    for epoch, expected in ((3, 409), (4, 201)):
        envelope = _opaque_envelope(object_id="patch", revision="r1", parent_revision=None, payload=b"synthetic-ciphertext")
        envelope["key_epoch"] = epoch
        result = http.post(f"/v1/vaults/{VAULT}/patches", json=envelope,
            headers=dict(headers[0], **{"If-Match": "*", "Idempotency-Key": "patch"}))
        assert result.status_code == expected
    old = _opaque_envelope(object_id="offline", revision="r1", parent_revision=None, payload=b"synthetic-ciphertext")
    old["key_epoch"] = 4
    assert http.post(f"/v1/vaults/{VAULT}/patches", json=old,
        headers=dict(headers[2], **{"If-Match": "*", "Idempotency-Key": "offline"})).status_code == 403
    reopened = AtlasVaultBackend(commitments_path=tmp_path / "backend.sqlite")
    assert reopened.commitments.activation(ACCOUNT_A, VAULT) == record


def test_concurrent_conflicting_activations_have_exactly_one_winner(tmp_path):
    backend, http, devices, headers, proof, view = environment(tmp_path)
    other = create_epoch_rotation(proof["revocation"], registry=proof["registry"], state_root=view["root"], signing_key=devices[0])
    barrier = threading.Barrier(2)
    def append(p):
        barrier.wait()
        return http.post(f"/v1/vaults/{VAULT}/activations", json=p, headers=headers[0]).status_code
    with ThreadPoolExecutor(2) as pool:
        results = [pool.submit(append, p) for p in (proof, other)]
        assert sorted(f.result() for f in results) == [200, 409]
    assert backend.commitments.activation(ACCOUNT_A, VAULT)["proof"]["root"] in (proof["root"], other["root"])


@pytest.mark.parametrize("attack", ["account_id", "vault_id", "previous_epoch", "new_epoch", "prior_registry_root", "resulting_registry_root", "state_root", "recipients", "version"])
def test_substitution_rejected_without_publication(tmp_path, attack):
    backend, http, _, headers, proof, _ = environment(tmp_path)
    bad = copy.deepcopy(proof)
    value = bad["plan"][attack]
    bad["plan"][attack] = [] if isinstance(value, list) else value+1 if type(value) is int else "wrong"
    result = http.post(f"/v1/vaults/{VAULT}/activations", json=bad, headers=headers[0])
    assert result.status_code in (409, 422)
    assert backend.commitments.activation(ACCOUNT_A, VAULT) is None
