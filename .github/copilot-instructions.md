# Copilot Instructions - un_job_application_helper

## Repository Layout

This repository is a root-level product monorepo. Run Git commands from the
workspace root unless a command explicitly targets a subpackage.

```text
un_job_application_helper/
├── .github/                 # Copilot agents, hooks, and CI workflows
├── agents/apex/             # ApexStrategist skills, prompts, topology, scripts
├── packages/jobagg/         # Python job aggregation/search package and CLI
├── services/job-api/        # HTTP API boundary over jobagg
├── apps/apple/              # Shared SwiftUI Mac/iOS app placeholder
├── contracts/               # API, runtime, and agent contracts
├── docs/                    # Architecture docs, runbooks, archives
├── templates/               # Public setup templates
├── tests/                   # Cross-package tests and hygiene checks
├── private/                 # Ignored personal inputs, outputs, DBs, logs, keys
└── AGENTS.md                # Tracked repo instructions
```

## Key Rules

1. **Git root is the workspace root.**
   - Use `cd <workspace>` for normal Git work.
   - The active unification branch is `repo-unification`.
   - `master`, `develop`, `multi-agent`, and `job_aggregator` are legacy
     snapshots until the unified branch is promoted.

2. **Skills live under `agents/apex/skills/`.**
   - `SKILL.md` is the source of truth for each skill.
   - Per-skill `agents/openai.yaml` files are runtime metadata only and must
     not add requirements absent from `SKILL.md`.
   - Prompts live under `agents/apex/prompts/`.

3. **Privacy boundary is `private/`.**
   - Do not stage personal histories, generated application outputs, SQLite
     bundles, HAR files, logs, keys, or local fetch artifacts.
   - Before committing, check staged files for PII and generated data.
   - Do not commit the full CCOG database at
     `agents/apex/skills/apex-ccog-resolver/resource/ccog_reference_full.md`.

4. **Runtime modes are explicit.**
   - Default document generation mode is `single` via
     `agents/apex/prompts/apex-single.prompt.md`.
   - Multi-agent v2 is optional and must be selected through runtime config
     before using `agents/apex/prompts/v2/` and
     `agents/apex/topology/server_manifest.yaml`.

5. **Metadata review hygiene.**
   - Exclude `.git`, virtualenvs, `agents/apex/agent_sync/`, caches, logs, and
     `private/` from broad scans unless directly required.
   - Skip large resource files unless the task specifically needs them.

## Git Workflow

```bash
cd <workspace>
git switch repo-unification
git status
git add -A
git status
git commit -m "descriptive message"
```

## What Not To Do

- Do not stage or commit files from `private/`, `inputs/`, `output/`, `tmp/`,
  `logs/`, or `bak/`.
- Do not use `git push --force` without explicit user confirmation.
- Do not add operational requirements to `agents/openai.yaml` that are absent
  from the corresponding `SKILL.md`.
