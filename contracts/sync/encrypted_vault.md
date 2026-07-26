# AtlasVault v1 Encrypted Vault Contract

Status: Phase 2D-61 contract. The Python reference package in
`packages/vaultsync` implements the cross-platform cryptographic format.
The Apple implementation generates and verifies recovery material and
encrypted exports, but production recovery unlock and import remain deferred.
This contract does not define cloud sync behavior, authentication, or data
migration.

## Purpose

AtlasVault v1 is a provider-neutral encrypted vault format for small
user-saved text data in UN Job Application Helper. It is intended for future
multi-device sync across Apple, Android, Windows, manual export/import, and
custom or cloud blob backends.

AtlasVault v1 is zero-knowledge by design:

- Clients may create and decrypt user records.
- Cloud providers, the FastAPI service, and future backends may store and route
  encrypted blobs.
- Cloud providers, the FastAPI service, and future backends must never receive
  plaintext user-saved text, raw vault keys, passphrases, recovery keys, or
  decrypted payloads.

## Scope

AtlasVault v1 covers small user-saved text records, such as future encrypted
forms of saved searches, saved job notes, and lightweight application tracker
state.

AtlasVault v1 does not cover:

- the public `jobagg` job database;
- generated application documents;
- local logs, histories, caches, or raw databases under `private/`;
- cloud authentication tokens;
- platform keychain or keystore implementation;
- migration of existing local plaintext saved searches or tracker records.

The public job database and the private user vault must remain separate.

## Current Local API Is Not A Sync API

The existing local service exposes plaintext endpoints for local app use:

- `GET /api/saved-searches`
- `POST /api/saved-searches`
- `GET /api/tracker`
- `POST /api/tracker`
- `POST /api/tracker/jobs/{job_key}`
- `DELETE /api/tracker/{record_id}`

These endpoints are local-only, unauthenticated, and designed for the local
FastAPI service, which defaults to `127.0.0.1:8765`.

They are not acceptable as cloud sync endpoints. They must not be exposed to the
internet or reused as multi-device sync endpoints without separate
authentication, authorization, transport hardening, and a redesign that accepts
only encrypted vault blobs.

## Security Model

AtlasVault v1 uses a two-layer key model:

1. Generate a random 256-bit vault key with secure randomness.
2. Use the vault key to encrypt record payloads.
3. Derive a wrapping key using the profile appropriate to the input:
   Argon2id for a passphrase-wrap v1 input, or HKDF-SHA256 for a generated
   256-bit recovery-key-wrap v2 input.
4. Encrypt, or wrap, the vault key with the derived wrapping key.
5. Serialize only wrapped vault keys, encrypted record blobs, and minimal
   plaintext sync metadata.

Rules:

- The passphrase or recovery key itself is never stored.
- Raw vault keys are never serialized into vault metadata or record files.
- Future devices may store a local copy of the unwrapped vault key in platform
  secure storage.
- Future Apple clients should use Apple Keychain.
- Future Android clients should use Android Keystore.
- Future Windows clients should use DPAPI or Credential Manager.
- Cloud providers and custom backends store only encrypted metadata and
  encrypted record blobs.
- Authentication and account identity are separate from encryption keys.

## Cryptographic Suite

The top-level AtlasVault v1 suite is:

- record encryption: AES-256-GCM;
- key wrapping AEAD: AES-256-GCM;
- passphrase-wrap v1 KDF: Argon2id;
- generated recovery-key-wrap v2 KDF: HKDF-SHA256;
- subkey derivation: HKDF-SHA256;
- passphrase salts: random 128-bit or larger salts;
- AES-GCM nonces: random 96-bit nonces generated securely and never reused with
  the same key;
- serialization: JSON with explicit, stable schema versions.

Record encryption should derive a record subkey from the vault key with
HKDF-SHA256 using the vault ID and record ID as context. This keeps record keys
domain-separated while retaining one vault root key.

HKDF-SHA256 recovery wrapping is only for a generated 256-bit recovery key. It
must not be reused as a password or passphrase KDF. XChaCha20-Poly1305 may be
considered only as a future crypto suite if libsodium is adopted consistently
across all supported clients.

## Vault Metadata

Vault metadata is JSON. It is safe for a sync provider to store because the raw
vault key and passphrase are absent.

