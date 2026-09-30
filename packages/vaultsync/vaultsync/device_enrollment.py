"""D099 additive enrollment signature. No activation, delivery, or HPKE mutation.

Client verification requires independently confirmed local ceremony state.
Backend attestation verification checks the ACTIVE issuer's signed claim; it
does not observe or independently confirm the human SAS comparison.
"""

import base64
import copy
import hashlib

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from .epoch_rotation import _canonical
from .revocation import _decode, _exact, _hex, _identifier, _number, registry_root

CONTEXT = {
    "account_id",
    "vault_id",
    "registry_generation",
    "key_epoch",
    "state_root",
    "activation_id",
}
FIELDS = CONTEXT | {
    "format",
    "version",
    "next_registry_generation",
    "prior_registry_root",
    "resulting_registry_root",
    "target_device_id",
    "target_signing_public_b64",
    "target_agreement_public_b64",
    "target_agreement_sha256",
    "transcript_sha256",
    "issuer_device_id",
    "authorization_category",
    "signature_algorithm",
}


class EnrollmentError(ValueError):
    """Stable public category; no raw failure context is exposed."""


def _reject():
    raise EnrollmentError("ATLAS_ENROLLMENT_REJECTED") from None


def _unsigned(proof):
    return {k: v for k, v in proof.items() if k not in ("root", "signature_b64")}


def _root(proof):
    return hashlib.sha256(
        b"atlasvault-device-enrollment-v1\n" + _canonical(_unsigned(proof))
    ).hexdigest()


def _message(root):
    return b"atlasvault-device-enrollment-signature-v1\0" + bytes.fromhex(root)


def verify_enrollment(proof, *, registry, context, confirmed_transcript, status):
    """Client admission: require a transcript obtained from local ceremony state."""
    try:
        if (
            _hex(confirmed_transcript) == "0" * 64
            or proof["transcript_sha256"] != confirmed_transcript
        ):
            _reject()
        return verify_enrollment_attestation(
            proof, registry=registry, context=context, status=status
        )
    except Exception:
        raise EnrollmentError("ATLAS_ENROLLMENT_REJECTED") from None


def verify_enrollment_attestation(proof, *, registry, context, status):
    """Server admission: authenticate the issuer's claim, not local SAS confirmation.

    Expected registry/context must still come from trusted durable state. A
    compromised ACTIVE issuer can sign a false confirmation claim; this verifier
    cannot distinguish that from an honestly completed client ceremony.
    """
    try:
        _exact(proof, FIELDS | {"root", "signature_b64"})
        _exact(context, CONTEXT)
        if status != "ACTIVE" or type(proof["version"]) is not int or proof["version"] != 1:
            _reject()
        if (
            proof["format"] != "atlasvault-device-enrollment"
            or proof["authorization_category"] != "SAS_CONFIRMED"
            or proof["signature_algorithm"] != "Ed25519"
        ):
            _reject()
        for name in CONTEXT:
            value = context[name]
            if name in ("key_epoch", "registry_generation"):
                _number(value)
            elif name in ("state_root", "activation_id"):
                _hex(value)
            else:
                _identifier(value)
            if type(proof[name]) is not type(value) or proof[name] != value:
                _reject()
        if (
            _number(proof["next_registry_generation"]) != context["registry_generation"] + 1
            or _hex(proof["transcript_sha256"]) == "0" * 64
            or registry_root(registry) != proof["prior_registry_root"]
        ):
            _reject()
        target = {
            "device_id": proof["target_device_id"],
            "signing_public_b64": proof["target_signing_public_b64"],
            "agreement_public_b64": proof["target_agreement_public_b64"],
            "state": "ACTIVE",
        }
        # Keeping revoked entries makes re-enrollment of that identity terminal.
        if any(e["device_id"] == target["device_id"] for e in registry):
            _reject()
        after = copy.deepcopy(registry) + [target]
        after.sort(key=lambda e: e["device_id"])
        if (
            registry_root(after) != proof["resulting_registry_root"]
            or hashlib.sha256(_decode(target["agreement_public_b64"], 32)).hexdigest()
            != proof["target_agreement_sha256"]
        ):
            _reject()
        signer = next(
            e
            for e in registry
            if e["device_id"] == proof["issuer_device_id"] and e["state"] == "ACTIVE"
        )
        root = _root(proof)
        if proof["root"] != root:
            _reject()
        Ed25519PublicKey.from_public_bytes(_decode(signer["signing_public_b64"], 32)).verify(
            _decode(proof["signature_b64"], 64), _message(root)
        )
        return after
    except Exception:  # noqa: BLE001 - untrusted boundary exposes no raw exception details.
        raise EnrollmentError("ATLAS_ENROLLMENT_REJECTED") from None


def create_enrollment(unsigned, *, registry, context, confirmed_transcript, status, signing_key):
    """Client-only signing; production must obtain confirmation from its ceremony."""
    try:
        _exact(unsigned, FIELDS)
        if (
            status != "ACTIVE"
            or confirmed_transcript != unsigned["transcript_sha256"]
            or _hex(confirmed_transcript) == "0" * 64
        ):
            _reject()
        proof = copy.deepcopy(unsigned)
        proof["root"] = _root(proof)
        proof["signature_b64"] = base64.b64encode(signing_key.sign(_message(proof["root"]))).decode(
            "ascii"
        )
        verify_enrollment(
            proof,
            registry=registry,
            context=context,
            confirmed_transcript=confirmed_transcript,
            status=status,
        )
        return proof
    except Exception:  # noqa: BLE001 - key-provider failures remain secret-free and fail closed.
        raise EnrollmentError("ATLAS_ENROLLMENT_REJECTED") from None
