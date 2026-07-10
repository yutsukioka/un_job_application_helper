# Phase 2D-26 AtlasVault Application Support Root Provider Design

## 1. Purpose

Phase 2D-26 designs a small Apple root-directory provider that can supply the
Application Support base URL to the existing injected-root AtlasVault path
locator. The design precedes implementation and runtime composition.

## 2. Scope And Status

This phase is design only. It adds no Swift provider, directory creation, file
creation, Application Support runtime call site, SwiftUI integration, Keychain
access, migration execution, cloud sync, device onboarding, or key rotation.

## 3. Existing Injected-Root Boundary

`AtlasInjectedRootVaultPathLocator` currently receives an explicit root and
computes:

```text
<root>/Atlas/Vaults/<vaultID>/atlasvault-local-store.json
```

It validates the vault ID and performs no filesystem mutation. Tests use
temporary injected roots. The new provider must preserve that boundary rather
than moving path construction into Application Support discovery.

## 4. Proposed Root Provider Protocol

The future protocol should remain minimal:

```swift
public protocol AtlasVaultRootDirectoryProviding: Sendable {
    func rootDirectory() throws -> URL
}
```

An injected implementation can return a test root. A real Apple implementation
can obtain the user-domain Application Support directory through a separately
injectable locating seam.

## 5. Proposed Apple Provider

Candidate implementation types are:

- `AtlasApplicationSupportVaultRootProvider`;
- `AtlasApplicationSupportDirectoryLocating`;
- `AtlasFoundationApplicationSupportDirectoryLocator`;
- `AtlasVaultRootDirectoryError`.

The Foundation locator may wrap:

```swift
FileManager.urls(for: .applicationSupportDirectory, in: .userDomainMask)
```

The provider should select exactly one result, validate it, standardize it, and
return it. Construction must be side-effect free; lookup occurs only when
`rootDirectory()` is explicitly called.

## 6. iOS And macOS Sandbox Considerations

On iOS, the user-domain Application Support URL should resolve inside the app
container. The provider must not escape to shared app groups, iCloud Drive, or
user-selected folders without a separate reviewed design.

On sandboxed macOS, the same API should resolve inside the sandbox container.
Unsandboxed development behavior may differ, so tests must inject a fake
locator rather than depend on the host path. The provider does not claim that
every returned host path is suitable for production until sandbox entitlements
and packaging are verified.

## 7. File URL Validation

The provider should reject:

- no Application Support result;
- a non-file URL;
- a relative or otherwise malformed file URL under the selected policy;
- an empty or root-only path that cannot safely represent an app support base;
- an environment the adapter does not support.

The returned URL should be absolute and standardized. Standardization is not a
substitute for later filesystem containment and symlink checks.

## 8. Root Path Privacy

The root provider must not append or derive components from:

- vault IDs;
- record IDs or record types;
- saved-search names, text, or filters;
- job keys or saved membership;
- notes, snippets, draft metadata, or document references;
- account, email, organization, or other personal context.

It returns only the generic system or injected root. Errors and logs should use
non-sensitive classes and avoid exposing full user-specific paths.

## 9. Relationship To The Path Locator

The root provider supplies the root URL. The existing path locator remains the
only component responsible for adding:

```text
Atlas/Vaults/<vaultID>/atlasvault-local-store.json
```

The provider does not validate vault IDs, know the local-store filename, or
construct per-vault paths.

## 10. Relationship To The Directory Preparer

The provider does not inspect whether the root exists and does not create it.
After the path locator computes a destination, the existing directory preparer
remains responsible for validating containment and explicitly preparing the
destination parent.

## 11. No Directory Creation

`rootDirectory()` is a lookup and validation operation only. It must not call
directory-creation APIs, mutate attributes, repair paths, or create the
`Atlas`, `Vaults`, or vault-ID directories.

## 12. No Store-File Creation

The provider must not create, read, validate, replace, or delete the live
local-store file, temporary files, backups, exports, public snapshots, or local
databases.

## 13. No Keychain Access

Root lookup is independent of vault unlock and key availability. The provider
must not call `AtlasVaultKeyStore`, SecItem APIs, LocalAuthentication, or any
vault-key/session service.

## 14. No UserDefaults

The provider must not discover, persist, or override paths through
`UserDefaults`. A future explicit development override would require a separate
configuration boundary and review.

## 15. No Runtime Call Site Yet

Phase 2D-27 may implement and test the provider, but neither this design nor
that implementation should connect it to app startup, SwiftUI,
`SearchViewModel`, `AtlasLocalCache`, or the persistence coordinator. Runtime
composition remains a later phase.

## 16. Test Injection Strategy

Tests should inject an `AtlasApplicationSupportDirectoryLocating` fake that
returns a controlled URL or error. They should never use the host's real
Application Support path and should verify lookup without filesystem mutation.

An injected root provider may also be useful in later composition tests, but it
must remain separate from the path locator to keep root selection and per-vault
path construction independently testable.

## 17. Error Handling

`AtlasVaultRootDirectoryError` should use non-sensitive cases such as:

- `applicationSupportUnavailable`;
- `invalidFileURL`;
- `malformedURL`;
- `unsupportedEnvironment`.

Errors should not carry full paths, vault IDs, private payload values, keys, or
underlying localized descriptions that could disclose host-specific details.

## 18. Backup And File-Protection Questions

The root provider does not decide backup inclusion or file protection. Those
policies apply to directories and files created later. Open questions include:

- whether the encrypted local store is included in device backups;
- which iOS protection class applies to live and temporary files;
- whether directory attributes should be set by the preparer or a separate
  policy service;
- how restored vault files interact with missing Keychain items.

## 19. Multiple Vault Behavior

One Application Support root can host multiple non-semantic vault-ID
subdirectories. The provider should return the same base root regardless of
which vault is active. It must not select a current vault, enumerate vaults, or
store vault names.

## 20. Public Snapshot Boundary

The root provider must not read or mutate `AtlasPublicLocalSnapshot` or public
job cache state. Public snapshots must not receive private paths, vault record
counts, saved membership, or private payload metadata as a side effect of root
selection.

## 21. Future Tests

Phase 2D-27 tests should cover:

- an injected locator returning a valid absolute file URL;
- unavailable, non-file, malformed, and relative URL outcomes;
- stable standardized output across repeated calls;
- no directories or files created;
- no vault ID or private sentinel appended to the root;
- no repository or `private/` path requirement;
- no Keychain, defaults, SwiftUI, cache, view-model, API, or network references;
- no `.atlasvault` artifact.

## 22. Deferred Runtime Composition

Deferred work includes:

- root-provider implementation and tests in Phase 2D-27;
- wiring provider output into the path locator;
- constructing the directory preparer, atomic writer, and persistence
  coordinator together;
- runtime app launch and SwiftUI integration;
- final backup and file-protection policy;
- migration execution and old plaintext cleanup;
- cloud sync, device onboarding, and key rotation.

## 23. Recommended Phase 2D-27

Implement `AtlasVaultRootDirectoryProviding`, an injected Application Support
locating seam, a Foundation user-domain adapter, and fake-locator tests. Keep
the implementation side-effect free and disconnected from runtime composition.
