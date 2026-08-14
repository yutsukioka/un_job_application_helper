from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import struct
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from threading import Lock
from typing import Any, Literal, Mapping, Protocol

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from vaultsync.device_identity import (
    DEVICE_KEY_BYTES,
    DEVICE_SIGNATURE_BYTES,
    DeviceIdentity,
    DeviceIdentityError,
    SignedDeviceDescriptor,
    verify_signed_device_descriptor,
)


PAIRING_VERSION = 1
PAIRING_OFFER_FORMAT = "atlasvault-pairing-offer"
SIGNED_PAIRING_OFFER_FORMAT = "atlasvault-signed-pairing-offer"
PAIRING_ACCEPTANCE_FORMAT = "atlasvault-pairing-acceptance"
SIGNED_PAIRING_ACCEPTANCE_FORMAT = "atlasvault-signed-pairing-acceptance"
PAIRING_NONCE_BYTES = 32
PAIRING_MAX_LIFETIME_SECONDS = 600
PAIRING_MAX_CLOCK_SKEW_SECONDS = 120
PAIRING_OFFER_SIGNATURE_DOMAIN = b"atlasvault-pairing-offer-signature-v1:"
PAIRING_ACCEPTANCE_SIGNATURE_DOMAIN = (
    b"atlasvault-pairing-acceptance-signature-v1:"
)
PAIRING_TRANSCRIPT_DOMAIN = b"atlasvault-pairing-transcript-v1:"
PAIRING_SESSION_INFO = b"atlasvault-pairing-session-v1"
PAIRING_INVITER_PROOF_DOMAIN = b"atlasvault-pairing-confirm-inviter-v1:"
PAIRING_INVITEE_PROOF_DOMAIN = b"atlasvault-pairing-confirm-invitee-v1:"


class PairingError(ValueError):
    """Raised when an AtlasVault pairing transcript cannot be verified."""


def _invalid_pairing() -> PairingError:
    return PairingError("pairing verification failed")


def _canonical_json_bytes(value: Mapping[str, Any]) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")


def _mapping(value: Any) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise _invalid_pairing()
    return value


def _exact_keys(value: Mapping[str, Any], expected: set[str]) -> None:
    if set(value) != expected:
        raise _invalid_pairing()


def _integer(value: Any) -> int:
    if type(value) is not int:
        raise _invalid_pairing()
    return value


def _bytes(value: Any, length: int) -> bytes:
    if not isinstance(value, bytes) or len(value) != length:
        raise _invalid_pairing()
    return bytes(value)


def _base64(value: bytes) -> str:
    return base64.b64encode(value).decode("ascii")


def _decode_base64(value: Any, length: int) -> bytes:
    if not isinstance(value, str):
        raise _invalid_pairing()
    try:
        decoded = base64.b64decode(value.encode("ascii"), validate=True)
    except (UnicodeEncodeError, binascii.Error, ValueError) as exc:
        raise _invalid_pairing() from exc
    if len(decoded) != length or _base64(decoded) != value:
        raise _invalid_pairing()
    return decoded


def _uuid(value: Any) -> str:
    if not isinstance(value, str):
        raise _invalid_pairing()
    try:
        parsed = uuid.UUID(value)
    except (AttributeError, TypeError, ValueError) as exc:
        raise _invalid_pairing() from exc
    if str(parsed) != value:
        raise _invalid_pairing()
    return value


def _timestamp(value: Any) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 20
        or value[4] != "-"
        or value[7] != "-"
        or value[10] != "T"
        or value[13] != ":"
        or value[16] != ":"
        or value[19] != "Z"
    ):
        raise _invalid_pairing()
    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError as exc:
        raise _invalid_pairing() from exc
    if parsed.strftime("%Y-%m-%dT%H:%M:%SZ") != value:
        raise _invalid_pairing()
    return value


def _time(value: str) -> datetime:
    _timestamp(value)
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")


def _sha256_hex(value: Any) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise _invalid_pairing()
    return value


