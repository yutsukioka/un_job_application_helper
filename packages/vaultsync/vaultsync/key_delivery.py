from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Mapping

from cryptography.exceptions import InvalidSignature, InvalidTag
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from vaultsync.device_identity import (
    DEVICE_KEY_BYTES,
    DEVICE_SIGNATURE_BYTES,
    DeviceIdentity,
    DeviceIdentityError,
    SignedDeviceDescriptor,
    verify_signed_device_descriptor,
)
from vaultsync.format import VaultFormatError, VaultMetadata, _require_vault_id
from vaultsync.records import EncryptedRecord, RecordFormatError


PAIRING_KEY_REQUEST_FORMAT = "atlasvault-pairing-key-request"
SIGNED_PAIRING_KEY_REQUEST_FORMAT = "atlasvault-signed-pairing-key-request"
PAIRING_BOOTSTRAP_FORMAT = "atlasvault-pairing-bootstrap"
VAULT_KEY_DELIVERY_FORMAT = "atlasvault-vault-key-delivery"
SIGNED_VAULT_KEY_DELIVERY_FORMAT = "atlasvault-signed-vault-key-delivery"
PAIRING_ACKNOWLEDGEMENT_FORMAT = "atlasvault-pairing-acknowledgement"
SIGNED_PAIRING_ACKNOWLEDGEMENT_FORMAT = "atlasvault-signed-pairing-acknowledgement"
PAIRING_KEY_DELIVERY_VERSION = 1
PAIRING_SAS_DOMAIN = b"atlasvault-pairing-sas-v1:"
PAIRING_KEY_REQUEST_SIGNATURE_DOMAIN = b"atlasvault-pairing-key-request-signature-v1:"
VAULT_KEY_DELIVERY_SIGNATURE_DOMAIN = b"atlasvault-vault-key-delivery-signature-v1:"
PAIRING_ACKNOWLEDGEMENT_SIGNATURE_DOMAIN = b"atlasvault-pairing-acknowledgement-signature-v1:"
VAULT_KEY_DELIVERY_INFO = b"atlasvault-vault-key-delivery-v1"
VAULT_KEY_DELIVERY_AAD_FORMAT = "atlasvault-vault-key-delivery-aad"
PAIRING_REQUEST_NONCE_BYTES = 32
AES_GCM_NONCE_BYTES = 12
VAULT_KEY_BYTES = 32
DELIVERY_CIPHERTEXT_BYTES = 48
MAXIMUM_REQUEST_LIFETIME_SECONDS = 1800
MAXIMUM_CLOCK_SKEW_SECONDS = 120
_DEVICE_ID = re.compile(r"^avd1-[0-9a-f]{64}$")


class PairingKeyDeliveryError(ValueError):
    """Raised when authenticated pairing delivery cannot be verified."""


def _invalid_delivery() -> PairingKeyDeliveryError:
    return PairingKeyDeliveryError("pairing key delivery verification failed")


def _canonical_bytes(value: Mapping[str, Any]) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")


def _mapping(value: Any) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise _invalid_delivery()
    return value


def _exact_keys(value: Mapping[str, Any], expected: set[str]) -> None:
    if set(value) != expected:
        raise _invalid_delivery()


def _require_printable_ascii_json(value: Any) -> None:
    if value is None or isinstance(value, (bool, int)):
        return
    if isinstance(value, str):
        if not value or any(
            ord(character) < 0x20 or ord(character) > 0x7E for character in value
        ):
            raise _invalid_delivery()
        return
    if isinstance(value, list):
        for item in value:
            _require_printable_ascii_json(item)
        return
    if isinstance(value, Mapping):
        for key, item in value.items():
            _require_printable_ascii_json(key)
            _require_printable_ascii_json(item)
        return
    raise _invalid_delivery()


def _integer(value: Any) -> int:
    if type(value) is not int:
        raise _invalid_delivery()
    return value


def _uuid(value: Any) -> str:
    if not isinstance(value, str):
        raise _invalid_delivery()
    try:
        parsed = uuid.UUID(value)
    except (AttributeError, TypeError, ValueError) as exc:
        raise _invalid_delivery() from exc
    if str(parsed) != value:
        raise _invalid_delivery()
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
        raise _invalid_delivery()
    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError as exc:
        raise _invalid_delivery() from exc
    if parsed.strftime("%Y-%m-%dT%H:%M:%SZ") != value:
        raise _invalid_delivery()
    return value


def _time(value: str) -> datetime:
    return datetime.strptime(_timestamp(value), "%Y-%m-%dT%H:%M:%SZ")


def _device_id(value: Any) -> str:
    if not isinstance(value, str) or _DEVICE_ID.fullmatch(value) is None:
        raise _invalid_delivery()
    return value


