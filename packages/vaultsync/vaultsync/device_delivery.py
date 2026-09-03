"""D089 signed projections of existing wrappers. No HPKE or v1 format changes."""

import base64
import copy
import hashlib

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from .epoch_rotation import RotationError, _canonical, _reject, verify_epoch_rotation
from .revocation import _decode, _exact, registry_root, validate_rotation_plan, verify_transition

SUITE = "0x0020/0x0001/0x0002"
FIELDS = {
    "format",
    "version",
    "activation_id",
    "plan",
    "revocation",
    "registry",
    "recipient_device_id",
    "recipient_agreement_sha256",
    "wrapper_sha256",
    "registry_generation",
    "issuer_device_id",
    "hpke_suite",
    "hpke_version",
    "signature_algorithm",
    "signature_version",
    "root",
    "signature_b64",
    "rotation_signer_device_id",
}


def _digest(value):
    return hashlib.sha256(value).hexdigest()


def _unsigned(proof):
    return {k: v for k, v in proof.items() if k not in ("root", "signature_b64")}


def _root(proof):
    return _digest(b"atlasvault-device-delivery-proof-v2\n" + _canonical(_unsigned(proof)))


def _message(root):
    return b"atlasvault-device-delivery-signature-v2\0" + bytes.fromhex(root)


def _hex(value):
    if (
        type(value) is not str
        or len(value) != 64
        or any(c not in "0123456789abcdef" for c in value)
    ):
        _reject()
    return value


def create_device_delivery(
    record,
    *,
    recipient_device_id,
    issuer_device_id,
    signing_key,
    current_registry,
    recovery_pending,
):
    """An ACTIVE client attests bytes it already holds; the server never signs.

    current_registry is the caller's durably authenticated current registry, not
    an unverified server list. Fresh removal authorization is not reused here:
    this attests an already sealed wrapper and creates no key or revocation.
    """
    try:
        _exact(record, {"format", "version", "status", "transition_id", "proof"})
        old = record["proof"]
        plan = old["plan"]
        if (
            recovery_pending is not False
            or record["format"] != "atlasvault-activation-record"
            or type(record["version"]) is not int
            or record["version"] != 1
            or record["status"] != "ACTIVATION_ACCEPTED"
            or record["transition_id"] != old["root"]
        ):
            _reject()
        result = verify_epoch_rotation(
            old,
            registry=old["registry"],
            account_id=plan["account_id"],
            vault_id=plan["vault_id"],
            previous_epoch=plan["previous_epoch"],
            state_root=plan["state_root"],
        )
        registry_root(current_registry)
        for device in (issuer_device_id, recipient_device_id):
            historic = next(
                e for e in result["registry"] if e["device_id"] == device and e["state"] == "ACTIVE"
            )
            current = next(
                e for e in current_registry if e["device_id"] == device and e["state"] == "ACTIVE"
            )
            if historic != current:
                _reject()
        wrapper = next(d for d in old["deliveries"] if d["device_id"] == recipient_device_id)
        recipient = next(e for e in result["registry"] if e["device_id"] == recipient_device_id)
        proof = dict(
            format="atlasvault-device-delivery-proof",
            version=2,
            activation_id=old["root"],
            plan=copy.deepcopy(plan),
            revocation=copy.deepcopy(old["revocation"]),
            registry=copy.deepcopy(old["registry"]),
            recipient_device_id=recipient_device_id,
            recipient_agreement_sha256=_digest(_decode(recipient["agreement_public_b64"], 32)),
            wrapper_sha256=_digest(_canonical(wrapper)),
            registry_generation=plan["new_epoch"],
            issuer_device_id=issuer_device_id,
            rotation_signer_device_id=old["rotation_signer_device_id"],
            hpke_suite=SUITE,
            hpke_version=2,
            signature_algorithm="Ed25519",
            signature_version=1,
        )
        proof["root"] = _root(proof)
        proof["signature_b64"] = base64.b64encode(signing_key.sign(_message(proof["root"]))).decode(
            "ascii"
        )
        packet = dict(proof=proof, wrapper=copy.deepcopy(wrapper))
        verify_device_delivery(
            packet,
            registry=old["registry"],
            account_id=plan["account_id"],
            vault_id=plan["vault_id"],
            previous_epoch=plan["previous_epoch"],
            state_root=plan["state_root"],
            activation_id=old["root"],
            recipient_device_id=recipient_device_id,
        )
        return packet
    except Exception:
        raise RotationError("ATLAS_DEVICE_DELIVERY_REJECTED") from None


