"""C27 local epoch publication and recovery; no distributed transaction claim."""

import base64
import copy
import hashlib

from .device_delivery import verify_device_delivery
from .epoch_rotation import _canonical, _reject, delivery_context, verify_epoch_rotation
from .key_epochs import EpochHPKESealedVaultKeyV2, open_epoch_hpke_v2
from .revocation import _decode
from .sync_queue import _EncryptedQueueFile, DurableEncryptedInbox


def bridge_records(state):
    if "epoch_bridges" in state and "epoch_bridge" in state:
        _reject()
    result = state.get(
        "epoch_bridges", [state["epoch_bridge"]] if state.get("epoch_bridge") else []
    )
    if not isinstance(result, list) or len(result) > 32:
        _reject()
    return result


def verify_bridges(records, registry, context):
    epoch = context["key_epoch"]
    result = []
    for raw in records:
        packet = "wrapper" in raw
        p = raw["proof"] if packet else raw
        args = dict(
            registry=registry,
            account_id=context["account_id"],
            vault_id=context["vault_id"],
            previous_epoch=epoch,
            state_root=p["plan"]["state_root"],
        )
        verified = (
            verify_device_delivery(
                raw,
                **args,
                activation_id=p["activation_id"],
                recipient_device_id=p["recipient_device_id"],
            )
            if packet
            else verify_epoch_rotation(raw, **args)
        )
        result.append(
            dict(
                plan=p["plan"],
                registry=p["registry"],
                rotation_signer_device_id=p["rotation_signer_device_id"],
            )
        )
        registry, epoch = verified["registry"], verified["new_epoch"]
    return result


class EpochPublication(_EncryptedQueueFile):
    """Encrypted write-ahead recovery record, then the complete owner publication.

    Once enabled, disagreement is unavailable, never a fallback to an older file.
    The record includes P6 alarms and P5 queues, not only the key-ring subset.
    """

    def __init__(self, path, key):
        super().__init__(path, key, kind="epoch-activation-v1")
        self.anchor = _EncryptedQueueFile(
            self.path.with_name("activation-recovery"), key, kind="epoch-recovery-v1"
        )

    def _record(self):
        record = self.anchor.read({})
        if (
            set(record) != {"state", "sha256"}
            or hashlib.sha256(_canonical(record["state"])).hexdigest() != record["sha256"]
        ):
            _reject("ATLAS_PUBLICATION_RECOVERY_REQUIRED")
        return record["state"]

    def enable(self):
        if not self.anchor.path.exists():
            s = super().read({})
            self.anchor.write(dict(state=s, sha256=hashlib.sha256(_canonical(s)).hexdigest()))

    def read(self, default):
        s = super().read(default)
        if self.anchor.path.exists() and _canonical(s) != _canonical(self._record()):
            _reject("ATLAS_PUBLICATION_RECOVERY_REQUIRED")
        return s

    def write(self, state, *, before_replace=None, after_record=None):
        if self.anchor.path.exists():
            self.anchor.write(
                dict(state=state, sha256=hashlib.sha256(_canonical(state)).hexdigest()),
                before_replace=before_replace,
            )
            if after_record is not None:
                after_record()
            super().write(state)
        else:
            super().write(state, before_replace=before_replace)

    def recover(self):
        s = self._record()
        if s["status"] == "ACTIVE":
            s["status"] = "CATCH_UP_PENDING"
        self.write(s)


class _StagedHistory:
    def __init__(self, state):
        self.state = state

    def read(self, default):
        return copy.deepcopy(self.state)

    def write(self, state):
        self.state = copy.deepcopy(state)


