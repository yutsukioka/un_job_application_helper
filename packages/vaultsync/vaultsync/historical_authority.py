"""D106: reconstruct a bounded historical inclusion witness, never infer one.

Version 1 covers the E024 two-view genesis/removal/enrollment lineage when the
current D102 projection is also the exact prior projection. Other histories stay
fenced; this format exports no earlier ciphertext or aggregate delivery artifact.
"""

import base64
import copy
import hashlib
import json

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from .authenticated_state_view import EMPTY_REGISTRY, StateViewError, _verified
from .authenticated_state_view import registry_root as descriptor_registry_root
from .device_enrollment import CONTEXT, verify_enrollment
from .device_identity import SignedDeviceDescriptor, verify_signed_device_descriptor
from .enrollment_bootstrap import bootstrap_records, verify_anchor
from .epoch_rotation import _canonical
from .revocation import _decode, _exact, registry_root, verify_transition
from .sync_queue import _canonical_json, _commitment_root

FORMAT = "atlasvault-historical-authority"
BINDINGS = (
    "account_id",
    "vault_id",
    "registry_root",
    "key_epoch",
    "registry_generation",
    "collection_sha256",
    "recipient_device_id",
    "transcript_sha256",
)
EVIDENCE = {"signed_descriptors", "prior_registry", "revocation", "views"}


class HistoricalAuthorityError(StateViewError):
    """A fixed category without untrusted or protected values."""


def reject(category="ATLAS_HISTORICAL_AUTHORITY_REJECTED"):
    raise HistoricalAuthorityError(category) from None


def build_authority(checkpoint, **evidence):
    """Package existing evidence only; the recipient must independently verify it."""
    _exact(evidence, EVIDENCE)
    return copy.deepcopy(
        dict(
            format=FORMAT,
            version=1,
            anchor_root=checkpoint["root"],
            **{k: checkpoint[k] for k in BINDINGS},
            **evidence,
        )
    )


