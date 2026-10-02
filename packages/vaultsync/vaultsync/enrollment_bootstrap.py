"""D102 signed first-contact anchor. This does not authorize replacing history."""

import base64
import copy
import hashlib
import json

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from .authenticated_state_view import _verified
from .device_enrollment import CONTEXT, verify_enrollment
from .epoch_rotation import _canonical
from .revocation import _decode, _exact, _hex, _number, registry_root
from .sync_queue import (
    _ROOT_SIGNATURE_DOMAIN,
    OpaqueCiphertextEnvelope,
    SignedStateCommitment,
    _canonical_json,
)

FIELDS = {
    "format",
    "version",
    "account_id",
    "vault_id",
    "activation_id",
    "registry_generation",
    "key_epoch",
    "registry_root",
    "state_root",
    "sequence",
    "collection_id",
    "collection_root",
    "collection_sha256",
    "enrollment_root",
    "recipient_device_id",
    "recipient_agreement_sha256",
    "transcript_sha256",
    "issuer_device_id",
    "signature_algorithm",
}


class BootstrapError(ValueError):
    """A stable, secret-free admission failure."""


def reject(category="ATLAS_BOOTSTRAP_REJECTED"):
    raise BootstrapError(category) from None


def _root(value):
    unsigned = {k: v for k, v in value.items() if k not in ("root", "signature_b64")}
    return hashlib.sha256(b"atlasvault-history-bootstrap-v1\n" + _canonical(unsigned)).hexdigest()


def _message(root):
    return b"atlasvault-history-bootstrap-signature-v1\0" + bytes.fromhex(root)


def verify_anchor(
    *,
    checkpoint,
    enrollment,
    registry,
    current_context,
    recipient_device_id,
    confirmed_transcript,
    view,
    trusted_signer,
):
    """Expectations come from the confirmed ceremony, not the incoming checkpoint."""
    try:
        p = checkpoint
        _exact(p, FIELDS | {"root", "signature_b64"})
        _exact(current_context, CONTEXT)
        if (
            p["format"] != "atlasvault-history-bootstrap"
            or type(p["version"]) is not int
            or p["version"] != 1
            or p["signature_algorithm"] != "Ed25519"
        ):
            reject()
        for key in CONTEXT:
            if type(p[key]) is not type(current_context[key]) or p[key] != current_context[key]:
                reject()
        if (
            p["recipient_device_id"] != recipient_device_id
            or p["recipient_device_id"] != enrollment["target_device_id"]
            or p["recipient_agreement_sha256"] != enrollment["target_agreement_sha256"]
            or p["transcript_sha256"] != confirmed_transcript
            or p["transcript_sha256"] != enrollment["transcript_sha256"]
            or p["enrollment_root"] != enrollment["root"]
            or p["registry_generation"] != enrollment["next_registry_generation"]
            or p["key_epoch"] != enrollment["key_epoch"]
            or p["activation_id"] != enrollment["activation_id"]
            or p["registry_root"] != enrollment["resulting_registry_root"]
            or p["registry_root"] != registry_root(registry)
        ):
            reject()
        prior = [r for r in registry if r["device_id"] != recipient_device_id]
        checked = verify_enrollment(
            enrollment,
            registry=prior,
            context={k: enrollment[k] for k in CONTEXT},
            confirmed_transcript=confirmed_transcript,
            status="ACTIVE",
        )
        if _canonical(checked) != _canonical(sorted(registry, key=lambda r: r["device_id"])):
            reject()
        issuer = next(
            r for r in prior if r["device_id"] == p["issuer_device_id"] and r["state"] == "ACTIVE"
        )
        public = _decode(issuer["signing_public_b64"], 32)
        if public != trusted_signer:
            reject()
        for key in ("state_root", "collection_root", "collection_sha256", "root"):
            _hex(p[key])
        _number(p["sequence"])
        if _root(p) != p["root"]:
            reject()
        Ed25519PublicKey.from_public_bytes(public).verify(
            _decode(p["signature_b64"], 64),
            _message(p["root"]),
        )
        v = _verified(view, public)
        if (
            any(
                v[k] != p[k]
                for k in (
                    "account_id",
                    "vault_id",
                    "key_epoch",
                    "sequence",
                    "registry_root",
                    "collection_root",
                )
            )
            or v["root"] != p["state_root"]
        ):
            reject()
        return copy.deepcopy(p)
    except Exception:  # noqa: BLE001 - fail closed without exposing untrusted values.
        reject()


