"""D087 activation through authenticated HTTP; synthetic identities only."""

import base64
import copy
import hashlib
import json
import threading
import os
import signal
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from atlasvault_api.app import AtlasVaultBackend, create_app
from test_atlasvault_backend_c13 import (
    ACCOUNT_A,
    _bootstrap,
    _session,
    _signed_transition,
)
from test_atlasvault_backend_c14 import _opaque_envelope
from vaultsync.authenticated_state_view import (
    EMPTY_REGISTRY,
    _message,
    _root,
    registry_root,
)
from vaultsync.device_identity import device_identity_from_private_keys
from vaultsync.epoch_rotation import create_epoch_rotation
from vaultsync.revocation import (
    RevocationRegistry,
    _message as removal_message,
    _root as removal_root,
)
from vaultsync.sync_queue import SignedStateCommitment

VAULT = "vault-c26"


def initial_body():
    return json.dumps(
        dict(
            format="atlasvault-guarded-collection", version=1, route="patch", records=[]
        ),
        sort_keys=True,
        separators=(",", ":"),
    ).encode()


def initial_collection(signer):
    return SignedStateCommitment.sign(
        initial_body(),
        collection_id="collection-c26",
        sequence=1,
        previous_root="0" * 64,
        signing_key=signer,
    )


def history_registry(devices):
    entries = []
    for device in devices:
        descriptor = device.sign_descriptor().to_dict()["descriptor"]
        entries.append(
            {
                "device_id": hashlib.sha256(device.device_id.encode()).hexdigest(),
                "descriptor_sha256": hashlib.sha256(
                    json.dumps(
                        descriptor, sort_keys=True, separators=(",", ":")
                    ).encode()
                ).hexdigest(),
            }
        )
    return entries


def environment(tmp_path, backend=None):
    devices = [
        device_identity_from_private_keys(
            signing_private_seed=bytes([10 + i]) * 32,
            agreement_private_key=bytes([20 + i]) * 32,
            created_at="2026-01-01T00:00:00Z",
            key_epoch=3,
        )
        for i in range(3)
    ]
    backend = backend or AtlasVaultBackend(commitments_path=tmp_path / "backend.sqlite")
    http = TestClient(create_app(backend))
    first = _bootstrap(http, devices[0])
    tokens = [_session(http, devices[0])[0]]
    parent = first["revision"]
    for i in (1, 2):
        transition = _signed_transition(
            account_id=ACCOUNT_A,
            revision=f"10000000-0000-4000-8000-{i + 1:012d}",
            parent_revision=parent,
            device=devices[i],
            signer=devices[0],
        )
        result = http.post(
            f"/v1/accounts/{ACCOUNT_A}/devices",
            json=transition,
            headers={"Authorization": "Bearer " + tokens[0]},
        )
        assert result.status_code == 200
        parent = result.json()["revision"]
        tokens.append(_session(http, devices[i])[0])
    entries = []
    for device in devices:
        descriptor = device.sign_descriptor().to_dict()["descriptor"]
        entries.append(
            {
                "device_id": hashlib.sha256(device.device_id.encode()).hexdigest(),
                "descriptor_sha256": hashlib.sha256(
                    json.dumps(
                        descriptor, sort_keys=True, separators=(",", ":")
                    ).encode()
                ).hexdigest(),
            }
        )
    unsigned = dict(
        format="atlasvault-authenticated-state-view",
        version=2,
        account_id=ACCOUNT_A,
        vault_id=VAULT,
        sequence=1,
        previous_root="0" * 64,
        collection_root=initial_collection(devices[0]).root,
        registry_root=registry_root(entries),
        previous_registry_root=EMPTY_REGISTRY,
        key_epoch=3,
    )
    root = _root(unsigned)
    view = dict(
        unsigned,
        root=root,
        signature_b64=base64.b64encode(devices[0].sign(_message(root))).decode(),
    )
    headers = [{"Authorization": "Bearer " + t} for t in tokens]
    existing = backend.commitments.activation(ACCOUNT_A, VAULT)
    if existing:
        return backend, http, devices, headers, existing["proof"], view
    assert (
        http.post(
            f"/v1/vaults/{VAULT}/commitments", json=view, headers=headers[0]
        ).status_code
        == 200
    )
    registry = [
        {
            "device_id": d.device_id,
            "signing_public_b64": base64.b64encode(d.signing_public_key).decode(),
            "agreement_public_b64": base64.b64encode(d.agreement_public_key).decode(),
            "state": "ACTIVE",
        }
        for d in devices
    ]
    removal = RevocationRegistry(
        tmp_path / "removal", bytes([50]) * 32, ACCOUNT_A, VAULT, 3, registry, root
    )
    removal.initialize()
    proposal = removal.prepare(devices[2].device_id, devices[0].device_id)
    digest = removal_root(proposal)
    transition = dict(
        proposal,
        root=digest,
        signature_b64=base64.b64encode(
            devices[0].sign(removal_message(digest))
        ).decode(),
    )
    removal.commit(transition)
    proof = create_epoch_rotation(
        transition, registry=registry, state_root=root, signing_key=devices[0]
    )
    return backend, http, devices, headers, proof, view


