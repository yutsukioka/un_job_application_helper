from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Mapping

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from cryptography.hazmat.primitives.asymmetric.x25519 import (
    X25519PrivateKey,
    X25519PublicKey,
)


DEVICE_DESCRIPTOR_FORMAT = "atlasvault-device-descriptor"
SIGNED_DEVICE_DESCRIPTOR_FORMAT = "atlasvault-signed-device-descriptor"
DEVICE_IDENTITY_SECRET_FORMAT = "atlasvault-device-identity-secret"
DEVICE_IDENTITY_VERSION = 1
DEVICE_KEY_BYTES = 32
DEVICE_SIGNATURE_BYTES = 64
MAXIMUM_DEVICE_KEY_EPOCH = (1 << 63) - 1
DEVICE_ID_DOMAIN = b"atlasvault-device-id-v1:"
DEVICE_DESCRIPTOR_SIGNATURE_DOMAIN = b"atlasvault-device-descriptor-signature-v1:"


class DeviceIdentityError(ValueError):
    """Raised when an AtlasVault device identity value is invalid."""


def _invalid_identity() -> DeviceIdentityError:
    return DeviceIdentityError("invalid device identity")


def _canonical_json_bytes(value: Mapping[str, Any]) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")


def _canonical_json_object(value: Any) -> Mapping[str, Any]:
    try:
        if not isinstance(value, bytes) or not value:
            raise _invalid_identity()
        return _require_mapping(json.loads(value.decode("utf-8")))
    except DeviceIdentityError:
        raise
    except Exception as exc:
        raise _invalid_identity() from exc


def _require_exact_keys(
    value: Mapping[str, Any],
    expected: set[str],
) -> None:
    if set(value) != expected:
        raise _invalid_identity()


def _require_mapping(value: Any) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise _invalid_identity()
    return value


def _require_int(value: Any, *, positive: bool = False) -> int:
    if type(value) is not int or (positive and value <= 0):
        raise _invalid_identity()
    return value


def _require_key_epoch(value: Any) -> int:
    epoch = _require_int(value, positive=True)
    if epoch > MAXIMUM_DEVICE_KEY_EPOCH:
        raise _invalid_identity()
    return epoch


def _require_utc_seconds(value: Any) -> str:
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
        raise _invalid_identity()
    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError as exc:
        raise _invalid_identity() from exc
    if parsed.strftime("%Y-%m-%dT%H:%M:%SZ") != value:
        raise _invalid_identity()
    return value


def _utc_now_seconds() -> str:
    return datetime.now(tz=UTC).replace(microsecond=0).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )


def _require_bytes(value: Any, length: int) -> bytes:
    if not isinstance(value, bytes) or len(value) != length:
        raise _invalid_identity()
    return bytes(value)


def _decode_base64(value: Any, length: int) -> bytes:
    if not isinstance(value, str):
        raise _invalid_identity()
    try:
        encoded = value.encode("ascii")
        decoded = base64.b64decode(encoded, validate=True)
    except (UnicodeEncodeError, binascii.Error, ValueError) as exc:
        raise _invalid_identity() from exc
    if len(decoded) != length or base64.b64encode(decoded).decode("ascii") != value:
        raise _invalid_identity()
    return decoded


def _encode_base64(value: bytes) -> str:
    return base64.b64encode(value).decode("ascii")


def _public_bytes(key: Ed25519PublicKey | X25519PublicKey) -> bytes:
    return key.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )


def derive_device_id(
    signing_public_key: bytes,
    agreement_public_key: bytes,
) -> str:
    signing = _require_bytes(signing_public_key, DEVICE_KEY_BYTES)
    agreement = _require_bytes(agreement_public_key, DEVICE_KEY_BYTES)
    digest = hashlib.sha256(DEVICE_ID_DOMAIN + signing + agreement).hexdigest()
    return f"avd1-{digest}"


def _require_device_id(
    value: Any,
    signing_public_key: bytes,
    agreement_public_key: bytes,
) -> str:
    expected = derive_device_id(signing_public_key, agreement_public_key)
    if not isinstance(value, str) or not hmac.compare_digest(value, expected):
        raise _invalid_identity()
    return value


