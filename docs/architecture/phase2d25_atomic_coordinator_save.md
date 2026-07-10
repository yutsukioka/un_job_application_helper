# Phase 2D-25 AtlasVault Atomic Coordinator Save

## Purpose

Phase 2D-25 integrates the Phase 2D-24 atomic writer into the testable
AtlasVault persistence coordinator. The integration remains an explicit seam
with no runtime app call site.

## Scope

Included:

- injected `AtlasVaultAtomicStoreWriting` in the persistence environment;
- explicit atomic save for a complete encrypted local-store envelope;
- encrypted record merge followed by atomic save;
- propagation of committed and committed-durability-unconfirmed results;
- temp-root and injected-writer tests.

Excluded:

- replacement of existing runtime call sites;
- SwiftUI, `SearchViewModel`, or `AtlasLocalCache` integration;
- automatic Application Support lookup;
- Keychain or `UserDefaults` access;
- record decryption or plaintext payload inspection;
- migration, cloud sync, device onboarding, or key rotation.

## Environment Injection

`AtlasVaultPersistenceEnvironment` now carries an
`AtlasVaultAtomicStoreWriting` existential in addition to the existing path
locator, directory preparer, and local-store reader/writer. A default
`AtlasVaultAtomicStoreWriter` preserves existing environment construction, while
tests can inject a recording or failing writer.

The existing direct-write methods remain available for compatibility with the
earlier isolated seam. No runtime call site is changed to use either path in
this phase.

## Atomic Save Flow

For a complete store, `saveEncryptedStoreAtomically`:

1. computes the explicit store URL from the injected path locator;
2. resolves symlinked ancestors in the injected root, then appends the validated
   root-relative store path without resolving below-root components or the
   destination file;
3. prepares only the parent directory using that canonical root and store URL;
4. passes the same canonical store URL, complete encrypted envelope, and
   explicit overwrite policy to the
   injected atomic writer, whose no-follow traversal still rejects below-root
   symlinks and symbolic-link destinations;
5. returns the writer's commit state unchanged.

For encrypted record mutations, `saveEncryptedRecordsAtomically`:

1. loads the existing encrypted local-store envelope;
2. merges encrypted envelopes without decryption;
3. fails duplicate or stale-revision checks before writer invocation;
4. passes the merged encrypted store to the atomic save flow;
5. returns committed or committed-durability-unconfirmed state.

Missing existing stores remain a non-sensitive `readFailed` result for the
record-merge method. First-store creation uses the complete-store atomic method
because store ID and vault metadata must be supplied explicitly.

## Failure Preservation

Merge failures occur before the atomic writer is called. Atomic pre-commit
failures are propagated without a direct-write fallback, so the previous
destination remains unchanged under the Phase 2D-24 contract.

A directory-sync failure after replacement is not converted into a generic
write failure. `committedDurabilityUnconfirmed` is returned so callers do not
assume the old destination is still active or retry blindly.

## Privacy Boundary

The coordinator and writer exchange only encrypted record and local-store
envelopes. They do not decrypt records, inspect private record types, mutate
`AtlasPublicLocalSnapshot`, or log private values. Record type remains inside
ciphertext, and public cache state remains independent.

## Tests

Tests cover:

- first-store atomic save;
- existing-store merge followed by atomic replacement;
- duplicate and stale-revision rejection before writer invocation;
- old-destination preservation for representative pre-commit writer failures;
- committed-durability-unconfirmed propagation;
- accepted symlinked-root canonicalization without following a symbolic-link
  destination;
- successful reload, untouched-record preservation, and tombstones;
- no private sentinel or plaintext record type leakage;
- no public snapshot mutation or `.atlasvault` artifacts;
- source guards against runtime, Keychain, defaults, networking, and automatic
  Application Support dependencies.

## Deferred

- selection of the atomic path by app runtime;
- expected store generation and save serialization;
- Application Support root provider;
- SwiftUI locked/unlocked state integration;
- migration execution;
- cloud sync;
- device onboarding;
- key rotation;
- cleanup of old plaintext snapshots.