def catch_up(
    owner, packets, current_activation_id, agreement_private_key, checkpoint, history_updates=()
):
    s = owner._load()
    if (
        s["status"] in ("REVOKED", "RECOVERY_PENDING", "CLEANUP_PENDING")
        or owner._history(s).recovery()["status"] != "ACTIVE"
    ):
        _reject("ATLAS_RECOVERY_PENDING")
    journal = s.get("journal") or {}
    if journal.get("kind") == "CATCH_UP" and journal["phase"] == "ACTIVE":
        if journal["target_id"] == current_activation_id:
            if _canonical(journal["packets"]) != _canonical(packets) or _canonical(
                journal.get("history_updates", [])
            ) != _canonical(list(history_updates)):
                _reject("ATLAS_EPOCH_CONFLICT")
            if s["status"] == "CATCH_UP_PENDING":
                s["status"] = "ACTIVE"
                owner._file.write(s)
                return True
            return False
    owner._file.enable()
    s["status"] = "CATCH_UP_PENDING"
    prior = copy.deepcopy(s.get("journal"))
    if journal.get("kind") == "CATCH_UP" and journal["phase"] != "ACTIVE":
        prior = journal["prior_journal"]
    s["journal"] = dict(
        kind="CATCH_UP",
        phase="CATCH_UP_PENDING",
        target_id=current_activation_id,
        packets=[],
        prior_journal=prior,
    )
    owner._file.write(s)
    checkpoint("catch_up_pending")
    if not isinstance(packets, list) or not 1 <= len(packets) <= 32:
        _reject()
    if (
        not isinstance(history_updates, (list, tuple))
        or len(history_updates) > 256
        or len(_canonical(list(history_updates))) > 4 * 1024 * 1024
    ):
        _reject()
    staged = copy.deepcopy(s)
    history = staged["components"]["history"]
    if history["status"] != "ACTIVE" or not history["views"]:
        _reject("ATLAS_RECOVERY_PENDING")
    registry, epoch = s["registry"], s["epoch"]
    bridges = bridge_records(history)
    stage = _StagedHistory(history)
    validator = owner._history(staged)
    validator._store = stage
    update_index = 0
    for packet in packets:
        if packet.get("format") == "atlasvault-activation-record":
            _reject("ATLAS_PER_DEVICE_PROOF_REQUIRED")
        p = packet["proof"]
        same_epoch = p["plan"]["new_epoch"] == epoch
        verify_registry, previous_epoch = registry, epoch
        if same_epoch:
            if not bridges:
                _reject()
            old = bridges[-1].get("proof", bridges[-1])
            old_id = old.get("activation_id", old["root"])
            if p["activation_id"] != old_id or any(
                _canonical(p[k]) != _canonical(old[k])
                for k in ("plan", "registry", "revocation", "rotation_signer_device_id")
            ):
                _reject("ATLAS_EPOCH_CONFLICT")
            old_wrapper = bridges[-1].get("wrapper") or next(
                w for w in old["deliveries"] if w["device_id"] == owner._context["device_id"]
            )
            if _canonical(old_wrapper) != _canonical(packet["wrapper"]):
                _reject()
            verify_registry, previous_epoch = old["registry"], old["plan"]["previous_epoch"]
            bridges.pop()
        while not same_epoch and stage.state["views"][-1]["root"] != p["plan"]["state_root"]:
            if update_index == len(history_updates):
                _reject("ATLAS_HISTORY_CHAIN_REQUIRED")
            update = history_updates[update_index]
            if set(update) != {"view", "registry", "collection", "opaque_state_b64"}:
                _reject()
            try:
                validator.ingest(
                    update["view"],
                    update["registry"],
                    update["collection"],
                    base64.b64decode(update["opaque_state_b64"], validate=True),
                )
            except Exception:
                if stage.state["status"] != "ACTIVE":
                    original = s["components"]["history"]
                    original["cases"] = stage.state["cases"]
                    original["status"] = "RECOVERY_PENDING"
                    s["status"] = "RECOVERY_PENDING"
                    owner._file.write(s)
                raise
            update_index += 1
        verified = verify_device_delivery(
            packet,
            registry=verify_registry,
            account_id=owner._context["account_id"],
            vault_id=owner._context["vault_id"],
            previous_epoch=previous_epoch,
            state_root=p["plan"]["state_root"] if same_epoch else stage.state["views"][-1]["root"],
            activation_id=p["activation_id"],
            recipient_device_id=owner._context["device_id"],
        )
        w = packet["wrapper"]
        opened = open_epoch_hpke_v2(
            recipient_private_key=agreement_private_key,
            sealed=EpochHPKESealedVaultKeyV2(
                key_epoch=w["key_epoch"],
                encapsulated_key=_decode(w["encapsulated_key_b64"], 32),
                ciphertext=_decode(w["ciphertext_b64"], 48),
            ),
            context=delivery_context(p["plan"], owner._context["device_id"]),
            minimum_key_epoch=verified["new_epoch"],
        )
        if same_epoch and _decode(staged["keys"][str(epoch)], 32) != opened.vault_key:
            _reject()
        staged["keys"][str(opened.key_epoch)] = base64.b64encode(opened.vault_key).decode()
        bridges.append(copy.deepcopy(packet))
        stage.state.pop("epoch_bridge", None)
        stage.state["epoch_bridges"] = bridges
        registry, epoch = verified["registry"], verified["new_epoch"]
        checkpoint("verified_epoch")
    if packets[-1]["proof"]["activation_id"] != current_activation_id:
        _reject("ATLAS_EPOCH_CONFLICT")
    if update_index != len(history_updates):
        _reject("ATLAS_HISTORY_CHAIN_REQUIRED")
    staged["components"]["history"] = stage.state
    verify_bridges(bridges, owner._registry, owner._context)
    staged.update(
        epoch=epoch,
        registry=registry,
        recipients=verified["recipients"],
        generation=s["generation"] + 1,
        status="ACTIVE",
    )
    staged["journal"] = dict(
        kind="CATCH_UP",
        phase="ACTIVE",
        target_id=current_activation_id,
        packets=copy.deepcopy(packets),
        history_updates=copy.deepcopy(list(history_updates)),
        prior_journal=None,
    )
    owner._ring(staged)
    owner._file.write(
        staged,
        before_replace=lambda: checkpoint("before_local_commit"),
        after_record=lambda: checkpoint("after_recovery_record"),
    )
    checkpoint("after_local_commit")
    return True