@dataclass(frozen=True)
class PairingOffer:
    offer_id: str
    inviter: SignedDeviceDescriptor
    nonce: bytes
    issued_at: str
    expires_at: str
    format: str = PAIRING_OFFER_FORMAT
    version: int = PAIRING_VERSION

    def __post_init__(self) -> None:
        try:
            if (
                self.format != PAIRING_OFFER_FORMAT
                or _integer(self.version) != PAIRING_VERSION
                or not isinstance(self.inviter, SignedDeviceDescriptor)
            ):
                raise _invalid_pairing()
            _uuid(self.offer_id)
            object.__setattr__(self, "nonce", _bytes(self.nonce, PAIRING_NONCE_BYTES))
            issued = _time(self.issued_at)
            expires = _time(self.expires_at)
            lifetime = (expires - issued).total_seconds()
            if lifetime <= 0 or lifetime > PAIRING_MAX_LIFETIME_SECONDS:
                raise _invalid_pairing()
        except PairingError:
            raise
        except Exception as exc:
            raise _invalid_pairing() from exc

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> PairingOffer:
        try:
            obj = _mapping(data)
            _exact_keys(
                obj,
                {
                    "format",
                    "version",
                    "offer_id",
                    "inviter",
                    "nonce",
                    "issued_at",
                    "expires_at",
                },
            )
            return cls(
                format=obj.get("format"),
                version=_integer(obj.get("version")),
                offer_id=_uuid(obj.get("offer_id")),
                inviter=SignedDeviceDescriptor.from_dict(
                    _mapping(obj.get("inviter"))
                ),
                nonce=_decode_base64(obj.get("nonce"), PAIRING_NONCE_BYTES),
                issued_at=_timestamp(obj.get("issued_at")),
                expires_at=_timestamp(obj.get("expires_at")),
            )
        except (PairingError, DeviceIdentityError):
            raise _invalid_pairing()
        except Exception as exc:
            raise _invalid_pairing() from exc

    def to_dict(self) -> dict[str, Any]:
        return {
            "format": self.format,
            "version": self.version,
            "offer_id": self.offer_id,
            "inviter": self.inviter.to_dict(),
            "nonce": _base64(self.nonce),
            "issued_at": self.issued_at,
            "expires_at": self.expires_at,
        }

    def canonical_bytes(self) -> bytes:
        return _canonical_json_bytes(self.to_dict())


@dataclass(frozen=True)
class SignedPairingOffer:
    offer: PairingOffer
    signature: bytes
    format: str = SIGNED_PAIRING_OFFER_FORMAT
    version: int = PAIRING_VERSION

    def __post_init__(self) -> None:
        if (
            self.format != SIGNED_PAIRING_OFFER_FORMAT
            or _integer(self.version) != PAIRING_VERSION
            or not isinstance(self.offer, PairingOffer)
        ):
            raise _invalid_pairing()
        object.__setattr__(
            self,
            "signature",
            _bytes(self.signature, DEVICE_SIGNATURE_BYTES),
        )

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> SignedPairingOffer:
        try:
            obj = _mapping(data)
            _exact_keys(obj, {"format", "version", "offer", "signature"})
            return cls(
                format=obj.get("format"),
                version=_integer(obj.get("version")),
                offer=PairingOffer.from_dict(_mapping(obj.get("offer"))),
                signature=_decode_base64(
                    obj.get("signature"),
                    DEVICE_SIGNATURE_BYTES,
                ),
            )
        except PairingError:
            raise
        except Exception as exc:
            raise _invalid_pairing() from exc

    def to_dict(self) -> dict[str, Any]:
        return {
            "format": self.format,
            "version": self.version,
            "offer": self.offer.to_dict(),
            "signature": _base64(self.signature),
        }

    def canonical_bytes(self) -> bytes:
        return _canonical_json_bytes(self.to_dict())

    def sha256_hex(self) -> str:
        return hashlib.sha256(self.canonical_bytes()).hexdigest()


