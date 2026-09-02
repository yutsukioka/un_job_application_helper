"""Bounded client evidence comparison; not a freshness or consensus protocol."""

from __future__ import annotations

import base64
import hashlib
import threading
from contextlib import contextmanager
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from .sync_queue import (
    _ROOT_SIGNATURE_DOMAIN,
    SignedStateCommitment,
    _canonical_base64,
    _commitment_hex,
    _commitment_sequence,
    _EncryptedQueueFile,
    _identifier,
    _state_digest,
)

FIELDS = (
    "account_id",
    "vault_id",
    "sequence",
    "previous_root",
    "collection_root",
    "registry_root",
    "previous_registry_root",
    "key_epoch",
)
FORMAT = "atlasvault-authenticated-state-view"
ZERO = "0" * 64
EMPTY_REGISTRY = hashlib.sha256(b"atlasvault-registry-root-v1\n").hexdigest()
LIMIT = 256


class StateViewError(ValueError):
    """Stable secret-free failure code."""


def _reject(code="ATLAS_STATE_VIEW_REJECTED"):
    raise StateViewError(code) from None


@contextmanager
def _boundary():
    try:
        yield
    except StateViewError:
        raise
    except (ValueError, TypeError, KeyError, OSError, InvalidSignature):
        raise StateViewError("ATLAS_STATE_VIEW_REJECTED") from None


def registry_root(entries):
    with _boundary():
        if not isinstance(entries, list) or not 1 <= len(entries) <= LIMIT:
            _reject("ATLAS_REGISTRY_SUBSTITUTION")
        checked = {}
        for entry in entries:
            if not isinstance(entry, dict) or set(entry) != {"device_id", "descriptor_sha256"}:
                _reject("ATLAS_REGISTRY_SUBSTITUTION")
            device = _commitment_hex(entry["device_id"])
            descriptor = _commitment_hex(entry["descriptor_sha256"])
            if device in checked:
                _reject("ATLAS_REGISTRY_SUBSTITUTION")
            checked[device] = descriptor
        transcript = "atlasvault-registry-root-v1\n" + "".join(
            f"{k}:{checked[k]}\n" for k in sorted(checked)
        )
        return hashlib.sha256(transcript.encode("ascii")).hexdigest()


def _root(unsigned):
    if set(unsigned) != {"format", "version", *FIELDS}:
        _reject()
    if (
        unsigned["format"] != FORMAT
        or type(unsigned["version"]) is not int
        or unsigned["version"] != 2
    ):
        _reject()
    for field in FIELDS:
        if field in ("account_id", "vault_id"):
            _identifier(unsigned[field])
        elif field in ("sequence", "key_epoch"):
            _commitment_sequence(unsigned[field])
        else:
            _commitment_hex(unsigned[field])
    transcript = "atlasvault-authenticated-state-view-v2\n" + "".join(
        str(unsigned[k]) + "\n" for k in FIELDS
    )
    return hashlib.sha256(transcript.encode("ascii")).hexdigest()


def _message(root):
    return b"atlasvault-state-view-signature-v2\0" + bytes.fromhex(root)


def _verified(view, public):
    if not isinstance(view, dict) or set(view) != {
        "format",
        "version",
        *FIELDS,
        "root",
        "signature_b64",
    }:
        _reject()
    value = dict(view)
    unsigned = {k: v for k, v in value.items() if k not in ("root", "signature_b64")}
    if _commitment_hex(value["root"]) != _root(unsigned):
        _reject()
    if not isinstance(value["signature_b64"], str) or len(value["signature_b64"]) != 88:
        _reject()
    signature = base64.b64decode(_canonical_base64(value["signature_b64"], exact_bytes=64))
    Ed25519PublicKey.from_public_bytes(public).verify(signature, _message(value["root"]))
    return value


def sign_state_view(unsigned, signing_key):
    with _boundary():
        root = _root(unsigned)
        value = dict(
            unsigned,
            root=root,
            signature_b64=base64.b64encode(signing_key.sign(_message(root))).decode("ascii"),
        )
        return _verified(
            value, signing_key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
        )