@dataclass(frozen=True)
class DeviceDescriptor:
    device_id: str
    signing_public_key: bytes
    agreement_public_key: bytes
    created_at: str
    key_epoch: int = 1
    format: str = DEVICE_DESCRIPTOR_FORMAT
    version: int = DEVICE_IDENTITY_VERSION

    def __post_init__(self) -> None:
        try:
            signing = _require_bytes(self.signing_public_key, DEVICE_KEY_BYTES)
            agreement = _require_bytes(self.agreement_public_key, DEVICE_KEY_BYTES)
            if self.format != DEVICE_DESCRIPTOR_FORMAT:
                raise _invalid_identity()
            if _require_int(self.version) != DEVICE_IDENTITY_VERSION:
                raise _invalid_identity()
            _require_key_epoch(self.key_epoch)
            _require_utc_seconds(self.created_at)
            _require_device_id(self.device_id, signing, agreement)
            object.__setattr__(self, "signing_public_key", signing)
            object.__setattr__(self, "agreement_public_key", agreement)
        except DeviceIdentityError:
            raise
        except Exception as exc:
            raise _invalid_identity() from exc

    @classmethod
    def from_public_keys(
        cls,
        *,
        signing_public_key: bytes,
        agreement_public_key: bytes,
        created_at: str,
        key_epoch: int = 1,
    ) -> DeviceDescriptor:
        return cls(
            device_id=derive_device_id(
                signing_public_key,
                agreement_public_key,
            ),
            signing_public_key=signing_public_key,
            agreement_public_key=agreement_public_key,
            created_at=created_at,
            key_epoch=key_epoch,
        )

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> DeviceDescriptor:
        try:
            obj = _require_mapping(data)
            _require_exact_keys(
                obj,
                {
                    "format",
                    "version",
                    "device_id",
                    "signing_public_key",
                    "agreement_public_key",
                    "created_at",
                    "key_epoch",
                },
            )
            return cls(
                format=obj.get("format"),
                version=_require_int(obj.get("version")),
                device_id=obj.get("device_id"),
                signing_public_key=_decode_base64(
                    obj.get("signing_public_key"),
                    DEVICE_KEY_BYTES,
                ),
                agreement_public_key=_decode_base64(
                    obj.get("agreement_public_key"),
                    DEVICE_KEY_BYTES,
                ),
                created_at=_require_utc_seconds(obj.get("created_at")),
                key_epoch=_require_key_epoch(obj.get("key_epoch")),
            )
        except DeviceIdentityError:
            raise
        except Exception as exc:
            raise _invalid_identity() from exc

    def to_dict(self) -> dict[str, Any]:
        return {
            "format": self.format,
            "version": self.version,
            "device_id": self.device_id,
            "signing_public_key": _encode_base64(self.signing_public_key),
            "agreement_public_key": _encode_base64(self.agreement_public_key),
            "created_at": self.created_at,
            "key_epoch": self.key_epoch,
        }

    def canonical_bytes(self) -> bytes:
        return _canonical_json_bytes(self.to_dict())


@dataclass(frozen=True)
class SignedDeviceDescriptor:
    descriptor: DeviceDescriptor
    signature: bytes
    format: str = SIGNED_DEVICE_DESCRIPTOR_FORMAT
    version: int = DEVICE_IDENTITY_VERSION

    def __post_init__(self) -> None:
        if (
            self.format != SIGNED_DEVICE_DESCRIPTOR_FORMAT
            or _require_int(self.version) != DEVICE_IDENTITY_VERSION
            or not isinstance(self.descriptor, DeviceDescriptor)
        ):
            raise _invalid_identity()
        object.__setattr__(
            self,
            "signature",
            _require_bytes(self.signature, DEVICE_SIGNATURE_BYTES),
        )

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> SignedDeviceDescriptor:
        try:
            obj = _require_mapping(data)
            _require_exact_keys(
                obj,
                {"format", "version", "descriptor", "signature"},
            )
            return cls(
                format=obj.get("format"),
                version=_require_int(obj.get("version")),
                descriptor=DeviceDescriptor.from_dict(
                    _require_mapping(obj.get("descriptor"))
                ),
                signature=_decode_base64(
                    obj.get("signature"),
                    DEVICE_SIGNATURE_BYTES,
                ),
            )
        except DeviceIdentityError:
            raise
        except Exception as exc:
            raise _invalid_identity() from exc

    @classmethod
    def from_canonical_bytes(cls, data: bytes) -> SignedDeviceDescriptor:
        try:
            decoded = cls.from_dict(_canonical_json_object(data))
            if not hmac.compare_digest(decoded.canonical_bytes(), data):
                raise _invalid_identity()
            return decoded
        except DeviceIdentityError:
            raise
        except Exception as exc:
            raise _invalid_identity() from exc

    def to_dict(self) -> dict[str, Any]:
        return {
            "format": self.format,
            "version": self.version,
            "descriptor": self.descriptor.to_dict(),
            "signature": _encode_base64(self.signature),
        }

    def canonical_bytes(self) -> bytes:
        return _canonical_json_bytes(self.to_dict())


