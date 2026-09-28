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

## Security

The local API is loopback-only by default. LAN binding requires
`ATLAS_API_HOST=0.0.0.0`, `ATLAS_ALLOW_LAN=1`, `ATLAS_PRIVATE_API_MODE=token`,
and exactly one external token source (`ATLAS_PRIVATE_API_TOKEN_FILE` or
`ATLAS_PRIVATE_API_TOKEN`). Start with `python -m job_api.launcher` or `job-api`.
Private routes require `Authorization: Bearer <token>`; public job reads remain
unauthenticated. See the [deployment decision tree](docs/security/deployment.md),
[hardening audit](docs/security/hardening_audit.md), and
[Job API changelog](services/job-api/CHANGELOG.md).

Security hardening index:

| Criterion | Mitigation |
| --- | --- |
| [A1](docs/security/hardening_audit.md) | Default bind remains loopback; LAN bind requires explicit opt-in and private token mode. |
| [A2](docs/security/hardening_audit.md) | Private token-mode requests require an `Authorization: Bearer` credential with constant-time comparison. |
| [A3](docs/security/hardening_audit.md) | `score_against` paths are confined by `JOB_API_STRATEGY_ROOT` and a fixed 1 MiB read cap. |
| [A4](docs/security/hardening_audit.md) | Encrypted-sync wire contract blocks raw passphrases and unwrapped vault keys; see [contract](contracts/api/encrypted_sync.md). |
| [B1](docs/security/hardening_audit.md) | Job API request models bound paging, text, notes, and list fields. |
| [B2](docs/security/hardening_audit.md) | Tracker and saved-search writes use locked, same-directory atomic replacement. |
| [B3](docs/security/hardening_audit.md) | Crawler fetches go through the SSRF-safe HTTP wrapper and organization host allowlist. |
| [B4](docs/security/hardening_audit.md) | Cookie assist refuses cross-domain cookies and logs redacted warnings. |
| [B5](docs/security/hardening_audit.md) | Vault imports cap KDF parameters and import size before allocation-heavy work. |
| [C1](docs/security/hardening_audit.md) | Compressed responses are streamed with hard decompressed-byte caps. |
| [C2](docs/security/hardening_audit.md) | Swift and Flutter URL openers allow only `http`, `https`, and `mailto`. |
| [C3](docs/security/hardening_audit.md) | `jobagg bundle verify` validates bundle size, SQLite integrity, and JSON schema before reads. |
| [C4](docs/security/hardening_audit.md) | Job API responses include `apply_url_trust` and `source_url_trust` annotations. |
| [D1](docs/security/hardening_audit.md) | Health and error responses scrub absolute filesystem paths. |
| [D2](docs/security/hardening_audit.md) | Saved-search names reject path separators, controls, empty names, and overlong names. |
| [D3](docs/security/hardening_audit.md) | CI pins actions and runs security gates in [.github/workflows/ci.yml](.github/workflows/ci.yml). |
| [D4](docs/security/hardening_audit.md) | Each organization URL host must have an explicit robots stance in [robots_policy.yaml](packages/jobagg/config/robots_policy.yaml). |

## Private Data

Never commit personal histories, generated application documents, SQLite job
bundles, HAR files, API keys, or CCOG source artifacts. Put them under
`private/`.