def verify_device_delivery(
    packet,
    *,
    registry,
    account_id,
    vault_id,
    previous_epoch,
    state_root,
    activation_id,
    recipient_device_id,
):
    try:
        if type(packet) is dict and packet.get("format") in (
            "atlasvault-activation-record",
            "atlasvault-epoch-rotation",
        ):
            _reject("ATLAS_PER_DEVICE_PROOF_REQUIRED")
        _exact(packet, {"proof", "wrapper"})
        p, w = packet["proof"], packet["wrapper"]
        _exact(p, FIELDS)
        if (
            p["format"] != "atlasvault-device-delivery-proof"
            or type(p["version"]) is not int
            or p["version"] != 2
            or p["hpke_suite"] != SUITE
            or type(p["hpke_version"]) is not int
            or p["hpke_version"] != 2
            or p["signature_algorithm"] != "Ed25519"
            or type(p["signature_version"]) is not int
            or p["signature_version"] != 1
            or p["activation_id"] != _hex(activation_id)
            or p["recipient_device_id"] != recipient_device_id
            or registry_root(p["registry"]) != registry_root(registry)
        ):
            _reject()
        after = verify_transition(p["revocation"], registry)
        t, plan = p["revocation"], p["plan"]
        if (
            t["account_id"] != account_id
            or t["vault_id"] != vault_id
            or type(previous_epoch) is not int
            or t["key_epoch"] != previous_epoch
            or t["sequence"] != 1
        ):
            _reject()
        validate_rotation_plan(plan, t, after, state_root)
        if (
            type(p["registry_generation"]) is not int
            or p["registry_generation"] != plan["new_epoch"]
        ):
            _reject()
        issuer = next(
            e for e in after if e["device_id"] == p["issuer_device_id"] and e["state"] == "ACTIVE"
        )
        next(
            e
            for e in after
            if e["device_id"] == p["rotation_signer_device_id"] and e["state"] == "ACTIVE"
        )
        recipient = next(
            e for e in after if e["device_id"] == recipient_device_id and e["state"] == "ACTIVE"
        )
        _exact(w, {"device_id", "key_epoch", "encapsulated_key_b64", "ciphertext_b64"})
        if (
            w["device_id"] != recipient_device_id
            or type(w["key_epoch"]) is not int
            or w["key_epoch"] != plan["new_epoch"]
        ):
            _reject()
        _decode(w["encapsulated_key_b64"], 32)
        _decode(w["ciphertext_b64"], 48)
        if (
            p["wrapper_sha256"] != _digest(_canonical(w))
            or p["recipient_agreement_sha256"]
            != _digest(_decode(recipient["agreement_public_b64"], 32))
            or p["root"] != _root(p)
        ):
            _reject()
        Ed25519PublicKey.from_public_bytes(_decode(issuer["signing_public_b64"], 32)).verify(
            _decode(p["signature_b64"], 64), _message(p["root"])
        )
        return dict(
            new_epoch=plan["new_epoch"],
            recipients=list(plan["recipients"]),
            registry=after,
            recipient_commitment=_digest(
                b"atlasvault-active-recipients-v1\n"
                + _canonical({"recipients": plan["recipients"]})
            ),
        )
    except RotationError:
        raise
    except Exception:
        raise RotationError("ATLAS_DEVICE_DELIVERY_REJECTED") from None
