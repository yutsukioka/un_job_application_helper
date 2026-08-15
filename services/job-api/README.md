# Job API Service

Local HTTP boundary for the Mac/iOS app. The service wraps `packages/jobagg`
instead of making native clients call the `jobagg` CLI directly.

## Run Locally

```bash
python -m pip install -e packages/jobagg
python -m pip install -e services/job-api
python -m job_api.launcher
```

Defaults:

- Database: `private/jobagg/output/all_jobs.sqlite3`
- Saved searches: `private/jobagg/saved_searches.json`
- Tracker: `private/jobagg/application_tracker.json`
- URL: `http://127.0.0.1:8765`
- Private access: direct loopback peers only

Override paths with `JOB_API_DB`, `JOB_API_SAVED_SEARCHES`, and
`JOB_API_TRACKER`.

The validated launcher supports three private-access modes through
`ATLAS_PRIVATE_API_MODE`: `loopback` (default), `token`, and `disabled`. There
is no unauthenticated open mode. Saved-search, tracker, and assistant-run routes
are private. The sync command and search requests that read a local
`score_against` strategy file are also private. Public health, ordinary job
search/detail, facets, taxonomies, source/update summaries, and sync status
remain available.

### Token-protected LAN launch

LAN binding is opt-in and exposes public routes to the local network. Use an
external permission-restricted token file and an explicit token mode:

```bash
umask 077
TOKEN_FILE="$(mktemp "${TMPDIR:-/tmp}/atlas-private-api-token.XXXXXX")"
openssl rand -base64 48 | tr -d '\n' > "$TOKEN_FILE"
ATLAS_API_HOST=0.0.0.0 \
ATLAS_ALLOW_LAN=1 \
ATLAS_PRIVATE_API_MODE=token \
ATLAS_PRIVATE_API_TOKEN_FILE="$TOKEN_FILE" \
python -m job_api.launcher
```

Delete the external token file after stopping the service. The launcher rejects
a LAN bind unless LAN opt-in, token mode, and one valid token source are all
present. Direct Uvicorn invocation is unsupported, but the application still
enforces private-route admission if the launcher is bypassed. Forwarding headers
do not influence loopback admission.

Cross-origin browser access is disabled by default. Set
`ATLAS_CORS_ORIGINS` to an exact comma-separated origin list when required.
Wildcard origins are rejected. See
`docs/security/local_api_private_endpoint_security.md` for the operational
policy.

## Endpoint Status

Implemented for MVP:

- `GET /api/health`
- `POST /api/search`
- `GET /api/jobs/{job_key}`
- `GET /api/facets`
- `POST /api/facets`
- `GET /api/taxonomies`
- saved-search CRUD/run
- `GET /api/updates`
- `GET /api/sync/runs`
- tracker list/upsert/save/delete

Contracted stubs:

- `POST /api/sync/run`
- `POST /api/assistant/runs`