def _sha256(value: Any) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise _invalid_delivery()
    return value


def _epoch(value: Any) -> int:
    result = _integer(value)
    if result <= 0 or result > (1 << 63) - 1:
        raise _invalid_delivery()
    return result


def _vault_id(value: Any) -> str:
    try:
        return _require_vault_id(value)
    except VaultFormatError as exc:
        raise _invalid_delivery() from exc


def _bytes(value: Any, length: int) -> bytes:
    if not isinstance(value, bytes) or len(value) != length:
        raise _invalid_delivery()
    return bytes(value)


def _base64(value: bytes) -> str:
    return base64.b64encode(value).decode("ascii")


def _decode_base64(value: Any, length: int) -> bytes:
    if not isinstance(value, str):
        raise _invalid_delivery()
    try:
        result = base64.b64decode(value.encode("ascii"), validate=True)
    except (UnicodeEncodeError, binascii.Error, ValueError) as exc:
        raise _invalid_delivery() from exc
    if len(result) != length or _base64(result) != value:
        raise _invalid_delivery()
    return result


def _canonical_object(data: bytes) -> Mapping[str, Any]:
    try:
        if not isinstance(data, bytes) or not data:
            raise _invalid_delivery()
        obj = _mapping(json.loads(data.decode("utf-8")))
        if not hmac.compare_digest(_canonical_bytes(obj), data):
            raise _invalid_delivery()
        return obj
    except PairingKeyDeliveryError:
        raise
    except Exception as exc:
        raise _invalid_delivery() from exc


def _public_bytes(key: X25519PublicKey) -> bytes:
    return key.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )


def _verify_signature(
    descriptor: SignedDeviceDescriptor,
    signature: bytes,
    domain: bytes,
    canonical_payload: bytes,
) -> str:
    try:
        verified = verify_signed_device_descriptor(descriptor)
        Ed25519PublicKey.from_public_bytes(verified.signing_public_key).verify(
            signature,
            domain + canonical_payload,
        )
        return verified.device_id
    except (DeviceIdentityError, InvalidSignature, Exception) as exc:
        raise _invalid_delivery() from exc


def derive_pairing_sas(pairing_session_key: bytes, transcript_sha256: bytes) -> str:
    key = _bytes(pairing_session_key, 32)
    transcript = _bytes(transcript_sha256, 32)
    digest = hmac.digest(key, PAIRING_SAS_DOMAIN + transcript, "sha256")[:6]
    rendered = digest.hex().upper()
    return f"{rendered[:4]}-{rendered[4:8]}-{rendered[8:12]}"


@dataclass(frozen=True)
class PairingKeyRequest:
    request_id: str
    transcript_sha256: str
    inviter_device_id: str
    invitee_device_id: str
    invitee_ephemeral_public_key: bytes
    nonce: bytes
    issued_at: str
    expires_at: str
    format: str = PAIRING_KEY_REQUEST_FORMAT
    version: int = PAIRING_KEY_DELIVERY_VERSION

    def __post_init__(self) -> None:
        if (
            self.format != PAIRING_KEY_REQUEST_FORMAT
            or _integer(self.version) != PAIRING_KEY_DELIVERY_VERSION
        ):
            raise _invalid_delivery()
        _uuid(self.request_id)
        _sha256(self.transcript_sha256)
        _device_id(self.inviter_device_id)
        _device_id(self.invitee_device_id)
        if self.inviter_device_id == self.invitee_device_id:
            raise _invalid_delivery()
        object.__setattr__(
            self,
            "invitee_ephemeral_public_key",
            _bytes(self.invitee_ephemeral_public_key, DEVICE_KEY_BYTES),
        )
        object.__setattr__(self, "nonce", _bytes(self.nonce, PAIRING_REQUEST_NONCE_BYTES))
        issued = _time(self.issued_at)
        expires = _time(self.expires_at)
        lifetime = (expires - issued).total_seconds()
        if lifetime <= 0 or lifetime > MAXIMUM_REQUEST_LIFETIME_SECONDS:
            raise _invalid_delivery()

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> PairingKeyRequest:
        obj = _mapping(data)
        _exact_keys(
            obj,
            {
                "format",
                "version",
                "request_id",
                "transcript_sha256",
                "inviter_device_id",
                "invitee_device_id",
                "invitee_ephemeral_public_key",
                "nonce",
                "issued_at",
                "expires_at",
            },
        )
        return cls(
            format=obj.get("format"),
            version=_integer(obj.get("version")),
            request_id=_uuid(obj.get("request_id")),
            transcript_sha256=_sha256(obj.get("transcript_sha256")),
            inviter_device_id=_device_id(obj.get("inviter_device_id")),
            invitee_device_id=_device_id(obj.get("invitee_device_id")),
            invitee_ephemeral_public_key=_decode_base64(
                obj.get("invitee_ephemeral_public_key"), DEVICE_KEY_BYTES
            ),
            nonce=_decode_base64(obj.get("nonce"), PAIRING_REQUEST_NONCE_BYTES),
            issued_at=_timestamp(obj.get("issued_at")),
            expires_at=_timestamp(obj.get("expires_at")),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "format": self.format,
            "version": self.version,
            "request_id": self.request_id,
            "transcript_sha256": self.transcript_sha256,
            "inviter_device_id": self.inviter_device_id,
            "invitee_device_id": self.invitee_device_id,
            "invitee_ephemeral_public_key": _base64(self.invitee_ephemeral_public_key),
            "nonce": _base64(self.nonce),
            "issued_at": self.issued_at,
            "expires_at": self.expires_at,
        }

    def canonical_bytes(self) -> bytes:
        return _canonical_bytes(self.to_dict())


