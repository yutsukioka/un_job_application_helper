"""D099 signed membership additions to an immutable D087 activation."""

import copy
import json
import sqlite3
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field
from vaultsync.device_enrollment import verify_enrollment_attestation
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


MAX_CACHED_MEMBERSHIPS = 8
MAX_MEMBERSHIP_CACHE_BYTES = 32 * 1024 * 1024


def _stamp(store):
    # Other connections advance data_version; this connection advances
    # total_changes, including rolled-back writes. Neither can reuse stale trust.
    return (
        store._db.execute("PRAGMA data_version").fetchone()[0],
        store._db.total_changes,
    )


def _cache(store, stamp):
    if store._membership_cache_stamp != stamp:
        store._membership_cache.clear()
        store._membership_cache_stamp = stamp
    return store._membership_cache


def _remember(store, account_id, vault_id, value, stamp):
    cache = _cache(store, stamp)
    size = len(json.dumps(value, separators=(",", ":")))
    if size > MAX_MEMBERSHIP_CACHE_BYTES:
        return
    cache[(account_id, vault_id)] = (copy.deepcopy(value), size)
    cache.move_to_end((account_id, vault_id))
    while (
        len(cache) > MAX_CACHED_MEMBERSHIPS
        or sum(item[1] for item in cache.values()) > MAX_MEMBERSHIP_CACHE_BYTES
    ):
        cache.popitem(last=False)


def membership(store, account_id, vault_id):
    """Reuse only verified state at the unchanged durable database version."""
    with store._lock:
        try:
            for _ in range(3):
                before = _stamp(store)
                cache = _cache(store, before)
                if (account_id, vault_id) in cache:
                    cache.move_to_end((account_id, vault_id))
                    return copy.deepcopy(cache[(account_id, vault_id)][0])
                value = _rebuild_membership(store, account_id, vault_id)
                if _stamp(store) == before:
                    _remember(store, account_id, vault_id, value, before)
                    return value
            raise ActivationUnavailable("ATLAS_ACTIVATION_STORAGE_UNAVAILABLE")
        except (sqlite3.Error, OSError):
            raise ActivationUnavailable(
                "ATLAS_ACTIVATION_STORAGE_UNAVAILABLE"
            ) from None
        except (ValueError, TypeError, KeyError):
            raise CommitmentConflict() from None


def _rebuild_membership(store, account_id, vault_id):
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
            registry = verify_enrollment_attestation(
                addition,
                registry=registry,
                context=context,
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
            # Cold signature/history verification precedes the writer lock.
            # BEGIN then rechecks the DB stamp so a competing commit cannot
            # bypass generation/root CAS with a warmed stale registry.
            membership(store, account_id, vault_id)
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
            registry = verify_enrollment_attestation(
                proof,
                registry=current["registry"],
                context=current["context"],
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
            # Capture while SQLite still excludes external writers. Sampling
            # after COMMIT could stamp our value with a competitor's newer write.
            committed_stamp = _stamp(store)
            store._db.execute("COMMIT")
            current["registry"] = registry
            current["context"]["registry_generation"] = proof[
                "next_registry_generation"
            ]
            current["records"].append(copy.deepcopy(proof))
            _remember(store, account_id, vault_id, current, committed_stamp)
            return True
        except (sqlite3.Error, OSError):
            raise ActivationUnavailable(
                "ATLAS_ACTIVATION_STORAGE_UNAVAILABLE"
            ) from None
        except (ValueError, TypeError, KeyError):
            raise CommitmentConflict() from None
        finally:
            if store._db.in_transaction:
                try:
                    store._db.execute("ROLLBACK")
                except (sqlite3.Error, OSError):
                    raise ActivationUnavailable(
                        "ATLAS_ACTIVATION_STORAGE_UNAVAILABLE"
                    ) from None
