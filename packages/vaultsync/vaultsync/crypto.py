from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import re
import secrets
import uuid
from typing import Any

from argon2.low_level import Type, hash_secret_raw
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from vaultsync.format import (
    SUPPORTED_VAULT_VERSION,
    VAULT_FORMAT,
    Argon2idParams,
    RECOVERY_WRAP_ID,
    RECOVERY_WRAP_KDF_INFO,
    RecoveryKeyError,
    RecoveryKeyWrapHKDFParams,
    RecoveryKeyWrapV2,
    VaultAuthenticationError,
    VaultFormatError,
    VaultKeyUnwrapError,
    VaultMetadata,
    WrappedKey,
    _stable_json_bytes,
)
from vaultsync.records import (
    SUPPORTED_RECORD_SCHEMA_VERSION,
    EncryptedRecord,
    PlaintextRecord,
    RecordFormatError,
)

VAULT_KEY_BYTES = 32
AES_GCM_NONCE_BYTES = 12
RECOVERY_KEY_BYTES = 32
RECOVERY_CHECKSUM_BYTES = 5
RECOVERY_BASE32_SYMBOLS = 60
RECOVERY_TEXT_PREFIX = "AVRK1"
RECOVERY_CHECKSUM_DOMAIN = b"atlasvault-recovery-key-v1:"


def generate_vault_key() -> bytes:
    """Generate a random 256-bit AtlasVault key."""

    return secrets.token_bytes(VAULT_KEY_BYTES)


def generate_recovery_key() -> bytes:
    """Generate a random 256-bit AtlasVault recovery key."""

    return secrets.token_bytes(RECOVERY_KEY_BYTES)


def _invalid_recovery_key() -> RecoveryKeyError:
    return RecoveryKeyError("invalid recovery key")


def _recovery_checksum(recovery_key: bytes) -> bytes:
    return hashlib.sha256(RECOVERY_CHECKSUM_DOMAIN + recovery_key).digest()[
        :RECOVERY_CHECKSUM_BYTES
    ]


def encode_recovery_key(recovery_key: bytes) -> str:
    if not isinstance(recovery_key, bytes) or len(recovery_key) != RECOVERY_KEY_BYTES:
        raise _invalid_recovery_key()
    symbols = base64.b32encode(
        recovery_key + _recovery_checksum(recovery_key)
    ).decode("ascii").rstrip("=")
    if len(symbols) != RECOVERY_BASE32_SYMBOLS:
        raise _invalid_recovery_key()
    groups = "-".join(
        symbols[index : index + 4]
        for index in range(0, len(symbols), 4)
    )
    return f"{RECOVERY_TEXT_PREFIX}-{groups}"


def parse_recovery_key(value: str) -> bytes:
    try:
        if not isinstance(value, str):
            raise _invalid_recovery_key()
        value.encode("ascii")
        normalized = value.strip(" \t\r\n")
        if "=" in normalized:
            raise _invalid_recovery_key()
        parts = re.split(r"[- ]+", normalized.upper())
        if (
            len(parts) != 16
            or parts[0] != RECOVERY_TEXT_PREFIX
            or any(len(group) != 4 for group in parts[1:])
        ):
            raise _invalid_recovery_key()
        symbols = "".join(parts[1:])
        if (
            len(symbols) != RECOVERY_BASE32_SYMBOLS
            or re.fullmatch(r"[A-Z2-7]{60}", symbols) is None
        ):
            raise _invalid_recovery_key()
        decoded = base64.b32decode(symbols + "====", casefold=False)
        if len(decoded) != RECOVERY_KEY_BYTES + RECOVERY_CHECKSUM_BYTES:
            raise _invalid_recovery_key()
        recovery_key = decoded[:RECOVERY_KEY_BYTES]
        checksum = decoded[RECOVERY_KEY_BYTES:]
        if not hmac.compare_digest(checksum, _recovery_checksum(recovery_key)):
            raise _invalid_recovery_key()
        return recovery_key
    except RecoveryKeyError:
        raise
    except (UnicodeEncodeError, binascii.Error, ValueError) as exc:
        raise _invalid_recovery_key() from exc


def _require_vault_key(vault_key: bytes) -> None:
    if not isinstance(vault_key, bytes) or len(vault_key) != VAULT_KEY_BYTES:
        raise VaultFormatError("vault key must be 256 bits")


def _nonce(nonce: bytes | None) -> bytes:
    if nonce is None:
        return secrets.token_bytes(AES_GCM_NONCE_BYTES)
    if not isinstance(nonce, bytes) or len(nonce) != AES_GCM_NONCE_BYTES:
        raise VaultFormatError("AES-GCM nonce must be 96 bits")
    return nonce


