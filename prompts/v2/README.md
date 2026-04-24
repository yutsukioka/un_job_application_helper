# v2 multi-agent prompts

Per-server, per-role prompt files for the v2 ensemble topology.

- Topology: [`.agents/topology/server_manifest.yaml`](../../topology/server_manifest.yaml)
- Design spec: [`.agents/`](../../../.agents/)
- Runbook: [`.agents/topology/runbook.md`](../../../.agents/topology/runbook.md)

## When to use these prompts

Only when `## RUN_MODE` in `inputs/application_context.md` declares
non-empty `ENSEMBLE_PHASE_1_7` or `ENSEMBLE_PHASE_8`. Otherwise use the
v1 prompts in the parent directory (single-agent linear mode).

## Phase 8 ensemble scope

Ensemble v2 generation currently covers **Phase 8 Options 1-4 only**
(Admin Profile, CV, Cover Letter, Qualification Answers). For
Options 5-8 (RA Split, Competency Mapping, Motivation Statement,
DRA Split), fall back to v1 single-agent generation skills.

## File naming

```
<server>-<agent>.<role>.prompt.md
```

- `<server>` ∈ {P0a, P0b, S1, S2, S3, C1, D1, D2, D3, C2}
- `<agent>`  ∈ {screening-lead, technical-lead, ats-format-lead, qa-auditor}
- `<role>`   ∈ {writer, advisor, canonical-tester}

## Writer prompts (10 explicit files)

| File | Server | Port | Writer |
|---|---|---|---|
| `P0a-screening-lead.writer.prompt.md` | P0a | 9800 | screening-lead |
| `P0b-technical-lead.writer.prompt.md` | P0b | 9801 | technical-lead |
| `S1-screening-lead.writer.prompt.md` | S1 | 9811 | screening-lead |
| `S2-technical-lead.writer.prompt.md` | S2 | 9812 | technical-lead |
| `S3-ats-format-lead.writer.prompt.md` | S3 | 9813 | ats-format-lead |
| `C1-qa-auditor.writer.prompt.md` | C1 | 9820 | qa-auditor |
| `D1-screening-lead.writer.prompt.md` | D1 | 9831 | screening-lead |
| `D2-technical-lead.writer.prompt.md` | D2 | 9832 | technical-lead |
| `D3-ats-format-lead.writer.prompt.md` | D3 | 9833 | ats-format-lead |
| `C2-qa-auditor.writer.prompt.md` | C2 | 9840 | qa-auditor |

## Role templates (use with placeholders)

Author servers also need an advisor and a canonical-tester instance. Use:

- `_advisor.template.prompt.md` — substitute `<AGENT_NAME>`, `<SERVER>`, `<PORT>`, `<WRITER_NAME>`.
- `_canonical-tester.template.prompt.md` — substitute `<SERVER>`, `<PORT>`, `<WRITER_NAME>`.

Per server you launch the writer prompt + 2 advisor instances (one per
non-writer authoring agent) + 1 canonical-tester instance (qa-auditor).

Consensus servers C1 and C2 do NOT use a canonical-tester instance —
the writer is qa-auditor. They use 3 advisor instances.

Join order is operationally significant. Stock `server_v6.py` assigns the
first joining agent as implementer. Start the writer prompt first, then
start advisor and canonical-tester prompts only after `status --port <PORT>`
shows `implementer` equal to that server's writer.

## Cross-references

All prompts assume:

- `apex-guardrails` and `apex-agent-sync-protocol` skills are loaded.
- The agent's `.agent.md` file (in `.agents/.github/agents/`) provides
  the v2 sections (Context Scoping / Voice & Emphasis / Advisor Mode /
  Writer Mode, or Role 1 / Role 2 for qa-auditor).
- `<JOB_SLUG>` is set per launch.

These prompts intentionally stay short — they encode only what is
server-specific. Persona behaviour lives in the `.agent.md` files.
