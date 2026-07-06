# ADR Security 0001: Bundle Verify Size Cap

## Status

Accepted

## Context

C3 requires `jobagg bundle verify <path>` to reject oversized bundle imports before
opening or parsing any bundle file. The goal did not specify a default cap value.

## Decision

Use a default cap of 256 MiB for the total size of files covered by one verifier
run. Operators can override it with `--max-bytes` or `JOBAGG_BUNDLE_MAX_BYTES`.

## Consequences

The default is above normal per-source bundle size while still bounding accidental
or hostile imports. Very large legitimate bundles require an explicit operator
override.