@dataclass(frozen=True)
class SignedPairingKeyRequest:
    request: PairingKeyRequest
    invitee: SignedDeviceDescriptor
    signature: bytes
    format: str = SIGNED_PAIRING_KEY_REQUEST_FORMAT
    version: int = PAIRING_KEY_DELIVERY_VERSION

    def __post_init__(self) -> None:
        if (
            self.format != SIGNED_PAIRING_KEY_REQUEST_FORMAT
            or _integer(self.version) != PAIRING_KEY_DELIVERY_VERSION
            or not isinstance(self.request, PairingKeyRequest)
            or not isinstance(self.invitee, SignedDeviceDescriptor)
        ):
            raise _invalid_delivery()
        object.__setattr__(self, "signature", _bytes(self.signature, DEVICE_SIGNATURE_BYTES))
        signer = _verify_signature(
            self.invitee,
            self.signature,
            PAIRING_KEY_REQUEST_SIGNATURE_DOMAIN,
            self.request.canonical_bytes(),
        )
        if not hmac.compare_digest(signer, self.request.invitee_device_id):
            raise _invalid_delivery()

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> SignedPairingKeyRequest:
        obj = _mapping(data)
        _exact_keys(obj, {"format", "version", "request", "invitee", "signature"})
        return cls(
            format=obj.get("format"),
            version=_integer(obj.get("version")),
            request=PairingKeyRequest.from_dict(_mapping(obj.get("request"))),
            invitee=SignedDeviceDescriptor.from_dict(_mapping(obj.get("invitee"))),
            signature=_decode_base64(obj.get("signature"), DEVICE_SIGNATURE_BYTES),
        )

    @classmethod
    def from_canonical_bytes(cls, data: bytes) -> SignedPairingKeyRequest:
        return cls.from_dict(_canonical_object(data))

    def to_dict(self) -> dict[str, Any]:
        return {
            "format": self.format,
            "version": self.version,
            "request": self.request.to_dict(),
            "invitee": self.invitee.to_dict(),
            "signature": _base64(self.signature),
        }

    def canonical_bytes(self) -> bytes:
        return _canonical_bytes(self.to_dict())

    def sha256_hex(self) -> str:
        return hashlib.sha256(self.canonical_bytes()).hexdigest()


def create_pairing_key_request(
    invitee: DeviceIdentity,
    *,
    request_id: str,
    transcript_sha256: bytes,
    inviter_device_id: str,
    invitee_ephemeral_public_key: bytes,
    nonce: bytes,
    issued_at: str,
    expires_at: str,
) -> SignedPairingKeyRequest:
    try:
        request = PairingKeyRequest(
            request_id=request_id,
            transcript_sha256=_bytes(transcript_sha256, 32).hex(),
            inviter_device_id=inviter_device_id,
            invitee_device_id=invitee.device_id,
            invitee_ephemeral_public_key=invitee_ephemeral_public_key,
            nonce=nonce,
            issued_at=issued_at,
            expires_at=expires_at,
        )
        return SignedPairingKeyRequest(
            request=request,
            invitee=invitee.sign_descriptor(),
            signature=invitee.sign(
                PAIRING_KEY_REQUEST_SIGNATURE_DOMAIN + request.canonical_bytes()
            ),
        )
    except PairingKeyDeliveryError:
        raise
    except Exception as exc:
        raise _invalid_delivery() from exc


