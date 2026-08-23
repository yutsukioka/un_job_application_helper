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

`atlasvault-cross-platform-security.yml` runs on every pull request, every
push to `master`, and explicit manual dispatch. Concurrency is grouped by
workflow and ref, with stale work cancelled. Workflow permissions are limited
to `contents: read`.

Its admission job runs before every expensive platform job. It scans the
proposed tree with case-folded filenames, skips `.git`, and rejects
`.atlasvault`, `.atlaspair`, identity-secret, and ephemeral-private artifacts
regardless of their directory or filename casing. It prints filenames only and
does not require a repository secret. The platform jobs all require successful
admission, so path filtering cannot bypass this artifact boundary.

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
  `initState` bodies for automatic pairing, import, export, and explicit
  device-identity creation calls and scheduled operation tear-offs, including
  aliases reached through plain, nullable, and null-asserted receiver chains.
  It ignores comments and literal string segments while preserving executable
  Dart string interpolations and permits harmless owner/property references.
  Linux executes the supported search
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
fake vault IDs and run prepare before verify. Windows migration recovery uses
the same stable ID across separate `prepare` and `verify` Flutter processes,
then separately coordinates admission rollback, finalization exclusion, and
crash-lock release through test-owned ready signals. Windows recovery-import
uses a separate stable ID and distinct processes for admission prepare/reset,
selection commitment, and crash-lock release. Waiters have bounded exit and
signal timeouts, capture stdout/stderr, and are awaited; crash holders are
deliberately terminated before their verifier proceeds. Cleanup stages and a
`finally` block remove only the deterministic test coordination roots and
runner-temporary logs. The Apple integration filter also includes the encrypted
recovery-export/import interoperability class.

The Apple job runs simulator-compatible Swift identity, pairing, key-delivery,
interoperability, registry, replay, and transaction tests. It explicitly runs
both the artifact-ring produce and verify stages in a runner-temporary
directory. It does not claim physical-device Keychain, biometric, Secure
Enclave, or fresh user-presence behavior.

The hosted jobs do not transfer pairing files between runners. Each platform
strictly validates fake canonical ingress and egress against the shared vector,
which exercises the platform parsers without uploading forbidden pairing
documents or weakening job isolation.

## Lifecycle Execution Policy

The Dart source guard retains method identity while extracting automatically
invoked widget hooks. `initState`, `didChangeDependencies`,
`didUpdateWidget`, `didChangeAppLifecycleState`, `activate`, `deactivate`, and
`dispose` use the strict policy:
sensitive calls, scheduled tear-offs, aliases, collections, and interpolated
calls are prohibited because the hook itself is automatic.

`build` uses an execution-aware policy. It rejects direct calls, `.call()`,
invoked aliases, selected-and-invoked list/map/set aliases, immediately invoked
closures, and sensitive work passed to `Future.microtask`,
`scheduleMicrotask`, `Future.sync`, `Future.delayed`, `Future`, `Timer.run`, or
a post-frame callback, including typed `Future<T>` forms. It accepts a
sensitive tear-off or either expression- or block-bodied closure only when it
is wired to an explicitly allowlisted user-event handler, including either
branch of a ternary handler; framework route/builder/error callbacks and
ordinary control-flow blocks remain build execution and are analyzed.
Parameterized, zero-argument, expression-bodied, and block-bodied immediately
invoked closures remain execution. The same masked build execution body is used
for direct calls, aliases, schedulers, and IIFEs, so deferred work inside an
allowlisted user-event callback is not treated as build-time execution. The
allowlist recognizes block handlers with `async`, including either ternary
branch, but does not mask framework callbacks. The guard traces local private
wrapper methods that directly or transitively invoke a sensitive operation,
including generic methods with nested callback parameter types, while passive
button tear-offs do not make a UI helper sensitive. It includes recovery-key
setup plus migration preparation, finalization, resume, and activation in the
sensitive operation set. It scans every Dart source under `lib`; it does not
equate a comma or named widget argument with execution during `build`. Its
self-tests exercise both accepted callback wiring and rejected build-time
execution, including map/set closing delimiters and dynamic collection indices.
Wrapper analysis follows direct, parenthesized, alias-invoked, and scheduled
execution, but not a passive widget tear-off. Arrow callback boundaries balance
nested delimiters so user-event expressions may contain calls with commas. The
guard treats `createState`, executable State field initializers, and State
constructors as automatic construction paths. State constructor parsing accepts
optional named, private, and factory constructor segments, nested parameter
signatures, typed collection initializer literals, and block-bodied callback
initializers before locating block or arrow bodies with balanced delimiters. It
follows indirect subclasses of `State` across every scanned Dart source file.
A State field closure is considered construction work only when that exact
closure is immediately invoked through `()`, `.call()`, `?.call()`, or
`Function.apply`; a stored callback remains passive even when another closure
in the initializer is executed, while non-closure direct execution outside
that closure is still analyzed. Arrow-bodied automatic hooks and local wrappers
use a balanced expression terminator, so nested IIFEs are scanned in full.
`Function.apply` of a sensitive tear-off or sensitive alias is also a direct
build-time invocation.