def derive_wrapping_key_argon2id(passphrase: str, params: Argon2idParams) -> bytes:
    if not isinstance(passphrase, str) or not passphrase:
        raise VaultKeyUnwrapError("passphrase is required")
    return hash_secret_raw(
        secret=passphrase.encode("utf-8"),
        salt=params.salt,
        time_cost=params.iterations,
        memory_cost=params.memory_kib,
        parallelism=params.parallelism,
        hash_len=VAULT_KEY_BYTES,
        type=Type.ID,
    )


def _key_wrap_aad(key_id: str, key_type: str, params: Argon2idParams) -> bytes:
    return _stable_json_bytes(
        {
            "format": f"{VAULT_FORMAT}-key-wrap",
            "version": SUPPORTED_VAULT_VERSION,
            "id": key_id,
            "type": key_type,
            "kdf": params.to_dict(),
        }
    )


def derive_recovery_wrapping_key(
    recovery_key: bytes,
    params: RecoveryKeyWrapHKDFParams,
) -> bytes:
    if not isinstance(recovery_key, bytes) or len(recovery_key) != RECOVERY_KEY_BYTES:
        raise VaultKeyUnwrapError("failed to unwrap vault key")
    return HKDF(
        algorithm=hashes.SHA256(),
        length=VAULT_KEY_BYTES,
        salt=params.salt,
        info=RECOVERY_WRAP_KDF_INFO.encode("utf-8"),
    ).derive(recovery_key)


def recovery_wrap_v2_aad(
    vault_id: str,
    wrapped_key: RecoveryKeyWrapV2,
) -> bytes:
    if not isinstance(vault_id, str) or not vault_id or not vault_id.isascii():
        raise VaultFormatError("vault_id must be non-empty ASCII text")
    return _stable_json_bytes(
        {
            "format": "atlas-vault-key-wrap",
            "version": wrapped_key.wrap_version,
            "vault_id": vault_id,
            "id": wrapped_key.id,
            "type": wrapped_key.type,
            "key_wrap_aead": "AES-256-GCM",
            "kdf": wrapped_key.kdf.to_dict(),
        }
    )


def wrap_vault_key_with_recovery(
    vault_key: bytes,
    recovery_key: bytes,
    *,
    vault_id: str,
    salt: bytes | None = None,
    nonce: bytes | None = None,
) -> RecoveryKeyWrapV2:
    _require_vault_key(vault_key)
    if not isinstance(recovery_key, bytes) or len(recovery_key) != RECOVERY_KEY_BYTES:
        raise VaultFormatError("recovery key must be 256 bits")
    params = RecoveryKeyWrapHKDFParams(
        salt=salt if salt is not None else secrets.token_bytes(32)
    )
    wrap_nonce = _nonce(nonce)
    shell = RecoveryKeyWrapV2(
        kdf=params,
        nonce=wrap_nonce,
        ciphertext=b"\0" * 48,
    )
    ciphertext = AESGCM(
        derive_recovery_wrapping_key(recovery_key, params)
    ).encrypt(
        wrap_nonce,
        vault_key,
        recovery_wrap_v2_aad(vault_id, shell),
    )
    return RecoveryKeyWrapV2(
        id=RECOVERY_WRAP_ID,
        kdf=params,
        nonce=wrap_nonce,
        ciphertext=ciphertext,
    )


def unwrap_vault_key_with_recovery(
    wrapped_key: RecoveryKeyWrapV2,
    recovery_key: bytes,
    *,
    vault_id: str,
) -> bytes:
    try:
        wrapping_key = derive_recovery_wrapping_key(recovery_key, wrapped_key.kdf)
        vault_key = AESGCM(wrapping_key).decrypt(
            wrapped_key.nonce,
            wrapped_key.ciphertext,
            recovery_wrap_v2_aad(vault_id, wrapped_key),
        )
    except (InvalidTag, VaultFormatError, ValueError) as exc:
        raise VaultKeyUnwrapError("failed to unwrap vault key") from exc
    if len(vault_key) != VAULT_KEY_BYTES:
        raise VaultKeyUnwrapError("failed to unwrap vault key")
    return vault_key


def wrap_vault_key(
    vault_key: bytes,
    passphrase: str,
    *,
    params: Argon2idParams | None = None,
    salt: bytes | None = None,
    nonce: bytes | None = None,
    key_id: str = "primary-passphrase",
) -> WrappedKey:
    _require_vault_key(vault_key)
    if params is None:
        params = Argon2idParams(salt=salt or secrets.token_bytes(16))
    elif salt is not None:
        params = params.with_salt(salt)
    wrapping_key = derive_wrapping_key_argon2id(passphrase, params)
    wrap_nonce = _nonce(nonce)
    ciphertext = AESGCM(wrapping_key).encrypt(
        wrap_nonce,
        vault_key,
        _key_wrap_aad(key_id, "passphrase", params),
    )
    return WrappedKey(
        id=key_id,
        type="passphrase",
        kdf=params,
        nonce=wrap_nonce,
        ciphertext=ciphertext,
    )


