from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import (
    X25519PrivateKey,
    X25519PublicKey,
)
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

try:
    from cryptography.hazmat.primitives.hpke import AEAD, KDF, KEM, Suite
except ImportError:  # cryptography 42-47 use the RFC 9180 fallback below.
    AEAD = KDF = KEM = Suite = None


HPKE_KEY_DELIVERY_VERSION = 2
HPKE_KEY_DELIVERY_INFO_PREFIX = b"atlasvault-vault-key-delivery-hpke-v2:"
HPKE_KEM_ID = 0x0020
HPKE_KDF_ID = 0x0001
HPKE_AEAD_ID = 0x0002
_KEY_BYTES = 32
_ENCAPSULATED_KEY_BYTES = 32
_CIPHERTEXT_BYTES = 48
_NONCE_BYTES = 12
_MAX_CONTEXT_BYTES = 4096


class HPKEKeyDeliveryError(ValueError):
    """Raised when the version-2 HPKE key-delivery seam fails closed."""


def _invalid() -> HPKEKeyDeliveryError:
    return HPKEKeyDeliveryError("HPKE key delivery failed")


def _bytes(value: bytes, length: int | None = None) -> bytes:
    if not isinstance(value, bytes) or (length is not None and len(value) != length):
        raise _invalid()
    return bytes(value)


def _context(value: bytes) -> bytes:
    result = _bytes(value)
    if not result or len(result) > _MAX_CONTEXT_BYTES:
        raise _invalid()
    return result


@dataclass(frozen=True)
class HPKESealedVaultKeyV2:
    encapsulated_key: bytes
    ciphertext: bytes

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "encapsulated_key",
            _bytes(self.encapsulated_key, _ENCAPSULATED_KEY_BYTES),
        )
        object.__setattr__(
            self,
            "ciphertext",
            _bytes(self.ciphertext, _CIPHERTEXT_BYTES),
        )

    def with_ciphertext(self, ciphertext: bytes) -> HPKESealedVaultKeyV2:
        return HPKESealedVaultKeyV2(
            encapsulated_key=self.encapsulated_key,
            ciphertext=ciphertext,
        )


def _info(context: bytes) -> bytes:
    return HPKE_KEY_DELIVERY_INFO_PREFIX + _context(context)


def _native_suite():
    if Suite is None:
        return None
    return Suite(KEM.X25519, KDF.HKDF_SHA256, AEAD.AES_256_GCM)


def seal_vault_key_hpke_v2(
    *,
    recipient_public_key: bytes,
    vault_key: bytes,
    context: bytes,
) -> HPKESealedVaultKeyV2:
    """Seal one vault key with production-owned HPKE encapsulation entropy."""

    try:
        public = X25519PublicKey.from_public_bytes(
            _bytes(recipient_public_key, _KEY_BYTES)
        )
        plaintext = _bytes(vault_key, _KEY_BYTES)
        info = _info(context)
        suite = _native_suite()
        if suite is not None:
            combined = suite.encrypt(plaintext, public, info=info)
            return HPKESealedVaultKeyV2(
                encapsulated_key=combined[:_ENCAPSULATED_KEY_BYTES],
                ciphertext=combined[_ENCAPSULATED_KEY_BYTES:],
            )
        return _seal_with_private_key(
            recipient_public_key=public,
            vault_key=plaintext,
            info=info,
            ephemeral_private_key=X25519PrivateKey.generate(),
        )
    except HPKEKeyDeliveryError:
        raise
    except Exception as exc:
        raise _invalid() from exc


def _seal_vault_key_hpke_v2_for_testing(
    *,
    recipient_public_key: bytes,
    vault_key: bytes,
    context: bytes,
    ephemeral_private_key: bytes,
) -> HPKESealedVaultKeyV2:
    """Deterministic RFC 9180 vector seam; not used by production callers."""

    try:
        return _seal_with_private_key(
            recipient_public_key=X25519PublicKey.from_public_bytes(
                _bytes(recipient_public_key, _KEY_BYTES)
            ),
            vault_key=_bytes(vault_key, _KEY_BYTES),
            info=_info(context),
            ephemeral_private_key=X25519PrivateKey.from_private_bytes(
                _bytes(ephemeral_private_key, _KEY_BYTES)
            ),
        )
    except HPKEKeyDeliveryError:
        raise
    except Exception as exc:
        raise _invalid() from exc


