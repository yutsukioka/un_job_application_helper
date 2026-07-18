# AtlasVault Phase 2D-51 Explicit Unlock Panel

## 1. Purpose

Phase 2D-51 adds an unwired, capability-driven SwiftUI panel over the merged
unlock presentation boundary. It gives future hosts an explicit unlock
surface without coupling the view to runtime activation, private state, or
storage services.

## 2. Phase Scope

This phase adds one pure view-state boundary, one SwiftUI view, focused tests,
and this architecture record. It does not wire the panel into an app entry
point, navigation, the public locked shell, or a production runtime host.

## 3. Reconstructed 2D-50 Baseline

The implementation baseline was reconstructed from Git and GitHub. Merged
Phase 2D-50 exposes immutable capabilities, selected method, and a sanitized
status through `AtlasVaultUnlockPresentationState`. Its controller remains the
owner of request admission, cancellation, timeout, and host reconciliation.

## 4. Exact Capability Surface

The presentation methods are local key, passphrase, and recovery key.
Capability status is available or unavailable. The panel derives its
`availableMethods` only from the supplied capability snapshot; it does not
enumerate and expose unsupported choices.

The merged statuses are locked, ready, method unavailable, activating,
unlocked, failed, cancelled, timed out, and host reconciliation required.
Phase 2D-51 maps only those statuses.

## 5. Production Local-Key-Only Projection

`AtlasVaultUnlockCapabilities.currentProduction` currently marks local key
available and leaves passphrase and recovery unavailable. The production
projection therefore shows only the explicit local-key action. It performs no
automatic selection or submission.

## 6. Hidden Passphrase And Recovery Behavior

Passphrase and recovery controls are absent when their capability is
unavailable. Tests may inject capability snapshots with fake providers to
exercise presentation behavior, but that does not advertise production
support or connect a provider.

The module-internal supplied-test-key request is not a presentation method and
never appears in the state or view.

## 7. Pure Public View-State Boundary

`AtlasExplicitUnlockViewState` is a non-persistent, non-sensitive projection.
It contains available methods, a sanitized selected method, the exact merged
status, fixed message text, interaction state, and an input-clear signal.

It contains no secret input, secret buffer, vault key, vault identifier,
wrapped-key metadata, encrypted record, filesystem path, private record
projection, provider, or save result.

## 8. Ephemeral Input-Draft Boundary

`AtlasExplicitUnlockInputDraft` is internal and owned by local SwiftUI state.
It holds only the temporary passphrase and recovery strings needed before
dispatch. It is not observable shared state and is not persisted.

Consumption validates capability and selection, creates the existing
one-shot `AtlasVaultInMemorySecretBuffer`, clears both local strings
synchronously, and returns the matching presentation submission. It cannot
construct a supplied-test-key request.

## 9. SecureField Use

Passphrase and recovery values use separate `SecureField` controls. A field is
rendered only when that method is both available and selected. The fields use
fixed accessibility metadata, disable autocorrection, and disable automatic
capitalization where the platform API supports it.

## 10. Clear-Before-Await Ordering

Submission first acquires a non-secret in-flight gate. It then consumes and
clears the draft synchronously. Only after the draft is empty does the view
create the asynchronous task and invoke the injected action.

Tests suspend an injected submit action at its first asynchronous boundary and
verify the draft is already empty and a duplicate attempt is rejected.

## 11. Submit Behavior

Local-key submission clears residual input, explicitly selects local key
through the injected boundary, and then submits the local-key request.
Passphrase and recovery submission require an available, selected method and
non-empty input. One non-secret gate permits only one active submission.

The view idempotently clears a submitted secret buffer after the injected
action returns. Request and controller layers retain their own mandatory
cleanup responsibilities.

## 12. Cancel Behavior

Cancel clears both local strings, cancels any view-owned submission task,
resets the local non-secret gate, and only then invokes the injected cancel
action. It does not infer or rewrite controller status.

## 13. Method-Change Behavior

Selecting another available method first clears both input strings and cancels
the current view-owned submission task. The view then delegates selection.
Passphrase text is never reused as recovery input or vice versa.

## 14. Disappearance Behavior

Disappearance clears local input, cancels the view-owned submission task,
resets local admission state, and delegates the disappearance event. View
construction itself invokes no action.

