# Local API Private-Endpoint Security

## Purpose

The local Job API serves public vacancy data and compatibility private state.
This policy prevents saved searches, tracker records, and document-generation
requests from becoming unauthenticated LAN services.

## Threat Boundary

The boundary covers direct local and LAN HTTP clients, accidental wildcard
binding, direct Uvicorn launch, malicious forwarding headers, malformed bearer
credentials, and unsafe browser origins. It does not provide TLS, device
identity, internet exposure, or authorization between users sharing one valid
token.

Application enforcement is mandatory. The validated launcher narrows process
configuration, but direct Uvicorn invocation cannot bypass private-route
admission in `create_app`.

## Access Modes

`ATLAS_PRIVATE_API_MODE` accepts exactly:

- `loopback`: the default. A private request is admitted only when its direct
  socket peer is IPv4 or IPv6 loopback. No bearer token is needed.
- `token`: every private request, including loopback requests, needs one exact
  `Authorization: Bearer ...` credential.
- `disabled`: every private route returns a fixed unavailable response.

There is no open mode. Proxy and forwarding headers do not affect peer
classification.

## Private Routes

The application classifies these route families as private:

- every `/api/saved-searches` route, including list, save, run, delete, and
  conditional delete;
- every `/api/tracker` route, including list, upsert, save-by-job, delete, and
  conditional delete;
- `/api/assistant/runs`, because it is a document-generation mutation boundary.
- `/api/sync/run`, because it is an administrative mutation boundary.
- the `score_against` option on `/api/search`, because it reads a caller-supplied
  local strategy file. Ordinary public search remains available.

The classification is prefix-based for saved-search and tracker routes so a new
subroute cannot silently omit the same admission check.

Reviewed public routes are health, ordinary vacancy search/detail, facets,
taxonomies, source summaries, update summaries, and sync summaries. Public
health preserves its response shape but does not disclose the configured
database path. Public database-unavailable errors are fixed and path-free.

## Bearer Token

Token mode loads exactly one of:

- `ATLAS_PRIVATE_API_TOKEN`;
- `ATLAS_PRIVATE_API_TOKEN_FILE`.

Both sources together fail startup. A token is ASCII, uses bearer-token-safe
characters, and is at least 32 bytes long. Operators must generate at least 32
bytes of cryptographic entropy. Verification uses `secrets.compare_digest`.
Responses and application code never log the configured or supplied token. A
query-string value is never accepted as authentication.

The token-file path must name one regular, bounded file. On POSIX, group or
other permissions are rejected. The file must contain only the token: embedded
NUL, whitespace, multiple lines, surrounding text, and oversized content fail
startup. Store it outside the repository and delete it after use.

## LAN Binding

`python -m job_api.launcher` defaults to `127.0.0.1:8765`. A non-loopback host
is accepted only when all of the following hold:

- `ATLAS_ALLOW_LAN=1`;
- private mode is `token`;
- one valid token source is configured.

The launcher disables Uvicorn proxy-header trust and access logging so a caller
cannot place credential text in a logged URL or query string. LAN operation
should be used only on a network the operator controls. The bearer token is sent
over HTTP, so an untrusted or monitored network is outside this phase's security
claim.

## CORS

Cross-origin browser access is absent by default. `ATLAS_CORS_ORIGINS` accepts
an exact comma-separated list of HTTP or HTTPS origins. Empty entries,
duplicates, user information, paths, queries, fragments, and wildcard origins
are rejected. Credentials are disabled; only the Authorization and Content-Type
request headers are admitted.

## Failure Behavior

Denied token or peer requests return one fixed 403 response. Disabled mode
returns one fixed 503 response. Configuration errors contain no token, token
file path, request path, or supplied private value. A bad private-access
configuration prevents application startup.

## Operational Checklist

1. Prefer default loopback mode.
2. Generate a dedicated high-entropy token for each temporary LAN session.
3. Store token files outside the repository with owner-only permissions.
4. Set LAN opt-in and token mode explicitly.
5. Configure exact CORS origins only when browser access is required.
6. Stop the launcher and delete the token file when testing finishes.
7. Rotate immediately after suspected disclosure.

## Deferred Work

This package does not add Apple token UI, TLS termination, trusted reverse
proxies, remote account authentication, cloud access, or device pairing.
