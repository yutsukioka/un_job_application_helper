# Jobagg and local Job API threat model

## Assets and boundaries

Public vacancy records and listing counts may be served over an explicitly
enabled LAN listener. Tracker entries, saved searches, local strategy files,
private application material, bearer credentials, and worker filesystem layout
are private. The production database and publication marker form a shared
consistency boundary; a successful listing observation does not certify full
job text or attachments.

Crawler HTML, JSON, URLs, DNS answers, redirects, compressed bodies, and browser
scripts are untrusted. Provider configuration controls allowed source hosts.
The local operator controls deployment settings and external token sources.
The OS account and process configuration are trusted; this is not a sandbox
against arbitrary code already running as that account.

## Controls

- Python HTTP and the native Chromium CONNECT proxy validate destination
  addresses and pin numeric connections. Private, unspecified, reserved and
  multicast addresses are rejected, including mapped IPv4 addresses.
- TLS verifies certificates and hostnames by default. Source-specific TLS
  overrides are explicit exceptions, not equivalent assurances.
- Browser tasks use fresh anonymous sessions, per-hop admission and durable
  capture; child frames, workers and unreviewed methods are constrained.
- Private API routes enforce direct-loopback policy or bearer-token mode before
  publication checks and route execution. Forwarded headers do not grant access.
- Non-loopback launch requires explicit LAN opt-in and token mode. Public routes
  remain public; token-mode HTTP does not encrypt traffic.
- Publication gating covers mounted and unmounted database routes and rechecks
  the generation before releasing buffered responses.
- Strategy reads are root-confined, bounded, and reject symlinks/non-regular
  files. Attachment reads are job-scoped and verify digests and sizes.
- Public inventory proof responses project typed facts and capture digests;
  internal paths, free-text errors and unrecognized fields are omitted.
- Search pages, response bodies, decompression, bundle imports and user-input
  fields have explicit resource limits. Apple clients aggregate bounded pages.
- Local JSON stores use locks and atomic replacement. Persistent lock sidecars
  preserve a stable lock identity across competing processes.

## Residual risks and follow-ups

CI currently resolves Python packages and tools at install time rather than
consuming one frozen environment. Package lockfiles do not yet make all security
and browser gates reproducible. Vulnerability-audit results apply to the exact
environment and database snapshot used for that run; they are not a permanent
claim that every developer environment is vulnerability-free.

Public availability remains subject to operational rate limits and deployment
network controls. URL-trust labels compare hostnames only; they do not establish
scheme/port equivalence or replace transport checks. Publication and proxy
failure diagnostics can be improved with redacted operator-facing reason codes;
public error bodies deliberately avoid internal paths and exception strings.

## Review evidence

See PR #115's [first summary](https://github.com/yutsukioka/un_job_application_helper/pull/115#issuecomment-5757464454)
and [second summary](https://github.com/yutsukioka/un_job_application_helper/pull/115#issuecomment-5757706288).
These are scoped reviews, not an exhaustive certification of all provider or
platform code. The historical [hardening audit](hardening_audit.md) preserves
its original per-commit results and reviewer-note placeholders.