def verify_pairing_key_request(
    signed: SignedPairingKeyRequest,
    *,
    transcript_sha256: bytes,
    inviter_device_id: str,
    invitee_device_id: str,
    current_time: str,
) -> PairingKeyRequest:
    try:
        if not isinstance(signed, SignedPairingKeyRequest):
            raise _invalid_delivery()
        value = signed.request
        now = _time(current_time)
        issued = _time(value.issued_at)
        expires = _time(value.expires_at)
        if (
            not hmac.compare_digest(value.transcript_sha256, _bytes(transcript_sha256, 32).hex())
            or not hmac.compare_digest(value.inviter_device_id, _device_id(inviter_device_id))
            or not hmac.compare_digest(value.invitee_device_id, _device_id(invitee_device_id))
            or now >= expires
            or issued > now + timedelta(seconds=MAXIMUM_CLOCK_SKEW_SECONDS)
        ):
            raise _invalid_delivery()
        return value
    except PairingKeyDeliveryError:
        raise
    except Exception as exc:
        raise _invalid_delivery() from exc


@dataclass(frozen=True)
class PairingBootstrap:
    snapshot_id: str
    created_at: str
    vault_metadata: VaultMetadata
    records: tuple[EncryptedRecord, ...]
    format: str = PAIRING_BOOTSTRAP_FORMAT
    version: int = PAIRING_KEY_DELIVERY_VERSION

    def __post_init__(self) -> None:
        try:
            if (
                self.format != PAIRING_BOOTSTRAP_FORMAT
                or _integer(self.version) != PAIRING_KEY_DELIVERY_VERSION
            ):
                raise _invalid_delivery()
            _uuid(self.snapshot_id)
            _timestamp(self.created_at)
            if not isinstance(self.vault_metadata, VaultMetadata):
                raise _invalid_delivery()
            records = tuple(self.records)
            if any(not isinstance(record, EncryptedRecord) for record in records):
                raise _invalid_delivery()
            if len({record.id for record in records}) != len(records):
                raise _invalid_delivery()
            object.__setattr__(self, "records", records)
            _require_printable_ascii_json(self.to_dict())
        except PairingKeyDeliveryError:
            raise
        except Exception as exc:
            raise _invalid_delivery() from exc

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> PairingBootstrap:
        try:
            obj = _mapping(data)
            _exact_keys(
                obj,
                {"format", "version", "snapshot_id", "created_at", "vault_metadata", "records"},
            )
            values = obj.get("records")
            if not isinstance(values, list):
                raise _invalid_delivery()
            return cls(
                format=obj.get("format"),
                version=_integer(obj.get("version")),
                snapshot_id=_uuid(obj.get("snapshot_id")),
                created_at=_timestamp(obj.get("created_at")),
                vault_metadata=VaultMetadata.from_dict(_mapping(obj.get("vault_metadata"))),
                records=tuple(EncryptedRecord.from_dict(_mapping(value)) for value in values),
            )
        except PairingKeyDeliveryError:
            raise
        except (VaultFormatError, RecordFormatError, Exception) as exc:
            raise _invalid_delivery() from exc

    @classmethod
    def from_canonical_bytes(cls, data: bytes) -> PairingBootstrap:
        return cls.from_dict(_canonical_object(data))

    def to_dict(self) -> dict[str, Any]:
        return {
            "format": self.format,
            "version": self.version,
            "snapshot_id": self.snapshot_id,
            "created_at": self.created_at,
            "vault_metadata": self.vault_metadata.to_dict(),
            "records": [record.to_dict() for record in self.records],
        }

    def canonical_bytes(self) -> bytes:
        return _canonical_bytes(self.to_dict())

    def sha256_hex(self) -> str:
        return hashlib.sha256(self.canonical_bytes()).hexdigest()


