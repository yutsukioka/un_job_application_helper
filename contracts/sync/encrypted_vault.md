# AtlasVault v1 Encrypted Vault Contract

Status: Phase 0 contract. The Python reference package in `packages/vaultsync`
implements the cryptographic format only. This contract does not define cloud
sync behavior, authentication, platform secure storage, or data migration.

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
3. Derive a wrapping key from a strong passphrase or recovery key.
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

The preferred v1 suite is:

- record encryption: AES-256-GCM;
- key wrapping AEAD: AES-256-GCM;
- passphrase/recovery-key KDF: Argon2id;
- subkey derivation: HKDF-SHA256;
- passphrase salts: random 128-bit or larger salts;
- AES-GCM nonces: random 96-bit nonces generated securely and never reused with
  the same key;
- serialization: JSON with explicit, stable schema versions.

Record encryption should derive a record subkey from the vault key with
HKDF-SHA256 using the vault ID and record ID as context. This keeps record keys
domain-separated while retaining one vault root key.

XChaCha20-Poly1305 may be considered only as a future crypto suite if libsodium
is adopted consistently across all supported clients.

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

Manual export/import is a first-class sync path. An export bundle may contain:

- vault metadata;
- encrypted record blobs;
- tombstones;
- opaque device or export metadata.

An export bundle must not contain plaintext saved searches, plaintext tracker
records, raw vault keys, passphrases, recovery keys, or decrypted payloads.

Import happens locally:

1. The user provides the passphrase or recovery key.
2. The client unwraps the vault key locally.
3. The client decrypts and merges records locally.
4. The client writes local plaintext only to approved local storage.

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
