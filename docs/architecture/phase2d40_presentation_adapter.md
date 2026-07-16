# Phase 2D-40 AtlasVault Presentation Adapter

## Purpose

Phase 2D-40 implements the runtime-neutral projector designed in Phase 2D-39.
It maps redacted runtime status and an optional unlocked hydrated snapshot into
UI-safe, in-memory presentation values.

## Scope

The phase adds one stateless adapter, presentation value types, and fake-state
tests. It adds no UI framework, observable object, property wrapper, app-entry
or app-host call site, runtime command orchestration, persistence, Keychain,
filesystem, crypto, migration, cloud sync, recovery, onboarding, key rotation,
or production-readiness claim.

## Adapter Boundary

`AtlasVaultPresentationAdapting` accepts `AtlasVaultRuntimeStatus`, an optional
`AtlasVaultHydratedState`, an explicit presentation generation, and a fixed
transient command state. The concrete
`AtlasVaultPresentationAdapter` is a value with no stored dependencies or
mutable state. It invokes no runtime service and performs only deterministic
projection.

## Status Mapping

Runtime statuses map to fixed presentation statuses. Activation failures are
handled exhaustively: missing stores become `noVault`; key-source failures
become `keyUnavailable`; authentication and corrupt-store failures become
`corruptStore`; unsupported versions remain distinct; cancellation is
redacted; and remaining validation or availability failures use `failed`.

Saving maps to generic progress. A transient pre-commit save failure or
cancellation may be represented only alongside a fresh runtime observation.
Locked, locking, activating, and failed runtime states always discard supplied
private state.

## Private Projection

The five active hydrated record models map to dedicated presentation values.
Each receives an opaque `AtlasVaultPresentationID` whose underlying record ID
is replaced by a process-local, non-persistable hash token salted by an explicit
unlock-generation value and redacted in diagnostics. The future presentation
owner must create a new generation after every lock/activation boundary; a
missing generation fails closed by omitting private projection. Links between
private presentation records use the same opaque ID instead of exposing source
record IDs. Revisions, parent revisions, key IDs,
tombstones, encrypted envelopes, keys, and filesystem locations are not
projected. Saved-search query and filter fields map into a dedicated redacted,
non-persistable presentation value rather than retaining the existing request
model.

The projection remains private, in-memory, and optional. It is never encoded,
persisted, added to `AtlasPublicLocalSnapshot`, or retained by the stateless
adapter. Tombstones remain a persistence concern and are excluded from active
presentation collections.

## Diagnostics And Privacy

Status, snapshot, adapter, opaque ID, private aggregate, and all five private
presentation types provide fixed redacted descriptions. Public status carries
no private count, identifier, record type, path, credential detail, or
underlying error. The implementation emits no logs or analytics.

## Verification

Tests cover all statuses, exhaustive failure mapping, lock and failure
clearing, save and cancellation transitions, all five projections, tombstone
exclusion, redacted diagnostics, absence of key/envelope/path retention,
non-encoding presentation state, side-effect-free construction, public-cache
immutability, no file output, and source guards for prohibited dependencies.

## Deferred

- MainActor observable adapter
- SwiftUI views and property wrappers
- app-host and app-entry integration
- unlock input and prompts
- platform lifecycle subscription
- migration and plaintext cleanup
- cloud sync, recovery UX, onboarding, and key rotation
