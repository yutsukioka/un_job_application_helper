# Phase 2D-10 Apple Vault Path Selection Design

Status: design only. This document defines the future Apple app-level path
selection policy for AtlasVault local encrypted stores. It does not add Swift
runtime path code, file writes, Keychain wiring, unlock UI, migration execution,
cloud sync, device onboarding, key rotation, or cleanup of legacy plaintext
snapshots.

## 1. Purpose

Phase 2D-10 decides how future Apple runtime code should choose, create,
validate, and protect the local encrypted vault file location before any app
runtime wiring is implemented.

This phase follows the public/private model split, Keychain unlock design,
Keychain adapter boundary, and explicit-URL local-store helpers. The goal is to
keep the encrypted vault path deterministic and testable while preventing path
names, public cache files, logs, or test artifacts from revealing private user
state.

## 2. Scope

In scope:

- app-level vault path selection design for iOS and macOS;
- privacy rules for directory and file names;
- test path injection rules;
- interaction boundaries with Keychain unlock state and public cache state;
- import/export and legacy plaintext cleanup boundaries;
- future tests for a path locator implementation.

Out of scope:

- Swift runtime path locator code;
- file creation, file reads, or file writes;
- Keychain unlock wiring;
- LocalAuthentication or biometric prompts;
- vault unlock UI;
- migration execution;
- cleanup or deletion of old plaintext snapshots;
- cloud sync or iCloud Drive integration;
- device onboarding;
- key rotation.

## 3. Current Storage Surfaces

Current Apple public cache storage is owned by `AtlasLocalCache` under the app
container Application Support directory:

- `Atlas/atlas-local-snapshot.json` for the local snapshot;
- `Atlas/JobDetails` for public job detail cache files;
- `Atlas/JobDetails.staging` and `Atlas/JobDetails.previous` for public detail
  cache staging and replacement.

The current app still exposes `savedSearches` and `savedJobs` in memory and has
legacy snapshot compatibility, but the Phase 2 design direction is that private
saved searches, saved jobs, notes, profile snippets, and draft metadata move to
encrypted AtlasVault records.

`AtlasVaultLocalStoreIO` now reads and writes only explicit caller-provided file
URLs. It does not choose an Application Support path and does not call Keychain.

`AtlasKeychainVaultKeyStore` can store local vault-key material by vault ID, but
it is not wired into runtime unlock behavior. Keychain item availability and
vault file availability remain separate future states.

## 4. Path Selection Goals

Future path selection should satisfy these goals:

- use a deterministic per-app encrypted vault location;
- support dependency injection so tests can use temporary directories;
- keep public cache files separate from private encrypted vault files;
- avoid plaintext private payloads in any directory name or file name;
- avoid saved-search names, search text, filters, job keys, notes, profile
  snippets, draft metadata, generated document references, and record type
  strings in paths;
- support iOS and macOS app sandbox containers;
- avoid repository paths and `private/` paths for app runtime storage;
- leave manual import/export compatible with `.atlasvault` files without using
  export files as the live local store;
- avoid iCloud Drive or cloud-sync paths by default.

## 5. Proposed App Vault Location

Recommended candidate path:

```text
Application Support/Atlas/Vaults/<vaultID>/atlasvault-local-store.json
```

Rules:

- `<vaultID>` must be random, stable for the vault, and non-semantic.
- `<vaultID>` must use a filesystem-safe canonical encoding such as lowercase
  hex, base32, or URL-safe base64 without padding. It must be bounded in length
  and must reject whitespace, path separators, `.`, `..`, `+`, and `/`.
- The vault ID must not be derived from saved-search names, job keys, email
  addresses, organization names, profile text, or other private payloads.
- Directory and file names must remain fixed or random/non-semantic.
- The live local-store file name should be generic, for example
  `atlasvault-local-store.json`.
- Staging or backup file names, if added later, should also be generic, such as
  `atlasvault-local-store.staging.json` or `atlasvault-local-store.previous.json`.
- App runtime code should use app container locations, not repo paths.
- iCloud Drive, shared app-group containers, and user-chosen external folders
  remain out of scope until a later explicit design.

This path keeps all private records encrypted in the local-store JSON while
using only a random vault ID as the path discriminator.

## 6. Development And Test Paths

Future implementation should expose a path locator protocol so tests and local
development can inject a base directory.

Rules:

- unit tests must inject temporary directories;
- tests must not write to the repository;
- tests must not write under `private/`;
- tests must not commit `.atlasvault` exports or live local-store files;
- local development may use an explicit override path, but no override should be
  enabled implicitly in production builds;
- `AtlasVaultLocalStoreIO` should continue receiving explicit file URLs from the
  caller rather than discovering paths itself.

The path locator should be the only component that knows the default app
container location. Store helpers should remain pure file-format IO helpers.

## 7. File Protection And Platform Notes

Future implementation should decide file protection and backup behavior before
runtime wiring.

iOS considerations:

- choose a file protection class for the encrypted local-store file and any
  staging files;
