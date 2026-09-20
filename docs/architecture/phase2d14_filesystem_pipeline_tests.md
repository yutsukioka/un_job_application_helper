# Phase 2D-14 AtlasVault Filesystem Pipeline Tests

Status: test-only integration. This phase proves the isolated Apple
filesystem seams work together under temporary roots without adding runtime app
wiring.

## Purpose

Phase 2D-14 integrates, in tests only:

- `AtlasVaultPathLocator`;
- `AtlasVaultDirectoryPreparer`;
- `AtlasVaultLocalStoreIO`.

The goal is to verify the explicit-path filesystem pipeline before any app
runtime storage selection, unlock coordination, or SwiftUI hydration exists.

## Scope

Included:

- temporary roots only;
- explicit injected root URLs;
- encrypted local-store JSON write/read tests;
- overwrite behavior tests;
- plaintext leakage checks.

Excluded:

- runtime app wiring;
- automatic Application Support lookup;
- Keychain coordination;
- LocalAuthentication;
- migration execution;
- cleanup of old plaintext snapshots;
- cloud sync;
- device onboarding;
- key rotation.

## Pipeline

The test pipeline is:

1. `AtlasInjectedRootVaultPathLocator` computes:
   `<temp-root>/Atlas/Vaults/<vaultID>/atlasvault-local-store.json`.
2. `AtlasFileManagerVaultDirectoryPreparer` prepares only the parent
   directory.
3. `AtlasVaultLocalStoreIO.write` writes encrypted local-store JSON.
4. `AtlasVaultLocalStoreIO.read` reads the store back.

The preparer must not create the final store file. The final file appears only
after the explicit write call.

## Privacy

The path contains only fixed directory names, the fixed local-store file name,
and a random non-semantic vault ID. It must not contain saved-search names, job
keys, notes, profile snippets, generated document references, record type
strings, or fake private sentinels.

The serialized local-store JSON preserves encrypted record envelopes and must
not contain fake private sentinels or plaintext record type strings such as
`saved_search`, `saved_job`, `application_note`, `profile_snippet`, or
`draft_metadata`.

## Overwrite Behavior

The integration tests preserve the local-store writer's overwrite contract:

- first write succeeds;
- second write without `overwrite: true` fails;
- write with `overwrite: true` succeeds.

Directory preparation remains idempotent and separate from overwrite policy.

## Error Cases

Tests cover:

- invalid vault ID fails before directory creation;
- outside-root store URL is rejected by the preparer;
- parent component that exists as a file fails during preparation;
- malformed store JSON read fails safely.

## Deferred

- runtime path selection;
- real Application Support path;
- Keychain unlock coordination;
- vault unlock UI;
- SwiftUI hydration;
- migration execution;
- cleanup of old plaintext snapshots;
- cloud sync;
- device onboarding;
- key rotation.

## Next Loop

Recommended next loop:

1. Review Phase 2D-14.
2. Add Phase 2D-15 vault session plus filesystem pipeline design, or a
   test-only session integration if review confirms the filesystem pipeline
   boundary is stable.
3. Continue deferring runtime SwiftUI hydration and app storage wiring.