Example:

```json
{
  "format": "atlas-vault",
  "version": 1,
  "vault_id": "random-uuid",
  "crypto": {
    "record_aead": "AES-256-GCM",
    "kdf": "Argon2id",
    "subkey_kdf": "HKDF-SHA256",
    "key_wrap_aead": "AES-256-GCM"
  },
  "key_wraps": [
    {
      "id": "primary-passphrase",
      "type": "passphrase",
      "kdf": {
        "algorithm": "Argon2id",
        "salt": "base64",
        "memory_kib": 65536,
        "iterations": 3,
        "parallelism": 4
      },
      "nonce": "base64",
      "ciphertext": "base64"
    }
  ]
}
```

`key_wraps[].ciphertext` contains only the AES-GCM encrypted vault key. It must
not contain plaintext records, the passphrase, the recovery key, or the raw vault
key.

### Passphrase Wrap V1 Compatibility

The existing passphrase wrap remains byte-compatible and is not reinterpreted:

- `type` is `passphrase`;
- no `wrap_version` field is present;
- KDF is Argon2id;
- AEAD is AES-256-GCM;
- nonce is 12 bytes;
- ciphertext is 32 encrypted vault-key bytes plus the 16-byte GCM tag;
- its historical associated data does not bind `vault_id`.

The v1 limitation is preserved for compatibility. It is not the profile used
for newly generated recovery material.

## Recovery-Key Text V1

A recovery key begins as exactly 32 bytes from a cryptographically secure
random generator. Its transcription checksum is:

```text
SHA256(
  UTF8("atlasvault-recovery-key-v1:")
  || raw_recovery_key_32_bytes
)[0:5]
```

The five-byte checksum detects transcription errors; it is not an
authentication tag. The 32-byte key and checksum are concatenated and encoded
with RFC 4648 Base32 using uppercase `A-Z2-7` and no padding. The result is
exactly 60 symbols, rendered as:

```text
AVRK1-XXXX-XXXX-XXXX-XXXX-XXXX-XXXX-XXXX-XXXX-XXXX-XXXX-XXXX-XXXX-XXXX-XXXX-XXXX
```

Parsers may accept lowercase ASCII and ASCII spaces or hyphens between groups,
with leading or trailing ASCII whitespace. They must reject unsupported
prefixes, padding, Unicode look-alikes, ambiguous digits, malformed lengths,
extra data, and checksum mismatches. Checksum comparison must be constant time.
Implementations must not autocorrect `0`, `1`, or `8`.

## Recovery-Key Wrap V2

Recovery wrap v2 is a versioned object inside top-level `atlas-vault` version
1 metadata:

```json
{
  "id": "primary-recovery-v2",
  "type": "recovery_key",
  "wrap_version": 2,
  "kdf": {
    "algorithm": "HKDF-SHA256",
    "salt": "canonical-base64-32-bytes",
    "info": "atlas-vault-recovery-wrap-v2"
  },
  "nonce": "canonical-base64-12-bytes",
  "ciphertext": "canonical-base64-48-bytes"
}
```

All keys are required and additional keys are invalid. Base64 must be
canonical. The wrapping key is derived as:

```text
HKDF-SHA256(
  IKM = raw recovery key (32 bytes),
  salt = wrap salt (32 bytes),
  info = UTF8("atlas-vault-recovery-wrap-v2"),
  L = 32 bytes
)
```

The 32-byte vault key is sealed with AES-256-GCM using the wrap's random
12-byte nonce. The canonical, sorted, compact UTF-8 JSON associated data is:

```json
{
  "format": "atlas-vault-key-wrap",
  "version": 2,
  "vault_id": "<validated-vault-id>",
  "id": "primary-recovery-v2",
  "type": "recovery_key",
  "key_wrap_aead": "AES-256-GCM",
  "kdf": {
    "algorithm": "HKDF-SHA256",
    "salt": "<canonical-base64>",
    "info": "atlas-vault-recovery-wrap-v2"
  }
}
```

This AAD binds the owning vault, wrap version, fixed wrap identity and type,
AEAD, KDF algorithm, salt, and info. Modifying any bound value, the nonce, or
the ciphertext must make unwrap fail. Raw recovery and vault keys are never
serialized.