@dataclass(frozen=True)
class PairingAcceptance:
    offer_id: str
    offer_sha256: str
    invitee: SignedDeviceDescriptor
    nonce: bytes
    accepted_at: str
    format: str = PAIRING_ACCEPTANCE_FORMAT
    version: int = PAIRING_VERSION

    def __post_init__(self) -> None:
        if (
            self.format != PAIRING_ACCEPTANCE_FORMAT
            or _integer(self.version) != PAIRING_VERSION
            or not isinstance(self.invitee, SignedDeviceDescriptor)
        ):
            raise _invalid_pairing()
        _uuid(self.offer_id)
        _sha256_hex(self.offer_sha256)
        object.__setattr__(self, "nonce", _bytes(self.nonce, PAIRING_NONCE_BYTES))
        _timestamp(self.accepted_at)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> PairingAcceptance:
        try:
            obj = _mapping(data)
            _exact_keys(
                obj,
                {
                    "format",
                    "version",
                    "offer_id",
                    "offer_sha256",
                    "invitee",
                    "nonce",
                    "accepted_at",
                },
            )
            return cls(
                format=obj.get("format"),
                version=_integer(obj.get("version")),
                offer_id=_uuid(obj.get("offer_id")),
                offer_sha256=_sha256_hex(obj.get("offer_sha256")),
                invitee=SignedDeviceDescriptor.from_dict(
                    _mapping(obj.get("invitee"))
                ),
                nonce=_decode_base64(obj.get("nonce"), PAIRING_NONCE_BYTES),
                accepted_at=_timestamp(obj.get("accepted_at")),
            )
        except (PairingError, DeviceIdentityError):
            raise _invalid_pairing()
        except Exception as exc:
            raise _invalid_pairing() from exc

    def to_dict(self) -> dict[str, Any]:
        return {
            "format": self.format,
            "version": self.version,
            "offer_id": self.offer_id,
            "offer_sha256": self.offer_sha256,
            "invitee": self.invitee.to_dict(),
            "nonce": _base64(self.nonce),
            "accepted_at": self.accepted_at,
        }

    def canonical_bytes(self) -> bytes:
        return _canonical_json_bytes(self.to_dict())


@dataclass(frozen=True)
class SignedPairingAcceptance:
    acceptance: PairingAcceptance
    signature: bytes
    format: str = SIGNED_PAIRING_ACCEPTANCE_FORMAT
    version: int = PAIRING_VERSION

    def __post_init__(self) -> None:
        if (
            self.format != SIGNED_PAIRING_ACCEPTANCE_FORMAT
            or _integer(self.version) != PAIRING_VERSION
            or not isinstance(self.acceptance, PairingAcceptance)
        ):
            raise _invalid_pairing()
        object.__setattr__(
            self,
            "signature",
            _bytes(self.signature, DEVICE_SIGNATURE_BYTES),
        )

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> SignedPairingAcceptance:
        try:
            obj = _mapping(data)
            _exact_keys(
                obj,
                {"format", "version", "acceptance", "signature"},
            )
            return cls(
                format=obj.get("format"),
                version=_integer(obj.get("version")),
                acceptance=PairingAcceptance.from_dict(
                    _mapping(obj.get("acceptance"))
                ),
                signature=_decode_base64(
                    obj.get("signature"),
                    DEVICE_SIGNATURE_BYTES,
                ),
            )
        except PairingError:
            raise
        except Exception as exc:
            raise _invalid_pairing() from exc

    def to_dict(self) -> dict[str, Any]:
        return {
            "format": self.format,
            "version": self.version,
            "acceptance": self.acceptance.to_dict(),
            "signature": _base64(self.signature),
        }

    def canonical_bytes(self) -> bytes:
        return _canonical_json_bytes(self.to_dict())


@dataclass(frozen=True, repr=False)
class PairingProofs:
    inviter: bytes
    invitee: bytes

    def __post_init__(self) -> None:
        object.__setattr__(self, "inviter", _bytes(self.inviter, 32))
        object.__setattr__(self, "invitee", _bytes(self.invitee, 32))

    def __repr__(self) -> str:
        return "PairingProofs(<redacted>)"


@dataclass(frozen=True, repr=False)
class PairingSession:
    transcript_sha256: bytes
    session_key: bytes

    def __post_init__(self) -> None:
        object.__setattr__(self, "transcript_sha256", _bytes(self.transcript_sha256, 32))
        object.__setattr__(self, "session_key", _bytes(self.session_key, 32))

    def __repr__(self) -> str:
        return "PairingSession(<redacted>)"


PairingReplayOutcome = Literal["accepted", "already_consumed"]


class PairingReplayGuard(Protocol):
    def consume(
        self,
        offer_id: str,
        transcript_sha256: bytes,
        expires_at: str,
    ) -> PairingReplayOutcome: ...


class InMemoryPairingReplayGuard:
    def __init__(self) -> None:
        self._consumed: set[tuple[str, bytes, str]] = set()
        self._lock = Lock()

    @property
    def consumed_count(self) -> int:
        with self._lock:
            return len(self._consumed)

    def consume(
        self,
        offer_id: str,
        transcript_sha256: bytes,
        expires_at: str,
    ) -> PairingReplayOutcome:
        item = (offer_id, bytes(transcript_sha256), expires_at)
        with self._lock:
            if item in self._consumed:
                return "already_consumed"
            self._consumed.add(item)
            return "accepted"