def bootstrap_records(checkpoint, collection, opaque_state, trusted_signer):
    """Authenticate the current ciphertext projection, including terminal tombstones."""
    try:
        if not isinstance(opaque_state, bytes) or len(opaque_state) > 1024 * 1024:
            reject()
        c = SignedStateCommitment.from_dict(collection)
        if (
            c.collection_id != checkpoint["collection_id"]
            or c.sequence != checkpoint["sequence"]
            or c.root != checkpoint["collection_root"]
            or c.state_sha256 != checkpoint["collection_sha256"]
            or c.state_sha256 != hashlib.sha256(opaque_state).hexdigest()
        ):
            reject()
        Ed25519PublicKey.from_public_bytes(trusted_signer).verify(
            _decode(c.signature_b64, 64),
            _ROOT_SIGNATURE_DOMAIN + bytes.fromhex(c.root),
        )
        body = json.loads(opaque_state)
        _exact(body, {"format", "version", "route", "records"})
        if (
            body["format"] != "atlasvault-guarded-collection"
            or type(body["version"]) is not int
            or body["version"] != 1
            or body["route"] not in ("patch", "snapshot", "compaction")
            or not isinstance(body["records"], list)
            or len(body["records"]) > 256
        ):
            reject()
        records = {}
        for raw in body["records"]:
            r = OpaqueCiphertextEnvelope.from_dict(raw)
            if r.version != 1 or r.object_id in records or r.key_epoch > checkpoint["key_epoch"]:
                reject()
            records[r.object_id] = {
                "object_id": r.object_id,
                "revision": r.revision,
                "content_sha256": r.content_sha256,
                "envelope_sha256": hashlib.sha256(_canonical_json(r.to_dict())).hexdigest(),
                "tombstone": r.tombstone,
            }
        return records
    except Exception:  # noqa: BLE001 - fail closed without exposing untrusted values.
        reject()


def create_history_bootstrap(owner, *, enrollment, signing_key, view, collection, opaque_state):
    """Only the active enrolled-history owner may authorize this first-contact anchor."""
    from .epoch_catch_up import bridge_records

    try:
        with owner._lock:
            s = owner._load()
            owner._active(s)
            if (
                signing_key.device_id != owner._context["device_id"]
                or not any(
                    r.get("enrollment") == enrollment
                    for r in bridge_records(s["components"]["history"])
                )
                or s["components"]["history"]["views"][-1] != view
            ):
                reject()
            context = owner.enrollment_context()
            p = dict(
                format="atlasvault-history-bootstrap",
                version=1,
                **context,
                registry_root=registry_root(s["registry"]),
                sequence=view["sequence"],
                collection_id=collection["collection_id"],
                collection_root=collection["root"],
                collection_sha256=hashlib.sha256(opaque_state).hexdigest(),
                enrollment_root=enrollment["root"],
                recipient_device_id=enrollment["target_device_id"],
                recipient_agreement_sha256=enrollment["target_agreement_sha256"],
                transcript_sha256=enrollment["transcript_sha256"],
                issuer_device_id=signing_key.device_id,
                signature_algorithm="Ed25519",
            )
            p["root"] = _root(p)
            p["signature_b64"] = base64.b64encode(signing_key.sign(_message(p["root"]))).decode(
                "ascii"
            )
            verify_anchor(
                checkpoint=p,
                enrollment=enrollment,
                registry=s["registry"],
                current_context=context,
                recipient_device_id=enrollment["target_device_id"],
                confirmed_transcript=enrollment["transcript_sha256"],
                view=view,
                trusted_signer=signing_key.signing_public_key,
            )
            if (
                bootstrap_records(p, collection, opaque_state, signing_key.signing_public_key)
                != s["components"]["history"]["records"]
            ):
                reject()
            return p
    except Exception:  # noqa: BLE001 - key-provider errors must not expose protected data.
        reject()
