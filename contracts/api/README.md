# Job API Contract

The Mac/iOS app talks to `services/job-api`; it must not scrape ATS sites or
read SQLite bundles directly.

## Core Endpoints

- `GET /api/health`
- `POST /api/search`
- `GET /api/jobs/{job_key}`
- `GET /api/facets`
- `POST /api/facets`
- `GET /api/taxonomies`
- `GET /api/saved-searches`
- `POST /api/saved-searches`
- `POST /api/saved-searches/{name}/run`
- `DELETE /api/saved-searches/{name}`
- `GET /api/updates`
- `POST /api/sync/run`
- `GET /api/sync/runs`
- `GET /api/tracker`
- `POST /api/tracker`
- `POST /api/tracker/jobs/{job_key}`
- `DELETE /api/tracker/{record_id}`
- `POST /api/assistant/runs`

## Shared Types

Canonical model definitions live in `services/job-api/job_api/models.py` and
are mirrored here for app/client planning:

- `VacancySearchRequest`
- `VacancySearchResponse`
- `JobDetail`
- `SavedSearch`
- `ApplicationRecord`
- `AssistantRunRequest`
- `AssistantRunResult`
- `LLMProviderConfig`

For the current MVP, the service owns persistence. Native clients may cache
summaries for display, but the server remains the source of truth for jobs,
saved searches, tracker records, strategy scores, and assistant run artifacts.

Phase 2 changes the target design for private user state: saved searches,
tracker records, future notes, profile snippets, and draft metadata should move
behind encrypted AtlasVault records instead of treating plaintext local JSON as
a sync source. See `docs/architecture/phase2_local_encrypted_vault_integration.md`.
