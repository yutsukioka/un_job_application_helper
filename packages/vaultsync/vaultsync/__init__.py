"""AtlasVault v1 reference implementation.

This package provides the Phase 1 Python reference implementation for encrypted
vault metadata, key wrapping, and record encryption. It does not implement cloud
sync, account authentication, platform key stores, or data migration.
"""

from vaultsync.crypto import (
    create_vault_metadata,
    decrypt_record_payload,
    derive_record_key,
    derive_wrapping_key_argon2id,
    encrypt_record_payload,
    generate_vault_key,
    unwrap_vault_key,
    wrap_vault_key,
)
from vaultsync.format import (
    Argon2idParams,
    UnsupportedVaultVersion,
    VaultAuthenticationError,
    VaultCryptoError,
    VaultCryptoSuite,
    VaultFormatError,
    VaultKeyUnwrapError,
    VaultMetadata,
    WrappedKey,
    deserialize_vault_metadata,
    serialize_vault_metadata,
)
from vaultsync.records import (
    EncryptedRecord,
    PlaintextRecord,
    RecordFormatError,
    UnsupportedRecordVersion,
    deserialize_encrypted_record,
    serialize_encrypted_record,
)

__all__ = [
    "Argon2idParams",
    "EncryptedRecord",
    "PlaintextRecord",
    "RecordFormatError",
    "UnsupportedRecordVersion",
    "UnsupportedVaultVersion",
    "VaultAuthenticationError",
    "VaultCryptoError",
    "VaultCryptoSuite",
    "VaultFormatError",
    "VaultKeyUnwrapError",
    "VaultMetadata",
    "WrappedKey",
    "create_vault_metadata",
    "decrypt_record_payload",
    "derive_record_key",
    "derive_wrapping_key_argon2id",
    "deserialize_encrypted_record",
    "deserialize_vault_metadata",
    "encrypt_record_payload",
    "generate_vault_key",
    "serialize_encrypted_record",
    "serialize_vault_metadata",
    "unwrap_vault_key",
    "wrap_vault_key",
]
