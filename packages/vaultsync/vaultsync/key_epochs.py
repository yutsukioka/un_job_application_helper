from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from .hpke_key_delivery import (
    HPKEKeyDeliveryError,
    HPKESealedVaultKeyV2,
    _seal_vault_key_hpke_v2_for_testing,
    open_vault_key_hpke_v2,
    seal_vault_key_hpke_v2,
)


MAXIMUM_KEY_EPOCH = (1 << 63) - 1
MAXIMUM_KEY_RING_ENTRIES = 32
_KEY_BYTES = 32
_MAX_IDENTIFIER_BYTES = 1024
_METADATA_FORMAT = "atlasvault-vault-key-ring"
_METADATA_VERSION = 1
_HPKE_EPOCH_CONTEXT_PREFIX = b"atlasvault-key-epoch-hpke-v1:"
_RECORD_SALT_PREFIX = "atlasvault-record-key-epoch-v1:"


class KeyEpochError(ValueError):
    """Raised when key-epoch state or delivery fails closed."""


def _invalid() -> KeyEpochError:
    return KeyEpochError("AtlasVault key epoch operation failed")


def _epoch(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise _invalid()
    if value < 1 or value > MAXIMUM_KEY_EPOCH:
        raise _invalid()
    return value


def _key(value: bytes) -> bytes:
    if not isinstance(value, bytes) or len(value) != _KEY_BYTES:
        raise _invalid()
    return bytes(value)


def _identifier(value: str) -> str:
    if not isinstance(value, str) or not value:
        raise _invalid()
    encoded = value.encode("utf-8")
    if len(encoded) > _MAX_IDENTIFIER_BYTES:
        raise _invalid()
    return value


@dataclass(frozen=True)
class KeyRingMetadata:
    current_key_epoch: int
    retained_key_epochs: tuple[int, ...] = ()

    def __post_init__(self) -> None:
        current = _epoch(self.current_key_epoch)
        retained = tuple(_epoch(value) for value in self.retained_key_epochs)
        if (
            tuple(sorted(set(retained))) != retained
            or any(value >= current for value in retained)
            or len(retained) + 1 > MAXIMUM_KEY_RING_ENTRIES
        ):
            raise _invalid()
        object.__setattr__(self, "current_key_epoch", current)
        object.__setattr__(self, "retained_key_epochs", retained)

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> KeyRingMetadata:
        if not isinstance(value, Mapping) or set(value) != {
            "format",
            "version",
            "current_key_epoch",
            "retained_key_epochs",
        }:
            raise _invalid()
        retained = value["retained_key_epochs"]
        if (
            value["format"] != _METADATA_FORMAT
            or isinstance(value["version"], bool)
            or not isinstance(value["version"], int)
            or value["version"] != _METADATA_VERSION
            or not isinstance(retained, list)
        ):
            raise _invalid()
        return cls(
            current_key_epoch=_epoch(value["current_key_epoch"]),
            retained_key_epochs=tuple(_epoch(item) for item in retained),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "format": _METADATA_FORMAT,
            "version": _METADATA_VERSION,
            "current_key_epoch": self.current_key_epoch,
            "retained_key_epochs": list(self.retained_key_epochs),
        }


@dataclass(frozen=True)
class EpochVaultKey:
    key_epoch: int
    vault_key: bytes

    def __post_init__(self) -> None:
        object.__setattr__(self, "key_epoch", _epoch(self.key_epoch))
        object.__setattr__(self, "vault_key", _key(self.vault_key))


@dataclass(frozen=True)
class EpochHPKESealedVaultKeyV2:
    key_epoch: int
    encapsulated_key: bytes
    ciphertext: bytes

    def __post_init__(self) -> None:
        sealed = HPKESealedVaultKeyV2(
            encapsulated_key=self.encapsulated_key,
            ciphertext=self.ciphertext,
        )
        object.__setattr__(self, "key_epoch", _epoch(self.key_epoch))
        object.__setattr__(self, "encapsulated_key", sealed.encapsulated_key)
        object.__setattr__(self, "ciphertext", sealed.ciphertext)


class VaultKeyEpochRing:
    """Bounded key lookup for one current and zero or more retained epochs.

    This primitive intentionally cannot generate, advance, retire, or rotate keys.
    """

    def __init__(
        self,
        *,
        metadata: KeyRingMetadata,
        keys: Mapping[int, bytes],
    ) -> None:
        if not isinstance(metadata, KeyRingMetadata) or not isinstance(keys, Mapping):
            raise _invalid()
        copied = {_epoch(epoch): _key(value) for epoch, value in keys.items()}
        expected = set(metadata.retained_key_epochs) | {metadata.current_key_epoch}
        if (
            set(copied) != expected
            or len(copied) > MAXIMUM_KEY_RING_ENTRIES
            or len(set(copied.values())) != len(copied)
        ):
            raise _invalid()
        self._metadata = metadata
        self._keys = MappingProxyType(copied)

    @classmethod
    def from_entries(
        cls,
        *,
        current_key_epoch: int,
        keys: Mapping[int, bytes],
    ) -> VaultKeyEpochRing:
        current = _epoch(current_key_epoch)
        if not isinstance(keys, Mapping) or current not in keys:
            raise _invalid()
        epochs = sorted(_epoch(epoch) for epoch in keys)
        if any(epoch >= current for epoch in epochs if epoch != current):
            raise _invalid()
        return cls(
            metadata=KeyRingMetadata(
                current_key_epoch=current,
                retained_key_epochs=tuple(epoch for epoch in epochs if epoch != current),
            ),
            keys=keys,
        )

    @classmethod
    def from_legacy(
        cls,
        vault_key: bytes,
        *,
        key_epoch: int = 1,
    ) -> VaultKeyEpochRing:
        epoch = _epoch(key_epoch)
        return cls.from_entries(current_key_epoch=epoch, keys={epoch: vault_key})

    @property
    def metadata(self) -> KeyRingMetadata:
        return self._metadata

    @property
    def current_key_epoch(self) -> int:
        return self._metadata.current_key_epoch

    @property
    def current_vault_key(self) -> bytes:
        return self.vault_key_for_epoch(self.current_key_epoch)

    def vault_key_for_epoch(self, key_epoch: int) -> bytes:
        try:
            return bytes(self._keys[_epoch(key_epoch)])
        except (KeyError, KeyEpochError) as exc:
            raise _invalid() from exc

    def derive_record_key(
        self,
        *,
        key_epoch: int,
        vault_id: str,
        record_id: str,
    ) -> bytes:
        epoch = _epoch(key_epoch)
        return HKDF(
            algorithm=hashes.SHA256(),
            length=_KEY_BYTES,
            salt=f"{_RECORD_SALT_PREFIX}{_identifier(vault_id)}".encode(),
            info=f"epoch:{epoch}:record:{_identifier(record_id)}".encode(),
        ).derive(self.vault_key_for_epoch(epoch))

    def seal_current_hpke_v2(
        self,
        *,
        recipient_public_key: bytes,
        context: bytes,
    ) -> EpochHPKESealedVaultKeyV2:
        try:
            sealed = seal_vault_key_hpke_v2(
                recipient_public_key=recipient_public_key,
                vault_key=self.current_vault_key,
                context=_epoch_context(self.current_key_epoch, context),
            )
            return _epoch_sealed(self.current_key_epoch, sealed)
        except (HPKEKeyDeliveryError, KeyEpochError) as exc:
            raise _invalid() from exc

    def _seal_current_hpke_v2_for_testing(
        self,
        *,
        recipient_public_key: bytes,
        context: bytes,
        ephemeral_private_key: bytes,
    ) -> EpochHPKESealedVaultKeyV2:
        try:
            sealed = _seal_vault_key_hpke_v2_for_testing(
                recipient_public_key=recipient_public_key,
                vault_key=self.current_vault_key,
                context=_epoch_context(self.current_key_epoch, context),
                ephemeral_private_key=ephemeral_private_key,
            )
            return _epoch_sealed(self.current_key_epoch, sealed)
        except (HPKEKeyDeliveryError, KeyEpochError) as exc:
            raise _invalid() from exc


def open_epoch_hpke_v2(
    *,
    recipient_private_key: bytes,
    sealed: EpochHPKESealedVaultKeyV2,
    context: bytes,
    minimum_key_epoch: int = 1,
) -> EpochVaultKey:
    try:
        if not isinstance(sealed, EpochHPKESealedVaultKeyV2):
            raise _invalid()
        minimum = _epoch(minimum_key_epoch)
        if sealed.key_epoch < minimum:
            raise _invalid()
        vault_key = open_vault_key_hpke_v2(
            recipient_private_key=recipient_private_key,
            sealed=HPKESealedVaultKeyV2(
                encapsulated_key=sealed.encapsulated_key,
                ciphertext=sealed.ciphertext,
            ),
            context=_epoch_context(sealed.key_epoch, context),
        )
        return EpochVaultKey(key_epoch=sealed.key_epoch, vault_key=vault_key)
    except (HPKEKeyDeliveryError, KeyEpochError) as exc:
        raise _invalid() from exc


def _epoch_context(key_epoch: int, context: bytes) -> bytes:
    epoch = _epoch(key_epoch)
    if not isinstance(context, bytes) or not context:
        raise _invalid()
    return _HPKE_EPOCH_CONTEXT_PREFIX + epoch.to_bytes(8, "big") + b":" + context


def _epoch_sealed(
    key_epoch: int,
    sealed: HPKESealedVaultKeyV2,
) -> EpochHPKESealedVaultKeyV2:
    return EpochHPKESealedVaultKeyV2(
        key_epoch=key_epoch,
        encapsulated_key=sealed.encapsulated_key,
        ciphertext=sealed.ciphertext,
    )
