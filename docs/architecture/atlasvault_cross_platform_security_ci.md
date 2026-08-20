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

The workflow has four independent jobs:

- Python runs focused identity, pairing, delivery, artifact, and registry
  vectors, the full VaultSync suite, secure-local-API admission tests, JSON
  vector validation, source guards, and repository artifact checks.
- Flutter runs the reviewed Flutter 3.44.4 toolchain, formatting, analysis,
  focused AtlasVault tests, the full Flutter suite, and an Android Debug build.
- Swift runs focused identity/pairing/interoperability tests, the full Swift
  suite, and generic Simulator builds for AtlasApple and AtlasIOSHost.
- Windows runs formatting, analysis, focused and full Flutter tests, Windows
  Debug and Release builds, native DPAPI/document source guards, and repository
  artifact checks.

Third-party actions are pinned to immutable Git commit SHAs.

## Platform Integration Workflow

`atlasvault-platform-integration.yml` runs only on manual dispatch and a
nightly schedule. It uses deterministic fake data.

The Android job exercises Keystore storage, migration and recovery,
interoperability and recovery, device identity persistence, trusted pairing,
replay behavior, fresh-process verification, and cleanup on an emulator.

The Windows job runs deterministic native storage, migration, interoperability,
identity, and trusted-pairing integrations supported by the hosted runner. It
does not claim Parallels-specific architecture, interactive desktop dialogs, or
the separately archived Parallels fresh-process evidence.

The Apple job runs simulator-compatible Swift identity, pairing, key-delivery,
interoperability, registry, replay, and transaction tests. It does not claim
physical-device Keychain, biometric, Secure Enclave, or fresh user-presence
behavior.

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