@dataclass(frozen=True)
class VaultKeyDelivery:
    delivery_id: str
    transcript_sha256: str
    inviter_device_id: str
    invitee_device_id: str
    request_sha256: str
    vault_id: str
    key_epoch: int
    bootstrap_sha256: str
    inviter_ephemeral_public_key: bytes
    nonce: bytes
    ciphertext: bytes
    expires_at: str
    format: str = VAULT_KEY_DELIVERY_FORMAT
    version: int = PAIRING_KEY_DELIVERY_VERSION

    def __post_init__(self) -> None:
        if (
            self.format != VAULT_KEY_DELIVERY_FORMAT
            or _integer(self.version) != PAIRING_KEY_DELIVERY_VERSION
        ):
            raise _invalid_delivery()
        _uuid(self.delivery_id)
        _sha256(self.transcript_sha256)
        _device_id(self.inviter_device_id)
        _device_id(self.invitee_device_id)
        if self.inviter_device_id == self.invitee_device_id:
            raise _invalid_delivery()
        _sha256(self.request_sha256)
        _vault_id(self.vault_id)
        _epoch(self.key_epoch)
        _sha256(self.bootstrap_sha256)
        object.__setattr__(
            self,
            "inviter_ephemeral_public_key",
            _bytes(self.inviter_ephemeral_public_key, DEVICE_KEY_BYTES),
        )
        object.__setattr__(self, "nonce", _bytes(self.nonce, AES_GCM_NONCE_BYTES))
        object.__setattr__(
            self,
            "ciphertext",
            _bytes(self.ciphertext, DELIVERY_CIPHERTEXT_BYTES),
        )
        _timestamp(self.expires_at)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> VaultKeyDelivery:
        obj = _mapping(data)
        _exact_keys(
            obj,
            {
                "format",
                "version",
                "delivery_id",
                "transcript_sha256",
                "inviter_device_id",
                "invitee_device_id",
                "request_sha256",
                "vault_id",
                "key_epoch",
                "bootstrap_sha256",
                "inviter_ephemeral_public_key",
                "nonce",
                "ciphertext",
                "expires_at",
            },
        )
        return cls(
            format=obj.get("format"),
            version=_integer(obj.get("version")),
            delivery_id=_uuid(obj.get("delivery_id")),
            transcript_sha256=_sha256(obj.get("transcript_sha256")),
            inviter_device_id=_device_id(obj.get("inviter_device_id")),
            invitee_device_id=_device_id(obj.get("invitee_device_id")),
            request_sha256=_sha256(obj.get("request_sha256")),
            vault_id=_vault_id(obj.get("vault_id")),
            key_epoch=_epoch(obj.get("key_epoch")),
            bootstrap_sha256=_sha256(obj.get("bootstrap_sha256")),
            inviter_ephemeral_public_key=_decode_base64(
                obj.get("inviter_ephemeral_public_key"), DEVICE_KEY_BYTES
            ),
            nonce=_decode_base64(obj.get("nonce"), AES_GCM_NONCE_BYTES),
            ciphertext=_decode_base64(obj.get("ciphertext"), DELIVERY_CIPHERTEXT_BYTES),
            expires_at=_timestamp(obj.get("expires_at")),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "format": self.format,
            "version": self.version,
            "delivery_id": self.delivery_id,
            "transcript_sha256": self.transcript_sha256,
            "inviter_device_id": self.inviter_device_id,
            "invitee_device_id": self.invitee_device_id,
            "request_sha256": self.request_sha256,
            "vault_id": self.vault_id,
            "key_epoch": self.key_epoch,
            "bootstrap_sha256": self.bootstrap_sha256,
            "inviter_ephemeral_public_key": _base64(self.inviter_ephemeral_public_key),
            "nonce": _base64(self.nonce),
            "ciphertext": _base64(self.ciphertext),
            "expires_at": self.expires_at,
        }

    def canonical_bytes(self) -> bytes:
        return _canonical_bytes(self.to_dict())

    def aad(self) -> bytes:
        return _canonical_bytes(
            {
                "format": VAULT_KEY_DELIVERY_AAD_FORMAT,
                "version": PAIRING_KEY_DELIVERY_VERSION,
                "delivery_id": self.delivery_id,
                "transcript_sha256": self.transcript_sha256,
                "inviter_device_id": self.inviter_device_id,
                "invitee_device_id": self.invitee_device_id,
                "request_sha256": self.request_sha256,
                "vault_id": self.vault_id,
                "key_epoch": self.key_epoch,
                "bootstrap_sha256": self.bootstrap_sha256,
                "expires_at": self.expires_at,
            }
        )


