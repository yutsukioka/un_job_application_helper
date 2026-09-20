# Phase 2D-11 Apple Vault Path Locator

Status: protocol and tests only. This phase adds a testable Swift path locator
for AtlasVault local-store URL calculation. It does not wire the locator into
app runtime behavior.

## Purpose

Phase 2D-11 creates the seam that future runtime code can use after path
selection review: a protocol plus an injected-root implementation for computing
privacy-preserving AtlasVault local-store paths.

The locator is intentionally smaller than the Phase 2D-10 app policy. It
computes URLs only. It does not create directories, read files, write files,
look up Application Support, call Keychain, or hydrate SwiftUI state.

## Scope

Included:

- `AtlasVaultPathLocator` protocol;
- explicit injected root URL implementation;
- vault ID validation;
- temp-root unit tests;
- source guards against runtime wiring.

Excluded:

- automatic Application Support lookup;
- app runtime use;
- file writes by the locator;
- Keychain unlock integration;
- LocalAuthentication;
- migration execution;
- cleanup of old plaintext snapshots;
- cloud sync;
- device onboarding;
- key rotation.

## Path Policy

The injected-root locator computes:

```text
<root>/Atlas/Vaults/<vaultID>/atlasvault-local-store.json
```

`vaultID` must be random, non-semantic, stable for the vault, and filesystem
safe. The local-store file name is fixed and non-sensitive.

The injected root is supplied by the caller. A later runtime phase may add a
separate production root provider after review, but this phase does not do that.

## Validation

The locator rejects:

- empty or whitespace-only vault IDs;
- IDs with leading or trailing whitespace;
- `.` and `..`;
- IDs containing path separators;
- IDs containing characters outside `A-Z`, `a-z`, `0-9`, `_`, and `-`;
- IDs longer than 96 characters;
- non-file root URLs.

This allows UUID-like IDs, hex-like IDs, and URL-safe base64/base32-like IDs
without padding. It rejects raw base64 values containing `/`, `+`, or `=`.

## Privacy

Paths must not contain saved-search names, job keys, filters, notes, profile
snippets, generated document references, record type strings, real personal
context, or private sentinels. The only variable path component is a validated
random vault ID.

## Tests

Tests cover:

- temp root path calculation;
- stable repeated paths for the same vault ID;
- invalid vault IDs;
- non-file root rejection;
- absence of private sentinel values in paths;
- no repository or `private/` path requirement;
- source guard against Application Support, Keychain, UserDefaults, SwiftUI, and
  networking.

## Deferred

- real Application Support root provider;
- app runtime wiring;
- Keychain unlock integration;
- vault unlock UI;
- migration execution;
- legacy plaintext cleanup;
- cloud sync;
- device onboarding;
- key rotation.
