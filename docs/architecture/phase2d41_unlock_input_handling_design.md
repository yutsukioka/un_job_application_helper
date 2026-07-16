# Phase 2D-41 AtlasVault Unlock Input Handling Design

## 1. Purpose

Phase 2D-41 defines how a future user-facing layer may submit passphrases,
recovery material, local-key activation choices, or explicitly supplied test
keys without placing secrets in presentation state. It establishes the
ownership and lifetime rules needed before a test-only coordinator is built.

## 2. Design-Only Scope

This phase adds documentation only. It adds no secret buffer, derivation,
unwrap, activation, prompt, SwiftUI, observable state, Keychain operation,
LocalAuthentication, filesystem access, migration, cloud sync, recovery UX,
onboarding, key rotation, or production-readiness claim.

## 3. Future UI Input Boundary

A future secure-input owner creates one request and transfers it directly to an
injected unlock request coordinator. The coordinator is the only bridge from
transient input to reviewed derivation and runtime activation seams. Views and
presentation adapters never call crypto, Keychain, the activation controller,
or the runtime composition graph directly.

Suggested design-only concepts are `AtlasVaultUnlockInput`,
`AtlasVaultUnlockRequest`, and `AtlasVaultSecretBuffering`. Final names and
visibility remain subject to Phase 2D-42 implementation review.

## 4. Passphrase Input

Passphrase input is transferred as a mutable byte buffer containing the exact
UTF-8 bytes entered by the user. The request must not normalize, trim, case
fold, interpolate, or convert those bytes back to `String`. Empty-input policy
is validated before expensive derivation and reported only as a non-sensitive
input category.

The passphrase buffer is consumed by an injected key-unwrapping seam. It never
enters `AtlasVaultRuntimeActivationRequest`; only a successfully unwrapped
32-byte vault key may cross that existing raw-key boundary.

## 5. Recovery-Key Input

Recovery material follows the same transient buffer ownership and cleanup
rules as a passphrase. A dedicated injected parser may validate a reviewed
encoding and produce canonical bytes, but it must not retain the original or
canonical form, include either in errors, or silently treat malformed recovery
material as a passphrase.

Recovery format, account policy, and user-facing recovery UX remain deferred.

## 6. Local Keychain Activation Option

A local-key request carries only the choice to use the existing
`AtlasVaultKeyStore` path and a non-semantic vault ID. The coordinator delegates
to the facade with no supplied key. It does not query Keychain, infer whether an
item exists, expose `SecItem` status, or fall back to another source after an
explicit source fails.

## 7. Explicitly Supplied Vault-Key Testing Path

Tests may provide a fake 32-byte vault key through a secret buffer and injected
activation spy. This path exists for deterministic boundary tests, not as a
general UI feature. Production composition must not expose a raw-key text
field, fixed key, fixed nonce, archive value, or diagnostic accessor.

## 8. No Secrets In Presentation State

Neither `AtlasVaultPresentationSnapshot` nor any private presentation model may
hold a passphrase, recovery key, raw vault key, secret buffer, wrapped key, KDF
input, or request object. Presentation state may expose only reviewed,
non-sensitive progress or failure categories.

## 9. No Secrets In Observable State

A future observable adapter may receive an action indicating that submission
occurred, but it must not own the input bytes. Secret-bearing bindings remain
inside the shortest-lived secure-input component and are cleared immediately
when ownership transfers or submission is cancelled.

## 10. No Secrets In Public Runtime Status

`AtlasVaultRuntimeStatus`, activation status, and public error categories remain
fixed and non-sensitive. They must not reveal the chosen key source, whether a
local item exists, KDF parameters, retry count, secret length, parsing detail,
or which credential bytes failed.

## 11. Secret Ownership

At any point, exactly one logical owner controls a secret buffer: first the
input component, then the request/coordinator, then the delegated derivation or
activation operation. APIs use consuming transfer semantics by policy even
where current Swift cannot enforce noncopyability for every storage type.

No owner may retain an alias after transfer. Tests must use spies that report
calls and clearing state without exposing captured secret bytes.

