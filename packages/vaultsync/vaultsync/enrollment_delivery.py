"""D100 recipient-only HPKE delivery of a verified D102/D106 current view.

This is an enrollment delivery, not a D087 activation or D089 proof rewrite.
The signing client must hold the current accepted view. Installation is new-store
only; an immutable receipt prevents substituting a different ceremony on retry.
"""

import base64
import copy
import hashlib
import hmac
import json
import sqlite3
from contextlib import closing
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from .anchored_history import AnchoredSyncState
from .enrollment_bootstrap import bootstrap_records, verify_anchor
from .epoch_rotation import _canonical
from .epoch_vault import EpochVault
from .historical_authority import verify_authority
from .key_epochs import EpochHPKESealedVaultKeyV2, VaultKeyEpochRing, open_epoch_hpke_v2
from .revocation import _decode, _exact

FIELDS = {
    "format",
    "version",
    "hpke_suite",
    "anchor",
    "registry",
    "collection",
    "opaque_b64",
    "historical_authority",
    "deliveries",
    "root",
    "signature_b64",
}
SUITE = "0x0020/0x0001/0x0002"


class EnrollmentDeliveryError(ValueError):
    def __init__(self):
        super().__init__("ATLAS_ENROLLMENT_DELIVERY_REJECTED")


def reject():
    raise EnrollmentDeliveryError() from None


def _unsigned(packet):
    return {k: v for k, v in packet.items() if k not in ("root", "signature_b64")}


def _root(packet):
    return hashlib.sha256(
        b"atlasvault-enrollment-delivery-v1\n" + _canonical(_unsigned(packet))
    ).hexdigest()


def _message(root):
    return b"atlasvault-enrollment-delivery-signature-v1\0" + bytes.fromhex(root)


def _context(packet):
    body = {k: v for k, v in _unsigned(packet).items() if k != "deliveries"}
    return b"atlasvault-enrollment-delivery-hpke-v1\0" + hashlib.sha256(_canonical(body)).digest()


def _receipt_body(packet, delivery_hash):
    p = packet["anchor"]["checkpoint"]
    return {
        "format": "atlasvault-enrollment-acknowledgement",
        "version": 1,
        "delivery_sha256": delivery_hash,
        "anchor_root": p["root"],
        "transcript_sha256": p["transcript_sha256"],
        "recipient_device_id": p["recipient_device_id"],
    }


def _receipt_message(receipt):
    body = {k: v for k, v in receipt.items() if k != "signature_b64"}
    return b"atlasvault-enrollment-acknowledgement-v1\0" + _canonical(body)


def acknowledge_enrollment(packet, delivery_hash, recipient):
    """Recipient signs only after protected installation and runtime activation."""
    try:
        receipt = _receipt_body(packet, delivery_hash)
        if receipt["recipient_device_id"] != recipient.device_id:
            reject()
        receipt["signature_b64"] = base64.b64encode(
            recipient.sign(_receipt_message(receipt))
        ).decode()
        return receipt
    except Exception:  # noqa: BLE001 - never expose ceremony artifacts.
        reject()


def verify_enrollment_acknowledgement(packet, delivery_hash, receipt, recipient):
    try:
        body = {k: v for k, v in receipt.items() if k != "signature_b64"}
        if (
            _canonical(body) != _canonical(_receipt_body(packet, delivery_hash))
            or body["recipient_device_id"] != recipient.device_id
        ):
            reject()
        Ed25519PublicKey.from_public_bytes(recipient.signing_public_key).verify(
            _decode(receipt["signature_b64"], 64), _receipt_message(receipt)
        )
    except Exception:  # noqa: BLE001 - stable public rejection only.
        reject()


