# Phase 2D-23 AtlasVault Atomic Local-Store Writer Design

## 1. Purpose

Phase 2D-23 designs atomic, crash-aware replacement of an encrypted AtlasVault
local-store file before any production runtime write-back is added. The writer
must prevent readers from observing a partially written store, preserve the
previous valid destination through every pre-commit failure, and make the
remaining durability limits explicit.

## 2. Scope

This phase is design only. It adds no atomic-writer implementation, runtime app
wiring, SwiftUI integration, migration execution, cleanup of old plaintext
snapshots, cloud sync, device onboarding, or key rotation.

The future boundary writes only encoded `AtlasVaultLocalStoreEnvelope` data.
It never receives or writes decrypted private payloads.

## 3. Atomicity Versus Durability

Atomicity and durability are separate guarantees:

- atomicity means a reader observes either the old complete destination or the
  new complete destination, never an intermediate byte sequence;
- durability means a reported commit survives relevant process, device, or
  power failures.

A same-filesystem rename or replacement can provide atomic namespace visibility
without proving full crash durability. Durability may also require flushing the
temporary file data and the destination directory entry with platform-appropriate
APIs. The exact guarantees of APFS, iOS data protection, Foundation, and lower-level
Darwin calls must be verified before implementation. This design does not claim
that `.atomic`, rename, `fsync`, or any single API is sufficient in every failure
mode.

## 4. Current Write Path And Risk

The current path is:

1. `AtlasVaultRecordSaver` creates encrypted record envelopes.
2. `AtlasVaultLocalStoreMerger` creates a complete encrypted local-store
   envelope.
3. `AtlasVaultPersistenceCoordinator` prepares the parent directory and passes
   the envelope to `AtlasVaultLocalStoreIO`.
4. `AtlasVaultLocalStoreIO.write` validates the file URL, performs a separate
   existence check when overwrite is false, encodes the complete envelope, and
   calls `Data.write(to:options: [.atomic])`.

Encoding before the write is already a useful boundary, and Foundation's
atomic option is preferable to a direct in-place overwrite. It is not yet an
auditable AtlasVault transaction contract. The current helper does not expose
temporary-file placement, exclusive creation, validation of staged bytes,
permission or file-protection handling, file and directory synchronization,
cleanup outcomes, or injected failure points. Its separate overwrite check is
also vulnerable to a time-of-check/time-of-use race.

Without a reviewed explicit contract, relevant risks remain:

- a partial or truncated destination if a future path falls back to direct
  overwrite or replacement is not atomic on a supported filesystem;
- a crash during overwrite or metadata commit;
- destination loss if replacement code removes the old file before commit;
- orphaned encrypted temporary files after interruption or cleanup failure;
- concurrent writers replacing each other's complete but stale stores.

## 5. Proposed Future Boundary

Candidate types are:

- `AtlasVaultAtomicStoreWriting` for the writer protocol;
- `AtlasVaultAtomicWritePolicy` for overwrite, validation, synchronization,
  protection, and precondition choices;
- `AtlasVaultAtomicWriteError` for non-sensitive staged failure reporting;
- `AtlasVaultFileSystemClient` for injected filesystem operations and failure
  tests.

The future writer should accept either a validated local-store envelope or its
already encoded encrypted bytes plus the destination and expected precondition.
It should write through a random same-directory temporary file, validate the
staged encrypted store, commit atomically, and clean the temporary file on
pre-commit failure. It must not decrypt or inspect records, log file contents,
choose a runtime root, or mutate public snapshots.

The result should distinguish `notCommitted`, `committed`, and
`committedDurabilityUnconfirmed` outcomes. A post-commit synchronization error
must not be reported as though the old destination were certainly still active.

## 6. Recommended Write Algorithm

The first implementation should use this sequence:

1. Validate that the destination is a file URL.
2. Confirm that the directory preparer has prepared an ordinary destination
   parent under the injected root.
3. Validate and encode the complete encrypted local-store envelope in memory
   before any filesystem mutation.
4. Generate a random, non-semantic temporary filename in the destination
   directory.
5. Create the temporary file exclusively and reject symbolic-link traversal.
6. Write all bytes, handling short writes rather than assuming one operation
   consumed the complete buffer.
7. Apply the reviewed permissions and file-protection policy; the temporary
   file must never be less protected than the destination.
8. Read and decode the staged bytes as an encrypted local-store envelope,
   without opening or decrypting any record, when validation is enabled.
