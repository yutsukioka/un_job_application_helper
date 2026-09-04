"""Selective ciphertext delivery through the actual authenticated API."""

import copy
import json
from vaultsync.device_delivery import create_device_delivery
from vaultsync.revocation import verify_transition
from test_atlasvault_activation_c26 import (
    VAULT,
    activation_packets,
    activation_record,
    environment,
)
from atlasvault_api.app import AtlasVaultBackend


def test_legacy_fenced_then_only_own_proof_returned(tmp_path):
    backend, http, devices, headers, proof, _ = environment(tmp_path, stage=False)
    route = f"/v1/vaults/{VAULT}/activations"
    record = activation_record(proof)
    body = json.dumps(record, sort_keys=True, separators=(",", ":"))
    backend.commitments._db.execute(
        "INSERT INTO activations VALUES(?,?,?,?,?)",
        (proof["plan"]["account_id"], VAULT, 4, proof["root"], body),
    )
    record = backend.commitments.activation(proof["plan"]["account_id"], VAULT)
    before = copy.deepcopy(record)
    response = http.get(route, headers=headers[1])
    assert response.status_code == 423, (
        "aggregate v1 must not disclose another recipient wrapper"
    )
    packets = []
    for device in devices[:2]:
        packet = create_device_delivery(
            record,
            recipient_device_id=device.device_id,
            issuer_device_id=devices[0].device_id,
            signing_key=devices[0],
            current_registry=verify_transition(proof["revocation"], proof["registry"]),
            recovery_pending=False,
        )
        packets.append(packet)
        post = f"{route}/4/delivery-proofs"
        assert http.post(post, json=packet, headers=headers[0]).status_code == 200
        assert http.post(post, json=packet, headers=headers[0]).status_code == 200
    for i in range(2):
        response = http.get(f"{route}/4/delivery", headers=headers[i])
        assert response.status_code == 200
        assert response.json() == packets[i]
        other = packets[1 - i]["wrapper"]["ciphertext_b64"]
        assert other not in response.text
        assert http.get(route, headers=headers[i]).json() == packets[i]
    assert http.get(f"{route}/4/delivery", headers=headers[2]).status_code == 403
    assert http.get(f"{route}/4/delivery").status_code == 401
    assert backend.commitments.activation(proof["plan"]["account_id"], VAULT) == before
    bad = copy.deepcopy(packets[1])
    bad["wrapper"] = packets[0]["wrapper"]
    assert http.post(
        f"{route}/4/delivery-proofs", json=bad, headers=headers[0]
    ).status_code in (409, 422)
    assert (
        http.post(
            f"{route}/4/delivery-proofs", json=packets[1], headers=headers[2]
        ).status_code
        == 403
    )
    reopened, client, _, new_headers, _, _ = environment(
        tmp_path, AtlasVaultBackend(commitments_path=tmp_path / "backend.sqlite")
    )
    for i in range(2):
        reply = client.get(f"{route}/4/delivery", headers=new_headers[i])
        assert reply.status_code == 200 and reply.json() == packets[i]
        assert packets[1 - i]["wrapper"]["ciphertext_b64"] not in reply.text
    prior = reopened.commitments.read(proof["plan"]["account_id"], VAULT)
    for bad_route in (
        f"{route}/3/delivery",
        f"{route}/5/delivery",
        "/v1/vaults/foreign/activations/4/delivery",
    ):
        assert client.get(bad_route, headers=new_headers[1]).status_code in (404, 423)
    assert client.get(f"{route}/4/delivery", headers=new_headers[2]).status_code == 403
    assert reopened.commitments.read(proof["plan"]["account_id"], VAULT) == prior


def test_activation_requires_every_selective_delivery_before_commit(tmp_path):
    backend, http, devices, headers, proof, _ = environment(tmp_path, stage=False)
    packets = activation_packets(proof, devices)
    route = f"/v1/vaults/{VAULT}/activations"
    post = f"{route}/4/delivery-proofs"
    assert http.post(post, json=packets[0], headers=headers[0]).status_code == 200
    assert http.post(route, json=proof, headers=headers[0]).status_code == 409
    assert backend.commitments.activation(proof["plan"]["account_id"], VAULT) is None
    assert http.post(post, json=packets[1], headers=headers[0]).status_code == 200
    assert http.post(route, json=proof, headers=headers[0]).status_code == 200
    for index in range(2):
        response = http.get(f"{route}/4/delivery", headers=headers[index])
        assert response.status_code == 200
        assert response.json() == packets[index]