Lifecycle names are treated as automatic only inside a State subclass, widget
subclass, or WidgetsBindingObserver subclass. Ordinary controller or service
methods named build or dispose remain explicit work. Private wrapper discovery
is keyed by owning class, so a same-named helper method cannot replace a State
wrapper. State member parsing skips ordinary methods before inspecting fields;
constructor parsing also recognizes expression-bodied callback initializers as
passive until a callback is actually invoked.

## No-Network Preflight

The complete AST-based VaultSync no-network policy executes before the first
VaultSync pytest command. It recursively parses the complete packaged module
set without importing `vaultsync`, so module-scope code cannot run before
policy admission. There is one canonical checker rather than a weaker early
import scan and a later dynamic-import scan.

The checker covers import/import-from roots, aliases, dotted `importlib`,
`importlib.import_module`, aliased import-module functions, direct and aliased
`builtins.__import__`, locally assigned dynamic-import aliases, `asyncio`, and
the blocked standard-library and third-party networking clients. Deterministic
samples include direct and aliased dynamic imports plus `asyncio` connection
and server forms plus process-launch imports; harmless JSON and importlib cache
operations remain allowed.

The process-launch policy also rejects os process-launch APIs. Artifact
admission case-folds the complete relative path, rejecting transport extensions
and identity-secret or ephemeral-private names in directory components as well
as final filenames.

## Windows Recovery Cleanup Modes

The deliberate crash proof remains strict: it verifies that the expected
test-owned `atlas.exe` runner is a descendant of the tracked holder root before
terminating that holder. The `finally` path is separate and tolerant: it owns
the tracked holder root and all descendants even when startup failed before an
Atlas runner existed. It never broadens ownership beyond the tracked root.

Finally cleanup independently attempts every live waiter and holder tree,
then the deterministic coordination and log roots. Paths already removed by
an earlier successful cleanup are skipped, while real removal failures are
collected so one failed termination cannot skip the remaining owned resources;
the workflow reports after all attempts complete. The workflow-policy self-tests
assert strict runner validation for deliberate crashes, runner-free holder
cleanup in `finally`, descendant termination, and resource deletion after
process cleanup attempts.

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

The Python preflight parses the complete VaultSync source closure before pytest
imports it. It rejects absolute networking and dynamic-import routes plus
`subprocess` and the standard `os` process-launch APIs, including imported
aliases. Relative imports remain local-module references even when their module
name resembles a blocked standard-library networking package.

The Flutter policy parser follows Flutter lifecycle ownership through qualified
base classes, State inheritance, and State-applied mixins. It preserves wrapper
ownership and inherited wrapper visibility, recognizes explicit generic calls
and null-asserted collection callbacks, and still distinguishes deferred
allowlisted user handlers from automatically executed lifecycle work.

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
- Windows migration and recovery-import process-boundary stage orchestration,
  including prepare/verify, admitted legacy waiters, selection/finalization,
  deliberate holder termination, verification, and cleanup;
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
