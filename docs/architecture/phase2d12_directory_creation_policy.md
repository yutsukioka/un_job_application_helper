# Phase 2D-12 AtlasVault Directory Creation Policy

Status: design only. This phase defines how future code should prepare parent
directories for the encrypted AtlasVault local store after path selection. It
does not implement directory creation or wire persistence into app runtime
behavior.

## Purpose

Phase 2D-12 defines the directory-creation boundary before implementation. The
current path locator computes a privacy-preserving local-store URL from an
explicit injected root. The next runtime-safe step is to decide which component
may create the parent directories for that URL, which paths are allowed, and
which operations remain forbidden.

## Scope

Included:

- directory creation policy and responsibilities;
- path containment rules;
- no-deletion behavior;
- iOS and macOS implementation notes;
- future test requirements.

Excluded:

- directory creation implementation;
- runtime app wiring;
- automatic Application Support lookup;
- Keychain coordination code;
- LocalAuthentication;
- migration execution;
- cleanup of old plaintext snapshots;
- cloud sync;
- device onboarding;
- key rotation.

## Directory Creation Responsibility

The path locator should remain pure. `AtlasVaultPathLocator` and
`AtlasInjectedRootVaultPathLocator` should continue to compute URLs only. They
must not create directories, check filesystem state, write files, or choose
runtime app roots.

Directory creation should be handled by a separate future service that receives:

- the caller-injected root URL used for path location;
- the computed local-store file URL;
- an explicit directory policy.

`AtlasVaultLocalStoreIO` should also remain focused on file-format read/write
behavior. It may assume its caller has prepared the parent directory, and it
should keep its existing overwrite policy separate from directory preparation.

## Proposed Future Type

A later implementation can add a small boundary such as:

- `AtlasVaultDirectoryPreparer`;
- `AtlasVaultDirectoryPolicy`;
- `AtlasVaultDirectoryError`.

The future preparer should:

- create only parent directories for a caller-provided vault local-store URL;
- verify the final parent directory remains under the intended injected root;
- reject unsafe or ambiguous paths;
- reject path components that escape the root;
- reject a path where a non-directory exists where a directory is required;
- treat an existing directory as success;
- avoid deleting, renaming, moving, or chmoding existing data;
- avoid backup policy or file protection changes until those policies are
  reviewed.

The future policy object can hold decisions such as whether intermediate
directories may be created, whether symlinks are permitted, and whether backup
or file-protection attributes should be applied. Until those decisions are
reviewed, the default design is conservative: create only ordinary directories
inside the injected root and do not mutate existing files.

## Path Containment

Directory preparation must prove that the target parent directory stays inside
the injected root.

Rules:

- standardize and resolve the injected root before comparison;
- standardize the local-store URL only as a preliminary step, not as proof of
  containment;
- before creating directories, either reject existing symlink components in the
  target parent path or resolve the final parent target through existing
  filesystem components and compare that resolved path against the resolved
  injected root;
- ensure the local-store parent directory, after the reviewed symlink policy is
  applied, is equal to or nested below the injected root;
- reject path traversal through `.` or `..`;
- reject paths that escape the root after normalization;
- reject non-file URLs;
- do not write outside the app sandbox or caller-provided container;
- do not follow user-controlled symbolic links unless a later review defines a
  safe policy.

Symlink handling is intentionally conservative. A future implementation should
test and document whether symlinks are rejected outright or resolved with
containment checks. A lexical path prefix check is not enough because a symlink
inside the injected root can point outside that root.

## iOS and macOS Platform Notes

iOS and macOS should share the same layered policy:

- path locator computes the local-store URL;
- directory preparer creates parent directories if explicitly asked;
- local-store writer writes the encrypted store file;
- unlock/session code coordinates state later.

iOS considerations:

- production roots should live inside the app container when runtime path
  selection is implemented;
- file protection class selection remains a later implementation detail;
- directory availability and encrypted-store availability may differ during
  device lock states;
- backup inclusion or exclusion for the encrypted local store requires a
  separate product/security decision.

