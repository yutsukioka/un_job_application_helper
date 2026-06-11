# Job API Service

Local HTTP boundary for the Mac/iOS app. The service wraps `packages/jobagg`
instead of making native clients call the `jobagg` CLI directly.

## Run Locally

```bash
python -m pip install -e packages/jobagg
python -m pip install -e services/job-api
job-api
```

Defaults:

- Database: `private/jobagg/output/all_jobs.sqlite3`
- Saved searches: `private/jobagg/saved_searches.json`
- Tracker: `private/jobagg/application_tracker.json`
- URL: `http://127.0.0.1:8765`

Override paths with `JOB_API_DB`, `JOB_API_SAVED_SEARCHES`, and
`JOB_API_TRACKER`.

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