@dataclass(frozen=True)
class SignedVaultKeyDelivery:
    delivery: VaultKeyDelivery
    inviter: SignedDeviceDescriptor
    signature: bytes
    format: str = SIGNED_VAULT_KEY_DELIVERY_FORMAT
    version: int = PAIRING_KEY_DELIVERY_VERSION

    def __post_init__(self) -> None:
        if (
            self.format != SIGNED_VAULT_KEY_DELIVERY_FORMAT
            or _integer(self.version) != PAIRING_KEY_DELIVERY_VERSION
            or not isinstance(self.delivery, VaultKeyDelivery)
            or not isinstance(self.inviter, SignedDeviceDescriptor)
        ):
            raise _invalid_delivery()
        object.__setattr__(self, "signature", _bytes(self.signature, DEVICE_SIGNATURE_BYTES))
        signer = _verify_signature(
            self.inviter,
            self.signature,
            VAULT_KEY_DELIVERY_SIGNATURE_DOMAIN,
            self.delivery.canonical_bytes(),
        )
        if not hmac.compare_digest(signer, self.delivery.inviter_device_id):
            raise _invalid_delivery()

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> SignedVaultKeyDelivery:
        obj = _mapping(data)
        _exact_keys(obj, {"format", "version", "delivery", "inviter", "signature"})
        return cls(
            format=obj.get("format"),
            version=_integer(obj.get("version")),
            delivery=VaultKeyDelivery.from_dict(_mapping(obj.get("delivery"))),
            inviter=SignedDeviceDescriptor.from_dict(_mapping(obj.get("inviter"))),
            signature=_decode_base64(obj.get("signature"), DEVICE_SIGNATURE_BYTES),
        )

    @classmethod
    def from_canonical_bytes(cls, data: bytes) -> SignedVaultKeyDelivery:
        return cls.from_dict(_canonical_object(data))

    def to_dict(self) -> dict[str, Any]:
        return {
            "format": self.format,
            "version": self.version,
            "delivery": self.delivery.to_dict(),
            "inviter": self.inviter.to_dict(),
            "signature": _base64(self.signature),
        }

    def canonical_bytes(self) -> bytes:
        return _canonical_bytes(self.to_dict())


def _delivery_key(shared_secret: bytes, transcript_sha256: bytes) -> bytes:
    secret = _bytes(shared_secret, 32)
    transcript = _bytes(transcript_sha256, 32)
    if hmac.compare_digest(secret, b"\0" * 32):
        raise _invalid_delivery()
    return HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=transcript,
        info=VAULT_KEY_DELIVERY_INFO,
    ).derive(secret)


def create_vault_key_delivery(
    inviter: DeviceIdentity,
    *,
    key_request: SignedPairingKeyRequest,
    transcript_sha256: bytes,
    bootstrap: PairingBootstrap,
    vault_key: bytes,
    inviter_ephemeral_private_key: bytes,
    nonce: bytes,
    delivery_id: str,
    key_epoch: int,
    expires_at: str,
) -> SignedVaultKeyDelivery:
    try:
        if (
            not isinstance(inviter, DeviceIdentity)
            or not isinstance(key_request, SignedPairingKeyRequest)
            or not isinstance(bootstrap, PairingBootstrap)
        ):
            raise _invalid_delivery()
        transcript = _bytes(transcript_sha256, 32)
        request = key_request.request
        if (
            not hmac.compare_digest(request.transcript_sha256, transcript.hex())
            or not hmac.compare_digest(request.inviter_device_id, inviter.device_id)
            or expires_at != request.expires_at
        ):
            raise _invalid_delivery()
        private = X25519PrivateKey.from_private_bytes(
            _bytes(inviter_ephemeral_private_key, DEVICE_KEY_BYTES)
        )
        remote = X25519PublicKey.from_public_bytes(request.invitee_ephemeral_public_key)
        key = _delivery_key(private.exchange(remote), transcript)
        template = VaultKeyDelivery(
            delivery_id=delivery_id,
            transcript_sha256=transcript.hex(),
            inviter_device_id=inviter.device_id,
            invitee_device_id=request.invitee_device_id,
            request_sha256=key_request.sha256_hex(),
            vault_id=bootstrap.vault_metadata.vault_id,
            key_epoch=key_epoch,
            bootstrap_sha256=bootstrap.sha256_hex(),
            inviter_ephemeral_public_key=_public_bytes(private.public_key()),
            nonce=nonce,
            ciphertext=b"\0" * DELIVERY_CIPHERTEXT_BYTES,
            expires_at=expires_at,
        )
        ciphertext = AESGCM(key).encrypt(
            template.nonce,
            _bytes(vault_key, VAULT_KEY_BYTES),
            template.aad(),
        )
        delivery = VaultKeyDelivery(
            **{
                **template.__dict__,
                "ciphertext": ciphertext,
            }
        )
        return SignedVaultKeyDelivery(
            delivery=delivery,
            inviter=inviter.sign_descriptor(),
            signature=inviter.sign(
                VAULT_KEY_DELIVERY_SIGNATURE_DOMAIN + delivery.canonical_bytes()
            ),
        )
    except PairingKeyDeliveryError:
        raise
    except Exception as exc:
        raise _invalid_delivery() from exc


