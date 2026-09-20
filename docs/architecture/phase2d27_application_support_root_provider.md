# Phase 2D-27 AtlasVault Application Support Root Provider

## Purpose

Phase 2D-27 adds the isolated root-directory seam designed in Phase 2D-26. It
lets a future composition root obtain a generic user-domain Application Support
URL without choosing a vault, constructing a store path, or mutating storage.

## Scope

This phase adds a protocol, a Foundation directory-locating adapter, an injected
provider, and fake-locator tests. It adds no runtime call site, directory or file
creation, path-locator call, Keychain or defaults access, SwiftUI integration,
migration execution, cloud sync, device onboarding, or key rotation.

## Types

- `AtlasVaultRootDirectoryProviding` exposes explicit root lookup.
- `AtlasApplicationSupportDirectoryLocating` isolates platform discovery.
- `AtlasFoundationApplicationSupportDirectoryLocator` queries the user-domain
  Application Support location only when explicitly called.
- `AtlasApplicationSupportVaultRootProvider` validates and standardizes the
  injected locator result.
- `AtlasVaultRootDirectoryError` reports non-sensitive error classes.

## Lookup And Validation

The Foundation locator wraps `FileManager.urls(for:in:)` for
`.applicationSupportDirectory` and `.userDomainMask`. The provider requires one
absolute local file URL, rejects remote hosts, relative URLs, encoded separators,
and the filesystem root, then returns `standardizedFileURL`.

Validation applies to the original URL before standardization so malformed
inputs are not made acceptable by normalization. Standardization performs no
filesystem resolution and is not a replacement for the later directory
preparer and atomic writer containment checks.

## Responsibility Boundaries

The provider returns only the generic Application Support root. It does not
append `Atlas/Vaults`, a vault ID, a local-store filename, record metadata, or
private payload values. `AtlasVaultPathLocator` remains responsible for the
per-vault path, and `AtlasVaultDirectoryPreparer` remains responsible for
explicit parent creation before a write.

The provider does not inspect whether its result exists. It does not read,
create, replace, synchronize, or delete any directory or file. It also remains
independent of unlock state, vault keys, public snapshots, and app UI state.

## Errors And Privacy

Locator failures map to `applicationSupportUnavailable`; invalid schemes and
malformed local paths use fixed error cases. Errors carry no underlying localized
description, full path, vault ID, private value, key, or ciphertext. The provider
does not log.

## Tests

Tests inject controlled URLs and errors instead of reading the host Application
Support location. Coverage includes valid and stable standardized output,
unavailable lookup, non-file and malformed URLs, root-only and remote-host
rejection, zero filesystem mutation, private-sentinel exclusion, repository-path
separation, error redaction, and production-source guards.

## Deferred

Runtime composition, path-locator construction, directory preparation, atomic
write activation, backup and file-protection policy, SwiftUI state, migration,
cloud sync, onboarding, recovery UX, and key rotation remain deferred. This
isolated seam does not claim production readiness.
