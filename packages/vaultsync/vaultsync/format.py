from __future__ import annotations

import base64
import binascii
import json
import secrets
import uuid
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

VAULT_FORMAT = "atlas-vault"
SUPPORTED_VAULT_VERSION = 1


class VaultFormatError(ValueError):
    """Raised when serialized vault metadata is invalid."""


class UnsupportedVaultVersion(VaultFormatError):
    """Raised when a vault metadata version is not supported by this package."""


class VaultCryptoError(ValueError):
    """Base class for vault cryptographic failures."""


class VaultKeyUnwrapError(VaultCryptoError):
    """Raised when a vault key cannot be unwrapped."""


class VaultAuthenticationError(VaultCryptoError):
    """Raised when authenticated encrypted data fails validation."""


def _b64encode(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _b64decode(value: Any, field_name: str) -> bytes:
    if not isinstance(value, str):
        raise VaultFormatError(f"{field_name} must be base64 text")
    try:
        return base64.b64decode(value.encode("ascii"), validate=True)
    except (binascii.Error, UnicodeEncodeError) as exc:
        raise VaultFormatError(f"{field_name} must be valid base64") from exc


def _stable_json_bytes(value: Mapping[str, Any]) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode(
        "utf-8"
    )


def _json_loads(data: str | bytes | bytearray, error_cls: type[Exception]) -> Any:
    if isinstance(data, (bytes, bytearray)):
        data = data.decode("utf-8")
    if not isinstance(data, str):
        raise error_cls("serialized data must be JSON text")
    try:
        return json.loads(data)
    except json.JSONDecodeError as exc:
        raise error_cls("serialized data must be valid JSON") from exc


def _require_mapping(value: Any, context: str, error_cls: type[Exception] = VaultFormatError) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise error_cls(f"{context} must be an object")
    return value


def _require_text(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value:
        raise VaultFormatError(f"{field_name} must be non-empty text")
    return value


def _require_int(value: Any, field_name: str) -> int:
    if not isinstance(value, int):
        raise VaultFormatError(f"{field_name} must be an integer")
    return value


@dataclass(frozen=True)
class Argon2idParams:
    salt: bytes = field(default_factory=lambda: secrets.token_bytes(16))
    memory_kib: int = 65_536
    iterations: int = 3
    parallelism: int = 4

    def __post_init__(self) -> None:
        if not isinstance(self.salt, bytes) or len(self.salt) < 16:
            raise VaultFormatError("Argon2id salt must be at least 128 bits")
        if self.memory_kib <= 0 or self.iterations <= 0 or self.parallelism <= 0:
            raise VaultFormatError("Argon2id parameters must be positive")

    def with_salt(self, salt: bytes) -> Argon2idParams:
        return Argon2idParams(
            salt=salt,
            memory_kib=self.memory_kib,
            iterations=self.iterations,
            parallelism=self.parallelism,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "algorithm": "Argon2id",
            "salt": _b64encode(self.salt),
            "memory_kib": self.memory_kib,
            "iterations": self.iterations,
            "parallelism": self.parallelism,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Argon2idParams:
        obj = _require_mapping(data, "kdf")
        if obj.get("algorithm") != "Argon2id":
            raise VaultFormatError("unsupported key-wrap KDF")
        return cls(
            salt=_b64decode(obj.get("salt"), "kdf.salt"),
            memory_kib=_require_int(obj.get("memory_kib"), "kdf.memory_kib"),
            iterations=_require_int(obj.get("iterations"), "kdf.iterations"),
            parallelism=_require_int(obj.get("parallelism"), "kdf.parallelism"),
        )


@dataclass(frozen=True)
class WrappedKey:
    id: str
    type: str
    kdf: Argon2idParams
    nonce: bytes
    ciphertext: bytes

    def __post_init__(self) -> None:
        _require_text(self.id, "key_wrap.id")
        if self.type != "passphrase":
            raise VaultFormatError("unsupported key-wrap type")
        if not isinstance(self.nonce, bytes) or len(self.nonce) != 12:
            raise VaultFormatError("key-wrap nonce must be 96 bits")
        if not isinstance(self.ciphertext, bytes) or not self.ciphertext:
            raise VaultFormatError("key-wrap ciphertext must be non-empty")

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": self.type,
            "kdf": self.kdf.to_dict(),
            "nonce": _b64encode(self.nonce),
            "ciphertext": _b64encode(self.ciphertext),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> WrappedKey:
        obj = _require_mapping(data, "key_wrap")
        return cls(
            id=_require_text(obj.get("id"), "key_wrap.id"),
            type=_require_text(obj.get("type"), "key_wrap.type"),
            kdf=Argon2idParams.from_dict(_require_mapping(obj.get("kdf"), "key_wrap.kdf")),
            nonce=_b64decode(obj.get("nonce"), "key_wrap.nonce"),
            ciphertext=_b64decode(obj.get("ciphertext"), "key_wrap.ciphertext"),
        )


@dataclass(frozen=True)
class VaultCryptoSuite:
    record_aead: str = "AES-256-GCM"
    kdf: str = "Argon2id"
    subkey_kdf: str = "HKDF-SHA256"
    key_wrap_aead: str = "AES-256-GCM"

    def to_dict(self) -> dict[str, str]:
        return {
            "record_aead": self.record_aead,
            "kdf": self.kdf,
            "subkey_kdf": self.subkey_kdf,
            "key_wrap_aead": self.key_wrap_aead,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> VaultCryptoSuite:
        obj = _require_mapping(data, "crypto")
        suite = cls(
            record_aead=_require_text(obj.get("record_aead"), "crypto.record_aead"),
            kdf=_require_text(obj.get("kdf"), "crypto.kdf"),
            subkey_kdf=_require_text(obj.get("subkey_kdf"), "crypto.subkey_kdf"),
            key_wrap_aead=_require_text(obj.get("key_wrap_aead"), "crypto.key_wrap_aead"),
        )
        if suite != cls():
            raise VaultFormatError("unsupported vault crypto suite")
        return suite


@dataclass(frozen=True)
class VaultMetadata:
    vault_id: str
    key_wraps: tuple[WrappedKey, ...]
    crypto: VaultCryptoSuite = field(default_factory=VaultCryptoSuite)
    format: str = VAULT_FORMAT
    version: int = SUPPORTED_VAULT_VERSION

    def __post_init__(self) -> None:
        if self.format != VAULT_FORMAT:
            raise VaultFormatError("unsupported vault format")
        if self.version != SUPPORTED_VAULT_VERSION:
            raise UnsupportedVaultVersion("unsupported vault version")
        _require_text(self.vault_id, "vault_id")
        if not isinstance(self.key_wraps, tuple):
            object.__setattr__(self, "key_wraps", tuple(self.key_wraps))

    @classmethod
    def new(
        cls,
        *,
        vault_id: str | None = None,
        key_wraps: Sequence[WrappedKey] = (),
        crypto: VaultCryptoSuite | None = None,
    ) -> VaultMetadata:
        return cls(
            vault_id=vault_id or str(uuid.uuid4()),
            key_wraps=tuple(key_wraps),
            crypto=crypto or VaultCryptoSuite(),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "format": self.format,
            "version": self.version,
            "vault_id": self.vault_id,
            "crypto": self.crypto.to_dict(),
            "key_wraps": [wrapped_key.to_dict() for wrapped_key in self.key_wraps],
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> VaultMetadata:
        obj = _require_mapping(data, "vault metadata")
        if obj.get("format") != VAULT_FORMAT:
            raise VaultFormatError("unsupported vault format")
        version = obj.get("version")
        if version != SUPPORTED_VAULT_VERSION:
            raise UnsupportedVaultVersion("unsupported vault version")
        key_wraps = obj.get("key_wraps")
        if not isinstance(key_wraps, list):
            raise VaultFormatError("key_wraps must be a list")
        return cls(
            format=VAULT_FORMAT,
            version=version,
            vault_id=_require_text(obj.get("vault_id"), "vault_id"),
            crypto=VaultCryptoSuite.from_dict(_require_mapping(obj.get("crypto"), "crypto")),
            key_wraps=tuple(WrappedKey.from_dict(item) for item in key_wraps),
        )


def serialize_vault_metadata(metadata: VaultMetadata) -> str:
    return json.dumps(
        metadata.to_dict(),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )


def deserialize_vault_metadata(data: str | bytes | bytearray | Mapping[str, Any]) -> VaultMetadata:
    obj = data if isinstance(data, Mapping) else _json_loads(data, VaultFormatError)
    return VaultMetadata.from_dict(_require_mapping(obj, "vault metadata"))