def _validate_view(packet, pins, trusted_signer, recipient_id, recipient_public):
    anchor, registry = packet["anchor"], packet["registry"]
    _exact(anchor, {"checkpoint", "enrollment", "view"})
    checkpoint = verify_anchor(
        **anchor,
        registry=registry,
        current_context=pins["current_context"],
        recipient_device_id=pins["recipient_device_id"],
        confirmed_transcript=pins["confirmed_transcript"],
        trusted_signer=trusted_signer,
    )
    if (
        pins["anchor_root"] != checkpoint["root"]
        or recipient_id != checkpoint["recipient_device_id"]
        or hashlib.sha256(recipient_public).hexdigest() != checkpoint["recipient_agreement_sha256"]
        or len(packet["opaque_b64"]) > 4 * ((1024 * 1024 + 2) // 3)
    ):
        reject()
    opaque = base64.b64decode(packet["opaque_b64"], validate=True)
    if base64.b64encode(opaque).decode() != packet["opaque_b64"]:
        reject()
    bootstrap_records(checkpoint, packet["collection"], opaque, trusted_signer)
    epochs = sorted(
        {checkpoint["key_epoch"], *(r["key_epoch"] for r in json.loads(opaque)["records"])}
    )
    if len(epochs) > 32:
        reject()
    authority = packet["historical_authority"]
    if epochs != [checkpoint["key_epoch"]]:
        verify_authority(
            authority,
            anchor=anchor,
            registry=registry,
            pins=pins,
            trusted_signer=trusted_signer,
            collection=packet["collection"],
            opaque_state=opaque,
        )
    elif authority is not None:
        # Do not transport an unnecessary archive for an all-current view.
        reject()
    return checkpoint, opaque, epochs


def create_enrollment_delivery(
    owner, *, signing_key, anchor, collection, opaque_state, historical_authority=None
):
    """Called after fresh target/transcript authorization, inside the ceremony."""
    try:
        with owner._lock:
            state = owner._load()
            owner._active(state)
            checkpoint = anchor["checkpoint"]
            if (
                signing_key.device_id != owner._context["device_id"]
                or signing_key.device_id != checkpoint["issuer_device_id"]
                or state["components"]["history"]["views"][-1] != anchor["view"]
            ):
                reject()
            pins = {
                "anchor_root": checkpoint["root"],
                "recipient_device_id": checkpoint["recipient_device_id"],
                "confirmed_transcript": checkpoint["transcript_sha256"],
                "current_context": owner.enrollment_context(),
            }
            recipient = next(
                r
                for r in state["registry"]
                if r["device_id"] == pins["recipient_device_id"] and r["state"] == "ACTIVE"
            )
            public = _decode(recipient["agreement_public_b64"], 32)
            packet = {
                "format": "atlasvault-enrollment-delivery",
                "version": 1,
                "hpke_suite": SUITE,
                "anchor": copy.deepcopy(anchor),
                "registry": copy.deepcopy(state["registry"]),
                "collection": copy.deepcopy(collection),
                "opaque_b64": base64.b64encode(opaque_state).decode(),
                "historical_authority": copy.deepcopy(historical_authority),
                "deliveries": [],
            }
            _, _, epochs = _validate_view(
                packet, pins, signing_key.signing_public_key, recipient["device_id"], public
            )
            ring = owner._ring(state)
            for epoch in epochs:
                selected = VaultKeyEpochRing.from_entries(
                    current_key_epoch=epoch, keys={epoch: ring.vault_key_for_epoch(epoch)}
                )
                sealed = selected.seal_current_hpke_v2(
                    recipient_public_key=public, context=_context(packet)
                )
                packet["deliveries"].append(
                    {
                        "key_epoch": epoch,
                        "encapsulated_key_b64": base64.b64encode(sealed.encapsulated_key).decode(),
                        "ciphertext_b64": base64.b64encode(sealed.ciphertext).decode(),
                    }
                )
            packet["root"] = _root(packet)
            packet["signature_b64"] = base64.b64encode(
                signing_key.sign(_message(packet["root"]))
            ).decode()
            return packet
    except Exception:  # noqa: BLE001 - fixed boundary: never expose key-provider or input errors.
        reject()


def install_enrollment_delivery(
    directory,
    packet,
    *,
    pins,
    trusted_signer,
    recipient_identity,
    storage_key,
    require_runtime_projection=False,
):
    """Verify before filesystem writes; publish one complete protected P7 owner.

    SQLite serializes this narrow install receipt across processes. It stores
    only a package hash; all history, keys and runtime state use existing
    encrypted stores. The receipt never permits replacing accepted history.
    """
    keys = {}
    try:
        encoded = _canonical(packet)
        if (
            type(storage_key) is not bytes
            or len(storage_key) != 32
            or len(encoded) > 2 * 1024 * 1024
        ):
            reject()
        packet = json.loads(encoded)
        pins = copy.deepcopy(pins)
        _exact(packet, FIELDS)
        if (
            packet["format"] != "atlasvault-enrollment-delivery"
            or type(packet["version"]) is not int
            or packet["version"] != 1
            or packet["hpke_suite"] != SUITE
            or packet["root"] != _root(packet)
        ):
            reject()
        Ed25519PublicKey.from_public_bytes(trusted_signer).verify(
            _decode(packet["signature_b64"], 64), _message(packet["root"])
        )
        p, opaque, epochs = _validate_view(
            packet,
            pins,
            trusted_signer,
            recipient_identity.device_id,
            recipient_identity.agreement_public_key,
        )
        if (
            type(packet["deliveries"]) is not list
            or [d["key_epoch"] for d in packet["deliveries"]] != epochs
        ):
            reject()
        for delivery in packet["deliveries"]:
            _exact(delivery, {"key_epoch", "encapsulated_key_b64", "ciphertext_b64"})
            opened = open_epoch_hpke_v2(
                recipient_private_key=recipient_identity.secret_bundle().agreement_private_key,
                sealed=EpochHPKESealedVaultKeyV2(
                    key_epoch=delivery["key_epoch"],
                    encapsulated_key=_decode(delivery["encapsulated_key_b64"], 32),
                    ciphertext=_decode(delivery["ciphertext_b64"], 48),
                ),
                context=_context(packet),
                minimum_key_epoch=delivery["key_epoch"],
            )
            keys[opened.key_epoch] = opened.vault_key
        root = Path(directory)
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        with closing(sqlite3.connect(root / "enrollment-receipt.sqlite", timeout=10)) as receipt:
            receipt.execute("PRAGMA synchronous=FULL")
            receipt.execute(
                "CREATE TABLE IF NOT EXISTS receipt (id INTEGER PRIMARY KEY CHECK(id=1), digest TEXT NOT NULL)"
            )
            receipt.execute("BEGIN IMMEDIATE")
            row = receipt.execute("SELECT digest FROM receipt WHERE id=1").fetchone()
            if row is None:
                if (root / "activation").exists():
                    reject()
                receipt.execute("INSERT INTO receipt VALUES (1, ?)", (packet["root"],))
            elif row[0] != packet["root"]:
                reject()
            receipt.commit()
            receipt.execute("BEGIN IMMEDIATE")
            history = AnchoredSyncState(
                root / "enrollment-history",
                encryption_key=hmac.new(
                    storage_key, b"atlasvault-enrollment-history-v1", hashlib.sha256
                ).digest(),
                account_id=p["account_id"],
                vault_id=p["vault_id"],
                collection_id=p["collection_id"],
                key_epoch=p["key_epoch"],
                trusted_signer=trusted_signer,
                rotation_registry=packet["registry"],
                **pins,
            )
            history.bootstrap(
                **packet["anchor"],
                registry=packet["registry"],
                collection=packet["collection"],
                opaque_state=opaque,
                **{
                    k: pins[k]
                    for k in ("current_context", "recipient_device_id", "confirmed_transcript")
                },
            )
            if packet["historical_authority"] is not None:
                history.install_historical_authority(
                    packet["historical_authority"],
                    collection=packet["collection"],
                    opaque_state=opaque,
                )
            owner = EpochVault(
                root,
                storage_key=storage_key,
                device_id=recipient_identity.device_id,
                registry=packet["registry"],
                account_id=p["account_id"],
                vault_id=p["vault_id"],
                key_epoch=p["key_epoch"],
                state_root=p["state_root"],
                history_origin=history.publication_origin(),
            )
            if owner._file.path.exists():
                state = owner._load()
                owner._active(state)
                if state["context"] != owner._context:
                    reject()
            else:
                owner.initialize(
                    keys,
                    history=history,
                    enrollment_projection=opaque if require_runtime_projection else None,
                )
            if require_runtime_projection:
                from .enrollment_runtime import runtime_projection

                if _canonical([r.to_dict() for r in runtime_projection(owner)]) != _canonical(
                    json.loads(opaque)["records"]
                ):
                    reject()
            receipt.commit()
            return owner
    except Exception:  # noqa: BLE001 - fixed boundary: never expose key-provider or input errors.
        reject()
    finally:
        # Python immutable cryptographic byte values cannot promise physical zeroization.
        keys.clear()