def cleanup(owner, retain_epochs, storage, checkpoint):
    from .epoch_vault import _EpochComponent

    s = owner._load()
    if (
        s["status"] not in ("ACTIVE", "CLEANUP_PENDING")
        or owner._history(s).recovery()["status"] != "ACTIVE"
    ):
        _reject("ATLAS_RECOVERY_PENDING")
    if (
        not isinstance(retain_epochs, set)
        or any(type(e) is not int for e in retain_epochs)
        or s["epoch"] not in retain_epochs
    ):
        _reject()
    if not retain_epochs.issubset({int(e) for e in s["keys"]}):
        _reject()
    needed = {op.envelope.key_epoch for op in owner.pending_operations()}
    inbox = DurableEncryptedInbox(owner._file.path, encryption_key=owner._key)
    inbox._store = _EpochComponent(owner, "inbox")
    needed.update(op.envelope.key_epoch for op in inbox.pending_operations())
    if not needed.issubset(retain_epochs):
        _reject("ATLAS_CLEANUP_PENDING")
    journal = s.get("journal")
    if not isinstance(journal, dict):
        _reject()
    intent = dict(retain_epochs=sorted(retain_epochs))
    if s["status"] == "CLEANUP_PENDING" and journal.get("cleanup") != intent:
        _reject("ATLAS_CLEANUP_PENDING")
    journal["cleanup"] = intent
    owner._file.enable()
    s["status"] = "CLEANUP_PENDING"
    owner._file.write(s)
    checkpoint("cleanup_pending")
    for epoch in sorted({int(k) for k in s["keys"]} - retain_epochs):
        storage.delete_epoch(epoch)
        if storage.contains_epoch(epoch) is not False:
            _reject("ATLAS_CLEANUP_PENDING")
        del s["keys"][str(epoch)]
        checkpoint("deleted_epoch")
    s["status"] = "ACTIVE"
    owner._file.write(s)
