# Job API Service

Local HTTP boundary for the Mac/iOS app. The service wraps `packages/jobagg`
instead of making native clients call the `jobagg` CLI directly.

## Run Locally

```bash
python -m pip install -e packages/jobagg
python -m pip install -e services/job-api
job-api
```

With uv, run the service project directly:

```bash
uv run --directory services/job-api job-api
```

Defaults:

- Database: `private/jobagg/output/all_jobs.sqlite3`
- Saved searches: `private/jobagg/saved_searches.json`
- Tracker: `private/jobagg/application_tracker.json`
- URL: `http://127.0.0.1:8765`

Override paths with `JOB_API_DB`, `JOB_API_SAVED_SEARCHES`, and
`JOB_API_TRACKER`.

## LAN Exposure

The service is loopback-only by default. Startup refuses `--host 0.0.0.0`
unless the operator sets `JOB_API_ALLOW_LAN=1` and configures either
`JOB_API_TOKEN` for the `X-Job-Api-Token` shared-secret header or
`JOB_API_UNIX_SOCKET` for Unix-socket mode.

`score_against` files are confined to `JOB_API_SCORING_ROOT`, which defaults
to the repository `strategies/` directory. Files must be regular files under
that root after symlink resolution and must not exceed
`JOB_API_SCORING_MAX_BYTES` (default: 2097152).

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
