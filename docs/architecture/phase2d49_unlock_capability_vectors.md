# Phase 2D-49 AtlasVault Unlock Capability And Key-Wrap Vectors

## Purpose

Phase 2D-49 adds a runtime-neutral capability boundary, strict Swift models for
AtlasVault v1 wrapped-key metadata, and shared fake Python/Swift compatibility
vectors. It does not enable passphrase or recovery unlock.

## Scope

This phase includes:

- non-sensitive unlock method and capability types;
- a context-aware `AtlasVaultKeyUnwrapping` protocol;
- strict decode-only v1 wrapped-key metadata models;
- one deterministic fake passphrase-wrap vector;
- Python reference validation and Swift parsing tests.

It adds no Swift Argon2id implementation, cryptographic dependency, runtime
activation call site, Keychain operation, filesystem operation, SwiftUI,
application entry-point integration, migration, or cloud behavior.

## Capability Boundary

`AtlasVaultUnlockCapabilities.currentProduction` advertises only the reviewed
local-key runtime path. Passphrase and recovery remain unavailable when their
provider is absent. `AtlasVaultUnlockMethod` contains no supplied-raw-key
production method.

Capability construction does not call a provider and does not probe whether a
credential or vault exists. A future composition may mark a wrapped-key method
available only by supplying a reviewed provider for that method.

## Key-Unwrapping Protocol

`AtlasVaultKeyUnwrapping` receives:

- an `AtlasVaultKeyUnwrapContext`;
- the validated non-semantic vault ID;
- the exact selected `AtlasVaultWrappedKeyEnvelope`;
- an existing one-shot `AtlasVaultSecretBuffer`.

The provider must not infer a current vault from global mutable state.
`validatedVaultKey(context:secret:)` maps unknown provider errors to a fixed
non-sensitive error and rejects any result that is not exactly 32 bytes.

No production provider is included in this phase.

## Swift Wrapped-Key Models

The decode-only models are:

- `AtlasVaultArgon2idParameters`;
- `AtlasVaultKeyWrapCryptoSuite`;
- `AtlasVaultWrappedKeyEnvelope`;
- `AtlasVaultWrappedKeyMetadata`.

They validate:

- `atlas-vault` format version 1;
- the v1 AES-256-GCM, Argon2id, and HKDF-SHA256 suite;
- passphrase key-wrap type;
- non-empty key-wrap ID;
- salt of at least 16 bytes;
- positive Argon2id parameters;
- 12-byte nonce;
- 48-byte encrypted 32-byte key plus GCM tag.

The models do not conform to `Encodable`, contain no raw vault key or secret
input, and use fixed redacted descriptions.

## Shared Fake Vector

`contracts/sync/test_vectors/atlasvault_key_wrap_vectors_v1.json` is:

- fake;
- test-only;
- not real user data;
- not a production vault;
- not a production key.

It uses deterministic fake bytes and low-cost test-only Argon2id parameters.
Those values must never be reused in production.

Python validates and recomputes the Argon2id/AES-GCM wrap, confirms correct
unwrap, rejects the wrong fake passphrase, and verifies metadata excludes the
fake passphrase and raw key. Swift validates the outer vector contract and
decodes the same metadata with production models; it does not perform
Argon2id.

## AtlasVault v1 Binding Limit

The vector faithfully records v1 behavior: key-wrap associated data
authenticates format, version, key-wrap ID, type, and KDF parameters, but not
`vault_id`. The explicit vault ID in `AtlasVaultKeyUnwrapContext` is routing
context, not authenticated provenance.

This phase does not invent v2 metadata, silently reinterpret v1 wraps, or claim
that an empty vault confirms the unwrapped key. Production passphrase and
recovery capability remain unavailable pending a separately reviewed
versioned vault-binding or key-confirmation design.

## Error And Privacy Behavior

Malformed base64, unsupported format/version/type/KDF/AEAD, invalid salt or
nonce, invalid ciphertext layout, invalid context, provider absence, provider
failure, and invalid key length fail with non-sensitive categories. Errors and
debug descriptions never include a passphrase, recovery key, raw vault key,
wrapped ciphertext, vault ID, or private record value.

## Tests

Swift tests cover:

- production capability classification;
- provider-presence classification without provider invocation;
- absence of a raw-key production method;
- strict v1 metadata decoding;
- unsupported and malformed metadata;
- 32-byte provider-result validation;
- provider-error redaction;
- explicit context and redacted descriptions;
- decode-only and `Sendable` model properties;
- v1 AAD exclusion of vault ID;
- no fake secret/raw-key value in wrapped metadata.

Python tests cover:

- vector markers and schema;
- deterministic reference recomputation;
- correct and wrong passphrase behavior;
- metadata leakage checks;
- explicit v1 AAD limitation;
- malformed base64, unsupported version/KDF/AEAD/type, invalid salt/nonce, and
  invalid fake key length.

## Deferred

- Production Swift Argon2id provider.
- Recovery-key format and provider.
- Authenticated vault binding or universal key confirmation.
- Runtime coordinator/facade integration.
- Explicit unlock presentation and SwiftUI.
- LocalAuthentication.
- Migration and plaintext cleanup.
- Cloud sync, recovery UX, onboarding, and key rotation.

## Recommended Phase 2D-50

Phase 2D-50 should add a runtime-neutral unlock presentation controller using
injected fake providers and existing request/runtime seams. It must keep
secret buffers one-shot, publish only non-sensitive state, and leave SwiftUI
and production provider enablement deferred.
