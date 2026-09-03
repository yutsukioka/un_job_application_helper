"""D087 local publication: one encrypted commit contains real P5/P6 components."""

import base64
import copy
import hashlib
import json
import secrets
import threading
from functools import wraps
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .epoch_rotation import (
    RotationError,
    _canonical,
    _reject,
    delivery_context,
    verify_epoch_rotation,
)
from .key_epochs import EpochHPKESealedVaultKeyV2, VaultKeyEpochRing, open_epoch_hpke_v2
from .revocation import _decode, _exact, registry_root
from .sync_queue import (
    DurableEncryptedOutbox,
    EncryptedPatchOperation,
    OpaqueCiphertextEnvelope,
    SignedStateCommitment,
    _identifier,
    _inbox_default,
    _outbox_default,
)
from .sync_recovery import GuardedSyncState


def _checked(method):
    @wraps(method)
    def call(*args, **kwargs):
        try:
            return method(*args, **kwargs)
        except RotationError:
            raise
        except Exception:
            _reject()

    return call


class _EpochComponent:
    """Private adapter to unchanged queue/history validators, not a second store."""

    def __init__(self, owner, name):
        self.owner, self.name, self.path = owner, name, owner._file.path

    def read(self, default):
        return copy.deepcopy(self.owner._load()["components"].get(self.name, default))

    def write(self, value):
        s = self.owner._load()
        s["components"][self.name] = copy.deepcopy(value)
        if self.name == "history" and value["status"] != "ACTIVE":
            s["status"] = "RECOVERY_PENDING"
        self.owner._file.write(s)


