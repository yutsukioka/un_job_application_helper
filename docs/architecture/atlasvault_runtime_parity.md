# AtlasVault production runtime and private-record parity

## C29 boundary

The intended private-record set is `saved_search`, `saved_job`,
`application_note`, `profile_snippet`, and `draft_metadata`. The machine-readable
matrix is `contracts/sync/atlasvault_private_record_parity_v1.json`. Its
per-family booleans describe validated core capabilities, not completed
production-host composition. The separate `production_runtime_integration`
map is currently false and is the C29 completion gate.
`saved_text` remains a version-1 cryptographic reference fixture; it is not an
intended product record family.

## Production composition

The native Apple package has two separate hosts. `AtlasPreviewApp` remains a
diagnostic screenshot/export shell. `AtlasMacHost` is the production macOS
application entry point. It owns one `AtlasMacAppProcessOwner`, creates the
existing production composition once, forwards normal AppKit lifecycle events,
and drains the retained composition during terminal shutdown. The composition
continues to use Keychain, authenticated encrypted local-store files, and the
existing production runtime. Safe status views disclose no private state.

Flutter Android and Windows retain their existing AtlasVault defaults. Flutter
iOS and macOS now select `AtlasVaultPrivateStateRuntime` by default, backed by
the native Apple Keychain and an atomic Application Support ciphertext store.
The selected-vault marker is also Keychain-backed. Native storage failure is a
fixed fail-closed error; there is no temporary, preferences, JSON plaintext, or
in-memory production fallback. Test fakes remain available only by explicit
constructor injection.

## CRUD semantics

All five record families use the same authenticated envelope and encrypted
record container on Python, Swift, and Dart. Create generates a fresh record
and revision. Update retains the record identifier and key identifier, binds the
previous revision as `parent_revision`, and creates a fresh revision. Delete
creates an authenticated terminal tombstone with an empty encrypted plaintext.
Read/list decrypts and validates the envelope only after secure vault activation.

The public Swift hydrated snapshot and Dart private-record snapshot expose all
five families to product presentation code. The shared P5 patch/snapshot,
deterministic conflict, P6 rollback/fork, and P7 epoch/revocation components can
carry these opaque encrypted records, but the live production hosts do not yet
construct `AtlasVaultEpochVault` or route each local mutation into its signed
outbox. Consequently the hosts also cannot enforce its
`ACTIVATION_PENDING`, `CATCH_UP_PENDING`, `RECOVERY_PENDING`, and
`CLEANUP_PENDING` admission states. Local encrypted CRUD is not evidence of
that production integration, so C29 remains incomplete until the join is
designed and wired without inventing account, device, registry, epoch, signing,
or accepted-history context.

Full device-management, pairing, recovery, conflict, and revocation UX remains
assigned to C30-C31. Real-device and full VM validation remains assigned to C32.

## Security limits retained

This change does not alter HPKE, key epochs, sync conflict resolution,
revocation, catch-up, recovery, or cleanup formats. R024 remains a
single-instance backend limitation. R026 and R029 remain controlled lifecycle
risks pending deterministic evidence. External review of the Dart RFC 9180
composition remains release-blocking.