## 12. Secret Transfer Into Runtime

Passphrase and recovery inputs are first converted by an injected unwrap seam
into a provisional 32-byte vault key. The coordinator passes that key once to
`AtlasVaultRuntimeActivationRequest` and releases its own reference when
dispatch completes or is cancelled. Local-key activation passes `nil` so the
activation controller uses its existing protocol-backed key source.

There is no automatic fallback from an explicit passphrase, recovery key, or
supplied-key attempt to the local-key path. This preserves user intent and
avoids a key-source oracle.

## 13. Single-Use Request Object

Each request has coordinator-owned lifecycle state: pending, dispatching,
completed, cancelled, or expired. Only pending may transition to dispatching.
Every other dispatch attempt fails before reading the buffer or invoking a
dependency.

A copyable value alone cannot enforce single use. Phase 2D-42 should keep the
authoritative request token and secret reference inside one actor, while any
caller-visible request handle contains no secret and cannot reset lifecycle
state.

## 14. Non-Codable Request Type

Secret input, secret buffers, requests, and request handles must not conform to
`Codable`, `Encodable`, or `Decodable`. No serialization helper, state
restoration hook, pasteboard payload, URL encoding, or persistence adapter may
accept them.

## 15. No Equatable Requirement For Secrets

Secret-bearing types do not conform to `Equatable` or `Hashable`; byte equality
is not needed for coordination and could encourage retention or diagnostics.
Non-sensitive request state and error enums may be `Equatable` for tests.

Tests compare call categories, lifecycle states, byte counts where safe, and
buffer-cleared flags rather than secret contents returned from production APIs.

## 16. No CustomStringConvertible Containing Secrets

No description implementation may interpolate a secret-bearing associated
value, buffer, request payload, KDF input, raw key, or underlying error. A type
that cannot guarantee a fixed redacted description should not adopt
`CustomStringConvertible` or `CustomDebugStringConvertible`.

## 17. Redacted Debug Description

If request diagnostics are required, both descriptions are constant, such as
`AtlasVaultUnlockRequest(input: <redacted>)`. They omit source, vault ID,
length, state token, retry count, timestamps, parsing result, and dependency
errors. Reflection-focused tests should verify fake sentinels are absent.

## 18. Secret Lifetime

Secret bytes exist only from user entry until the delegated operation has
copied or consumed the minimum material it needs. The coordinator holds no
secret in completed, failed, cancelled, or expired state. It never caches a
credential for retry.

## 19. Clearing After Dispatch

Once dispatch obtains the input, the coordinator removes its stored reference
before awaiting an external operation where practical and schedules
best-effort buffer clearing with a `defer`-equivalent cleanup path. Single-use
state is committed before the first suspension point so concurrent callers
cannot obtain the same input.

## 20. Clearing After Success

On successful unwrap, clear the passphrase or recovery buffer before runtime
activation continues. The provisional raw key then follows existing activation
ownership and teardown rules. On successful activation, the coordinator clears
its key reference and retains only a non-sensitive completion state.

## 21. Clearing After Failure

Parsing, derivation, unwrap, validation, and activation failures all converge
on cleanup. Clear input and provisional key buffers, remove stored references,
and return a stable category without underlying error text. No failed secret is
retained for convenience or automatic retry.

## 22. Clearing After Cancellation

Cancellation marks a pending request cancelled before clearing its buffer. For
an in-flight request, cancel the child operation, clear coordinator-owned
material immediately, and require the injected dependency to honor its own
cleanup contract. Late completions cannot reactivate an invalidated request.

Timeout is treated as cancellation with an expired terminal state. It does not
leak whether parsing, derivation, Keychain access, or activation was in flight.

## 23. Swift String Memory Limitations

Swift `String` is immutable, copy-on-write, bridged in some contexts, and not a
securely zeroizable container. A future secure field may transiently produce a
`String`; clearing its binding is necessary but cannot prove all backing copies
were erased. This limitation must be documented rather than represented as a
guaranteed wipe.

