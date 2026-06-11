# UN Job Application Helper

Monorepo for UN and international-organization job discovery, search, tracking,
and application-document generation.

## Layout

- `agents/apex/` - ApexStrategist skills, prompts, runtime profiles, and v2
  multi-agent coordination.
- `packages/jobagg/` - Python package and CLI for fetching, classifying,
  searching, and exporting job openings.
- `services/job-api/` - FastAPI service boundary for Mac/iOS clients.
- `apps/apple/` - placeholder for the shared SwiftUI Mac/iOS app.
- `contracts/` - API, runtime, and agent contracts.
- `docs/` - architecture docs, product specs, and legacy archives.
- `templates/` - setup templates for user context files and agent instructions.
- `tests/` - cross-package tests and repo hygiene checks.
- `private/` - ignored local inputs, outputs, databases, logs, caches, and keys.

See `docs/repo-structure.md` for migration details and data-boundary rules.

## Runtime Modes

Application document generation defaults to the low-cost single-agent profile:

```text
agent_mode: single
prompt: agents/apex/prompts/apex-single.prompt.md
```

The v2 ensemble remains available only when explicitly selected:

```text
agent_mode: ensemble_v2
manifest: agents/apex/topology/server_manifest.yaml
launcher: agents/apex/scripts/launch_v2_servers.sh
```

## Job Aggregator

```bash
python -m pip install -e packages/jobagg
jobagg init-db
jobagg sync-bundles
jobagg search --text "programme management"
```

By default, generated databases and exports are written under
`private/jobagg/output/`.

## Local API

```bash
python -m pip install -e packages/jobagg
python -m pip install -e services/job-api
job-api
```

The API defaults to `http://127.0.0.1:8765` and reads
`private/jobagg/output/all_jobs.sqlite3`.

## Private Data

Never commit personal histories, generated application documents, SQLite job
bundles, HAR files, API keys, or CCOG source artifacts. Put them under
`private/`.