def _verify_offer_signature(signed_offer: SignedPairingOffer) -> None:
    try:
        descriptor = verify_signed_device_descriptor(
            signed_offer.offer.inviter
        )
        Ed25519PublicKey.from_public_bytes(descriptor.signing_public_key).verify(
            signed_offer.signature,
            PAIRING_OFFER_SIGNATURE_DOMAIN
            + signed_offer.offer.canonical_bytes(),
        )
    except Exception as exc:
        raise _invalid_pairing() from exc

def _verify_acceptance_signature(
    signed_acceptance: SignedPairingAcceptance,
) -> None:
    try:
        descriptor = verify_signed_device_descriptor(
            signed_acceptance.acceptance.invitee
        )
        Ed25519PublicKey.from_public_bytes(descriptor.signing_public_key).verify(
            signed_acceptance.signature,
            PAIRING_ACCEPTANCE_SIGNATURE_DOMAIN
            + signed_acceptance.acceptance.canonical_bytes(),
        )
    except Exception as exc:
        raise _invalid_pairing() from exc


def _verify_offer_time(offer: PairingOffer, current_time: str) -> None:
    current = _time(current_time)
    issued = _time(offer.issued_at)
    expires = _time(offer.expires_at)
    if current >= expires:
        raise _invalid_pairing()
    if issued > current + timedelta(seconds=PAIRING_MAX_CLOCK_SKEW_SECONDS):
        raise _invalid_pairing()


def _verify_acceptance_relation(
    signed_offer: SignedPairingOffer,
    signed_acceptance: SignedPairingAcceptance,
) -> None:
    offer = signed_offer.offer
    acceptance = signed_acceptance.acceptance
    if acceptance.offer_id != offer.offer_id:
        raise _invalid_pairing()
    if not hmac.compare_digest(acceptance.offer_sha256, signed_offer.sha256_hex()):
        raise _invalid_pairing()
    inviter_id = offer.inviter.descriptor.device_id
    invitee_id = acceptance.invitee.descriptor.device_id
    if hmac.compare_digest(inviter_id, invitee_id):
        raise _invalid_pairing()
    accepted = _time(acceptance.accepted_at)
    issued = _time(offer.issued_at)
    expires = _time(offer.expires_at)
    skew = timedelta(seconds=PAIRING_MAX_CLOCK_SKEW_SECONDS)
    if accepted < issued - skew or accepted > expires + skew:
        raise _invalid_pairing()


def create_pairing_offer(
    inviter: DeviceIdentity,
    *,
    offer_id: str,
    nonce: bytes,
    issued_at: str,
    expires_at: str,
) -> SignedPairingOffer:
    try:
        offer = PairingOffer(
            offer_id=offer_id,
            inviter=inviter.sign_descriptor(),
            nonce=nonce,
            issued_at=issued_at,
            expires_at=expires_at,
        )
        return SignedPairingOffer(
            offer=offer,
            signature=inviter.sign(
                PAIRING_OFFER_SIGNATURE_DOMAIN + offer.canonical_bytes()
            ),
        )
    except Exception as exc:
        raise _invalid_pairing() from exc


def verify_pairing_offer(
    signed_offer: SignedPairingOffer,
    *,
    current_time: str,
) -> PairingOffer:
    try:
        _verify_offer_signature(signed_offer)
        _verify_offer_time(signed_offer.offer, current_time)
        return signed_offer.offer
    except Exception as exc:
        raise _invalid_pairing() from exc


def create_pairing_acceptance(
    invitee: DeviceIdentity,
    signed_offer: SignedPairingOffer,
    *,
    nonce: bytes,
    accepted_at: str,
    current_time: str,
) -> SignedPairingAcceptance:
    try:
        offer = verify_pairing_offer(signed_offer, current_time=current_time)
        acceptance = PairingAcceptance(
            offer_id=offer.offer_id,
            offer_sha256=signed_offer.sha256_hex(),
            invitee=invitee.sign_descriptor(),
            nonce=nonce,
            accepted_at=accepted_at,
        )
        signed = SignedPairingAcceptance(
            acceptance=acceptance,
            signature=invitee.sign(
                PAIRING_ACCEPTANCE_SIGNATURE_DOMAIN
                + acceptance.canonical_bytes()
            ),
        )
        _verify_acceptance_relation(signed_offer, signed)
        return signed
    except Exception as exc:
        raise _invalid_pairing() from exc


