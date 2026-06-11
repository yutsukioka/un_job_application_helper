# ApexStrategist Agent Runtime

This directory contains the application-document generation agent system.

## Layout

- `skills/` - canonical `SKILL.md` contracts and runtime adapters.
- `prompts/` - single-agent, v1 multi-agent, and v2 ensemble prompts.
- `prompts/apex-single.prompt.md` - default low-cost runtime prompt.
- `topology/server_manifest.yaml` - optional v2 ensemble topology.
- `scripts/launch_v2_servers.sh` - optional local `agent_sync` server launcher.
- `.github/agents/` - role/persona files used by Copilot-style agent runtimes.
- `runtime_profiles.yaml` - explicit `single`, `ensemble_v2`, and future
  `auto_budget` runtime profiles.

## Runtime Defaults

Use `single` unless the user explicitly selects a multi-agent run or provides a
budget/runtime policy that enables `ensemble_v2`.

Personal inputs and generated documents live outside this source tree:

- `private/inputs/application_context.md`
- `private/output/generated_documents/`
- `private/tmp/agent_sync/`

`agent_sync` itself is a local runtime dependency and is ignored at
`agents/apex/agent_sync/`.

## Contracts

For repository-level structure, see `docs/repo-structure.md`.
For runtime config shape, see `contracts/agents/runtime_config.schema.json`.