def verify_signed_device_descriptor(
    signed: SignedDeviceDescriptor,
) -> DeviceDescriptor:
    try:
        if not isinstance(signed, SignedDeviceDescriptor):
            raise _invalid_identity()
        Ed25519PublicKey.from_public_bytes(
            signed.descriptor.signing_public_key
        ).verify(
            signed.signature,
            DEVICE_DESCRIPTOR_SIGNATURE_DOMAIN
            + signed.descriptor.canonical_bytes(),
        )
        return signed.descriptor
    except DeviceIdentityError:
        raise
    except Exception as exc:
        raise _invalid_identity() from exc


@dataclass(frozen=True, repr=False)
class DeviceIdentitySecret:
    device_id: str
    created_at: str
    key_epoch: int
    signing_private_key: bytes
    agreement_private_key: bytes
    format: str = DEVICE_IDENTITY_SECRET_FORMAT
    version: int = DEVICE_IDENTITY_VERSION

    def __post_init__(self) -> None:
        try:
            if self.format != DEVICE_IDENTITY_SECRET_FORMAT:
                raise _invalid_identity()
            if _require_int(self.version) != DEVICE_IDENTITY_VERSION:
                raise _invalid_identity()
            _require_key_epoch(self.key_epoch)
            _require_utc_seconds(self.created_at)
            signing = _require_bytes(self.signing_private_key, DEVICE_KEY_BYTES)
            agreement = _require_bytes(
                self.agreement_private_key,
                DEVICE_KEY_BYTES,
            )
            signing_public = _public_bytes(
                Ed25519PrivateKey.from_private_bytes(signing).public_key()
            )
            agreement_public = _public_bytes(
                X25519PrivateKey.from_private_bytes(agreement).public_key()
            )
            _require_device_id(self.device_id, signing_public, agreement_public)
            object.__setattr__(self, "signing_private_key", signing)
            object.__setattr__(self, "agreement_private_key", agreement)
        except DeviceIdentityError:
            raise
        except Exception as exc:
            raise _invalid_identity() from exc

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> DeviceIdentitySecret:
        try:
            obj = _require_mapping(data)
            _require_exact_keys(
                obj,
                {
                    "format",
                    "version",
                    "device_id",
                    "created_at",
                    "key_epoch",
                    "signing_private_key",
                    "agreement_private_key",
                },
            )
            return cls(
                format=obj.get("format"),
                version=_require_int(obj.get("version")),
                device_id=obj.get("device_id"),
                created_at=_require_utc_seconds(obj.get("created_at")),
                key_epoch=_require_key_epoch(obj.get("key_epoch")),
                signing_private_key=_decode_base64(
                    obj.get("signing_private_key"),
                    DEVICE_KEY_BYTES,
                ),
                agreement_private_key=_decode_base64(
                    obj.get("agreement_private_key"),
                    DEVICE_KEY_BYTES,
                ),
            )
        except DeviceIdentityError:
            raise
        except Exception as exc:
            raise _invalid_identity() from exc

    @classmethod
    def from_canonical_bytes(cls, data: bytes) -> DeviceIdentitySecret:
        try:
            decoded = cls.from_dict(_canonical_json_object(data))
            if not hmac.compare_digest(decoded.canonical_bytes(), data):
                raise _invalid_identity()
            return decoded
        except DeviceIdentityError:
            raise
        except Exception as exc:
            raise _invalid_identity() from exc

    def to_dict(self) -> dict[str, Any]:
        return {
            "format": self.format,
            "version": self.version,
            "device_id": self.device_id,
            "created_at": self.created_at,
            "key_epoch": self.key_epoch,
            "signing_private_key": _encode_base64(self.signing_private_key),
            "agreement_private_key": _encode_base64(
                self.agreement_private_key
            ),
        }

    def canonical_bytes(self) -> bytes:
        return _canonical_json_bytes(self.to_dict())

    def load_identity(self) -> DeviceIdentity:
        return device_identity_from_private_keys(
            signing_private_seed=self.signing_private_key,
            agreement_private_key=self.agreement_private_key,
            created_at=self.created_at,
            key_epoch=self.key_epoch,
            expected_device_id=self.device_id,
        )

    def __repr__(self) -> str:
        return "DeviceIdentitySecret(<redacted>)"

    __str__ = __repr__


