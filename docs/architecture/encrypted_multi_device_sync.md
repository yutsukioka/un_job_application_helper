# Encrypted Multi-Device Sync Architecture

Status: Phase 0 and Phase 1 foundation only. This document describes the target
architecture and the current boundary. It does not claim cloud sync, platform
secure storage, account management, or migration is implemented.

## Current State

The Apple app has real SwiftUI code under `apps/apple/Sources/AtlasUI/`.
`AtlasLocalCache` stores `atlas-local-snapshot.json` under Application Support.
`AtlasLocalSnapshot` includes `savedSearches` and `savedJobs`, so user-specific
saved state can currently exist locally in plaintext.

The local FastAPI service in `services/job-api` exposes plaintext local
endpoints for saved searches and tracker records:

- `GET /api/saved-searches`
- `POST /api/saved-searches`
- `GET /api/tracker`
- `POST /api/tracker`
- `POST /api/tracker/jobs/{job_key}`
- `DELETE /api/tracker/{record_id}`

The service has no authentication and is currently designed as a local service,
defaulting to `127.0.0.1:8765`.

These local plaintext paths are not cloud sync architecture. They must not be
used as internet-facing sync endpoints.

## Target Architecture

Atlas multi-device sync should use a zero-knowledge vault model:

```mermaid
flowchart LR
  subgraph Client["Client Device"]
    UI["Atlas UI"]
    LocalPlain["Local plaintext app state"]
    Vault["AtlasVault crypto"]
    SecureStore["Future platform secure storage"]
  end
  subgraph Sync["Future sync provider or backend"]
    Auth["Authentication and account identity"]
    BlobStore["Ciphertext blob storage"]
  end
  UI --> LocalPlain
  LocalPlain --> Vault
  Vault --> BlobStore
  SecureStore --> Vault
  Auth --> BlobStore
```

The client owns encryption and decryption. The provider or backend owns only
authentication, routing, storage, cursors, and conflict metadata.

## Component Boundaries

### Local Plaintext API

The existing local API may continue to serve the local app during the transition.
It returns plaintext saved searches and tracker records and is appropriate only
for local loopback use.

It is not acceptable as a cloud sync API because it exposes user-saved text to
the service boundary and lacks authentication.

### Future Ciphertext-Only Sync API

The future sync API may accept only encrypted vault metadata, encrypted record
blobs, record IDs, revisions, parent revisions, tombstone markers, key IDs,
logical clocks, timestamps, and opaque device IDs.

It must never accept plaintext saved searches, saved jobs, application notes,
generated documents, personal histories, raw vault keys, passphrases, recovery
keys, or decrypted payloads.

### Authentication And Account Identity

Authentication identifies the account or device allowed to store and retrieve
opaque blobs. It is separate from encryption.

Authentication tokens, account IDs, and device IDs must never be treated as vault
keys. Account services must never receive passphrases, recovery keys, or raw
vault keys.

### Encryption Keys

AtlasVault v1 generates a random 256-bit vault key. The vault key encrypts
record payloads. A passphrase or recovery key wraps the vault key. The passphrase
or recovery key itself is never stored, and raw vault keys are never serialized
into vault files.

Future devices may store a local unwrapped vault key copy in platform secure
storage after the user unlocks the vault.

### Local Secure Storage

Platform secure storage is a future phase:

- Apple Keychain for Apple platforms;
- Android Keystore for Android;
- Windows DPAPI or Credential Manager for Windows.

Phase 1 does not implement any of these. The Python reference package only
defines and tests the portable cryptographic format.

### Cloud Blob Storage

Cloud storage is future work. CloudKit, object storage, a custom FastAPI
backend, or another provider may store encrypted blobs only. The provider must
not store plaintext user text or raw secrets.

### Manual Encrypted Export And Import

Manual export/import is a required non-cloud sync path. Export bundles should
contain encrypted vault metadata and encrypted record blobs. A user imports by
providing a passphrase or recovery key locally; decryption and merge happen on
the client.

## Data Separation

The `jobagg` public job database remains separate from private user vault data.
Public job records, taxonomies, and search indexes are not vault records.

The `private/` directory remains the boundary for generated documents, local
histories, databases, logs, caches, keys, and personal data. Sync foundation
work must not move private user data into tracked source files.

## Record Model

AtlasVault v1 uses record-level encrypted objects:

- each record has an opaque `id`;
- each update creates a `revision`;
- `parent_revision` links updates for conflict detection;
- `deleted` marks tombstones;
- `key_id` selects a wrapped-key context without revealing plaintext;
- `nonce` and `ciphertext` carry AES-GCM encryption output.

The server can compare revisions and tombstones without decrypting records.
Clients resolve semantic conflicts locally after decrypting.

## Cryptographic Decisions

The v1 suite is:

- AES-256-GCM for authenticated record encryption and key wrapping;
- HKDF-SHA256 for record subkeys;
- Argon2id for passphrase-based wrapping-key derivation;
- random salts of at least 128 bits;
- random 96-bit AES-GCM nonces that are never reused with the same key;
- stable JSON schema versions for metadata and records.

XChaCha20-Poly1305 is only a possible future suite if libsodium is adopted
consistently across all clients.

## Lifecycle

### Create A Vault

1. Client generates a 256-bit vault key.
2. Client derives a wrapping key from a passphrase or recovery key with Argon2id.
3. Client wraps the vault key using AES-256-GCM.
4. Client serializes vault metadata containing wrapped-key metadata only.

### Add Or Update A Record

1. Client creates a plaintext `saved_text` payload locally.
2. Client derives a record subkey from the vault key, vault ID, and record ID.
3. Client encrypts the payload with AES-256-GCM.
4. Client authenticates vault and record metadata through AES-GCM associated
   data.
5. Client stores or syncs only the encrypted record object.

### Sync Records

1. Client uploads encrypted metadata and encrypted records.
2. Provider stores opaque blobs and conflict metadata.
3. Client downloads changed encrypted records.
4. Client decrypts locally and merges locally.

### Onboard A Device

Future device-to-device onboarding should add a device-specific key wrap or use
an encrypted pairing flow. A server may broker encrypted messages but must not
receive raw vault keys or plaintext.

### Remove A Device

Future removal should support key rotation:

1. remove or disable the device key wrap;
2. generate a new vault key or new key epoch;
3. re-encrypt records or future writes according to the rotation policy;
4. preserve enough revision metadata for conflict resolution.

Phase 1 does not implement key rotation.

## Implementation Phases

Phase 0 creates contracts and architecture docs.

Phase 1 creates the Python reference package and crypto tests:

- metadata serialization;
- vault key generation;
- passphrase wrapping and unwrapping;
- record encryption and decryption;
- tamper detection;
- version rejection;
- deterministic test-only vectors.

Later phases may add local migrations, platform secure storage, manual export UI,
device onboarding, cloud sync APIs, and key rotation.

## Security Warnings

Do not use:

- `SHA256(password)` as an encryption key;
- AES-CBC without authentication;
- unauthenticated encryption;
- reused AES-GCM nonces with the same key;
- hardcoded keys;
- plaintext cloud sync;
- sync providers that store plaintext user text;
- backends that store raw vault keys, passphrases, or recovery keys;
- logs containing plaintext, keys, passphrases, or decrypted vault contents;
- current plaintext `/api/saved-searches` or `/api/tracker` endpoints as
  internet-facing sync endpoints.