## 15. Host-Reconciliation Behavior

Host reconciliation required is preserved as an exact non-sensitive status.
Controls are disabled, input is cleared, and the view does not submit another
request or reinterpret the condition as an ordinary unlock failure.

## 16. Generic Failure Behavior

The merged controller reports a generic failed status for request failures it
cannot safely distinguish. The view displays one fixed unlock-failed message.
It does not invent wrong-passphrase, corrupt-vault, missing-key, provider, or
filesystem failure subtypes.

## 17. Accessibility Privacy

Secret fields use static labels and the fixed value "Protected input".
Passphrase and recovery content is not interpolated into labels, values,
status text, help, errors, or diagnostics.

## 18. Keyboard, Autocorrection, And Autofill Privacy

Autocorrection is disabled for both secret fields. Automatic capitalization is
disabled on iOS; the corresponding modifier is unavailable in the macOS
SwiftUI target. No text content type or autofill hint is assigned, and the
view does not read or monitor the clipboard.

## 19. Swift Memory-Clearing Limitations

The implementation minimizes secret ownership and lifetime, clears local
bindings synchronously, and uses the existing one-shot byte buffer. Swift
`String`, `Data`, framework bindings, and intermediate copies cannot be
universally zeroized. This phase makes no universal memory-erasure claim.

## 20. No Provider Integration

The panel owns no passphrase or recovery provider and does not connect the
Phase 2D-49 context-aware protocol to coordinator derivation closures. Fake
capabilities test presentation dispatch only.

## 21. No Cryptography

The view and pure state perform no Argon2id, AES-GCM, key unwrapping, key
derivation, record encryption, or record decryption.

## 22. No Keychain

The panel has no Keychain or `SecItem` dependency. A local-key request is an
injected presentation action; the view never reaches the key-store adapter.

## 23. No Filesystem

The phase performs no file read, write, directory preparation, path lookup, or
Application Support access.

## 24. No Networking Or Compatibility Endpoint

The panel has no network or API client. It cannot call saved-search, tracker,
or sidebar-refresh compatibility paths.

## 25. No Private-State Rendering

The panel does not render saved searches, saved jobs, application notes,
profile snippets, draft metadata, generated-document references, private
counts, or encrypted record envelopes.

## 26. No Save-Outcome Behavior

Save progress, recoverable failures, durability warnings, fatal containment,
mutations, and persistence results belong to other presentation boundaries
and are absent from this phase.

## 27. No App-Entry Or Navigation Wiring

`AtlasExplicitUnlockView` is a new standalone type. No app entry point,
production root, navigation source, `AtlasRootView`, `SearchViewModel`, or
locked-shell source is modified.

## 28. TDD Evidence

The merged Phase 2D-50 focused baseline passed 35 tests. Phase 2D-51 then
established a valid missing-type red compile and pushed that test-only
checkpoint before production source was added.

The first implementation compile exposed one macOS-only modifier mismatch,
which was corrected with a platform-specific privacy helper. The initial
focused green run executed 24 tests with zero failures.

## 29. Test Coverage

Tests cover production and fake capability projection, every merged status,
generic failure handling, unavailable-method rejection, one-shot input
consumption, synchronous clearing, duplicate admission, cleanup transitions,
redacted descriptions, injected action routing, side-effect-free
construction, static accessibility, source boundaries, and artifact absence.

No external SwiftUI view-inspection dependency or new runtime test host was
added.

## 30. Deferred Work

Deferred work includes production host composition, navigation, provider
integration, production passphrase and recovery support, private-state
rendering, save handling, LocalAuthentication, migration, plaintext cleanup,
cloud sync, recovery UX, onboarding, key rotation, and production-readiness
review.

## 31. Next Product Gate

After Phase 2D-51, Phase 2D-52 must be a thin locked-shell integration. It must
reuse the existing Phase 2D-46 `AtlasVaultTestHost`, public-search fake, and
endpoint-call recorder rather than creating another host.

Phase 2D-52 must verify that public search remains usable while locked and
that saved-search and tracker compatibility call counts remain zero while
locked. It must remain unwired from production app entry points.

After that thin integration, work should move toward production-host
composition for a real explicit local-key user journey. Phase 2D-52 is not
created or implemented in this phase.