## 24. Prefer Mutable Byte Buffers Where Appropriate

After submission, convert directly to a narrowly owned mutable byte buffer and
clear the source binding. `AtlasVaultSecretBuffering` should provide a scoped
byte-access operation and idempotent best-effort `clear`, without a public
bytes getter. Implementations must avoid implicit copies where practical and
document that compiler, allocator, framework, and `Data` copy-on-write behavior
prevent a formal zeroization guarantee.

## 25. Passphrase-To-Key Derivation Boundary

The unlock request coordinator delegates derivation and key unwrap through an
injected protocol. That dependency accepts scoped secret bytes plus validated
wrapped-key metadata and returns either a provisional 32-byte vault key or a
non-sensitive failure. The coordinator itself does not implement Argon2id,
AES-GCM, metadata parsing, or cryptographic policy.

Untrusted KDF parameters must be schema-validated and bounded before allocation
or work begins. Exact upper bounds require a separate cryptographic and device
performance review.

## 26. Existing Python Argon2id Compatibility Considerations

Compatibility must match the existing `vaultsync` contract exactly:

- encode passphrase text as UTF-8 with no implicit normalization;
- use Argon2id and metadata-provided salt, `memory_kib`, iterations, and
  parallelism;
- derive 32 bytes;
- require a salt of at least 16 bytes;
- unwrap with AES-256-GCM and the canonical key-wrap AAD containing format,
  version, wrap ID, wrap type, and KDF metadata;
- map an authentication failure to one generic unwrap failure.

Shared fake cross-language vectors must verify behavior before a Swift crypto
dependency is accepted. Test-only low-cost parameters must never become
production defaults.

## 27. Swift Implementation Dependency Decision Deferred

This design does not select, vendor, or implement a Swift Argon2id library.
Dependency provenance, maintenance, platform support, constant-time behavior,
memory handling, concurrency, denial-of-service limits, licensing, and vector
compatibility require a separate decision. Phase 2D-42 uses injected fake
derivation and activation seams only.

## 28. Recovery-Key Parsing

A future parser must define one canonical alphabet, grouping, checksum, and
version policy before accepting recovery input. It validates syntax in bounded
time, returns a generic invalid-input category, and never echoes a segment or
distinguishes checksum position. Normalization is explicit and covered by fake
vectors; permissive guessing is prohibited.

## 29. Clipboard Considerations

AtlasVault code must not place a passphrase or recovery key on the pasteboard,
read it automatically, retain a pasteboard change token, or attempt to erase a
user-controlled system clipboard. A future UI may permit an explicit user paste
into a secure field, but must warn through product policy that clipboard history
and other devices are outside the app's clearing guarantees.

## 30. Autofill Considerations

Credential autofill and password-manager support require a separate policy.
The initial UI should not advertise secret fields as ordinary usernames,
search text, or reusable form values. No input may be copied into restoration,
suggestion, analytics, or form-history state.

## 31. Secure-Field Considerations

A future secure field should obscure display, minimize binding lifetime, avoid
previewing real input, and clear on submit, cancel, disappearance, lock, and
backgrounding. Visual obscuring is not memory protection and does not change
the coordinator's one-shot transfer rules.

## 32. Accessibility Considerations

Accessibility labels may identify the field purpose and non-sensitive error
category, but accessibility values, announcements, focus restoration, and
debug accessibility trees must not speak or preserve secret text. Product and
assistive-technology testing is required before UI release.

## 33. Screen Capture Considerations

Screen recording, screenshots, mirroring, task-switcher snapshots, and remote
support tools can expose even visually obscured workflows. Future UI design
must evaluate capture notifications and background covers without claiming
complete prevention. No capture handling is implemented in this phase.

## 34. Keyboard Suggestion Considerations

Future secret fields should disable ordinary autocorrection, capitalization,
spell checking, and learned suggestions where platform APIs permit. Third-party
keyboard behavior cannot be fully controlled, so the risk and any managed-device
policy must be documented.

## 35. Backgrounding Behavior

