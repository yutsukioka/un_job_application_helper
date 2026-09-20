# Phase 2D-22 AtlasVault Local Store Merger

## Purpose

Phase 2D-22 adds a test-only encrypted local-store merger after the Phase 2D-21
design. It proves that encrypted record envelopes from the saver can be merged
into an encrypted local-store envelope before any runtime app wiring.

## Scope

Included:

- encrypted envelope merge behavior;
- temp-root coordinator save tests;
- non-sensitive metadata updates;
- source guards for runtime boundary violations.

Excluded:

- record decryption or plaintext payload inspection;
- SwiftUI, `SearchViewModel`, or `AtlasLocalCache` integration;
- public snapshot mutation;
- migration execution;
- cloud sync.

## Merger Behavior

The merger:

1. validates existing store record IDs before building a map;
2. rejects duplicate incoming record IDs;
3. preserves untouched encrypted records;
4. inserts new encrypted records with new IDs;
5. replaces existing records by ID when parent revision matches;
6. replaces an active record with an encrypted tombstone envelope;
7. rejects stale parent revisions without inspecting payload content.

Tombstones are treated as encrypted record envelopes with `deleted == true`.

## Metadata

The merger preserves store format, store ID, creation timestamp, and vault
metadata. It updates only the local-store `updatedAt` value through an injected
clock so tests can stay deterministic.

Store metadata must not contain saved-search names, job keys, notes, snippets,
draft references, private record counts, or plaintext record type strings.

## Coordinator Save Tests

Coordinator save tests cover:

- saver output flowing into the merger and then the persistence coordinator;
- temp roots only;
- explicit overwrite behavior;
- preservation of untouched encrypted records;
- stale and duplicate failures before replacement writes;
- serialized local-store JSON without fake private sentinels or plaintext record
  type strings.

The coordinator test seam remains below app runtime integration.

## Error Behavior

The first implementation uses non-sensitive errors for:

- duplicate existing record IDs;
- duplicate incoming record IDs;
- stale parent revision;
- unsupported encrypted record version;
- invalid record metadata;
- invalid store metadata;
- conflict detection for create-with-existing-ID cases.

Malformed stores from disk remain handled by the existing local-store reader.
Write failures remain surfaced by the persistence coordinator.

## Deferred

- atomic writer;
- runtime app integration;
- conflict UI;
- migration execution;
- cloud sync;
- device onboarding;
- key rotation;
- cleanup of old plaintext snapshots.