def test_activation_is_durable_idempotent_and_fences_old_writes(tmp_path):
    backend, http, devices, headers, proof, _ = environment(tmp_path)
    route = f"/v1/vaults/{VAULT}/activations"
    assert http.post(route, json=proof).status_code == 401
    accepted = http.post(route, json=proof, headers=headers[0])
    assert accepted.status_code == 200
    receipt = accepted.json()
    record = backend.commitments.activation(ACCOUNT_A, VAULT)
    assert receipt["status"] == "ACTIVATION_ACCEPTED"
    assert receipt["transition_id"] == record["transition_id"]
    assert receipt["key_epoch"] == 4
    assert record["proof"]["plan"]["new_epoch"] == 4
    assert http.post(route, json=proof, headers=headers[0]).json() == receipt
    # D089 preserves aggregate verification, but forbids aggregate disclosure.
    assert http.get(route, headers=headers[1]).status_code == 423
    assert http.get(route, headers=headers[2]).status_code == 403
    assert devices[2].device_id not in proof["plan"]["recipients"]
    for epoch, expected in ((3, 409), (4, 201)):
        envelope = _opaque_envelope(
            object_id="patch",
            revision="r1",
            parent_revision=None,
            payload=b"synthetic-ciphertext",
        )
        envelope["key_epoch"] = epoch
        result = http.post(
            f"/v1/vaults/{VAULT}/patches",
            json=envelope,
            headers=dict(headers[0], **{"If-Match": "*", "Idempotency-Key": "patch"}),
        )
        assert result.status_code == expected
    old = _opaque_envelope(
        object_id="offline",
        revision="r1",
        parent_revision=None,
        payload=b"synthetic-ciphertext",
    )
    old["key_epoch"] = 4
    assert (
        http.post(
            f"/v1/vaults/{VAULT}/patches",
            json=old,
            headers=dict(headers[2], **{"If-Match": "*", "Idempotency-Key": "offline"}),
        ).status_code
        == 403
    )
    reopened = AtlasVaultBackend(commitments_path=tmp_path / "backend.sqlite")
    assert reopened.commitments.activation(ACCOUNT_A, VAULT) == record


def test_ephemeral_backend_cannot_claim_durable_activation(tmp_path):
    backend, http, _, headers, proof, _ = environment(tmp_path, AtlasVaultBackend())
    response = http.post(
        f"/v1/vaults/{VAULT}/activations", json=proof, headers=headers[0]
    )
    assert response.status_code == 503
    assert backend.commitments.activation(ACCOUNT_A, VAULT) is None