def pairing_transcript_sha256(
    signed_offer: SignedPairingOffer,
    signed_acceptance: SignedPairingAcceptance,
) -> bytes:
    offer_bytes = signed_offer.canonical_bytes()
    acceptance_bytes = signed_acceptance.canonical_bytes()
    return hashlib.sha256(
        PAIRING_TRANSCRIPT_DOMAIN
        + struct.pack(">Q", len(offer_bytes))
        + offer_bytes
        + struct.pack(">Q", len(acceptance_bytes))
        + acceptance_bytes
    ).digest()


def derive_pairing_session_key_from_shared_secret(
    *,
    shared_secret: bytes,
    transcript_sha256: bytes,
) -> bytes:
    try:
        secret = _bytes(shared_secret, DEVICE_KEY_BYTES)
        transcript = _bytes(transcript_sha256, 32)
        if hmac.compare_digest(secret, b"\0" * DEVICE_KEY_BYTES):
            raise _invalid_pairing()
        return HKDF(
            algorithm=hashes.SHA256(),
            length=32,
            salt=transcript,
            info=PAIRING_SESSION_INFO,
        ).derive(secret)
    except PairingError:
        raise
    except Exception as exc:
        raise _invalid_pairing() from exc


def derive_pairing_session_key(
    local_identity: DeviceIdentity,
    signed_offer: SignedPairingOffer,
    signed_acceptance: SignedPairingAcceptance,
) -> bytes:
    try:
        _verify_offer_signature(signed_offer)
        _verify_acceptance_signature(signed_acceptance)
        _verify_acceptance_relation(signed_offer, signed_acceptance)
        inviter = signed_offer.offer.inviter.descriptor
        invitee = signed_acceptance.acceptance.invitee.descriptor
        if local_identity.device_id == inviter.device_id:
            remote_public = invitee.agreement_public_key
        elif local_identity.device_id == invitee.device_id:
            remote_public = inviter.agreement_public_key
        else:
            raise _invalid_pairing()
        shared_secret = local_identity.shared_secret_for(remote_public)
        return derive_pairing_session_key_from_shared_secret(
            shared_secret=shared_secret,
            transcript_sha256=pairing_transcript_sha256(
                signed_offer,
                signed_acceptance,
            ),
        )
    except Exception as exc:
        raise _invalid_pairing() from exc


def derive_pairing_proofs(
    session_key: bytes,
    transcript_sha256: bytes,
) -> PairingProofs:
    try:
        key = _bytes(session_key, 32)
        transcript = _bytes(transcript_sha256, 32)
        return PairingProofs(
            inviter=hmac.new(
                key,
                PAIRING_INVITER_PROOF_DOMAIN + transcript,
                hashlib.sha256,
            ).digest(),
            invitee=hmac.new(
                key,
                PAIRING_INVITEE_PROOF_DOMAIN + transcript,
                hashlib.sha256,
            ).digest(),
        )
    except Exception as exc:
        raise _invalid_pairing() from exc


def verify_pairing_transcript(
    *,
    local_identity: DeviceIdentity,
    signed_offer: SignedPairingOffer,
    signed_acceptance: SignedPairingAcceptance,
    proofs: PairingProofs,
    current_time: str,
    replay_guard: PairingReplayGuard,
) -> PairingSession:
    try:
        if replay_guard is None:
            raise _invalid_pairing()
        verify_pairing_offer(signed_offer, current_time=current_time)
        _verify_acceptance_signature(signed_acceptance)
        _verify_acceptance_relation(signed_offer, signed_acceptance)
        transcript = pairing_transcript_sha256(
            signed_offer,
            signed_acceptance,
        )
        session_key = derive_pairing_session_key(
            local_identity,
            signed_offer,
            signed_acceptance,
        )
        expected = derive_pairing_proofs(session_key, transcript)
        if not hmac.compare_digest(expected.inviter, proofs.inviter):
            raise _invalid_pairing()
        if not hmac.compare_digest(expected.invitee, proofs.invitee):
            raise _invalid_pairing()
        outcome = replay_guard.consume(
            signed_offer.offer.offer_id,
            transcript,
            signed_offer.offer.expires_at,
        )
        if outcome != "accepted":
            raise _invalid_pairing()
        return PairingSession(
            transcript_sha256=transcript,
            session_key=session_key,
        )
    except Exception as exc:
        raise _invalid_pairing() from exc
