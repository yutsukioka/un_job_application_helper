# Phase 2D-4 Bidirectional Crypto Vectors

Status: Phase 2D-4 feasibility and test-vector work. This phase remains
test-only and does not add production Swift vault runtime behavior.

## Purpose

Phase 2D-3 proved Swift CryptoKit can derive AtlasVault record keys and decrypt
Python-generated AES-256-GCM records. Phase 2D-4 adds the reverse check: Swift
must seal the exact canonical plaintext bytes with the same key, nonce, and AAD
and produce the same `ciphertext || 16-byte GCM tag` bytes as Python
`vaultsync`.

## Scope

Included:

- a `plaintext_json_b64` field in the fake crypto vector;
- Python tests proving that field is the stable JSON bytes used by
  `vaultsync`;
- Swift tests proving CryptoKit `AES.GCM.seal` reproduces the Python ciphertext
  and tag exactly.

Deferred:

- production Swift encryption helpers;
- Keychain;
- vault unlock UI;
- runtime vault file I/O;
- migration execution;
- cloud sync;
- device onboarding;
- key rotation.

## Compatibility Contract

The bidirectional vector binds four deterministic inputs:

- record key derived with HKDF-SHA256;
- 12-byte nonce from the encrypted record envelope;
- AAD reconstructed from vault and record metadata;
- canonical plaintext JSON bytes from `plaintext_json_b64`.

Python and Swift must both treat encrypted-record `ciphertext` as
`base64(ciphertext || tag)`, with the nonce stored separately. Swift splits this
layout when opening a record and concatenates `sealedBox.ciphertext` and
`sealedBox.tag` when sealing.

## Safety Boundary

The vector key, nonce, vault ID, record ID, revision, plaintext bytes, and
ciphertext are fake and test-only. The vector is not a production vault file and
not a `.atlasvault` export. No Keychain access, runtime vault persistence, or
application state hydration is introduced in this phase.
