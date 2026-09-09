"""D102/D104 recipient-pinned origin; admission remains the shared P6 validator."""

import copy
import hashlib

from .authenticated_state_view import LIMIT, _boundary, _reject
from .enrollment_bootstrap import bootstrap_records, reject, verify_anchor
from .epoch_rotation import _canonical
from .sync_queue import _EncryptedQueueFile
from .sync_recovery import GuardedSyncState, _validate_history_chain


class _AnchorStore:
    def __init__(self, file, owner):
        self.file, self.owner, self.path = file, owner, file.path
        self.anchor = None

    def read(self, default):
        outer = self.file.read({})
        if set(outer) != {"anchor", "state"}:
            _reject()
        self.owner._verify(outer["anchor"])
        self.anchor = copy.deepcopy(outer["anchor"])
        return outer["state"]

    def write(self, state):
        if self.anchor is None:
            _reject()
        self.file.write({"anchor": self.anchor, "state": state})


class AnchoredSyncState(GuardedSyncState):
    def __init__(
        self,
        path,
        *,
        encryption_key,
        account_id,
        vault_id,
        collection_id,
        key_epoch,
        trusted_signer,
        rotation_registry,
        anchor_root,
        recipient_device_id,
        confirmed_transcript,
        current_context,
    ):
        super().__init__(
            path,
            encryption_key=encryption_key,
            account_id=account_id,
            vault_id=vault_id,
            collection_id=collection_id,
            key_epoch=key_epoch,
            trusted_signer=trusted_signer,
            rotation_registry=copy.deepcopy(rotation_registry),
        )
        self._pins = copy.deepcopy(
            {
                "anchor_root": anchor_root,
                "recipient_device_id": recipient_device_id,
                "confirmed_transcript": confirmed_transcript,
                "current_context": current_context,
            }
        )
        binding = dict(self._context, **self._pins, registry=self._rotation_registry)
        digest = hashlib.sha256(_canonical(binding)).hexdigest()
        self._store = _AnchorStore(
            _EncryptedQueueFile(
                path, encryption_key=encryption_key, kind=f"anchored-history-v1:{digest}"
            ),
            self,
        )

    def open_context(self):
        return copy.deepcopy(
            dict(
                account_id=self._context["account_id"],
                vault_id=self._context["vault_id"],
                collection_id=self._context["collection_id"],
                key_epoch=self._context["key_epoch"],
                trusted_signer=self._public,
                rotation_registry=self._rotation_registry,
                **self._pins,
            )
        )

    def initialize(self):
        reject("ATLAS_BOOTSTRAP_REQUIRED")

    def _verify(self, anchor):
        if set(anchor) != {"checkpoint", "enrollment", "view"}:
            reject()
        p = verify_anchor(
            **anchor,
            registry=self._rotation_registry,
            current_context=self._pins["current_context"],
            recipient_device_id=self._pins["recipient_device_id"],
            confirmed_transcript=self._pins["confirmed_transcript"],
            trusted_signer=self._public,
        )
        if (
            p["root"] != self._pins["anchor_root"]
            or p["collection_id"] != self._context["collection_id"]
            or p["account_id"] != self._context["account_id"]
            or p["vault_id"] != self._context["vault_id"]
            or p["key_epoch"] != self._context["key_epoch"]
        ):
            reject()
        self._origin = copy.deepcopy(anchor["view"])

    def _chain(self, raw, proof=None):
        if not isinstance(raw, list) or len(raw) > LIMIT:
            _reject("ATLAS_HISTORY_LIMIT")
        if not isinstance(raw, list) or not raw or self._origin is None or raw[0] != self._origin:
            _reject()
        return [raw[0], *_validate_history_chain(self, raw[1:], proof, origin=self._origin)]

    def bootstrap(self, **args):
        with self._lock:
            try:
                if (
                    args["current_context"] != self._pins["current_context"]
                    or args["recipient_device_id"] != self._pins["recipient_device_id"]
                    or args["confirmed_transcript"] != self._pins["confirmed_transcript"]
                    or args["registry"] != self._rotation_registry
                ):
                    reject()
                anchor = {k: copy.deepcopy(args[k]) for k in ("checkpoint", "enrollment", "view")}
                self._verify(anchor)
                records = bootstrap_records(
                    args["checkpoint"], args["collection"], args["opaque_state"], self._public
                )
                if self._store.path.exists():
                    self._active(self._load())
                    if anchor != self._store.anchor:
                        reject()
                    return False
                self._store.anchor = anchor
                self._store.write(
                    {
                        "context": self._context,
                        "views": [anchor["view"]],
                        "records": records,
                        "cases": [],
                        "status": "ACTIVE",
                    }
                )
                return True
            except (ValueError, TypeError, KeyError, OSError):
                reject()

    def recovery(self):
        value = super().recovery()
        if value["status"] == "MANUAL_REQUIRED":
            value["status"] = "RECOVERY_PENDING"
        return value

    def resolve(self, disposition, local_root, peer_root):
        # Anchored-store recovery cannot select or replace its signed origin.
        with self._lock, _boundary():
            self._active(self._load())
            _reject("ATLAS_RECOVERY_PENDING")


def bootstrap_history(history, **args):
    if not isinstance(history, AnchoredSyncState):
        reject("ATLAS_BOOTSTRAP_EXISTING_HISTORY")
    return history.bootstrap(**args)