9. Synchronize the temporary file according to the reviewed durability policy.
10. Re-check the overwrite and expected-generation preconditions, while using
    a commit primitive that enforces them atomically where possible.
11. Atomically rename or replace the destination without first deleting it.
12. Synchronize the parent directory where supported and required by policy.
13. Remove the temporary file after any pre-commit failure; report cleanup
    failure separately if removal is not possible.
14. Never delete the previous destination before atomic commit and never delete
    the new destination to compensate for a post-commit reporting failure.

The writer reports success only when the selected commit requirements are met.
If replacement occurred but directory synchronization failed, it should return
a distinct durability-uncertain result rather than attempt an unsafe rollback.

## 7. Foundation API Alternatives

`Data.write(to:options: .atomic)` is compact and already used. It does not give
the caller enough control to verify same-directory staging, exclusive temporary
creation, staged-file validation, synchronization, cleanup, permissions, or
failure injection. Its contract must not be treated as full crash durability.

`FileManager.replaceItemAt` makes replacement explicit and may help preserve or
select metadata, but the caller still must create, write, validate, protect, and
synchronize the staged file. Its destination-existence requirements, metadata
behavior, backup options, and failure guarantees need Apple-platform tests.

A same-directory temporary file plus a verified Darwin no-replace/replace
primitive provides the most control over exclusive creation, no-follow flags,
short writes, synchronization, and commit behavior. It also carries more
platform-specific complexity and requires careful descriptor-relative path
handling.

`AtlasVaultFileSystemClient` is an injection boundary, not an atomicity
mechanism. It should wrap the chosen Foundation or Darwin operations so tests
can fail each stage deterministically.

Recommended first strategy: introduce the injected filesystem client and an
explicit same-directory staging algorithm, then use the narrowest verified
Apple-platform replacement primitive for each overwrite mode. Do not rely on
`Data.write(.atomic)` as the complete AtlasVault policy. Before coding, verify
the exact macOS and iOS behavior of exclusive commit, metadata preservation,
file protection, and file/directory synchronization.

## 8. Same-Filesystem Requirement

The commit temporary file must be created in the destination directory, or by
an operation that proves it is on the same filesystem. A general system
temporary directory is not acceptable because cross-filesystem rename is not
atomic and may fail or degrade into copying.

Temporary filenames must be random and non-semantic. They must not contain a
vault ID, record ID, record type, job key, search name, or other private value.

## 9. Overwrite And Destination Preconditions

`overwrite == false` must refuse to replace an existing destination. A separate
existence check is not sufficient; the final commit should use a no-replace or
exclusive primitive where the platform provides one.

`overwrite == true` may replace an existing destination atomically, but must
never delete it before commit. The operation should also accept a future
expected store generation or digest so a writer cannot silently replace a
store that changed after it was read. Precondition failure leaves the
destination unchanged and removes the staged file when possible.

## 10. Concurrency And Lost Updates

Atomic replacement prevents partial files; it does not prevent lost updates.
Record parent-revision checks happen against the envelope loaded by the
coordinator, but another writer can commit a different store between that load
and this writer's commit.

The first implementation should serialize writes per destination within the
process, potentially with an actor or injected write coordinator. It should
also carry an expected store generation or content digest from load through
commit. A generation check that is not coupled to serialization or an atomic
compare-and-swap primitive still has a race and must not be overclaimed.

Multi-process writers, extension processes, file presenters/coordinators, and
cross-device conflict behavior remain open questions.

## 11. Path Containment And Symlink Policy

The destination should come from the validated injected-root path locator, and
its parent should be prepared by `AtlasVaultDirectoryPreparer`. The writer must
still defend against filesystem changes between preparation and commit.

The writer must:

- prove the destination parent remains under the injected root;
- reject symlink components or use descriptor-relative no-follow operations
  whose containment behavior has been reviewed;
- avoid following an existing destination symlink to another file;
- create and commit the temporary file within the validated parent;
- reject a parent or destination that changes into an unsafe type.

Lexical path-prefix comparison alone is insufficient. The exact no-follow and
directory-descriptor policy may require lower-level APIs and must have race and
symlink tests before runtime use.

## 12. File Permissions And Protection

The implementation review must choose and test:

- an iOS file-protection class and behavior while the device is locked;
- expected behavior in the macOS sandbox;
- whether overwrite preserves existing ownership and permissions or applies a
  fixed reviewed policy to the replacement;
