"""C27/D089 recipient proofs: unchanged synthetic v1/HPKE bytes, new signatures."""

import copy
import hashlib
import json
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from vaultsync.device_delivery import create_device_delivery, verify_device_delivery
from vaultsync.epoch_rotation import RotationError, verify_epoch_rotation
from vaultsync.revocation import verify_transition

ROOT = Path(__file__).resolve().parents[3]


def vector():
    return json.loads((ROOT / "contracts/sync/test_vectors/atlasvault_device_delivery_v2.json").read_text())


def original():
    return json.loads((ROOT / "contracts/sync/test_vectors/atlasvault_activation_v1.json").read_text())["record"]


def verify(packet, **changes):
    source = original()["proof"]
    plan = source["plan"]
    context = dict(registry=source["registry"], account_id=plan["account_id"],
                   vault_id=plan["vault_id"], previous_epoch=plan["previous_epoch"],
                   state_root=plan["state_root"], activation_id=source["root"],
                   recipient_device_id=vector()["recipient_device_id"])
    context.update(changes)
    return verify_device_delivery(packet, **context)


def test_v2_shared_signature_preserves_wrapper_and_v1_bytes():
    source = original()
    p = source["proof"]
    expected = vector()
    result = verify(expected["packet"])
    assert result["new_epoch"] == 4
    created = create_device_delivery(source, recipient_device_id=expected["recipient_device_id"],
        issuer_device_id=p["rotation_signer_device_id"], signing_key=Ed25519PrivateKey.from_private_bytes(bytes([10])*32),
        current_registry=verify_transition(p["revocation"], p["registry"]), recovery_pending=False)
    assert created == expected["packet"]
    assert created["proof"]["rotation_signer_device_id"] == p["rotation_signer_device_id"]
    assert created["wrapper"] == next(d for d in p["deliveries"] if d["device_id"] == expected["recipient_device_id"])
    assert len(created["proof"]["plan"]["recipients"]) == 2
    assert "deliveries" not in created["proof"]
    unsigned = {k:v for k,v in created["proof"].items() if k not in ("root","signature_b64")}
    assert hashlib.sha256(json.dumps(unsigned,sort_keys=True,separators=(",",":")).encode()).hexdigest() == expected["canonical_sha256"]
    verify_epoch_rotation(p,registry=p["registry"], account_id=p["plan"]["account_id"],
        vault_id=p["plan"]["vault_id"], previous_epoch=3,state_root=p["plan"]["state_root"])
    reduced=copy.deepcopy(p)
    reduced["deliveries"]=reduced["deliveries"][:1]
    with pytest.raises(RotationError):
        verify_epoch_rotation(reduced,registry=p["registry"],account_id=p["plan"]["account_id"],
            vault_id=p["plan"]["vault_id"],previous_epoch=3,state_root=p["plan"]["state_root"])


@pytest.mark.parametrize("field", ["activation_id","recipient_device_id","recipient_agreement_sha256",
    "wrapper_sha256","issuer_device_id","rotation_signer_device_id","registry_generation","hpke_suite","hpke_version",
    "signature_algorithm","signature_version","version","root","signature_b64"])
def test_authenticated_field_substitution_fails_closed(field):
    packet=copy.deepcopy(vector()["packet"])
    packet["proof"][field]="substituted"
    with pytest.raises(RotationError): verify(packet)


@pytest.mark.parametrize("field",["account_id","vault_id","previous_epoch","new_epoch",
    "prior_registry_root","resulting_registry_root","state_root","recipients","revocation_root"])
def test_plan_substitution_fails_closed(field):
    packet=copy.deepcopy(vector()["packet"])
    packet["proof"]["plan"][field]="substituted"
    with pytest.raises(RotationError): verify(packet)


def test_foreign_wrapper_wrong_context_and_v1_only_are_rejected():
    packet=copy.deepcopy(vector()["packet"])
    packet["wrapper"]=next(d for d in original()["proof"]["deliveries"] if d["device_id"] != vector()["recipient_device_id"])
    with pytest.raises(RotationError): verify(packet)
    with pytest.raises(RotationError,match="ATLAS_PER_DEVICE_PROOF_REQUIRED"): verify(original())
    for context in ({"account_id":"other"},{"vault_id":"other"},{"previous_epoch":4},
                    {"state_root":"ab"*32},{"activation_id":"ab"*32},{"recipient_device_id":"other"}):
        with pytest.raises(RotationError): verify(vector()["packet"],**context)


def test_revoked_or_recovery_pending_issuer_cannot_upgrade():
    source=original()
    p=source["proof"]
    after=verify_transition(p["revocation"],p["registry"])
    args=dict(recipient_device_id=vector()["recipient_device_id"],issuer_device_id=p["rotation_signer_device_id"],
        signing_key=Ed25519PrivateKey.from_private_bytes(bytes([10])*32),current_registry=after)
    with pytest.raises(RotationError): create_device_delivery(source,**args,recovery_pending=True)
    after=copy.deepcopy(after)
    for e in after:
        if e["device_id"] == args["issuer_device_id"]: e["state"]="REVOKED"
    args["current_registry"]=after
    with pytest.raises(RotationError): create_device_delivery(source,**args,recovery_pending=False)


def test_v2_schema():
    import jsonschema
    jsonschema.validate(vector()["packet"],json.loads((ROOT/"contracts/sync/atlasvault_device_delivery_v2.schema.json").read_text()))
