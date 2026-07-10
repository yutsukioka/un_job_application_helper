# Phase 2D-24 AtlasVault Atomic Store Writer

## Purpose

Phase 2D-24 implements a testable atomic writer for complete encrypted
`AtlasVaultLocalStoreEnvelope` values. It follows the reviewed Phase 2D-23
design before any persistence coordinator or runtime app integration.

## Scope

Included:

- full in-memory local-store encoding before filesystem mutation;
- exclusive same-directory temporary-file creation;
- restrictive temporary-file permissions before staged bytes;
- complete-byte writes and encrypted-envelope revalidation;
- temporary-file and parent-directory synchronization;
- atomic first-write and overwrite commits;
- injected pre-commit failures and commit-state results.

Excluded:

- persistence coordinator integration;
- Application Support path selection;
- SwiftUI, `SearchViewModel`, or `AtlasLocalCache` integration;
- Keychain or `UserDefaults` access;
- migration, cloud sync, device onboarding, or key rotation.

## Writer Boundary

`AtlasVaultAtomicStoreWriting` accepts a validated local-store envelope, an
explicit destination file URL, and an explicit overwrite choice. The default
`AtlasVaultAtomicStoreWriter` encodes the full envelope before it asks its
filesystem dependency to inspect or mutate the destination.

The writer creates a random hidden sibling name with a `.tmp` extension. The
token accepts only non-semantic ASCII letters, digits, and hyphens, so it cannot
escape the prepared parent or include private path components.

## Filesystem Client

`AtlasVaultAtomicFileSystemClient` isolates filesystem operations for injected
failure tests. `AtlasFoundationAtomicFileSystemClient` uses Darwin operations
to:

- require an existing ordinary parent directory;
- walk from the filesystem root with descriptor-relative, no-follow directory
  opens so symbolic-link ancestors are rejected;
- reject symbolic-link or non-file destinations with descriptor-relative
  metadata checks;
- create the temporary file with exclusive, no-follow flags and mode `0600`;
- verify mode `0600` before writing;
- write all encoded bytes;
- read staged bytes without following a symbolic link;
- synchronize the staged file;
- use same-directory descriptor-relative `renameatx_np(..., RENAME_EXCL)` for
  first writes;
- use same-directory descriptor-relative `renameat` for explicit overwrite;
- synchronize the parent directory after commit;
- remove only the temporary path after pre-commit failure.

This adapter does not claim complete power-loss durability on every Apple
filesystem. Exact full-sync, iOS file-protection, and multi-process policies
remain deferred.

## Validation And Privacy

The staged bytes must equal the encoded bytes and decode back into the same
encrypted local-store envelope before commit. Validation never opens or
decrypts record ciphertext.

The writer and filesystem client do not log vault keys, ciphertext, record
types, saved-search names, job keys, notes, snippets, draft metadata, document
references, or path components derived from private payloads.

## Failure And Commit States

Any failure before atomic replacement removes the writer-created temporary file
when possible and leaves the existing destination unchanged. Cleanup failure is
reported with the non-sensitive stage at which the primary failure occurred.

After successful replacement, parent-directory synchronization failure cannot
restore the previous destination. The writer therefore returns
`committedDurabilityUnconfirmed` rather than throwing a pre-commit-style error
or deleting the committed store.

## Tests

Tests use an injected fake filesystem client for deterministic failures and
real temporary directories for successful Darwin-backed commits. Coverage
includes:

- first write, overwrite, and overwrite refusal;
- same-directory non-semantic temporary names;
- parent and destination validation;
- protection-before-write ordering;
- old-destination preservation across every pre-commit failure;
- temporary cleanup;
- complete decodable committed output;
- committed durability-unconfirmed reporting;
- no unsafe rollback;
- no private sentinel or plaintext record type leakage;
- no path escape, repository write, or `.atlasvault` artifact;
- source guards against runtime, Keychain, defaults, networking, and automatic
  Application Support dependencies.

## Deferred

- persistence coordinator atomic-save integration;
- expected-generation compare-and-swap;
- in-process writer actor;
- multi-process coordination;
- final iOS file-protection and backup policy;
- full-sync policy beyond current `fsync` calls;
- SwiftUI/runtime app integration;
- migration execution;
- cloud sync, device onboarding, and key rotation.