def test_activation_errors_and_telemetry_are_secret_free(tmp_path, caplog):
    import logging

    backend, http, _, headers, proof, _ = environment(tmp_path)
    caplog.set_level(logging.INFO, logger="atlasvault_api.security")
    bad = copy.deepcopy(proof)
    bad["passphrase"] = "SYNTHETIC_FORBIDDEN_FIELD"
    response = http.post(
        f"/v1/vaults/{VAULT}/activations", json=bad, headers=headers[0]
    )
    assert response.status_code == 422
    rejected = response.text
    assert (
        http.post(
            f"/v1/vaults/{VAULT}/activations", json=proof, headers=headers[0]
        ).status_code
        == 200
    )
    response = http.get(f"/v1/vaults/{VAULT}/activations", headers=headers[2])
    assert response.json() == {"detail": "ATLAS_DEVICE_REVOKED"}
    observed = (
        caplog.text
        + rejected
        + response.text
        + json.dumps(backend.telemetry.snapshot())
    )
    for value in (
        "SYNTHETIC_FORBIDDEN_FIELD",
        headers[0]["Authorization"].split()[1],
        proof["root"],
        proof["deliveries"][0]["ciphertext_b64"],
    ):
        assert value not in observed


def test_concurrent_conflicting_activations_have_exactly_one_winner(tmp_path):
    backend, http, devices, headers, proof, view = environment(tmp_path)
    other = create_epoch_rotation(
        proof["revocation"],
        registry=proof["registry"],
        state_root=view["root"],
        signing_key=devices[0],
    )
    barrier = threading.Barrier(2)

    def append(p):
        barrier.wait()
        return http.post(
            f"/v1/vaults/{VAULT}/activations", json=p, headers=headers[0]
        ).status_code

    with ThreadPoolExecutor(2) as pool:
        results = [pool.submit(append, p) for p in (proof, other)]
        assert sorted(f.result() for f in results) == [200, 409]
    assert backend.commitments.activation(ACCOUNT_A, VAULT)["proof"]["root"] in (
        proof["root"],
        other["root"],
    )


@pytest.mark.parametrize(
    "attack",
    [
        "account_id",
        "vault_id",
        "previous_epoch",
        "new_epoch",
        "prior_registry_root",
        "resulting_registry_root",
        "state_root",
        "recipients",
        "version",
    ],
)
def test_substitution_rejected_without_publication(tmp_path, attack):
    backend, http, _, headers, proof, _ = environment(tmp_path)
    bad = copy.deepcopy(proof)
    value = bad["plan"][attack]
    bad["plan"][attack] = (
        [] if isinstance(value, list) else value + 1 if type(value) is int else "wrong"
    )
    result = http.post(f"/v1/vaults/{VAULT}/activations", json=bad, headers=headers[0])
    assert result.status_code in (409, 422)
    assert backend.commitments.activation(ACCOUNT_A, VAULT) is None


@pytest.mark.parametrize(
    "attack",
    [
        "revoked",
        "omitted",
        "duplicate",
        "unknown",
        "signer",
        "generation",
        "wrong_state",
    ],
)
def test_validly_signed_bad_activation_is_rejected(tmp_path, attack):
    from vaultsync.epoch_rotation import _rotation_root, _rotation_message

    backend, http, devices, headers, proof, _ = environment(tmp_path)
    bad = copy.deepcopy(proof)
    if attack == "revoked":
        bad["plan"]["recipients"].append(devices[2].device_id)
    elif attack == "omitted":
        bad["plan"]["recipients"].pop()
        bad["deliveries"].pop()
    elif attack == "duplicate":
        bad["plan"]["recipients"].append(bad["plan"]["recipients"][0])
        bad["deliveries"].append(bad["deliveries"][0])
    elif attack == "unknown":
        bad["plan"]["recipients"][0] = "unknown-device"
    elif attack == "signer":
        bad["rotation_signer_device_id"] = devices[2].device_id
    elif attack == "generation":
        bad["revocation"]["sequence"] = 2
    else:
        bad["plan"]["state_root"] = "de" * 32
    unsigned = {k: v for k, v in bad.items() if k not in ("root", "signature_b64")}
    bad["root"] = _rotation_root(unsigned)
    bad["signature_b64"] = base64.b64encode(
        devices[2 if attack == "signer" else 0].sign(_rotation_message(bad["root"]))
    ).decode()
    before = backend.commitments.read(ACCOUNT_A, VAULT)
    result = http.post(f"/v1/vaults/{VAULT}/activations", json=bad, headers=headers[0])
    assert result.status_code in (409, 422)
    assert backend.commitments.activation(ACCOUNT_A, VAULT) is None
    assert backend.commitments.read(ACCOUNT_A, VAULT) == before


