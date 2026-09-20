"""Versioned removal contract; epoch application deliberately belongs to C26."""

from __future__ import annotations

import asyncio
import base64
import copy
import hashlib
import math
import re
import threading
import time
from functools import wraps

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from .device_identity import derive_device_id
from .sync_queue import _EncryptedQueueFile


class RevocationError(ValueError):
    """Stable, secret-free failure; no platform exception text is propagated."""


def _fail(code="ATLAS_REVOCATION_REJECTED"):
    raise RevocationError(code) from None


def _boundary(function):
    @wraps(function)
    def checked(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except RevocationError:
            raise
        except Exception:  # noqa: BLE001 - convert all boundary failures to secret-free rejection.
            raise RevocationError("ATLAS_REVOCATION_REJECTED") from None

    return checked


def _exact(value, fields):
    if type(value) is not dict or set(value) != set(fields):
        _fail()


def _identifier(value):
    if type(value) is not str or not re.fullmatch(r"[A-Za-z0-9_.~-]{1,128}", value):
        _fail()
    return value


def _hex(value):
    if type(value) is not str or not re.fullmatch(r"[0-9a-f]{64}", value):
        _fail()
    return value


def _number(value):
    if type(value) is not int or not 1 <= value < 9007199254740991:
        _fail()
    return value


def _decode(value, size):
    if type(value) is not str or len(value) != 4 * ((size + 2) // 3):
        _fail()
    result = base64.b64decode(value, validate=True)
    if len(result) != size or base64.b64encode(result).decode() != value:
        _fail()
    return result


@_boundary
def registry_root(entries):
    if type(entries) is not list or not 1 <= len(entries) <= 256:
        _fail()
    rows = {}
    for entry in entries:
        _exact(entry, {"device_id", "signing_public_b64", "agreement_public_b64", "state"})
        signing = _decode(entry["signing_public_b64"], 32)
        agreement = _decode(entry["agreement_public_b64"], 32)
        device = derive_device_id(signing, agreement)
        if (
            entry["device_id"] != device
            or device in rows
            or entry["state"] not in ("ACTIVE", "REVOKED")
        ):
            _fail()
        rows[device] = f"{device}:{signing.hex()}:{agreement.hex()}:{entry['state']}\n"
    return hashlib.sha256(
        ("atlasvault-revocation-registry-v1\n" + "".join(rows[k] for k in sorted(rows))).encode()
    ).hexdigest()


FIELDS = (
    "account_id",
    "vault_id",
    "target_device_id",
    "initiator_device_id",
    "prior_registry_root",
    "resulting_registry_root",
    "key_epoch",
    "sequence",
    "authorization_category",
)


def _root(unsigned):
    _exact(unsigned, {"format", "version", *FIELDS})
    if (
        unsigned["format"] != "atlasvault-device-revocation"
        or type(unsigned["version"]) is not int
        or unsigned["version"] != 1
    ):
        _fail()
    for field in FIELDS:
        if field in ("key_epoch", "sequence"):
            _number(unsigned[field])
        elif field.endswith("root"):
            _hex(unsigned[field])
        else:
            _identifier(unsigned[field])
    if unsigned["authorization_category"] != "DEVICE_PRESENCE":
        _fail()
    return hashlib.sha256(
        (
            "atlasvault-device-revocation-v1\n" + "".join(str(unsigned[f]) + "\n" for f in FIELDS)
        ).encode()
    ).hexdigest()


def _message(root):
    return b"atlasvault-revocation-signature-v1\0" + bytes.fromhex(root)


def _removed(entries, target, initiator):
    registry_root(entries)
    active = {e["device_id"] for e in entries if e["state"] == "ACTIVE"}
    if (
        target == initiator
        or target not in active
        or initiator not in active
        or len(active - {target}) == 0
    ):
        _fail("ATLAS_REMOVAL_AUTHORITY")
    return [dict(e, state="REVOKED" if e["device_id"] == target else e["state"]) for e in entries]


@_boundary
def verify_transition(transition, registry):
    _exact(transition, {"format", "version", *FIELDS, "root", "signature_b64"})
    unsigned = {k: v for k, v in transition.items() if k not in ("root", "signature_b64")}
    root = _root(unsigned)
    after = _removed(registry, transition["target_device_id"], transition["initiator_device_id"])
    if (
        transition["root"] != root
        or transition["prior_registry_root"] != registry_root(registry)
        or transition["resulting_registry_root"] != registry_root(after)
    ):
        _fail()
    signer = next(e for e in registry if e["device_id"] == transition["initiator_device_id"])
    Ed25519PublicKey.from_public_bytes(_decode(signer["signing_public_b64"], 32)).verify(
        _decode(transition["signature_b64"], 64), _message(root)
    )
    return after


@_boundary
def validate_rotation_plan(plan, transition, registry, state_root):
    """Validate metadata against an already verified removal; no key generation/apply."""
    _hex(state_root)
    _exact(transition, {"format", "version", *FIELDS, "root", "signature_b64"})
    if transition["root"] != _root(
        {k: v for k, v in transition.items() if k not in ("root", "signature_b64")}
    ):
        _fail()
    _decode(transition["signature_b64"], 64)
    if registry_root(registry) != transition["resulting_registry_root"]:
        _fail()
    expected = {
        "format": "atlasvault-rotation-plan",
        "version": 1,
        "account_id": transition["account_id"],
        "vault_id": transition["vault_id"],
        "previous_epoch": _number(transition["key_epoch"]),
        "new_epoch": transition["key_epoch"] + 1,
        "prior_registry_root": transition["prior_registry_root"],
        "resulting_registry_root": transition["resulting_registry_root"],
        "state_root": state_root,
        "initiator_device_id": transition["initiator_device_id"],
        "revocation_root": transition["root"],
        "recipients": sorted(e["device_id"] for e in registry if e["state"] == "ACTIVE"),
    }
    _exact(plan, expected)
    if any(type(plan[k]) is not type(v) or plan[k] != v for k, v in expected.items()):
        _fail("ATLAS_ROTATION_PLAN_REJECTED")


class RevocationRegistry:
    """One owner per file, anchored to a trusted P6 registry/checkpoint. No pruning."""

    @_boundary
    def __init__(self, path, encryption_key, account_id, vault_id, key_epoch, registry, state_root):
        self._context = {
            "account_id": _identifier(account_id),
            "vault_id": _identifier(vault_id),
            "key_epoch": _number(key_epoch),
            "state_root": _hex(state_root),
            "registry_root": registry_root(registry),
        }
        self._initial = copy.deepcopy(registry)
        self._file = _EncryptedQueueFile(path, encryption_key, kind="device-revocation-v1")
        self._lock = threading.RLock()

    def _read(self):
        if not self._file.path.exists() or self._file.path.stat().st_size > 1024 * 1024:
            _fail()
        value = self._file.read({})
        _exact(value, {"context", "transition", "recovery_pending"})
        if value["context"] != self._context or type(value["recovery_pending"]) is not bool:
            _fail()
        transition = value["transition"]
        if transition is not None:
            self._check_context(transition)
            verify_transition(transition, self._initial)
        return value

    def _check_context(self, transition):
        if (
            any(transition[k] != self._context[k] for k in ("account_id", "vault_id", "key_epoch"))
            or type(transition["sequence"]) is not int
            or transition["sequence"] != 1
        ):
            _fail()

    def _assert_history(self, views):
        if (
            not views
            or any(
                views[-1][k] != self._context[k] for k in ("account_id", "vault_id", "key_epoch")
            )
            or views[-1]["root"] != self._context["state_root"]
        ):
            _fail("ATLAS_REMOVAL_PENDING")

    @_boundary
    def initialize(self):
        with self._lock:
            if self._file.path.exists():
                _fail()
            self._file.write(
                {"context": self._context, "transition": None, "recovery_pending": False}
            )

    @_boundary
    def snapshot(self):
        with self._lock:
            value = self._read()
            transition = value["transition"]
            entries = (
                self._initial
                if transition is None
                else verify_transition(transition, self._initial)
            )
            return copy.deepcopy(
                {
                    "registry": entries,
                    "root": registry_root(entries),
                    "sequence": 0 if transition is None else 1,
                    "status": "RECOVERY_PENDING"
                    if value["recovery_pending"]
                    else "ACTIVE"
                    if transition is None
                    else "REVOCATION_PENDING",
                    "transition": transition,
                }
            )

    @_boundary
    def prepare(self, target, initiator):
        with self._lock:
            state = self.snapshot()
            if state["status"] != "ACTIVE":
                _fail("ATLAS_REMOVAL_PENDING")
            after = _removed(state["registry"], target, initiator)
            return {
                "format": "atlasvault-device-revocation",
                "version": 1,
                "account_id": self._context["account_id"],
                "vault_id": self._context["vault_id"],
                "target_device_id": target,
                "initiator_device_id": initiator,
                "prior_registry_root": state["root"],
                "resulting_registry_root": registry_root(after),
                "key_epoch": self._context["key_epoch"],
                "sequence": 1,
                "authorization_category": "DEVICE_PRESENCE",
            }

    @_boundary
    def commit(self, transition):
        with self._lock:
            value = self._read()
            if value["recovery_pending"]:
                _fail("ATLAS_REMOVAL_PENDING")
            self._check_context(transition)
            verify_transition(transition, self._initial)
            if (
                value["transition"] is not None
                and value["transition"]["root"] == transition["root"]
            ):
                return False
            if value["transition"] is not None:
                _fail("ATLAS_REVOCATION_CONFLICT")
            value["transition"] = copy.deepcopy(transition)
            self._file.write(value)
            return True

    @_boundary
    def fence(self):
        with self._lock:
            value = self._read()
            value["recovery_pending"] = True
            self._file.write(value)


class RemovalController:
    """Ephemeral prompt ownership. Boolean authorization is never cached or exported."""

    def __init__(self, registry, initiator, authorize, sign, *, clock=time.monotonic, history=None):
        self.registry, self.initiator = registry, initiator
        self._authorize, self._sign, self._clock = authorize, sign, clock
        self._target, self._generation, self._busy = None, 0, False
        self._history = history

    def _history_guard(self, operation):
        if self._history is None:
            return operation()
        try:
            self.registry._assert_history(self._history.export_evidence())
            return self._history.automatic_sync(operation)
        except Exception:  # noqa: BLE001 - unknown history failures must fence removal.
            self.registry.fence()
            raise RevocationError("ATLAS_REMOVAL_PENDING") from None

    def select(self, target):
        self._target = _identifier(target)
        self._generation += 1

    def cancel(self):
        self._generation += 1

    async def remove(self, confirmed_target):
        if self._busy or confirmed_target != self._target:
            _fail("ATLAS_REMOVAL_AUTHORIZATION")
        self._busy = True
        generation, started = self._generation, self._clock()
        try:
            unsigned = self._history_guard(
                lambda: self.registry.prepare(confirmed_target, self.initiator)
            )

            def revalidate():
                elapsed = self._clock() - started
                if (
                    not math.isfinite(elapsed)
                    or not 0 <= elapsed < 60
                    or generation != self._generation
                    or self._history_guard(
                        lambda: self.registry.prepare(confirmed_target, self.initiator)
                    )
                    != unsigned
                ):
                    _fail("ATLAS_REMOVAL_AUTHORIZATION")

            if await asyncio.wait_for(self._authorize(), timeout=60) is not True:
                _fail("ATLAS_REMOVAL_AUTHORIZATION")
            revalidate()
            root = _root(unsigned)
            signature = await asyncio.wait_for(self._sign(_message(root)), timeout=60)
            revalidate()
            signed = dict(unsigned, root=root, signature_b64=base64.b64encode(signature).decode())
            self._history_guard(lambda: self.registry.commit(signed))
            self.cancel()
            return signed
        except asyncio.CancelledError:
            self.cancel()
            raise
        except Exception:  # noqa: BLE001 - provider exceptions are denied, never logged or propagated.
            raise RevocationError("ATLAS_REMOVAL_AUTHORIZATION") from None
        finally:
            self._busy = False
