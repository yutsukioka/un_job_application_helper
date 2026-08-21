# AtlasVault Cross-Platform Security CI

## Purpose

This package makes the existing AtlasVault security and interoperability
regressions visible as dedicated GitHub checks. It changes workflow policy and
runner scripts only. Product code, dependencies, wire formats, vectors, and
platform authority behavior remain unchanged.

## Baseline

Package C starts from PR #100 merge `a8fdca7c62d9c9c656d1aa83c6190fe8bcdb85a2`.
The pre-implementation repository had one combined Python workflow and no
dedicated Flutter, Swift, Windows, Android-build, or scheduled integration
gates.

## Scope

The package is restricted to two workflows, four platform runner scripts, and
this document. No eighth path is permitted.

## Pull-Request Workflow

`atlasvault-cross-platform-security.yml` runs on relevant pull requests,
relevant pushes to `master`, and explicit manual dispatch. Concurrency is
grouped by workflow and ref, with stale work cancelled. Workflow permissions
are limited to `contents: read`.

GitHub reports `master` as the repository default branch and every security
phase in this milestone targets it. Older repository text describing `master`
as a legacy snapshot is not branch-authority evidence for this workflow.

The workflow has four independent jobs:

- Python runs focused identity, pairing, delivery, artifact, and registry
  vectors, the full VaultSync suite, secure-local-API admission tests, JSON
  vector validation, source guards, and repository artifact checks. Its
  no-network guard parses pairing primitives with Python's AST, including
  aliased, `from ... import ...`, third-party client, and standard-library
  client forms such as `http.client`. It also rejects direct `__import__`
  calls and `importlib.import_module` calls reached through module, import, or
  local aliases instead of relying on textual module-name matching.
- Flutter runs the reviewed Flutter 3.44.4 toolchain on Ubuntu, formatting,
  analysis, focused AtlasVault tests, every host-independent Flutter test, and
  an Android Debug build. Python 3.12 is provisioned explicitly for a
  brace-aware Dart lifecycle-body source guard, which checks multiline
  `initState` bodies for automatic pairing, import, or export calls and
  scheduled operation tear-offs, including tear-offs first assigned to local
  aliases, while ignoring comments and literal string segments but preserving
  executable Dart string interpolations. Linux executes the supported search
  pixel goldens and their semantic cases; the tab golden remains on macOS.
- Swift runs focused identity/pairing/interoperability tests, the full Swift
  suite, the host-supported Flutter goldens, and generic Simulator builds for
  AtlasApple and AtlasIOSHost. Three cancellation/lifecycle tests run in isolated
  Swift test processes before the rest of the suite to remove unrelated runner
  scheduling from their cancellation assertions. The full-suite invocation
  skips only those already-executed tests; no test is omitted. The Swift job
  uses a full-history checkout so Git-backed scope tests do not take their
  shallow-repository skip paths. A fresh public-only CryptoKit signed
  transcript is written under runner-temporary storage and verified directly
  by the production Python and Dart pairing implementations before cleanup.
  A second runner-temporary ring then passes an Apple production encrypted
  export into Dart and a Dart production encrypted export back into Swift,
  preserving canonical bytes without uploading either fake artifact.
- Windows runs formatting, analysis, focused and full Flutter tests, Windows
  Debug and Release builds, native DPAPI/document source guards, and repository
  artifact checks. Test files run serially to avoid Windows directory-handle
  contention during deterministic cleanup.

Third-party actions are pinned to immutable Git commit SHAs.

## Platform Integration Workflow

`atlasvault-platform-integration.yml` runs only on manual dispatch and a
nightly schedule. It uses deterministic fake data.

The Android job uses separate fresh-runner matrix legs for protected-state
persistence and the explicit pairing journey. The persistence leg runs
prepare before verify. The journey leg seeds fake inbound Apple artifacts from
the checked-in canonical vector in app-private storage, injects that same
test-only vector at compile time, then pulls and validates Android output in a
runner-temporary ring. Package state is retained only across explicit
prepare/verify pairs and is removed after each scenario.

The Windows job also separates persistence and journey onto fresh matrix
runners. This prevents the journey's intentionally retained empty registry and
replay authorities from invalidating a later create-only persistence test. Its
journey leg seeds fake inbound Android artifacts from the same canonical vector
and validates Windows output. It does not claim Parallels-specific
architecture, interactive desktop dialogs, or the separately archived
Parallels fresh-process evidence.

Windows storage and private-state persistence tests receive explicit stable
fake vault IDs and run prepare before verify. The Apple integration filter also
includes the encrypted recovery-export/import interoperability class.

The Apple job runs simulator-compatible Swift identity, pairing, key-delivery,
interoperability, registry, replay, and transaction tests. It explicitly runs
both the artifact-ring produce and verify stages in a runner-temporary
directory. It does not claim physical-device Keychain, biometric, Secure
Enclave, or fresh user-presence behavior.

The hosted jobs do not transfer pairing files between runners. Each platform
strictly validates fake canonical ingress and egress against the shared vector,
which exercises the platform parsers without uploading forbidden pairing
documents or weakening job isolation.

## Artifact Policy

The workflows upload no raw workspaces or native protected-state outputs.
Allowed future diagnostics are limited to test logs, JUnit/XML reports, build
logs, source-guard reports, coverage summaries, fake-data screenshots, and
SHA-256 manifests with short retention.

The following must never be uploaded:

- AtlasVault encrypted transport or pairing documents;
- device-identity secret bundles or private keys;
- recovery keys or vault keys;
- local encrypted stores;
- Keychain exports, Android protected blobs, or DPAPI blobs;
- migration, import, or pairing journals.

Runner scripts fail when forbidden generated files are found in the repository.
Temporary fake pairing-ring files are confined to the runner temporary
directory, are never uploaded, and are removed on exit.

## Runner Safety

Shell scripts use `set -euo pipefail`. The Windows script uses strict mode and
terminating errors. Every script resolves the repository root from its own
location, uses no user-specific path, prints no secret, fails on the first
failed command, and preserves checked-in deterministic fake vectors.

Temporary Swift build state is created under the runner temporary directory and
removed on exit. No workflow requires a repository secret.

## Limitations

Pull-request CI does not replace the archived real Android API 37 and Windows
11/Parallels DPAPI evidence. Hosted integration jobs validate deterministic
fake-data behavior within runner capabilities. Production hardening in issue
#101 and later ciphertext synchronization, rollback detection, revocation, and
rotation remain separately gated.

## Verification

Package verification requires:

- valid YAML;
- immutable action pins;
- minimum permissions;
- path-filter and concurrency checks;
- shell and PowerShell syntax checks;
- successful local platform scripts where the host supports them;
- successful GitHub jobs on the exact PR head;
- isolated Android and Windows pairing persistence/journey scenarios;
- non-skipping Apple, Android, and Windows canonical artifact-ring checks;
- no repository AtlasVault artifact;
- exactly seven changed files;
- clean exact-head Codex review;
- one-time Copilot review;
- zero unresolved review threads.

## Go/No-Go

- Dedicated VaultSync pull-request check: required.
- Full Flutter and Android Debug pull-request check: required.
- Full Swift and both Apple Simulator builds: required.
- Full Windows tests and Debug/Release builds: required.
- Scheduled/manual platform integration: required.
- Minimum permissions and immutable action pins: required.
- Private-artifact upload: prohibited.
- Product-source or dependency change: prohibited.
- Production multi-device readiness: not claimed.

## Deferred

Issue #101 remains the immediate production-hardening gate. Phase 2F-3
ciphertext-only account and device-registry backend work has not started.
