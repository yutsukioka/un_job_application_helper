"""Selective ciphertext delivery through the actual authenticated API."""
import copy
from vaultsync.device_delivery import create_device_delivery
from vaultsync.revocation import verify_transition
from test_atlasvault_activation_c26 import environment, VAULT


def test_legacy_fenced_then_only_own_proof_returned(tmp_path):
    backend,http,devices,headers,proof,_=environment(tmp_path)
    route=f"/v1/vaults/{VAULT}/activations"
    accepted=http.post(route,json=proof,headers=headers[0])
    assert accepted.status_code==200
    record=backend.commitments.activation(proof["plan"]["account_id"],VAULT)
    before=copy.deepcopy(record)
    response=http.get(route,headers=headers[1])
    assert response.status_code==423, "aggregate v1 must not disclose another recipient wrapper"
    packets=[]
    for device in devices[:2]:
        packet=create_device_delivery(record,recipient_device_id=device.device_id,
            issuer_device_id=devices[0].device_id,signing_key=devices[0],
            current_registry=verify_transition(proof["revocation"],proof["registry"]),recovery_pending=False)
        packets.append(packet)
        post=f"{route}/4/delivery-proofs"
        assert http.post(post,json=packet,headers=headers[0]).status_code==200
        assert http.post(post,json=packet,headers=headers[0]).status_code==200
    for i in range(2):
        response=http.get(f"{route}/4/delivery",headers=headers[i])
        assert response.status_code==200
        assert response.json()==packets[i]
        other=packets[1-i]["wrapper"]["ciphertext_b64"]
        assert other not in response.text
        assert http.get(route,headers=headers[i]).json()==packets[i]
    assert http.get(f"{route}/4/delivery",headers=headers[2]).status_code==403
    assert http.get(f"{route}/4/delivery").status_code==401
    assert backend.commitments.activation(proof["plan"]["account_id"],VAULT)==before
    bad=copy.deepcopy(packets[1])
    bad["wrapper"]=packets[0]["wrapper"]
    assert http.post(f"{route}/4/delivery-proofs",json=bad,headers=headers[0]).status_code in (409,422)
    assert http.post(f"{route}/4/delivery-proofs",json=packets[1],headers=headers[2]).status_code==403