@dataclass(frozen=True, repr=False)
class DeviceIdentity:
    _signing_private_seed: bytes
    _agreement_private_key: bytes
    descriptor: DeviceDescriptor

    def __post_init__(self) -> None:
        try:
            signing_seed = _require_bytes(
                self._signing_private_seed,
                DEVICE_KEY_BYTES,
            )
            agreement_key = _require_bytes(
                self._agreement_private_key,
                DEVICE_KEY_BYTES,
            )
            if not isinstance(self.descriptor, DeviceDescriptor):
                raise _invalid_identity()
            signing_public = _public_bytes(
                Ed25519PrivateKey.from_private_bytes(signing_seed).public_key()
            )
            agreement_public = _public_bytes(
                X25519PrivateKey.from_private_bytes(agreement_key).public_key()
            )
            expected_descriptor = DeviceDescriptor.from_public_keys(
                signing_public_key=signing_public,
                agreement_public_key=agreement_public,
                created_at=self.descriptor.created_at,
                key_epoch=self.descriptor.key_epoch,
            )
            if expected_descriptor != self.descriptor:
                raise _invalid_identity()
            object.__setattr__(self, "_signing_private_seed", signing_seed)
            object.__setattr__(self, "_agreement_private_key", agreement_key)
        except DeviceIdentityError:
            raise
        except Exception as exc:
            raise _invalid_identity() from exc

    @property
    def device_id(self) -> str:
        return self.descriptor.device_id

    @property
    def signing_public_key(self) -> bytes:
        return self.descriptor.signing_public_key

    @property
    def agreement_public_key(self) -> bytes:
        return self.descriptor.agreement_public_key

    def sign(self, message: bytes) -> bytes:
        if not isinstance(message, bytes):
            raise _invalid_identity()
        try:
            return Ed25519PrivateKey.from_private_bytes(
                self._signing_private_seed
            ).sign(message)
        except Exception as exc:
            raise _invalid_identity() from exc

    def sign_descriptor(self) -> SignedDeviceDescriptor:
        return SignedDeviceDescriptor(
            descriptor=self.descriptor,
            signature=self.sign(
                DEVICE_DESCRIPTOR_SIGNATURE_DOMAIN
                + self.descriptor.canonical_bytes()
            ),
        )

    def shared_secret_for(self, agreement_public_key: bytes) -> bytes:
        try:
            public_key = X25519PublicKey.from_public_bytes(
                _require_bytes(agreement_public_key, DEVICE_KEY_BYTES)
            )
            secret = X25519PrivateKey.from_private_bytes(
                self._agreement_private_key
            ).exchange(public_key)
            if len(secret) != DEVICE_KEY_BYTES or hmac.compare_digest(
                secret,
                b"\0" * DEVICE_KEY_BYTES,
            ):
                raise _invalid_identity()
            return secret
        except DeviceIdentityError:
            raise
        except Exception as exc:
            raise _invalid_identity() from exc

    def secret_bundle(self) -> DeviceIdentitySecret:
        return DeviceIdentitySecret(
            device_id=self.device_id,
            created_at=self.descriptor.created_at,
            key_epoch=self.descriptor.key_epoch,
            signing_private_key=self._signing_private_seed,
            agreement_private_key=self._agreement_private_key,
        )

    def __repr__(self) -> str:
        return "DeviceIdentity(<redacted>)"

    __str__ = __repr__


def device_identity_from_private_keys(
    *,
    signing_private_seed: bytes,
    agreement_private_key: bytes,
    created_at: str,
    key_epoch: int = 1,
    expected_device_id: str | None = None,
) -> DeviceIdentity:
    try:
        signing_seed = _require_bytes(signing_private_seed, DEVICE_KEY_BYTES)
        agreement_key = _require_bytes(
            agreement_private_key,
            DEVICE_KEY_BYTES,
        )
        signing_public = _public_bytes(
            Ed25519PrivateKey.from_private_bytes(signing_seed).public_key()
        )
        agreement_public = _public_bytes(
            X25519PrivateKey.from_private_bytes(agreement_key).public_key()
        )
        descriptor = DeviceDescriptor.from_public_keys(
            signing_public_key=signing_public,
            agreement_public_key=agreement_public,
            created_at=created_at,
            key_epoch=key_epoch,
        )
        if expected_device_id is not None and not hmac.compare_digest(
            descriptor.device_id,
            expected_device_id,
        ):
            raise _invalid_identity()
        return DeviceIdentity(
            _signing_private_seed=signing_seed,
            _agreement_private_key=agreement_key,
            descriptor=descriptor,
        )
    except DeviceIdentityError:
        raise
    except Exception as exc:
        raise _invalid_identity() from exc


def generate_device_identity(
    *,
    created_at: str | None = None,
    key_epoch: int = 1,
) -> DeviceIdentity:
    try:
        signing = Ed25519PrivateKey.generate().private_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PrivateFormat.Raw,
            encryption_algorithm=serialization.NoEncryption(),
        )
        agreement = X25519PrivateKey.generate().private_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PrivateFormat.Raw,
            encryption_algorithm=serialization.NoEncryption(),
        )
        return device_identity_from_private_keys(
            signing_private_seed=signing,
            agreement_private_key=agreement,
            created_at=_utc_now_seconds() if created_at is None else created_at,
            key_epoch=key_epoch,
        )
    except DeviceIdentityError:
        raise
    except Exception as exc:
        raise _invalid_identity() from exc