def unwrap_vault_key(wrapped_key: WrappedKey, passphrase: str) -> bytes:
    try:
        wrapping_key = derive_wrapping_key_argon2id(passphrase, wrapped_key.kdf)
        vault_key = AESGCM(wrapping_key).decrypt(
            wrapped_key.nonce,
            wrapped_key.ciphertext,
            _key_wrap_aad(wrapped_key.id, wrapped_key.type, wrapped_key.kdf),
        )
    except (InvalidTag, ValueError) as exc:
        raise VaultKeyUnwrapError("failed to unwrap vault key") from exc
    if len(vault_key) != VAULT_KEY_BYTES:
        raise VaultKeyUnwrapError("failed to unwrap vault key")
    return vault_key


def create_vault_metadata(
    vault_key: bytes,
    passphrase: str,
    *,
    vault_id: str | None = None,
    key_id: str = "primary-passphrase",
    params: Argon2idParams | None = None,
    salt: bytes | None = None,
    nonce: bytes | None = None,
) -> VaultMetadata:
    wrapped_key = wrap_vault_key(
        vault_key,
        passphrase,
        params=params,
        salt=salt,
        nonce=nonce,
        key_id=key_id,
    )
    return VaultMetadata.new(vault_id=vault_id, key_wraps=(wrapped_key,))


def derive_record_key(vault_key: bytes, vault_id: str, record_id: str) -> bytes:
    _require_vault_key(vault_key)
    if not vault_id or not record_id:
        raise VaultFormatError("vault_id and record_id are required")
    return HKDF(
        algorithm=hashes.SHA256(),
        length=VAULT_KEY_BYTES,
        salt=f"{VAULT_FORMAT}:v1:{vault_id}".encode("utf-8"),
        info=f"record:{record_id}".encode("utf-8"),
    ).derive(vault_key)


def _record_aad_dict(
    vault_metadata: VaultMetadata,
    *,
    record_id: str,
    schema_version: int,
    revision: str,
    parent_revision: str | None,
    deleted: bool,
    key_id: str,
) -> dict[str, Any]:
    return {
        "vault_format": vault_metadata.format,
        "vault_version": vault_metadata.version,
        "vault_id": vault_metadata.vault_id,
        "record_id": record_id,
        "record_schema_version": schema_version,
        "revision": revision,
        "parent_revision": parent_revision,
        "deleted": deleted,
        "key_id": key_id,
    }


def _record_aad(vault_metadata: VaultMetadata, encrypted_record: EncryptedRecord) -> bytes:
    return _stable_json_bytes(
        _record_aad_dict(
            vault_metadata,
            record_id=encrypted_record.id,
            schema_version=encrypted_record.schema_version,
            revision=encrypted_record.revision,
            parent_revision=encrypted_record.parent_revision,
            deleted=encrypted_record.deleted,
            key_id=encrypted_record.key_id,
        )
    )


def encrypt_record_payload(
    vault_key: bytes,
    vault_metadata: VaultMetadata,
    plaintext_record: PlaintextRecord,
    *,
    record_id: str | None = None,
    revision: str | None = None,
    parent_revision: str | None = None,
    deleted: bool = False,
    key_id: str = "primary-passphrase",
    nonce: bytes | None = None,
) -> EncryptedRecord:
    record_id = record_id or str(uuid.uuid4())
    revision = revision or str(uuid.uuid4())
    record_nonce = _nonce(nonce)
    record_key = derive_record_key(vault_key, vault_metadata.vault_id, record_id)
    aad = _stable_json_bytes(
        _record_aad_dict(
            vault_metadata,
            record_id=record_id,
            schema_version=SUPPORTED_RECORD_SCHEMA_VERSION,
            revision=revision,
            parent_revision=parent_revision,
            deleted=deleted,
            key_id=key_id,
        )
    )
    ciphertext = AESGCM(record_key).encrypt(
        record_nonce,
        _stable_json_bytes(plaintext_record.to_dict()),
        aad,
    )
    return EncryptedRecord(
        id=record_id,
        schema_version=SUPPORTED_RECORD_SCHEMA_VERSION,
        revision=revision,
        parent_revision=parent_revision,
        deleted=deleted,
        key_id=key_id,
        nonce=record_nonce,
        ciphertext=ciphertext,
    )


def decrypt_record_payload(
    vault_key: bytes,
    vault_metadata: VaultMetadata,
    encrypted_record: EncryptedRecord,
) -> PlaintextRecord:
    try:
        record_key = derive_record_key(vault_key, vault_metadata.vault_id, encrypted_record.id)
        plaintext = AESGCM(record_key).decrypt(
            encrypted_record.nonce,
            encrypted_record.ciphertext,
            _record_aad(vault_metadata, encrypted_record),
        )
    except (InvalidTag, ValueError) as exc:
        raise VaultAuthenticationError("record authentication failed") from exc
    try:
        return PlaintextRecord.from_json_bytes(plaintext)
    except RecordFormatError:
        raise
