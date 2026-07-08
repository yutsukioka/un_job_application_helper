# Phase 2D-13 AtlasVault Directory Preparer

Status: protocol, implementation, and temp-path tests only. This phase adds the
directory-preparation seam after the Phase 2D-12 policy review. It does not wire
directory preparation into app runtime behavior.

## Purpose

Phase 2D-13 implements a testable boundary for preparing the parent directory of
an AtlasVault local-store file URL. The preparer sits between the path locator
and the local-store writer so each layer remains independently testable.

## Scope

Included:

- `AtlasVaultDirectoryPreparer` protocol;
- FileManager-backed implementation;
- explicit root containment checks;
- temporary-directory tests;
- no-deletion behavior.

Excluded:

- app runtime wiring;
- default Application Support lookup;
- Keychain access;
- LocalAuthentication;
- migration execution;
- cleanup of old plaintext snapshots;
- cloud sync;
- device onboarding;
- key rotation.

## Directory Preparer Behavior

The preparer receives a caller-provided local-store URL and caller-provided root
directory. It prepares only the local-store parent directory.

Rules:

- the root and store URL must be file URLs;
- the root must already exist as a directory;
- the store URL parent must remain contained under the root;
- existing parent directories are accepted as success;
- missing parent directories are created with intermediate directories;
- the final `atlasvault-local-store.json` file is never created;
- paths outside the root are rejected.

## No-Deletion Policy

The preparer never deletes files or directories.

It does not:

- clean old plaintext snapshots;
- remove corrupted vault files;
- remove existing encrypted store files;
- delete staging files;
- delete unrelated files under the root;
- repair unrelated filesystem state.

## Symlink Policy

The first implementation is conservative: existing symlink components from the
root through the target parent path are rejected with `unsupportedSymlink`.

This avoids accepting a lexical path under the root that would be followed by
FileManager into another location during directory creation. A future reviewed
policy may resolve symlink targets and perform target containment checks, but
that is not part of this phase.

## Error Handling

The preparer surfaces non-sensitive errors:

- `invalidURL` for non-file roots or store URLs;
- `rootNotDirectory` when the root is missing or is not a directory;
- `pathEscapesRoot` when the parent path is outside the root;
- `parentExistsAsFile` when a required parent component is a file;
- `unsupportedSymlink` when an existing parent component is a symlink;
- `createDirectoryFailed` when FileManager cannot create the parent directory.

Errors do not include vault keys, passphrases, decrypted payloads, private
record values, or user-provided private metadata.

## Future Runtime Integration

The intended future layering remains:

1. `AtlasVaultPathLocator` computes the local-store URL from an injected root
   and random vault ID.
2. `AtlasVaultDirectoryPreparer` prepares the local-store parent directory.
3. `AtlasVaultLocalStoreIO` writes the encrypted local-store JSON.
4. A future unlock/session service coordinates Keychain state, vault state, and
   UI state.

This phase implements only the second layer and tests it with temporary roots.

## Tests

Tests cover:

- temp-root parent directory creation;
- no final store-file creation;
- idempotent preparation;
- outside-root rejection;
- root-as-file rejection;
- parent-component-as-file rejection;
- no deletion of existing files;
- no modification of unrelated files;
- no repository or `private/` path writes;
- no `.atlasvault` artifacts;
- source guard against Keychain, UserDefaults, Application Support lookup, app
  runtime state, and networking;
- symlink escape rejection when symlink creation is available.

## Deferred

- runtime app integration;
- default Application Support root provider;
- file protection policy;
- backup inclusion or exclusion;
- Keychain coordination;
- vault unlock UI;
- migration execution;
- cleanup of old plaintext snapshots;
- cloud sync;
- device onboarding;
- key rotation.