def test_offline_revoked_attacks_do_not_advance_state_after_backend_restart(tmp_path):
    backend, http, _, headers, proof, old_view = environment(tmp_path)
    response = http.post(
        f"/v1/vaults/{VAULT}/activations", json=proof, headers=headers[0]
    )
    assert response.status_code == 200
    record = backend.commitments.activation(ACCOUNT_A, VAULT)
    reopened, client, _, new_headers, _, _ = environment(
        tmp_path, AtlasVaultBackend(commitments_path=tmp_path / "backend.sqlite")
    )
    before = reopened.commitments.read(ACCOUNT_A, VAULT)
    for token in (new_headers[0], new_headers[2]):
        for kind in ("patches", "snapshots"):
            envelope = _opaque_envelope(
                object_id=kind,
                revision="r1",
                parent_revision=None,
                payload=b"synthetic-ciphertext",
            )
            envelope["key_epoch"] = 3
            response = client.request(
                "PUT" if kind == "snapshots" else "POST",
                f"/v1/vaults/{VAULT}/{kind}",
                json=envelope,
                headers=dict(token, **{"If-Match": "*", "Idempotency-Key": kind}),
            )
            assert response.status_code in (403, 409)
        assert client.post(
            f"/v1/vaults/{VAULT}/commitments", json=old_view, headers=token
        ).status_code in (403, 409)
    assert (
        client.post(
            f"/v1/vaults/{VAULT}/activations", json=proof, headers=new_headers[2]
        ).status_code
        == 403
    )
    assert (
        client.get(
            f"/v1/vaults/{VAULT}/activations", headers=new_headers[2]
        ).status_code
        == 403
    )
    assert reopened.commitments.read(ACCOUNT_A, VAULT) == before
    assert reopened.commitments.activation(ACCOUNT_A, VAULT) == record
    assert (
        client.get(f"/v1/vaults/{VAULT}/patches", headers=new_headers[0]).json()[
            "objects"
        ]
        == []
    )


@pytest.mark.parametrize("stage", ["before_backend_commit", "after_backend_commit"])
def test_backend_global_commit_survives_sigkill(tmp_path, stage):
    code = """
import sys,time
from pathlib import Path
from test_atlasvault_activation_c26 import environment,VAULT
root=Path(sys.argv[1]);stage=sys.argv[2]
backend,http,devices,headers,proof,view=environment(root)
def stop():
 (root/'ready').write_text(stage)
 while True: time.sleep(1)
def trace(sql):
 if sql=='COMMIT' and stage=='before_backend_commit':stop()
backend.commitments._db.set_trace_callback(trace)
response=http.post(f'/v1/vaults/{VAULT}/activations',json=proof,headers=headers[0])
assert response.status_code==200
stop()
"""
    child = subprocess.Popen(
        [sys.executable, "-S", "-c", code, str(tmp_path), stage],
        env=dict(os.environ, PYTHONPATH=os.pathsep.join(sys.path)),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.monotonic() + 20
        while (
            not (tmp_path / "ready").exists()
            and child.poll() is None
            and time.monotonic() < deadline
        ):
            time.sleep(0.02)
        assert (tmp_path / "ready").exists()
        child.kill()
        assert child.wait(timeout=10) == -signal.SIGKILL
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=10)
    reopened = AtlasVaultBackend(commitments_path=tmp_path / "backend.sqlite")
    record = reopened.commitments.activation(ACCOUNT_A, VAULT)
    assert (record is None) == (stage == "before_backend_commit")
    if record:
        assert record["proof"]["plan"]["new_epoch"] == 4
        assert len(record["proof"]["deliveries"]) == 2
        assert (
            reopened.commitments.read(ACCOUNT_A, VAULT)[-1]["root"]
            == record["proof"]["plan"]["state_root"]
        )
    print(
        f"C26 D087 backend SIGKILL stage={stage} pid={child.pid} epoch={4 if record else 3}"
    )
