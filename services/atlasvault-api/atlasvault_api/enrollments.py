"""D099 signed membership additions to an immutable D087 activation."""

import json
import sqlite3
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field
from vaultsync.device_enrollment import verify_enrollment
from vaultsync.revocation import verify_transition

from .commitments import (
    ActivationUnavailable,
    CommitmentConflict,
    Counter,
    Digest,
    Identifier,
)

PublicKey = Annotated[
    str, Field(min_length=44, max_length=44, pattern=r"^[A-Za-z0-9+/]+=$")
]


class DeviceEnrollmentProof(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    format: Literal["atlasvault-device-enrollment"]
    version: Literal[1]
    account_id: Identifier
    vault_id: Identifier
    registry_generation: Counter
    next_registry_generation: Counter
    key_epoch: Counter
    state_root: Digest
    activation_id: Digest
    prior_registry_root: Digest
    resulting_registry_root: Digest
    target_device_id: Identifier
    target_signing_public_b64: PublicKey
    target_agreement_public_b64: PublicKey
    target_agreement_sha256: Digest
    transcript_sha256: Digest
    issuer_device_id: Identifier
    authorization_category: Literal["SAS_CONFIRMED"]
    signature_algorithm: Literal["Ed25519"]
    root: Digest
    signature_b64: Annotated[
        str, Field(min_length=88, max_length=88, pattern=r"^[A-Za-z0-9+/]+==$")
    ]


class EnrollmentReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    root: Digest
    registry_generation: Counter
    key_epoch: Counter
    appended: bool


def membership(store, account_id, vault_id):
    with store._lock:
        current = store.activation(account_id, vault_id)
        if current is None:
            raise CommitmentConflict()
        proof = current["proof"]
        registry = verify_transition(proof["revocation"], proof["registry"])
        history = store.read(account_id, vault_id)
        roots = {view["root"]: index for index, view in enumerate(history)}
        context = {
            "account_id": account_id,
            "vault_id": vault_id,
            "key_epoch": proof["plan"]["new_epoch"],
            "registry_generation": proof["plan"]["new_epoch"],
            "activation_id": current["transition_id"],
            "state_root": proof["plan"]["state_root"],
        }
        position = roots.get(context["state_root"], -1)
        if position < 0:
            raise CommitmentConflict()
        records = []
        for row in store._db.execute(
            "SELECT body FROM enrollments WHERE account=? AND vault=? AND activation=? ORDER BY generation",
            (account_id, vault_id, current["transition_id"]),
        ):
            addition = json.loads(row[0])
            next_position = roots.get(addition["state_root"], -1)
            if next_position < position:
                raise CommitmentConflict()
            context["state_root"] = addition["state_root"]
            registry = verify_enrollment(
                addition,
                registry=registry,
                context=context,
                confirmed_transcript=addition["transcript_sha256"],
                status="ACTIVE",
            )
            position = next_position
            context["registry_generation"] = addition["next_registry_generation"]
            records.append(addition)
        context["state_root"] = history[-1]["root"]
        return {"registry": registry, "context": context, "records": records}


def accept(store, account_id, vault_id, proof, issuer_id):
    if not store._durable:
        raise ActivationUnavailable("ATLAS_ACTIVATION_STORAGE_UNAVAILABLE")
    with store._lock:
        try:
            store._db.execute("BEGIN IMMEDIATE")
            current = membership(store, account_id, vault_id)
            if proof["issuer_device_id"] != issuer_id or not any(
                entry["device_id"] == issuer_id and entry["state"] == "ACTIVE"
                for entry in current["registry"]
            ):
                raise CommitmentConflict()
            body = json.dumps(proof, sort_keys=True, separators=(",", ":"))
            for old in current["records"]:
                if old["root"] == proof["root"]:
                    if old != proof:
                        raise CommitmentConflict()
                    store._db.execute("COMMIT")
                    return False
            verify_enrollment(
                proof,
                registry=current["registry"],
                context=current["context"],
                confirmed_transcript=proof["transcript_sha256"],
                status="ACTIVE",
            )
            if (
                len(body) > 16384
                or store._db.execute(
                    "SELECT COUNT(*) FROM enrollments WHERE account=? AND vault=?",
                    (account_id, vault_id),
                ).fetchone()[0]
                >= 8192
            ):
                raise CommitmentConflict()
            store._db.execute(
                "INSERT INTO enrollments VALUES(?,?,?,?,?,?)",
                (
                    account_id,
                    vault_id,
                    proof["activation_id"],
                    proof["next_registry_generation"],
                    proof["root"],
                    body,
                ),
            )
            store._db.execute("COMMIT")
            return True
        except (ValueError, TypeError, KeyError, sqlite3.Error):
            raise CommitmentConflict() from None
        finally:
            if store._db.in_transaction:
                store._db.execute("ROLLBACK")
