# Secure Local API Launch

## Purpose

This package makes local-only behavior the default and establishes a validated,
token-protected path for deliberate LAN testing without relying on launcher
discipline for private-route security.

## Components

`job_api.auth` owns strict token loading and constant-time verification.
`job_api.private_access` owns immutable startup policy, CORS parsing, direct-peer
classification, private-route classification, and ASGI enforcement.
`job_api.launcher` owns bind validation and invokes Uvicorn with proxy headers
disabled. `job_api.app.create_app` installs the access middleware for every
launch path.

## Startup Flow

1. Load the private mode, token source, and CORS origins.
2. Fail startup on an unknown mode, ambiguous token source, malformed token, or
   unsafe CORS value.
3. For the validated launcher, parse host and port.
4. Require explicit LAN opt-in and token mode for every non-loopback bind.
5. Start Uvicorn without proxy-header trust or access logging.
6. Reconstruct the same immutable policy when Uvicorn imports the application.

Application construction reads configuration but performs no network bind and
does not emit credential material.

## Request Admission

The ASGI middleware runs before request-body parsing or route execution. Public
paths continue directly. Private paths are handled as follows:

- loopback mode parses only `scope["client"]`; forwarded headers are ignored;
- token mode requires exactly one Authorization header and an exact bearer
  token match;
- disabled mode rejects before route validation or store access.

IPv4 loopback, IPv6 loopback, and IPv4-mapped IPv6 loopback are admitted in
loopback mode. Missing, malformed, hostname, and non-loopback peer values are
denied.

## Route Classification

Private route families:

- `/api/saved-searches` and every descendant;
- `/api/tracker` and every descendant;
- `/api/assistant/runs`;
- `/api/sync/run`;
- `/api/search` requests that supply `score_against`, while ordinary search
  remains public.

Public routes:

- `/api/health`;
- `/api/search` without a local strategy-file request;
- `/api/job-detail`, `/api/jobs/by-key`, and `/api/jobs/...`;
- `/api/facets` and `/api/taxonomies`;
- `/api/updates` and `/api/sources`;
- `/api/sync/runs`.

The public classification exposes vacancy and service state, not user private
records. The health payload and database-unavailable errors do not return a
local database path.

## Browser Boundary

No CORS middleware is installed without explicit origins. With exact origins,
CORS remains outside the private admission middleware so a valid preflight can
complete; the actual private request still requires its bearer credential.
Wildcard reflection and credentialed wildcard requests are impossible.

## Launch Profiles

Default development uses `python -m job_api.launcher` and binds
`127.0.0.1:8765`. A deliberate LAN session sets `ATLAS_API_HOST`,
`ATLAS_ALLOW_LAN`, token mode, and one external token source. Disabled mode is
not accepted for LAN binding because it cannot claim usable private
functionality.

The existing `job-api` console entry delegates to the validated launcher. Raw
Uvicorn remains unsupported, but private routes still enforce the application
policy if raw Uvicorn is used.

## Failure and Privacy Properties

Configuration errors are fixed and redacted. Request denials do not echo the
token, private content, peer address, or token-file path. The validated launcher
disables access logs, and query-string values are never accepted as credentials.

## Verification

Deterministic tests cover all modes, all private route families, forwarded
header bypass attempts, malformed secret sources, exact CORS, launcher bind
validation, and direct application enforcement. A live non-loopback interface
test uses fake state and a temporary owner-only token file.

## Limitations

Token-mode HTTP does not encrypt LAN traffic and is not suitable for hostile
networks. No trusted-proxy mode exists. The Apple app does not gain token UI in
this package. Pairing, device identity, and remote services remain separate
phases.
