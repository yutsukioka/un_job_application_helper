"""Authenticated epoch activation; no offline catch-up or fork resolution."""

from __future__ import annotations

import hashlib
import json
import base64
import secrets

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from .revocation import _decode, _exact, registry_root, validate_rotation_plan, verify_transition
from .key_epochs import VaultKeyEpochRing


class RotationError(ValueError):
    """A stable, secret-free epoch admission failure."""


def __getattr__(name):
    if name == "EpochVault":
        from .epoch_vault import EpochVault

        return EpochVault
    raise AttributeError(name)


def _reject(code="ATLAS_EPOCH_ROTATION_REJECTED"):
    raise RotationError(code) from None


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode(
        "ascii"
    )


def rotation_binding(plan):
    return hashlib.sha256(b"atlasvault-rotation-binding-v1\n" + _canonical(plan)).hexdigest()


def _rotation_root(unsigned):
    return hashlib.sha256(b"atlasvault-epoch-rotation-v1\n" + _canonical(unsigned)).hexdigest()


def _rotation_message(root):
    return b"atlasvault-epoch-rotation-signature-v1\0" + bytes.fromhex(root)


def delivery_context(plan, device_id):
    return f"atlasvault-rotation-delivery-v1:{rotation_binding(plan)}:{device_id}".encode("ascii")


def create_epoch_rotation(transition, *, registry, state_root, signing_key):
    """Seal a fresh epoch to the authorized remaining devices; never return raw material."""
    try:
        after = verify_transition(transition, registry)
        plan = dict(
            format="atlasvault-rotation-plan",
            version=1,
            account_id=transition["account_id"],
            vault_id=transition["vault_id"],
            previous_epoch=transition["key_epoch"],
            new_epoch=transition["key_epoch"] + 1,
            prior_registry_root=transition["prior_registry_root"],
            resulting_registry_root=transition["resulting_registry_root"],
            state_root=state_root,
            initiator_device_id=transition["initiator_device_id"],
            revocation_root=transition["root"],
            recipients=sorted(e["device_id"] for e in after if e["state"] == "ACTIVE"),
        )
        validate_rotation_plan(plan, transition, after, state_root)
        ring = VaultKeyEpochRing.from_entries(
            current_key_epoch=plan["new_epoch"], keys={plan["new_epoch"]: secrets.token_bytes(32)}
        )
        deliveries = []
        for device in plan["recipients"]:
            entry = next(e for e in after if e["device_id"] == device)
            sealed = ring.seal_current_hpke_v2(
                recipient_public_key=_decode(entry["agreement_public_b64"], 32),
                context=delivery_context(plan, device),
            )
            deliveries.append(
                dict(
                    device_id=device,
                    key_epoch=sealed.key_epoch,
                    encapsulated_key_b64=base64.b64encode(sealed.encapsulated_key).decode(),
                    ciphertext_b64=base64.b64encode(sealed.ciphertext).decode(),
                )
            )
        unsigned = dict(
            format="atlasvault-epoch-rotation",
            version=1,
            plan=plan,
            revocation=transition,
            registry=registry,
            rotation_signer_device_id=transition["initiator_device_id"],
            deliveries=deliveries,
        )
        root = _rotation_root(unsigned)
        proof = dict(
            unsigned,
            root=root,
            signature_b64=base64.b64encode(signing_key.sign(_rotation_message(root))).decode(),
        )
        verify_epoch_rotation(
            proof,
            registry=registry,
            account_id=plan["account_id"],
            vault_id=plan["vault_id"],
            previous_epoch=plan["previous_epoch"],
            state_root=state_root,
        )
        return proof
    except Exception:
        _reject()


def verify_epoch_rotation(proof, *, registry, account_id, vault_id, previous_epoch, state_root):
    try:
        _exact(
            proof,
            {
                "format",
                "version",
                "plan",
                "revocation",
                "registry",
                "rotation_signer_device_id",
                "deliveries",
                "root",
                "signature_b64",
            },
        )
        if (
            proof["format"] != "atlasvault-epoch-rotation"
            or type(proof["version"]) is not int
            or proof["version"] != 1
        ):
            _reject()
        if registry_root(proof["registry"]) != registry_root(registry):
            _reject()
        revocation = proof["revocation"]
        after = verify_transition(revocation, registry)
        if (
            revocation["account_id"] != account_id
            or revocation["vault_id"] != vault_id
            or type(previous_epoch) is not int
            or revocation["key_epoch"] != previous_epoch
            or revocation["sequence"] != 1
        ):
            _reject()
        plan = proof["plan"]
        validate_rotation_plan(plan, revocation, after, state_root)
        recipients = plan["recipients"]
        if proof["rotation_signer_device_id"] not in recipients:
            _reject("ATLAS_ROTATION_AUTHORITY")
        deliveries = proof["deliveries"]
        if type(deliveries) is not list or len(deliveries) != len(recipients):
            _reject()
        for device, delivery in zip(recipients, deliveries):
            _exact(delivery, {"device_id", "key_epoch", "encapsulated_key_b64", "ciphertext_b64"})
            if (
                delivery["device_id"] != device
                or type(delivery["key_epoch"]) is not int
                or delivery["key_epoch"] != plan["new_epoch"]
            ):
                _reject()
            _decode(delivery["encapsulated_key_b64"], 32)
            _decode(delivery["ciphertext_b64"], 48)
        unsigned = {k: v for k, v in proof.items() if k not in ("root", "signature_b64")}
        root = _rotation_root(unsigned)
        if root != proof["root"]:
            _reject()
        signer = next(e for e in after if e["device_id"] == proof["rotation_signer_device_id"])
        Ed25519PublicKey.from_public_bytes(_decode(signer["signing_public_b64"], 32)).verify(
            _decode(proof["signature_b64"], 64), _rotation_message(root)
        )
        return {
            "new_epoch": plan["new_epoch"],
            "recipients": list(recipients),
            "binding_root": rotation_binding(plan),
            "registry": after,
        }
    except RotationError:
        raise
    except Exception:  # noqa: BLE001 - boundary errors must not expose source payloads.
        raise RotationError("ATLAS_EPOCH_ROTATION_REJECTED") from None