- consider how file availability interacts with background refresh and
  after-first-unlock Keychain availability;
- ensure missing or temporarily unavailable protected files map to a
  non-sensitive locked or unavailable state.

macOS considerations:

- use the app sandbox container Application Support directory when sandboxed;
- avoid user-visible document locations by default;
- support explicit development overrides without changing production defaults.

Backup and restore questions:

- including the encrypted local-store file in device backups may improve
  restore, but the vault still needs passphrase/recovery or valid local unlock
  material;
- excluding the encrypted local-store file from backups reduces backup surface
  but can make device replacement depend on export/import or cloud work that is
  not implemented yet;
- Keychain item restore behavior may differ from file restore behavior, so
  missing Keychain item and present vault file must remain separate states.

Do not claim production readiness until these policies are selected and tested.

## 8. Interaction With Keychain Unlock

Keychain unlock material should be indexed by random `vaultID`. The vault path
locator should find the encrypted local-store file for that same `vaultID`.

Rules:

- Keychain item account identifiers should use only random, non-semantic vault
  IDs.
- The vault path locator must not derive paths from private metadata or
  decrypted payloads.
- A missing Keychain item means local unlock material is unavailable.
- A missing vault file means the encrypted store is absent or not created.
- A corrupt vault file means path lookup succeeded but envelope validation or
  record validation failed.
- These states should be represented separately so future UI can offer recovery,
  import, or create-vault actions without exposing private details.

The unlock service should receive a locator result and key-store result as
separate inputs. It should not discover paths by reading public snapshots or
private metadata.

## 9. Interaction With Public Snapshot

The public snapshot should remain public-only.

Rules:

- public cache files may keep public search results, public job details, source
  summaries, source runs, health, and cache timestamps;
- public cache files must not contain saved-search names, saved-job membership,
  private job-key linkage, notes, snippets, draft metadata, generated document
  references, or decrypted payloads;
- public snapshot JSON should not store full encrypted vault file paths if those
  paths could reveal user-specific private metadata;
- if a public config needs the current `vaultID`, that ID must be random and
  non-semantic;
- public detail-cache warmup must not write saved-only job detail files into the
  public `Atlas/JobDetails` cache. If saved-only details are fetched after
  unlock, they must use a private/vault-backed path or remain in memory until a
  reviewed private-detail storage design exists.

The encrypted vault path should be treated as private operational configuration
unless the stored value is proven non-sensitive.

## 10. Import/Export Boundary

`.atlasvault` export/import remains an explicit user action and is not the same
as the live local-store file.

Rules:

- export should create a user-selected `.atlasvault` artifact only after an
  explicit export action;
- import should validate the export envelope before writing a live encrypted
  local store;
- import should not overwrite an existing live store unless the user explicitly
  confirms replacement or merge behavior;
- local-store writes should continue using explicit file URLs;
- no automatic cloud upload or sync should occur during import/export;
- export files must not be committed to the repository.

Future import code should stage validation and write operations so a failed
import does not corrupt the live local-store file.

## 11. Legacy Plaintext Cleanup Boundary

Old plaintext snapshots may still exist until a later cleanup phase.

Rules:

- no automatic cleanup occurs in Phase 2D-10;
- cleanup must require explicit user confirmation in a future phase;
- cleanup should occur only after the encrypted vault has been validated and the
  user has an acceptable recovery/export path;
- cleanup should not delete public job cache files unless a separate public
  cache reset action is requested;
- cleanup logs and summaries must not include saved-search text, job keys,
  notes, snippets, or generated document references.

This boundary prevents path-selection work from becoming an implicit migration
or deletion workflow.

## 12. Future Tests

Future path locator tests should cover:

- generated paths use only a random vault ID and generic filenames;
- no private sentinel strings appear in paths;
- saved-search names, job keys, record type strings, notes, snippets, and
  generated document references never appear in paths;
- tests inject temporary base directories;
- no repository writes occur;
- no `private/` writes occur;
- missing vault file maps to a missing-vault state;
- malformed local-store JSON maps to a corrupt-vault state;
- public snapshot serialization does not contain private path metadata;
- local-store writes use explicit path-locator URLs;
- iOS/macOS path roots can be tested without writing to real app container
  paths.

## 13. Open Questions

- Should encrypted local-store files be included in device backups by default?
- Which iOS file protection class should the live store and staging files use?
- How should multiple vaults be represented in app settings without exposing
  private meaning?
- Should a random current `vaultID` be stored in public config, Keychain
  metadata, a small separate locator file, or app preferences?
- What is the safest local-development override shape for packaged apps versus
  command-line test runs?
- How should support diagnostics report path and file states without leaking
  private path metadata or user-specific vault identifiers?

## 14. Recommended Next Implementation Loop

Recommended next loop:

- Phase 2D-10 review pass;
- Phase 2D-11 test-only vault path locator protocol plus temporary-path tests;
- continue avoiding runtime UI hydration, migration execution, cleanup,
  Keychain unlock wiring, and cloud sync until the path locator boundary is
  reviewed.