class AuthenticatedHistory:
    def __init__(
        self,
        path: Path,
        *,
        encryption_key: bytes,
        account_id: str,
        vault_id: str,
        collection_id: str,
        key_epoch: int,
        trusted_signer: bytes,
    ):
        with _boundary():
            if not isinstance(trusted_signer, bytes) or len(trusted_signer) != 32:
                _reject()
            self._public = trusted_signer
            self._context = {
                "account_id": _identifier(account_id),
                "vault_id": _identifier(vault_id),
                "collection_id": _identifier(collection_id),
                "key_epoch": _commitment_sequence(key_epoch),
                "signing_public_b64": base64.b64encode(trusted_signer).decode("ascii"),
            }
            self._store = _EncryptedQueueFile(
                Path(path), encryption_key=encryption_key, kind="authenticated-state-history-v2"
            )
            self._lock = threading.Lock()

    def _chain(self, views):
        if not isinstance(views, list) or len(views) > LIMIT:
            _reject("ATLAS_HISTORY_LIMIT")
        checked = []
        previous, registry = ZERO, EMPTY_REGISTRY
        for i, raw in enumerate(views):
            view = _verified(raw, self._public)
            if any(view[k] != self._context[k] for k in ("account_id", "vault_id", "key_epoch")):
                _reject()
            if (
                view["sequence"] != i + 1
                or view["previous_root"] != previous
                or view["previous_registry_root"] != registry
            ):
                _reject()
            checked.append(view)
            previous, registry = view["root"], view["registry_root"]
        return checked

    def _load(self):
        value = self._store.read({})
        if (
            set(value) != {"context", "views", "blocked"}
            or value["context"] != self._context
            or type(value["blocked"]) is not bool
        ):
            _reject()
        return self._chain(value["views"]), value["blocked"]

    def _save(self, views, blocked=False):
        self._store.write({"context": self._context, "views": views, "blocked": blocked})

    def initialize(self):
        with self._lock, _boundary():
            if self._store.path.exists():
                _reject()
            self._save([])

    def export_evidence(self):
        with self._lock, _boundary():
            return self._load()[0]

    def _fork(self, views):
        self._save(views, blocked=True)
        _reject("ATLAS_STATE_EQUIVOCATION")

    def compare_evidence(self, peer):
        with self._lock, _boundary():
            local, blocked = self._load()
            if blocked:
                _reject("ATLAS_STATE_EQUIVOCATION")
            peer = self._chain(peer)
            if not local or not peer:
                _reject("ATLAS_CHECKPOINT_REQUIRED")
            for left, right in zip(local, peer):
                if left["root"] != right["root"]:
                    self._fork(local)
            return min(len(local), len(peer))

    def observe(self, raw_view, registry, raw_collection, opaque_state):
        with self._lock, _boundary():
            views, blocked = self._load()
            if blocked:
                _reject("ATLAS_STATE_EQUIVOCATION")
            view = _verified(raw_view, self._public)
            if any(view[k] != self._context[k] for k in ("account_id", "vault_id", "key_epoch")):
                _reject()
            if registry_root(registry) != view["registry_root"]:
                _reject("ATLAS_REGISTRY_SUBSTITUTION")
            collection = SignedStateCommitment.from_dict(raw_collection)
            if (
                collection.collection_id != self._context["collection_id"]
                or collection.sequence != view["sequence"]
                or collection.root != view["collection_root"]
                or collection.state_sha256 != _state_digest(opaque_state)
            ):
                _reject()
            Ed25519PublicKey.from_public_bytes(self._public).verify(
                base64.b64decode(collection.signature_b64),
                _ROOT_SIGNATURE_DOMAIN + bytes.fromhex(collection.root),
            )
            sequence = view["sequence"]
            if sequence <= len(views) and view["root"] != views[sequence - 1]["root"]:
                self._fork(views)
            if sequence < len(views):
                _reject("ATLAS_ROLLBACK_REJECTED")
            if sequence == len(views):
                return False
            if len(views) == LIMIT:
                _reject("ATLAS_HISTORY_LIMIT")
            previous = views[-1] if views else None
            if (
                sequence != len(views) + 1
                or view["previous_root"] != (previous["root"] if previous else ZERO)
                or view["previous_registry_root"]
                != (previous["registry_root"] if previous else EMPTY_REGISTRY)
                or collection.previous_root != (previous["collection_root"] if previous else ZERO)
            ):
                _reject()
            self._save([*views, view])
            return True