def open_vault_key_hpke_v2(
    *,
    recipient_private_key: bytes,
    sealed: HPKESealedVaultKeyV2,
    context: bytes,
) -> bytes:
    try:
        if not isinstance(sealed, HPKESealedVaultKeyV2):
            raise _invalid()
        private = X25519PrivateKey.from_private_bytes(
            _bytes(recipient_private_key, _KEY_BYTES)
        )
        info = _info(context)
        suite = _native_suite()
        if suite is not None:
            plaintext = suite.decrypt(
                sealed.encapsulated_key + sealed.ciphertext,
                private,
                info=info,
            )
        else:
            remote = X25519PublicKey.from_public_bytes(sealed.encapsulated_key)
            key, nonce = _key_schedule(
                dh=private.exchange(remote),
                encapsulated_key=sealed.encapsulated_key,
                recipient_public_key=_public_bytes(private.public_key()),
                info=info,
            )
            plaintext = AESGCM(key).decrypt(nonce, sealed.ciphertext, b"")
        return _bytes(plaintext, _KEY_BYTES)
    except HPKEKeyDeliveryError:
        raise
    except (InvalidTag, Exception) as exc:
        raise _invalid() from exc


def _seal_with_private_key(
    *,
    recipient_public_key: X25519PublicKey,
    vault_key: bytes,
    info: bytes,
    ephemeral_private_key: X25519PrivateKey,
) -> HPKESealedVaultKeyV2:
    encapsulated_key = _public_bytes(ephemeral_private_key.public_key())
    key, nonce = _key_schedule(
        dh=ephemeral_private_key.exchange(recipient_public_key),
        encapsulated_key=encapsulated_key,
        recipient_public_key=_public_bytes(recipient_public_key),
        info=info,
    )
    return HPKESealedVaultKeyV2(
        encapsulated_key=encapsulated_key,
        ciphertext=AESGCM(key).encrypt(nonce, vault_key, b""),
    )


def _key_schedule(
    *,
    dh: bytes,
    encapsulated_key: bytes,
    recipient_public_key: bytes,
    info: bytes,
) -> tuple[bytes, bytes]:
    shared = _extract_and_expand(
        _bytes(dh, _KEY_BYTES),
        encapsulated_key + recipient_public_key,
    )
    suite = (
        b"HPKE"
        + HPKE_KEM_ID.to_bytes(2, "big")
        + HPKE_KDF_ID.to_bytes(2, "big")
        + HPKE_AEAD_ID.to_bytes(2, "big")
    )
    psk_id_hash = _labeled_extract(suite, b"", b"psk_id_hash", b"")
    info_hash = _labeled_extract(suite, b"", b"info_hash", info)
    schedule_context = b"\x00" + psk_id_hash + info_hash
    secret = _labeled_extract(suite, shared, b"secret", b"")
    return (
        _labeled_expand(suite, secret, b"key", schedule_context, _KEY_BYTES),
        _labeled_expand(
            suite,
            secret,
            b"base_nonce",
            schedule_context,
            _NONCE_BYTES,
        ),
    )


def _extract_and_expand(dh: bytes, kem_context: bytes) -> bytes:
    suite = b"KEM" + HPKE_KEM_ID.to_bytes(2, "big")
    eae_prk = _labeled_extract(suite, b"", b"eae_prk", dh)
    return _labeled_expand(suite, eae_prk, b"shared_secret", kem_context, _KEY_BYTES)


def _labeled_extract(suite: bytes, salt: bytes, label: bytes, ikm: bytes) -> bytes:
    return hmac.digest(
        salt or (b"\x00" * hashlib.sha256().digest_size),
        b"HPKE-v1" + suite + label + ikm,
        "sha256",
    )


def _labeled_expand(
    suite: bytes,
    prk: bytes,
    label: bytes,
    info: bytes,
    length: int,
) -> bytes:
    labeled = length.to_bytes(2, "big") + b"HPKE-v1" + suite + label + info
    output = b""
    block = b""
    for counter in range(1, (length + 31) // 32 + 1):
        block = hmac.digest(prk, block + labeled + bytes([counter]), "sha256")
        output += block
    return output[:length]


def _public_bytes(key: X25519PublicKey) -> bytes:
    return key.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