def verify_authority(proof, *, anchor, registry, pins, trusted_signer, collection, opaque_state):
    """Return authority for exact covered envelopes, not a historical registry API."""
    try:
        _exact(proof, {"format", "version", "anchor_root", *BINDINGS, *EVIDENCE})
        if len(_canonical(proof)) > 128 * 1024:
            reject()
        if proof["format"] != FORMAT or type(proof["version"]) is not int or proof["version"] != 1:
            reject()
        p = verify_anchor(
            **{k: anchor[k] for k in ("checkpoint", "enrollment", "view")},
            registry=registry,
            current_context=pins["current_context"],
            recipient_device_id=pins["recipient_device_id"],
            confirmed_transcript=pins["confirmed_transcript"],
            trusted_signer=trusted_signer,
        )
        if (
            p["root"] != pins["anchor_root"]
            or proof["anchor_root"] != p["root"]
            or any(type(proof[k]) is not type(p[k]) or proof[k] != p[k] for k in BINDINGS)
        ):
            reject()
        records = bootstrap_records(p, collection, opaque_state, trusted_signer)
        views = proof["views"]
        if type(views) is not list or len(views) != 2:
            reject("ATLAS_HISTORY_PREIMAGE_REQUIRED")
        prior, current = [_verified(v, trusted_signer) for v in views]
        if current != anchor["view"]:
            reject()
        removal, old = proof["revocation"], proof["prior_registry"]
        after = verify_transition(removal, old)
        admission = anchor["enrollment"]
        admitted = verify_enrollment(
            admission,
            registry=after,
            context={k: admission[k] for k in CONTEXT},
            confirmed_transcript=pins["confirmed_transcript"],
            status="ACTIVE",
        )
        if registry_root(admitted) != p["registry_root"]:
            reject()
        raw_descriptors = proof["signed_descriptors"]
        if type(raw_descriptors) is not list or not 1 <= len(raw_descriptors) <= 32:
            reject()
        descriptors = [
            verify_signed_device_descriptor(SignedDeviceDescriptor.from_dict(d))
            for d in raw_descriptors
        ]
        entries, reconstructed = [], []
        for d in descriptors:
            entries.append(
                {
                    "device_id": hashlib.sha256(d.device_id.encode()).hexdigest(),
                    "descriptor_sha256": hashlib.sha256(_canonical_json(d.to_dict())).hexdigest(),
                }
            )
            reconstructed.append(
                {
                    "device_id": d.device_id,
                    "state": "ACTIVE",
                    "signing_public_b64": base64.b64encode(d.signing_public_key).decode(),
                    "agreement_public_b64": base64.b64encode(d.agreement_public_key).decode(),
                }
            )
        if descriptor_registry_root(entries) != prior["registry_root"] or registry_root(
            reconstructed
        ) != registry_root(old):
            reject()
        signer = next(
            e for e in old if e["device_id"] == p["issuer_device_id"] and e["state"] == "ACTIVE"
        )
        if (
            _decode(signer["signing_public_b64"], 32) != trusted_signer
            or removal["initiator_device_id"] != p["issuer_device_id"]
        ):
            reject()
        if (
            any(
                prior[k] != current[k] or removal[k] != current[k]
                for k in ("account_id", "vault_id")
            )
            or prior["sequence"] != 1
            or current["sequence"] != 2
            or prior["previous_root"] != "0" * 64
            or prior["previous_registry_root"] != EMPTY_REGISTRY
            or current["previous_root"] != prior["root"]
            or current["previous_registry_root"] != prior["registry_root"]
            or prior["key_epoch"] != removal["key_epoch"]
            or current["key_epoch"] != prior["key_epoch"] + 1
            or removal["sequence"] != 1
            or admission["state_root"] != prior["root"]
            or admission["registry_generation"] != current["key_epoch"]
        ):
            reject()
        # No transport-supplied old payload is accepted. Reconstruct only from
        # the exact D102-authenticated CURRENT ciphertext projection.
        if (
            _commitment_root(
                p["collection_id"],
                prior["sequence"],
                "0" * 64,
                hashlib.sha256(opaque_state).hexdigest(),
            )
            != prior["collection_root"]
        ):
            reject("ATLAS_HISTORY_PREIMAGE_REQUIRED")
        result = {}
        for r in json.loads(opaque_state)["records"]:
            if r["key_epoch"] >= p["key_epoch"]:
                continue
            if r["key_epoch"] != prior["key_epoch"]:
                reject("ATLAS_HISTORY_PREIMAGE_REQUIRED")
            aad = base64.b64decode(r["aad_b64"], validate=True)
            metadata = json.loads(aad)
            _exact(
                metadata,
                {
                    "format",
                    "version",
                    "account_id",
                    "vault_id",
                    "key_epoch",
                    "device_id",
                    "kind",
                    "object_id",
                    "revision",
                },
            )
            if (
                _canonical(metadata) != aad
                or metadata["format"] != "atlasvault-epoch-ciphertext"
                or type(metadata["version"]) is not int
                or metadata["version"] != 1
                or metadata["kind"] not in ("patch", "snapshot")
                or any(metadata[k] != prior[k] for k in ("account_id", "vault_id", "key_epoch"))
                or any(metadata[k] != r[k] for k in ("object_id", "revision", "key_epoch"))
            ):
                reject()
            author = next(
                e for e in old if e["device_id"] == metadata["device_id"] and e["state"] == "ACTIVE"
            )
            Ed25519PublicKey.from_public_bytes(_decode(author["signing_public_b64"], 32)).verify(
                _decode(r["signature_b64"], 64),
                b"atlasvault-epoch-ciphertext-signature-v1\0"
                + aad
                + base64.b64decode(r["nonce_b64"], validate=True)
                + base64.b64decode(r["ciphertext_b64"], validate=True),
            )
            result[r["object_id"]] = {
                "key_epoch": r["key_epoch"],
                "envelope_sha256": records[r["object_id"]]["envelope_sha256"],
                "author": copy.deepcopy(author),
            }
        return result
    except HistoricalAuthorityError:
        raise
    except Exception:  # noqa: BLE001 - untrusted evidence must not leak through errors.
        reject()


def retained_author(owner, state, envelope):
    """Anchored historical reads only. Never used for active registry or writes."""
    if owner._history_origin is None:
        reject()
    owner._active(state)
    history = owner._history(state)
    history._active(history._load())
    row = history._historical_records.get(envelope.object_id)
    if (
        row is None
        or row["key_epoch"] != envelope.key_epoch
        or row["envelope_sha256"] != hashlib.sha256(_canonical_json(envelope.to_dict())).hexdigest()
    ):
        reject()
    return [copy.deepcopy(row["author"])]