## Encrypted Records

Each record is encrypted independently. The sync layer may inspect only the
minimal plaintext metadata required for conflict handling.

Example:

```json
{
  "id": "random-uuid",
  "schema_version": 1,
  "revision": "random-or-derived-revision-id",
  "parent_revision": null,
  "deleted": false,
  "key_id": "primary-passphrase",
  "nonce": "base64",
  "ciphertext": "base64"
}
```

The plaintext payload before encryption is small JSON:

```json
{
  "type": "saved_text",
  "payload_schema": 1,
  "payload": {
    "text": "small user-saved text"
  },
  "client_created_at": "ISO-8601 UTC timestamp",
  "client_updated_at": "ISO-8601 UTC timestamp"
}
```

For Phase 1, `saved_text` is the only reference plaintext record type.

## Authenticated Metadata

AES-GCM associated data must bind important plaintext metadata to the
ciphertext. At minimum, record encryption must authenticate:

- vault format;
- vault version;
- vault ID;
- record ID;
- record schema version;
- revision;
- parent revision;
- deleted flag;
- key ID.

Associated data must use stable JSON encoding:

- UTF-8;
- sorted keys;
- compact separators;
- deterministic field names.

Changing any authenticated metadata must cause decryption failure.

## Conflict Handling

AtlasVault v1 uses record-level conflict metadata:

- `id` identifies the logical record.
- `revision` identifies the current encrypted version.
- `parent_revision` links an update to the version it replaced.
- `deleted` marks tombstones.

Future sync clients should use these fields to detect concurrent edits and
tombstones without decrypting data on the server. The server may store multiple
conflicting ciphertext revisions for a client to resolve locally after
decryption.

## Manual Encrypted Export And Import

Manual encrypted export uses this exact envelope:

```json
{
  "format": "atlasvault-export",
  "version": 1,
  "export_id": "<lowercase-uuid>",
  "created_at": "<utc-iso-8601-seconds>",
  "vault_metadata": {},
  "records": []
}
```

The envelope contains complete validated vault metadata, encrypted record
blobs, and encrypted tombstones. It excludes local store IDs and paths,
selected-vault registration, Keychain state, raw keys, recovery text,
passphrases, and plaintext payloads. Encoding is UTF-8 JSON with sorted keys,
compact separators, and stable field names. Record order is preserved.

Before an Apple client offers an export for saving, it must strictly decode its
own canonical bytes, validate metadata and vault identity, unwrap with the
entered recovery key, compare the recovered key to the local vault key in
constant time, and hydrate all encrypted records into temporary in-memory
state. Hydrated private state must be discarded without publication. Any
corrupt encrypted record blocks export readiness.

An export bundle must not contain plaintext saved searches, plaintext tracker
records, raw vault keys, passphrases, recovery keys, or decrypted payloads.

Future import happens locally:

1. The user provides the passphrase or recovery key.
2. The client unwraps the vault key locally.
3. The client decrypts and merges records locally.
4. The client writes local plaintext only to approved local storage.

Phase 2D-61 does not implement import, clean-install restoration, or ordinary
production recovery-key unlock. The production Apple unlock capability remains
local-key-only. Those recovery-consumption behaviors require a separate
reviewed phase.

## Future Device Onboarding And Removal

Future device-to-device onboarding may transfer access by adding a new
device-specific key wrap or by using an encrypted pairing flow. The onboarding
service must not see plaintext records or raw vault keys.

When a device is removed, a future phase should support key rotation:

- generate a new vault key;
- re-encrypt records or future records under the new key policy;
- remove the old device key wrap;
- keep enough revision metadata for local conflict resolution.

Phase 1 does not implement onboarding, device removal, or key rotation.

## Prohibited Designs

Do not use:

- `SHA256(password)` as an encryption key;
- AES-CBC without authentication;
- unauthenticated encryption;
- AES-GCM nonce reuse with the same key;
- hardcoded encryption keys;
- plaintext cloud sync;
- sync providers that store plaintext user text;
- backends that store raw vault keys, passphrases, or recovery keys;
- logs containing plaintext, keys, passphrases, or decrypted vault contents;
- the current plaintext `/api/saved-searches` or `/api/tracker` endpoints as
  internet-facing sync endpoints.