- whether the encrypted store is included in device backups;
- how protection and backup attributes are applied before commit.

The temporary file must not have weaker permissions or protection than the
final file. A replacement must not accidentally broaden access. No policy is
selected or implemented in this phase.

## 13. Backup And Recovery Policy

No plaintext backup is permitted. Any future last-known-good backup must be a
complete encrypted local-store envelope with protection no weaker than the
destination.

Whether to retain an encrypted backup, how to rotate it, how to validate it,
and whether it participates in device backup are deferred. A failed new write
must never delete an existing valid store merely because backup creation,
replacement, or cleanup failed.

## 14. Failure Model

| Failure stage | Destination expectation | Temporary-file expectation |
| --- | --- | --- |
| Temporary creation | Existing destination unchanged; no destination created | No temp, or remove any partially created encrypted temp |
| Byte write | Existing destination unchanged | Close and remove temp when possible |
| Staged validation | Existing destination unchanged | Remove invalid encrypted temp when possible |
| File synchronization | Existing destination unchanged | Remove temp when possible |
| Precondition check | Existing destination unchanged | Remove temp when possible |
| Atomic replacement | Existing destination remains unchanged if the selected primitive reports no commit | Remove temp when possible; verify platform semantics |
| Directory synchronization | Commit may already be visible; never delete or roll back the new destination | No temp should remain; report durability as unconfirmed |
| Temp cleanup | Existing destination unchanged before commit, or committed destination retained after commit | Encrypted orphan may remain; report cleanup failure without deleting destination |

Every injected failure test must assert both destination bytes and temporary
artifacts. Cleanup is best effort after a primary failure, but cleanup failure
must not hide whether commit occurred.

## 15. Error And Logging Policy

Candidate non-sensitive errors are:

- `invalidDestination`;
- `destinationExists`;
- `tempCreationFailed`;
- `writeFailed`;
- `validationFailed`;
- `synchronizationFailed`;
- `replacementFailed`;
- `cleanupFailed`;
- `concurrentModification`;
- `durabilityUnconfirmed`.

Errors and logs must not expose vault keys, ciphertext bodies, nonces,
saved-search names, query text, job keys, notes, profile snippets, document
references, record types, or private path components. Diagnostics should use
only a redacted operation stage, commit state, and non-sensitive error class.

## 16. Interaction With The Persistence Coordinator

The intended write-side sequence is:

1. the saver creates encrypted record envelopes;
2. the merger creates a new encrypted local-store envelope;
3. the coordinator supplies the destination, overwrite policy, and expected
   current store generation when available;
4. the atomic writer encodes or accepts validated encrypted bytes and performs
   the final commit;
5. the coordinator reports success only after the required commit result is
   known.

The atomic writer does not derive keys, decrypt payloads, inspect record types,
mutate the public snapshot, or choose an Application Support root.

## 17. Tests For Future Implementation

Phase 2D-24 should use injected roots and an injected filesystem client to test:

- successful first write;
- successful overwrite;
- overwrite false refusing an existing destination;
- temporary file creation in the destination directory;
- temporary creation failure leaving the destination unchanged;
- byte-write failure leaving the destination unchanged;
- validation failure leaving the destination unchanged;
- file-synchronization failure leaving the destination unchanged;
- replacement failure leaving the destination unchanged;
- cleanup removing an orphan temporary file when possible;
- cleanup failure preserving the destination and reporting non-sensitive state;
- directory-synchronization failure reporting committed durability as
  unconfirmed;
- every visible destination decoding as one complete encrypted local-store
  envelope;
- no fake private sentinel leakage;
- no plaintext private record type leakage;
- no `.atlasvault` artifacts;
- no writes outside the injected root;
- concurrency or expected-generation failure preventing a lost update;
- symlink escape rejection;
- temporary and destination permissions/file protection matching the selected
  policy.

## 18. Deferred

- atomic-writer implementation;
- exact `fsync`, full-sync, and directory-synchronization strategy;
- file-protection and permission policy;
- encrypted backup policy;
- multi-process locking or coordination;
- SwiftUI and runtime app integration;
- migration execution;
- cloud sync;
- device onboarding;
- key rotation;
- cleanup of old plaintext snapshots.

## 19. Recommended Next Loop

Review Phase 2D-23, then implement Phase 2D-24 as a test-only atomic writer
protocol with injected filesystem failures. Keep that implementation below the
persistence coordinator runtime boundary and before SwiftUI integration.