macOS considerations:

- sandboxed builds should use the sandbox container when runtime path selection
  is implemented;
- unsandboxed development overrides should stay explicit and testable;
- user-visible document folders should not be used by default;
- backup and restore behavior should be documented before runtime wiring.

Open questions:

- should the encrypted local store be included in device backups by default;
- which iOS file protection class should apply to the store and staging files;
- should backup exclusion be applied to directories, files, or neither;
- how should the app report protected-file unavailability without leaking path
  details.

## Error Handling

Future directory-preparer errors should be non-sensitive and narrow enough for
callers to choose a recovery path.

Candidate errors:

- root unavailable;
- invalid root URL;
- path escapes root;
- parent component exists as file;
- permission denied;
- directory creation failed;
- disk full or resource unavailable, reported generically.

Non-error outcome:

- existing directory is accepted as success.

Errors must not include vault keys, passphrases, recovery keys, decrypted
payloads, saved-search names, job keys, notes, snippets, generated document
references, or other private values.

## No Deletion Policy

The directory preparer must not delete files.

It must not:

- remove old plaintext snapshots;
- remove corrupted vault files;
- remove previous encrypted stores;
- delete staging files;
- clear cache directories;
- repair unrelated filesystem state.

Cleanup of legacy plaintext snapshots remains a future explicit,
user-confirmed flow. It should happen only after encrypted-vault validation and
after the user understands what will be removed.

## Privacy and Logging

Directory and file names must remain fixed or random/non-semantic. Paths must
not include saved-search names, job keys, record types, notes, profile snippets,
draft metadata, generated document references, organization names, email
addresses, or personal context.

Logging policy:

- prefer redacted path logging;
- avoid logging random vault IDs unless reviewed;
- never log vault keys, passphrases, recovery keys, decrypted payloads, or
  private metadata;
- report directory errors with generic, non-sensitive messages.

## Tests For Future Implementation

Future implementation tests should use temporary roots only and should cover:

- creates parent directories under a temp root;
- is idempotent when parent directories already exist;
- refuses when a required parent component exists as a file;
- refuses paths that escape the injected root;
- does not create the final local-store file;
- does not delete existing files;
- does not follow unsafe symlinks unless a reviewed policy explicitly allows
  that behavior;
- does not write under the repository or `private/`;
- does not create `.atlasvault` artifacts;
- keeps private sentinels out of paths and logs;
- keeps Keychain, UserDefaults, Application Support lookup, runtime app state,
  and networking out of the preparer.

## Interaction With Local Store Helper

The intended layering is:

1. `AtlasVaultPathLocator` computes the local-store file URL from an injected
   root and random vault ID.
2. A future directory preparer validates containment and prepares only the
   parent directory.
3. `AtlasVaultLocalStoreIO` writes the encrypted local-store JSON with its own
   overwrite policy.

Each layer should remain separately testable. The directory preparer should not
encode, decode, decrypt, or inspect local-store records.

## Interaction With Keychain

Directory creation does not access Keychain.

Missing states should remain distinct:

- missing Keychain item means local unlock material is unavailable;
- missing directory means the local-store parent path is not prepared;
- missing vault file means no encrypted local store exists at the computed URL;
- corrupt vault file means the file exists but cannot be validated.

A later unlock/session coordinator can combine these states. The directory
preparer should not decide unlock policy, prompt for credentials, or create
Keychain items.

## Deferred

- directory creation implementation;
- real Application Support root provider;
- runtime app path wiring;
- file protection policy;
- backup inclusion or exclusion;
- Keychain unlock coordination;
- vault unlock UI;
- migration execution;
- cleanup of old plaintext snapshots;
- cloud sync;
- device onboarding;
- key rotation.

## Recommended Next Loop

Recommended next loop:

1. Review Phase 2D-12.
2. Add Phase 2D-13 test-only directory preparer protocol and temp-path tests.
3. Keep runtime app wiring, Keychain unlock coordination, migration execution,
   cleanup, and cloud sync deferred.
