# Repository Structure

This repository is now a root-level product monorepo.

```text
agents/apex/       ApexStrategist skills, prompts, runtime profiles, v2 topology
packages/jobagg/   Python job aggregation, classification, search, and CLI package
services/job-api/  Local/remote HTTP API for native clients
apps/apple/        Shared SwiftUI Mac/iOS app workspace placeholder
contracts/         API, runtime, and agent contract documents
docs/              Architecture notes, product specs, and legacy archives
templates/         User setup templates
tests/             Cross-package integration and hygiene tests
private/           Ignored local inputs, outputs, databases, logs, keys, caches
```

## Runtime Modes

`agents/apex/runtime_profiles.yaml` makes `single` the default document
generation mode. This mode uses `agents/apex/prompts/apex-single.prompt.md` and
keeps costs low by collapsing expert lenses into one agent.

`ensemble_v2` remains available for explicit higher-budget runs. It uses
`agents/apex/topology/server_manifest.yaml`, `agents/apex/prompts/v2/`, and
`agents/apex/scripts/launch_v2_servers.sh`.

## Private Data

All personal and generated artifacts belong under `private/`, which is ignored:

- `private/inputs/`
- `private/output/`
- `private/tmp/`
- `private/jobagg/output/`
- `private/jobagg/application_tracker.json`
- `private/jobagg/saved_searches.json`

Do not reintroduce generated SQLite bundles, application outputs, user histories,
API keys, HAR files, or CCOG source artifacts into tracked source directories.

## Legacy Branches

The previous branch tips were tagged before migration:

- `legacy/master-2026-06-11`
- `legacy/develop-2026-06-11`
- `legacy/multi-agent-2026-06-11`

The current unification branch is `repo-unification`, based on `job_aggregator`.
