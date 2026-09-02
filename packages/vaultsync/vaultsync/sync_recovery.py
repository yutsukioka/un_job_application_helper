"""Bounded admission fence and explicit recovery; no automatic fork selection."""

from __future__ import annotations

import base64
import hashlib
import json
import threading
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from .authenticated_state_view import (
    EMPTY_REGISTRY,
    LIMIT,
    ZERO,
    StateViewError,
    _boundary,
    _reject,
    _verified,
    registry_root,
)
from .sync_queue import (
    _ROOT_SIGNATURE_DOMAIN,
    OpaqueCiphertextEnvelope,
    SignedStateCommitment,
    _canonical_json,
    _commitment_sequence,
    _EncryptedQueueFile,
    _identifier,
    _state_digest,
)

_PENDING = "ATLAS_RECOVERY_PENDING"
_META = ("sequence", "root", "registry_root", "key_epoch")


class GuardedSyncState:
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
            if len(trusted_signer) != 32:
                _reject()
            self._public = bytes(trusted_signer)
            self._context = {
                "account_id": _identifier(account_id),
                "vault_id": _identifier(vault_id),
                "collection_id": _identifier(collection_id),
                "key_epoch": _commitment_sequence(key_epoch),
                "signing_public_b64": base64.b64encode(trusted_signer).decode("ascii"),
            }
            self._store = _EncryptedQueueFile(
                Path(path), encryption_key=encryption_key, kind="guarded-sync-state-v1"
            )
            self._lock = threading.RLock()

    def _chain(self, raw):
        if not isinstance(raw, list) or len(raw) > LIMIT:
            _reject("ATLAS_HISTORY_LIMIT")
        views, previous, registry = [], ZERO, EMPTY_REGISTRY
        for i, item in enumerate(raw):
            v = _verified(item, self._public)
            if any(v[k] != self._context[k] for k in ("account_id", "vault_id", "key_epoch")):
                _reject()
            if (
                v["sequence"] != i + 1
                or v["previous_root"] != previous
                or v["previous_registry_root"] != registry
            ):
                _reject()
            views.append(v)
            previous, registry = v["root"], v["registry_root"]
        return views

    def _load(self):
        s = self._store.read({})
        if (
            set(s) != {"context", "views", "records", "cases", "status"}
            or s["context"] != self._context
        ):
            _reject()
        s["views"] = self._chain(s["views"])
        if (
            s["status"] not in ("ACTIVE", "MANUAL_REQUIRED", "RECOVERY_PENDING")
            or not isinstance(s["cases"], list)
            or len(s["cases"]) > 8
        ):
            _reject()
        if not isinstance(s["records"], dict) or len(s["records"]) > LIMIT:
            _reject()
        return s

    def initialize(self):
        with self._lock, _boundary():
            if self._store.path.exists():
                _reject()
            self._store.write(
                {
                    "context": self._context,
                    "views": [],
                    "records": {},
                    "cases": [],
                    "status": "ACTIVE",
                }
            )

    def _active(self, s):
        if s["status"] != "ACTIVE":
            _reject(_PENDING)
        if len(s["cases"]) == 8:
            s["status"] = "RECOVERY_PENDING"
            self._store.write(s)
            _reject(_PENDING)

    def automatic_sync(self, operation):
        with self._lock, _boundary():
            self._active(self._load())
            return operation()

    def checkpoint(self):
        with self._lock, _boundary():
            s = self._load()
            return {
                "sequence": len(s["views"]),
                "cursor": s["views"][-1]["root"] if s["views"] else ZERO,
                "records": [s["records"][k] for k in sorted(s["records"])],
            }

    def export_evidence(self):
        with self._lock, _boundary():
            return self._load()["views"]

    def _alarm(self, s, reason, peer, registry=None):
        s["cases"].append(
            {
                "reason": reason,
                "local": list(s["views"]),
                "peer": peer,
                "presented_registry_root": registry,
                "disposition": None,
                "rejected_branch": None,
            }
        )
        s["status"] = "MANUAL_REQUIRED"
        self._store.write(s)
        _reject(reason)

    def evidence(self):
        with self._lock, _boundary():
            s = self._load()
            c = s["cases"][-1] if s["cases"] else {"local": s["views"], "peer": []}
            return {"local": c["local"], "peer": c["peer"]}

    def recovery(self):
        with self._lock, _boundary():
            s = self._load()
            c = s["cases"][-1] if s["cases"] else None
            return {
                "status": s["status"],
                "reason": c["reason"] if c else None,
                "local": [{k: v[k] for k in _META} for v in (c["local"] if c else s["views"])],
                "peer": [{k: v[k] for k in _META} for v in (c["peer"] if c else [])],
                "disposition": c["disposition"] if c else None,
                "rejected_branch": c["rejected_branch"] if c else None,
                "presented_registry_root": c["presented_registry_root"] if c else None,
            }

    def resolve(self, disposition, local_root, peer_root):
        with self._lock, _boundary():
            s = self._load()
            if s["status"] != "MANUAL_REQUIRED" or disposition not in (
                "retain_accepted",
                "select_peer",
                "keep_blocked",
            ):
                _reject()
            c = s["cases"][-1]
            if local_root != (c["local"][-1]["root"] if c["local"] else ZERO) or peer_root != (
                c["peer"][-1]["root"] if c["peer"] else ZERO
            ):
                _reject()
            # Only rejection of an already-known signed replay can safely resume v1.
            safe = (
                c["reason"] == "ATLAS_ROLLBACK_REJECTED"
                and bool(c["peer"])
                and all(
                    v["sequence"] <= len(c["local"])
                    and v["root"] == c["local"][v["sequence"] - 1]["root"]
                    for v in c["peer"]
                )
            )
            c["disposition"] = disposition
            c["rejected_branch"] = {
                "retain_accepted": "peer",
                "select_peer": "local",
                "keep_blocked": None,
            }[disposition]
            s["status"] = (
                "ACTIVE" if disposition == "retain_accepted" and safe else "RECOVERY_PENDING"
            )
            self._store.write(s)
            return s["status"]

    def compare_evidence(self, peer):
        with self._lock, _boundary():
            s = self._load()
            self._active(s)
            signed = []
            try:
                with _boundary():
                    if not isinstance(peer, list) or len(peer) > LIMIT:
                        _reject("ATLAS_HISTORY_LIMIT")
                    signed = [_verified(v, self._public) for v in peer]
                    checked = self._chain(signed)
                    if not checked or not s["views"]:
                        _reject("ATLAS_CHECKPOINT_REQUIRED")
                    if any(a["root"] != b["root"] for a, b in zip(s["views"], checked)):
                        _reject("ATLAS_STATE_EQUIVOCATION")
                    return min(len(s["views"]), len(checked))
            except StateViewError as e:
                self._alarm(s, str(e), signed)

    def ingest(self, raw_view, registry, raw_collection, opaque_state):
        with self._lock, _boundary():
            s = self._load()
            self._active(s)
            peer = []
            registry_digest = None
            try:
                with _boundary():
                    view = _verified(raw_view, self._public)
                    peer = [view]
                    if any(
                        view[k] != self._context[k] for k in ("account_id", "vault_id", "key_epoch")
                    ):
                        _reject()
                    registry_digest = registry_root(registry)
                    if registry_digest != view["registry_root"]:
                        _reject("ATLAS_REGISTRY_SUBSTITUTION")
                    if len(opaque_state) > 1024 * 1024:
                        _reject("ATLAS_HISTORY_LIMIT")
                    c = SignedStateCommitment.from_dict(raw_collection)
                    if (
                        c.collection_id != self._context["collection_id"]
                        or c.sequence != view["sequence"]
                        or c.root != view["collection_root"]
                        or c.state_sha256 != _state_digest(opaque_state)
                    ):
                        _reject()
                    Ed25519PublicKey.from_public_bytes(self._public).verify(
                        base64.b64decode(c.signature_b64),
                        _ROOT_SIGNATURE_DOMAIN + bytes.fromhex(c.root),
                    )
                    n = view["sequence"]
                    views = s["views"]
                    if n <= len(views) and view["root"] != views[n - 1]["root"]:
                        _reject("ATLAS_STATE_EQUIVOCATION")
                    if n < len(views):
                        _reject("ATLAS_ROLLBACK_REJECTED")
                    if n == len(views):
                        return False
                    if n > LIMIT:
                        _reject("ATLAS_HISTORY_LIMIT")
                    previous = views[-1] if views else None
                    if (
                        n != len(views) + 1
                        or view["previous_root"] != (previous["root"] if previous else ZERO)
                        or view["previous_registry_root"]
                        != (previous["registry_root"] if previous else EMPTY_REGISTRY)
                        or c.previous_root != (previous["collection_root"] if previous else ZERO)
                    ):
                        _reject()
                    if len(opaque_state) > 1024 * 1024:
                        _reject("ATLAS_HISTORY_LIMIT")
                    body = json.loads(opaque_state)
                    if (
                        set(body) != {"format", "version", "route", "records"}
                        or body["format"] != "atlasvault-guarded-collection"
                        or type(body["version"]) is not int
                        or body["version"] != 1
                        or body["route"] not in ("patch", "snapshot", "compaction")
                    ):
                        _reject()
                    if not isinstance(body["records"], list) or len(body["records"]) > LIMIT:
                        _reject("ATLAS_HISTORY_LIMIT")
                    records = {}
                    for raw in body["records"]:
                        r = OpaqueCiphertextEnvelope.from_dict(raw)
                        if (
                            r.version != 1
                            or r.object_id in records
                            or r.key_epoch != view["key_epoch"]
                        ):
                            _reject()
                        records[r.object_id] = {
                            "object_id": r.object_id,
                            "revision": r.revision,
                            "content_sha256": r.content_sha256,
                            "envelope_sha256": hashlib.sha256(
                                _canonical_json(r.to_dict())
                            ).hexdigest(),
                            "tombstone": r.tombstone,
                        }
                    for key, old in s["records"].items():
                        if old["tombstone"] and records.get(key) != old:
                            _reject("ATLAS_TOMBSTONE_RESURRECTION")
                        if key not in records:
                            _reject("ATLAS_STALE_STATE")
                    s["records"] = records
                    s["views"] = [*views, view]
            except StateViewError as e:
                self._alarm(s, str(e), peer, registry_digest)
            self._store.write(s)
            return True