Backgrounding during entry clears the field and cancels an unsubmitted request.
Backgrounding during dispatch cancels or invalidates the request and triggers
the same cleanup as explicit cancellation. Resuming never replays input or
automatically unlocks; actual lifecycle subscription remains deferred.

## 36. Retry Behavior

Each retry creates a fresh request and fresh buffer after prior cleanup. The
coordinator does not retain or prefill failed input, and it does not silently
change key source. UI may describe that another attempt is allowed using a
generic category only.

## 37. Rate Limiting

Rate limiting must balance local brute-force resistance, Argon2id cost, and
denial-of-service risk without persisting secret-derived state. Phase 2D-42 may
test bounded in-memory dispatch and timeout behavior, but durable attempt
counters, lockouts, and recovery policy remain separate design work.

## 38. Error Redaction

Public errors are finite categories such as invalid request, already used,
cancelled, expired, unavailable, or `unlockFailed`. Every source-specific
failure after dispatch, including recovery parsing, passphrase derivation, key
unwrap, local-key loading, and runtime activation, collapses to the same
`unlockFailed` category. Public callers cannot infer which input source was
attempted or which stage rejected it.

Errors contain no secret, source-specific oracle, vault ID, Keychain status,
KDF parameter, wrapped-key metadata, path, payload, record count, timing detail,
or underlying localized description. Internal fake seams may expose invocation
categories to tests, but those details never cross the coordinator's public
result boundary.

## 39. No Logging

The input boundary emits no logs, signposts, task names, assertions, metrics,
crash breadcrumbs, or tracing fields containing request values. If operational
instrumentation is later required, it is limited to reviewed aggregate event
categories and must not distinguish key source or credential outcome.

## 40. No Analytics

Analytics must not receive input, source, length, retry count, vault identity,
success timing, failure subtype, Keychain presence, or private-state result.
Unlock funnels and credential experiments require a separate privacy review.

## 41. No Keychain Write From Input Coordinator

The coordinator never calls `saveVaultKey`, `deleteVaultKey`, `SecItem`, or a
Keychain client. Local-key activation is a read choice delegated through the
existing runtime protocol boundary. Persisting a newly unwrapped key requires
an explicit future consent and storage-policy design.

## 42. No UI Implementation

This phase adds no SwiftUI import, view, observable object, property wrapper,
navigation, secure field, preview, app-host call site, or app-entry wiring. It
does not alter `AtlasSearchViewModel`, `AtlasLocalCache`, or public job-cache state.

## 43. No LocalAuthentication

No biometric prompt, device-owner authentication, access-control flag,
`LAContext`, or Keychain prompt policy is added. LocalAuthentication remains a
separate threat-model and UX decision and must not leak into presentation or
unlock request types.

## 44. Future Tests

Phase 2D-42 should use fake seams and fake sentinels to verify:

- construction invokes no dependency;
- passphrase, recovery, local-key, and supplied-test-key dispatch;
- single-use and concurrent double-dispatch rejection;
- cancellation before and during dispatch, timeout, and late-completion safety;
- cleanup after success, failure, cancellation, and expiration;
- no retained secret buffer or provisional key reference;
- constant redacted request, error, and debug descriptions;
- every parser, derivation, unwrap, local-key, and activation failure maps to
  the same public `unlockFailed` category;
- no secret in presentation or public runtime state;
- no Keychain write, filesystem, network, UI, or public-cache call;
- actor serialization with no automatic source fallback;
- fake data only and no `.atlasvault` artifact.

Crypto compatibility requires later shared vectors once a Swift dependency is
selected; Phase 2D-42 must not simulate production Argon2id correctness.

## 45. Recommended Phase 2D-42 Coordinator

Phase 2D-42 should implement a runtime-neutral actor that owns request tokens,
single-use transitions, cancellation, timeout, and best-effort buffer cleanup.
It should dispatch only to injected fake derivation and activation protocols,
return non-sensitive outcomes, and remain disconnected from SwiftUI,
LocalAuthentication, Keychain writes, filesystem, app launch, migration, cloud
sync, onboarding, recovery UX, and key rotation.