def open_vault_key_delivery(
    signed: SignedVaultKeyDelivery,
    *,
    key_request: SignedPairingKeyRequest,
    invitee_ephemeral_private_key: bytes,
    bootstrap: PairingBootstrap,
    transcript_sha256: bytes,
    current_time: str,
) -> bytes:
    try:
        if (
            not isinstance(signed, SignedVaultKeyDelivery)
            or not isinstance(key_request, SignedPairingKeyRequest)
            or not isinstance(bootstrap, PairingBootstrap)
        ):
            raise _invalid_delivery()
        value = signed.delivery
        transcript = _bytes(transcript_sha256, 32)
        request = verify_pairing_key_request(
            key_request,
            transcript_sha256=transcript,
            inviter_device_id=value.inviter_device_id,
            invitee_device_id=value.invitee_device_id,
            current_time=current_time,
        )
        if (
            _time(current_time) >= _time(value.expires_at)
            or value.expires_at != request.expires_at
            or not hmac.compare_digest(value.transcript_sha256, transcript.hex())
            or not hmac.compare_digest(value.request_sha256, key_request.sha256_hex())
            or not hmac.compare_digest(value.vault_id, bootstrap.vault_metadata.vault_id)
            or not hmac.compare_digest(value.bootstrap_sha256, bootstrap.sha256_hex())
        ):
            raise _invalid_delivery()
        private = X25519PrivateKey.from_private_bytes(
            _bytes(invitee_ephemeral_private_key, DEVICE_KEY_BYTES)
        )
        remote = X25519PublicKey.from_public_bytes(value.inviter_ephemeral_public_key)
        key = _delivery_key(private.exchange(remote), transcript)
        plaintext = AESGCM(key).decrypt(value.nonce, value.ciphertext, value.aad())
        return _bytes(plaintext, VAULT_KEY_BYTES)
    except PairingKeyDeliveryError:
        raise
    except (InvalidTag, Exception) as exc:
        raise _invalid_delivery() from exc


@dataclass(frozen=True)
class PairingAcknowledgement:
    acknowledgement_id: str
    delivery_id: str
    transcript_sha256: str
    inviter_device_id: str
    invitee_device_id: str
    vault_id: str
    key_epoch: int
    bootstrap_sha256: str
    installed_at: str
    format: str = PAIRING_ACKNOWLEDGEMENT_FORMAT
    version: int = PAIRING_KEY_DELIVERY_VERSION

    def __post_init__(self) -> None:
        if (
            self.format != PAIRING_ACKNOWLEDGEMENT_FORMAT
            or _integer(self.version) != PAIRING_KEY_DELIVERY_VERSION
        ):
            raise _invalid_delivery()
        _uuid(self.acknowledgement_id)
        _uuid(self.delivery_id)
        _sha256(self.transcript_sha256)
        _device_id(self.inviter_device_id)
        _device_id(self.invitee_device_id)
        if self.inviter_device_id == self.invitee_device_id:
            raise _invalid_delivery()
        _vault_id(self.vault_id)
        _epoch(self.key_epoch)
        _sha256(self.bootstrap_sha256)
        _timestamp(self.installed_at)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> PairingAcknowledgement:
        obj = _mapping(data)
        _exact_keys(
            obj,
            {
                "format",
                "version",
                "acknowledgement_id",
                "delivery_id",
                "transcript_sha256",
                "inviter_device_id",
                "invitee_device_id",
                "vault_id",
                "key_epoch",
                "bootstrap_sha256",
                "installed_at",
            },
        )
        return cls(
            format=obj.get("format"),
            version=_integer(obj.get("version")),
            acknowledgement_id=_uuid(obj.get("acknowledgement_id")),
            delivery_id=_uuid(obj.get("delivery_id")),
            transcript_sha256=_sha256(obj.get("transcript_sha256")),
            inviter_device_id=_device_id(obj.get("inviter_device_id")),
            invitee_device_id=_device_id(obj.get("invitee_device_id")),
            vault_id=_vault_id(obj.get("vault_id")),
            key_epoch=_epoch(obj.get("key_epoch")),
            bootstrap_sha256=_sha256(obj.get("bootstrap_sha256")),
            installed_at=_timestamp(obj.get("installed_at")),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "format": self.format,
            "version": self.version,
            "acknowledgement_id": self.acknowledgement_id,
            "delivery_id": self.delivery_id,
            "transcript_sha256": self.transcript_sha256,
            "inviter_device_id": self.inviter_device_id,
            "invitee_device_id": self.invitee_device_id,
            "vault_id": self.vault_id,
            "key_epoch": self.key_epoch,
            "bootstrap_sha256": self.bootstrap_sha256,
            "installed_at": self.installed_at,
        }

    def canonical_bytes(self) -> bytes:
        return _canonical_bytes(self.to_dict())