class EpochVault:
    """One owner per directory. Only this facade publishes new epoch-scoped output."""

    @_checked
    def __init__(
        self,
        directory,
        *,
        storage_key,
        device_id,
        registry,
        account_id,
        vault_id,
        key_epoch,
        state_root,
    ):
        self._key = bytes(storage_key)
        self._registry = copy.deepcopy(registry)
        self._context = dict(
            account_id=_identifier(account_id),
            vault_id=_identifier(vault_id),
            device_id=_identifier(device_id),
            key_epoch=key_epoch,
            state_root=state_root,
            registry_root=registry_root(registry),
        )
        if (
            device_id not in [e["device_id"] for e in registry]
            or type(key_epoch) is not int
            or not 1 <= key_epoch < 9007199254740991
        ):
            _reject()
        if (
            type(state_root) is not str
            or len(state_root) != 64
            or any(c not in "0123456789abcdef" for c in state_root)
        ):
            _reject()
        from .epoch_catch_up import EpochPublication

        self._file = EpochPublication(Path(directory) / "activation", self._key)
        self._lock = threading.RLock()

    def _verify(self, proof):
        return verify_epoch_rotation(
            proof,
            registry=self._registry,
            account_id=self._context["account_id"],
            vault_id=self._context["vault_id"],
            previous_epoch=self._context["key_epoch"],
            state_root=self._context["state_root"],
        )

    def _record(self, record):
        _exact(record, {"format", "version", "status", "transition_id", "proof"})
        if (
            record["format"] != "atlasvault-activation-record"
            or type(record["version"]) is not int
            or record["version"] != 1
            or record["status"] != "ACTIVATION_ACCEPTED"
            or record["transition_id"] != record["proof"]["root"]
        ):
            _reject()
        return self._verify(record["proof"])

    def _load(self):
        s = self._file.read({})
        _exact(
            s,
            {
                "context",
                "status",
                "epoch",
                "registry",
                "recipients",
                "keys",
                "components",
                "journal",
                "generation",
            },
        )
        if s["context"] != self._context or s["status"] not in (
            "ACTIVE",
            "ACTIVATION_PENDING",
            "REVOKED",
            "RECOVERY_PENDING",
            "CATCH_UP_PENDING",
            "CLEANUP_PENDING",
        ):
            _reject()
        registry_root(s["registry"])
        self._ring(s)
        if set(s["components"]) != {"history", "outbox", "inbox"}:
            _reject()
        journal = s["journal"]
        if journal and journal.get("kind") == "CATCH_UP":
            from .epoch_catch_up import bridge_records, verify_bridges

            if journal["phase"] not in ("ACTIVE", "CATCH_UP_PENDING"):
                _reject()
            bridges = verify_bridges(
                bridge_records(s["components"]["history"]), self._registry, self._context
            )
            if journal["phase"] == "ACTIVE":
                if (
                    not bridges
                    or s["epoch"] != bridges[-1]["plan"]["new_epoch"]
                    or registry_root(s["registry"])
                    != bridges[-1]["plan"]["resulting_registry_root"]
                    or s["recipients"] != bridges[-1]["plan"]["recipients"]
                ):
                    _reject()
            return s
        if journal:
            if journal["phase"] not in (
                "PREPARED",
                "BACKEND_SUBMITTED",
                "BACKEND_ACCEPTED",
                "LOCAL_PUBLISHING",
                "ACTIVE",
                "RECOVERY_PENDING",
            ):
                _reject()
            verified = self._verify(journal["proof"])
            if journal.get("record") is not None:
                self._record(journal["record"])
                if journal["record"]["proof"] != journal["proof"]:
                    _reject()
            if journal["phase"] == "ACTIVE":
                if (
                    s["epoch"] != verified["new_epoch"]
                    or s["registry"] != verified["registry"]
                    or s["recipients"] != verified["recipients"]
                    or s["components"]["history"].get("epoch_bridge") != journal["proof"]
                ):
                    _reject()
        return s

    def _ring(self, s):
        if type(s["keys"]) is not dict or not 1 <= len(s["keys"]) <= 32:
            _reject()
        keys = {int(k): _decode(v, 32) for k, v in s["keys"].items()}
        if any(str(k) not in s["keys"] for k in keys):
            _reject()
        return VaultKeyEpochRing.from_entries(current_key_epoch=s["epoch"], keys=keys)

    def _history(self, s):
        context = s["components"]["history"]["context"]
        if any(context[k] != self._context[k] for k in ("account_id", "vault_id", "key_epoch")):
            _reject()
        h = GuardedSyncState(
            self._file.path,
            encryption_key=self._key,
            account_id=context["account_id"],
            vault_id=context["vault_id"],
            key_epoch=context["key_epoch"],
            collection_id=context["collection_id"],
            trusted_signer=_decode(context["signing_public_b64"], 32),
            rotation_registry=self._registry,
        )
        h._store = _EpochComponent(self, "history")
        return h

    def _active(self, s):
        if s["status"] in ("CATCH_UP_PENDING", "CLEANUP_PENDING"):
            _reject("ATLAS_" + s["status"])
        if s["status"] == "REVOKED":
            _reject("ATLAS_DEVICE_REVOKED")
        if s["status"] == "ACTIVATION_PENDING":
            _reject("ATLAS_ACTIVATION_PENDING")
        if s["status"] != "ACTIVE" or self._history(s).recovery()["status"] != "ACTIVE":
            _reject("ATLAS_RECOVERY_PENDING")

    @_checked
    def initialize(self, keys, *, history, outbox=None, inbox=None):
        with self._lock:
            if self._file.path.exists():
                _reject()
            h = history._load()
            if (
                h["status"] != "ACTIVE"
                or not h["views"]
                or h["views"][-1]["root"] != self._context["state_root"]
                or any(
                    h["context"][k] != self._context[k]
                    for k in ("account_id", "vault_id", "key_epoch")
                )
            ):
                _reject("ATLAS_RECOVERY_PENDING")
            VaultKeyEpochRing.from_entries(current_key_epoch=self._context["key_epoch"], keys=keys)
            if outbox:
                outbox.pending_operations()
            if inbox:
                inbox.pending_operations()
            self._file.write(
                dict(
                    context=self._context,
                    status="ACTIVE",
                    epoch=self._context["key_epoch"],
                    registry=self._registry,
                    recipients=sorted(
                        e["device_id"] for e in self._registry if e["state"] == "ACTIVE"
                    ),
                    keys={str(k): base64.b64encode(v).decode() for k, v in keys.items()},
                    components=dict(
                        history=h,
                        outbox=outbox._store.read(_outbox_default())
                        if outbox
                        else _outbox_default(),
                        inbox=inbox._store.read(_inbox_default()) if inbox else _inbox_default(),
                    ),
                    journal=None,
                    generation=1,
                )
            )

    def catch_up(
        self, packets, *, current_activation_id, agreement_private_key, history_updates=()
    ):
        return self._catch_up_for_testing(
            packets,
            current_activation_id=current_activation_id,
            agreement_private_key=agreement_private_key,
            history_updates=history_updates,
        )

    @_checked
    def _catch_up_for_testing(
        self,
        packets,
        *,
        current_activation_id,
        agreement_private_key,
        history_updates=(),
        checkpoint=lambda stage: None,
    ):
        from .epoch_catch_up import catch_up

        with self._lock:
            return catch_up(
                self,
                packets,
                current_activation_id,
                agreement_private_key,
                checkpoint,
                history_updates,
            )

    @_checked
    def recover_publication(self):
        with self._lock:
            self._file.recover()
            self._history(self._load()).recovery()

    @_checked
    def available_epochs(self):
        with self._lock:
            return sorted(int(k) for k in self._load()["keys"])

    def cleanup_epochs(self, *, retain_epochs, storage):
        return self._cleanup_epochs_for_testing(retain_epochs=retain_epochs, storage=storage)

    @_checked
    def _cleanup_epochs_for_testing(self, *, retain_epochs, storage, checkpoint=lambda stage: None):
        from .epoch_catch_up import cleanup

        with self._lock:
            return cleanup(self, retain_epochs, storage, checkpoint)

    @_checked
    def observation(self):
        with self._lock:
            s = self._load()
            history = self._history(s)
            h = history.checkpoint()
            return dict(
                status=s["status"],
                key_epoch=s["epoch"],
                registry_root=registry_root(s["registry"]),
                recipients=s["recipients"],
                state_root=h["cursor"],
                sequence=h["sequence"],
                generation=s["generation"],
                journal_phase=s["journal"]["phase"] if s["journal"] else None,
                recipient_commitment=hashlib.sha256(
                    b"atlasvault-active-recipients-v1\n"
                    + _canonical({"recipients": s["recipients"]})
                ).hexdigest(),
            )

    @_checked
    def prepare_rotation(self, proof):
        with self._lock:
            s = self._load()
            self._active(s)
            self._verify(proof)
            if self._history(s).checkpoint()["cursor"] != proof["plan"]["state_root"]:
                _reject("ATLAS_RECOVERY_PENDING")
            if s["journal"] and s["journal"]["proof"]["root"] != proof["root"]:
                _reject("ATLAS_EPOCH_CONFLICT")
            s["journal"] = dict(phase="PREPARED", proof=copy.deepcopy(proof), record=None)
            self._file.write(s)

    @_checked
    def begin_activation(self, proof):
        with self._lock:
            self.prepare_rotation(proof)
            s = self._load()
            s["status"] = "ACTIVATION_PENDING"
            s["journal"]["phase"] = "BACKEND_SUBMITTED"
            self._file.write(s)

    def accept_rotation(self, proof, *, accepted_record, agreement_private_key):
        return self._accept_rotation_for_testing(
            proof, accepted_record=accepted_record, agreement_private_key=agreement_private_key
        )

    @_checked
    def _accept_rotation_for_testing(
        self, proof, *, accepted_record, agreement_private_key, checkpoint=lambda stage: None
    ):
        with self._lock:
            s = self._load()
            result = self._record(accepted_record)
            if accepted_record["proof"] != proof:
                _reject()
            if s["status"] == "REVOKED":
                _reject("ATLAS_DEVICE_REVOKED")
            if (
                s["status"] == "RECOVERY_PENDING"
                or self._history(s).recovery()["status"] != "ACTIVE"
            ):
                _reject("ATLAS_RECOVERY_PENDING")
            if s["journal"] and s["journal"]["proof"]["root"] != proof["root"]:
                _reject("ATLAS_EPOCH_CONFLICT")
            if s["journal"] and s["journal"]["phase"] == "ACTIVE":
                return False
            s["status"] = "ACTIVATION_PENDING"
            s["journal"] = dict(
                phase="BACKEND_ACCEPTED",
                proof=copy.deepcopy(proof),
                record=copy.deepcopy(accepted_record),
            )
            self._file.write(s)
            checkpoint("backend_accepted")
            device = self._context["device_id"]
            if device not in result["recipients"]:
                s["status"] = "REVOKED"
                self._file.write(s)
                _reject("ATLAS_DEVICE_REVOKED")
            delivery = next(d for d in proof["deliveries"] if d["device_id"] == device)
            opened = open_epoch_hpke_v2(
                recipient_private_key=agreement_private_key,
                sealed=EpochHPKESealedVaultKeyV2(
                    key_epoch=delivery["key_epoch"],
                    encapsulated_key=_decode(delivery["encapsulated_key_b64"], 32),
                    ciphertext=_decode(delivery["ciphertext_b64"], 48),
                ),
                context=delivery_context(proof["plan"], device),
                minimum_key_epoch=result["new_epoch"],
            )
            staged = copy.deepcopy(s)
            staged["components"]["history"] = self._history(s)._stage_epoch(proof)
            staged["keys"][str(opened.key_epoch)] = base64.b64encode(opened.vault_key).decode()
            staged.update(
                epoch=result["new_epoch"],
                registry=result["registry"],
                recipients=result["recipients"],
                generation=s["generation"] + 1,
                status="ACTIVE",
            )
            self._ring(staged)
            staged["journal"]["phase"] = "ACTIVE"
            s["journal"]["phase"] = "LOCAL_PUBLISHING"
            self._file.write(s)
            checkpoint("local_publishing")
            self._file.write(staged, before_replace=lambda: checkpoint("before_local_commit"))
            checkpoint("after_local_commit")
            return True

    @_checked
    def queue_operation(self, operation):
        with self._lock:
            s = self._load()
            self._active(s)
            operation = EncryptedPatchOperation.from_dict(operation.to_dict())
            if (
                operation.envelope.key_epoch != s["epoch"]
                or operation.author_device_id != self._context["device_id"]
            ):
                _reject("ATLAS_EPOCH_WRITE_REJECTED")
            outbox = DurableEncryptedOutbox(self._file.path, encryption_key=self._key)
            outbox._store = _EpochComponent(self, "outbox")
            outbox.enqueue(operation)

    @_checked
    def pending_operations(self):
        with self._lock:
            outbox = DurableEncryptedOutbox(self._file.path, encryption_key=self._key)
            outbox._store = _EpochComponent(self, "outbox")
            return outbox.pending_operations()

    @_checked
    def compare_evidence(self, peer):
        with self._lock:
            return self._history(self._load()).compare_evidence(peer)

    @_checked
    def recovery(self):
        with self._lock:
            s = self._load()
            result = self._history(s).recovery()
            if result["status"] == "ACTIVE" and s["status"] != "ACTIVE":
                result.update(status=s["status"], reason="ATLAS_" + s["status"])
            return result

    @_checked
    def pending_activation(self):
        """Recover the exact ciphertext-safe request for a lost-response retry."""
        with self._lock:
            state = self._load()
            if (
                state["status"] in ("REVOKED", "RECOVERY_PENDING")
                or self._history(state).recovery()["status"] != "ACTIVE"
            ):
                _reject("ATLAS_RECOVERY_PENDING")
            journal = state["journal"]
            return (
                copy.deepcopy(journal["proof"])
                if journal and journal["phase"] != "ACTIVE"
                else None
            )

    @_checked
    def create_commitment(self, opaque_state, *, signing_key):
        from .authenticated_state_view import _message, _root

        with self._lock:
            s = self._load()
            self._active(s)
            if not s["journal"] or s["journal"]["phase"] != "ACTIVE":
                _reject()
            history = self._history(s)
            prior = history.export_evidence()[-1]
            c = SignedStateCommitment.sign(
                opaque_state,
                collection_id=s["components"]["history"]["context"]["collection_id"],
                sequence=prior["sequence"] + 1,
                previous_root=prior["collection_root"],
                signing_key=signing_key,
            )
            unsigned = dict(
                format="atlasvault-authenticated-state-view",
                version=2,
                account_id=self._context["account_id"],
                vault_id=self._context["vault_id"],
                sequence=c.sequence,
                previous_root=prior["root"],
                collection_root=c.root,
                registry_root=registry_root(s["registry"]),
                previous_registry_root=prior["registry_root"],
                key_epoch=s["epoch"],
            )
            root = _root(unsigned)
            view = dict(
                unsigned,
                root=root,
                signature_b64=base64.b64encode(signing_key.sign(_message(root))).decode(),
            )
            history.ingest(view, s["registry"], c.to_dict(), opaque_state)
            return dict(view=view, collection=c.to_dict())

    @_checked
    def delivery(self, recipient):
        with self._lock:
            s = self._load()
            self._active(s)
            if (
                not s["journal"]
                or s["journal"]["phase"] != "ACTIVE"
                or recipient not in s["recipients"]
            ):
                _reject("ATLAS_DEVICE_REVOKED")
            if s["journal"].get("kind") == "CATCH_UP":
                if recipient != self._context["device_id"]:
                    _reject("ATLAS_DEVICE_DELIVERY_REJECTED")
                return copy.deepcopy(s["journal"]["packets"][-1]["wrapper"])
            return copy.deepcopy(
                next(d for d in s["journal"]["proof"]["deliveries"] if d["device_id"] == recipient)
            )

    @_checked
    def seal(self, kind, plaintext, *, object_id, revision, signing_key):
        with self._lock:
            s = self._load()
            self._active(s)
            ring = self._ring(s)
            if (
                kind not in ("patch", "snapshot")
                or type(plaintext) is not bytes
                or len(plaintext) > 1024 * 1024
            ):
                _reject()
            metadata = dict(
                format="atlasvault-epoch-ciphertext",
                version=1,
                account_id=self._context["account_id"],
                vault_id=self._context["vault_id"],
                key_epoch=s["epoch"],
                device_id=self._context["device_id"],
                kind=kind,
                object_id=_identifier(object_id),
                revision=_identifier(revision),
            )
            aad, nonce = _canonical(metadata), secrets.token_bytes(12)
            key = ring.derive_record_key(
                key_epoch=s["epoch"], vault_id=self._context["vault_id"], record_id=object_id
            )
            ciphertext = AESGCM(key).encrypt(nonce, plaintext, aad)
            message = b"atlasvault-epoch-ciphertext-signature-v1\0" + aad + nonce + ciphertext
            signature = signing_key.sign(message)
            entry = next(e for e in s["registry"] if e["device_id"] == self._context["device_id"])
            Ed25519PublicKey.from_public_bytes(_decode(entry["signing_public_b64"], 32)).verify(
                signature, message
            )

            def b64(value):
                return base64.b64encode(value).decode()

            return OpaqueCiphertextEnvelope.from_dict(
                dict(
                    format="atlasvault-opaque-ciphertext-envelope",
                    version=1,
                    object_id=object_id,
                    revision=revision,
                    parent_revision=None,
                    key_epoch=s["epoch"],
                    nonce_b64=b64(nonce),
                    ciphertext_b64=b64(ciphertext),
                    aad_b64=b64(aad),
                    signature_b64=b64(signature),
                    tombstone=False,
                    content_sha256=hashlib.sha256(ciphertext).hexdigest(),
                )
            )

    @_checked
    def open(self, envelope):
        with self._lock:
            s = self._load()
            ring = self._ring(s)
            envelope = OpaqueCiphertextEnvelope.from_dict(envelope.to_dict())
            aad = base64.b64decode(envelope.aad_b64)
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
                metadata["format"] != "atlasvault-epoch-ciphertext"
                or type(metadata["version"]) is not int
                or metadata["version"] != 1
                or metadata["kind"] not in ("patch", "snapshot")
                or any(metadata[k] != self._context[k] for k in ("account_id", "vault_id"))
                or metadata["key_epoch"] != envelope.key_epoch
                or metadata["object_id"] != envelope.object_id
                or metadata["revision"] != envelope.revision
                or _canonical(metadata) != aad
            ):
                _reject()
            if (
                envelope.key_epoch > self._context["key_epoch"]
                and metadata["device_id"] not in s["recipients"]
            ):
                _reject("ATLAS_DEVICE_REVOKED")
            nonce, ciphertext = (
                base64.b64decode(envelope.nonce_b64),
                base64.b64decode(envelope.ciphertext_b64),
            )
            entry = next(e for e in self._registry if e["device_id"] == metadata["device_id"])
            Ed25519PublicKey.from_public_bytes(_decode(entry["signing_public_b64"], 32)).verify(
                _decode(envelope.signature_b64, 64),
                b"atlasvault-epoch-ciphertext-signature-v1\0" + aad + nonce + ciphertext,
            )
            key = ring.derive_record_key(
                key_epoch=envelope.key_epoch,
                vault_id=self._context["vault_id"],
                record_id=envelope.object_id,
            )
            return AESGCM(key).decrypt(nonce, ciphertext, aad)