@dataclass(frozen=True)
class SignedPairingAcknowledgement:
    acknowledgement: PairingAcknowledgement
    invitee: SignedDeviceDescriptor
    signature: bytes
    format: str = SIGNED_PAIRING_ACKNOWLEDGEMENT_FORMAT
    version: int = PAIRING_KEY_DELIVERY_VERSION

    def __post_init__(self) -> None:
        if (
            self.format != SIGNED_PAIRING_ACKNOWLEDGEMENT_FORMAT
            or _integer(self.version) != PAIRING_KEY_DELIVERY_VERSION
            or not isinstance(self.acknowledgement, PairingAcknowledgement)
            or not isinstance(self.invitee, SignedDeviceDescriptor)
        ):
            raise _invalid_delivery()
        object.__setattr__(self, "signature", _bytes(self.signature, DEVICE_SIGNATURE_BYTES))
        signer = _verify_signature(
            self.invitee,
            self.signature,
            PAIRING_ACKNOWLEDGEMENT_SIGNATURE_DOMAIN,
            self.acknowledgement.canonical_bytes(),
        )
        if not hmac.compare_digest(signer, self.acknowledgement.invitee_device_id):
            raise _invalid_delivery()

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> SignedPairingAcknowledgement:
        obj = _mapping(data)
        _exact_keys(
            obj,
            {"format", "version", "acknowledgement", "invitee", "signature"},
        )
        return cls(
            format=obj.get("format"),
            version=_integer(obj.get("version")),
            acknowledgement=PairingAcknowledgement.from_dict(_mapping(obj.get("acknowledgement"))),
            invitee=SignedDeviceDescriptor.from_dict(_mapping(obj.get("invitee"))),
            signature=_decode_base64(obj.get("signature"), DEVICE_SIGNATURE_BYTES),
        )

    @classmethod
    def from_canonical_bytes(cls, data: bytes) -> SignedPairingAcknowledgement:
        return cls.from_dict(_canonical_object(data))

    def to_dict(self) -> dict[str, Any]:
        return {
            "format": self.format,
            "version": self.version,
            "acknowledgement": self.acknowledgement.to_dict(),
            "invitee": self.invitee.to_dict(),
            "signature": _base64(self.signature),
        }

    def canonical_bytes(self) -> bytes:
        return _canonical_bytes(self.to_dict())

    def sha256_hex(self) -> str:
        return hashlib.sha256(self.canonical_bytes()).hexdigest()


def create_pairing_acknowledgement(
    invitee: DeviceIdentity,
    *,
    acknowledgement_id: str,
    delivery: SignedVaultKeyDelivery,
    installed_at: str,
) -> SignedPairingAcknowledgement:
    try:
        value = delivery.delivery
        if not hmac.compare_digest(invitee.device_id, value.invitee_device_id):
            raise _invalid_delivery()
        acknowledgement = PairingAcknowledgement(
            acknowledgement_id=acknowledgement_id,
            delivery_id=value.delivery_id,
            transcript_sha256=value.transcript_sha256,
            inviter_device_id=value.inviter_device_id,
            invitee_device_id=value.invitee_device_id,
            vault_id=value.vault_id,
            key_epoch=value.key_epoch,
            bootstrap_sha256=value.bootstrap_sha256,
            installed_at=installed_at,
        )
        return SignedPairingAcknowledgement(
            acknowledgement=acknowledgement,
            invitee=invitee.sign_descriptor(),
            signature=invitee.sign(
                PAIRING_ACKNOWLEDGEMENT_SIGNATURE_DOMAIN + acknowledgement.canonical_bytes()
            ),
        )
    except PairingKeyDeliveryError:
        raise
    except Exception as exc:
        raise _invalid_delivery() from exc


def verify_pairing_acknowledgement(
    signed: SignedPairingAcknowledgement,
    *,
    delivery: SignedVaultKeyDelivery,
    inviter_device_id: str,
    invitee_device_id: str,
) -> PairingAcknowledgement:
    try:
        if not isinstance(signed, SignedPairingAcknowledgement):
            raise _invalid_delivery()
        acknowledgement = signed.acknowledgement
        value = delivery.delivery
        if (
            not hmac.compare_digest(acknowledgement.delivery_id, value.delivery_id)
            or not hmac.compare_digest(acknowledgement.transcript_sha256, value.transcript_sha256)
            or not hmac.compare_digest(
                acknowledgement.inviter_device_id, _device_id(inviter_device_id)
            )
            or not hmac.compare_digest(
                acknowledgement.invitee_device_id, _device_id(invitee_device_id)
            )
            or not hmac.compare_digest(acknowledgement.vault_id, value.vault_id)
            or acknowledgement.key_epoch != value.key_epoch
            or not hmac.compare_digest(acknowledgement.bootstrap_sha256, value.bootstrap_sha256)
        ):
            raise _invalid_delivery()
        return acknowledgement
    except PairingKeyDeliveryError:
        raise
    except Exception as exc:
        raise _invalid_delivery() from exc
