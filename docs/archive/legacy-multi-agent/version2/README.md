# multi-agent-version2

This folder is the **design specification** for v2 of the ApexStrategist
multi-agent workflow. It is **documentation only** — no skills, agents, or
runtime code are activated by this folder.

## Status

- Version: 2.0.0 (design spec)
- Implementation: **not yet applied** to `.agents/`. Applying the design
  is a separate, later task that edits `.agents/.github/agents/*.agent.md`,
  `.agents/application_context.template.md`, and the `.agents/README.md`.
- This folder lives at the workspace root, **outside `.agents/`**, and is
  **not part of the `.agents` git repository**. Same posture as `inputs/`,
  `output/`, `tmp/`, and `AGENTS.md`.

## What this design changes vs. v1

v1 has the **personas** of a multi-agent system but the **runtime** of a
linear single-author pipeline. v2 moves runtime behaviour to match the
design intent, without forking the upstream `agent_sync` runtime, by:

1. Running a two-fold parallel ensemble (Phase 1–7 strategy fold, then
   Phase 8 document fold), each followed by a consensus round.
2. Co-resident advisors on every author server: one writer + two advisors +
   `qa-auditor` (canonical tester), so each draft is cross-pollinated
   **within the same author-server review loop, before consensus**.
3. Honouring stock `agent_sync` semantics exactly: single implementer per
   server, IMPLEMENT/TEST/DISCUSS/SHUTDOWN phases, destructive `listen`,
   no retrospective send/broadcast retrieval.

## Reading order

1. [README.md](README.md) — this file
2. [spec/00_consolidated_plan.md](spec/00_consolidated_plan.md) — the canonical, fully-corrected plan
3. [spec/01_tier_a_ensemble_workflow.md](spec/01_tier_a_ensemble_workflow.md) through [spec/07_tier_g_safety_budgets.md](spec/07_tier_g_safety_budgets.md) — per-tier deep dives
4. [spec/99_rollout_and_parked.md](spec/99_rollout_and_parked.md) — rollout order and intentionally-parked items
5. [topology/server_manifest.yaml](topology/server_manifest.yaml) — the 10-server topology
6. [topology/runbook.md](topology/runbook.md) — operational sequence
7. [templates/](templates/) — paste-ready snippets for the future implementation task
8. [scripts/check_scope.py](scripts/check_scope.py) — stub for the optional scope verifier
9. [CHANGELOG.md](CHANGELOG.md) — diff vs. the original v1 plan

## Mapping to the existing repo (overlay model)

| v2 spec file | Eventually applied to (later task) |
|---|---|
| `templates/agent_overlays/screening-lead.overlay.md` | `.agents/.github/agents/screening-lead.agent.md` |
| `templates/agent_overlays/technical-lead.overlay.md` | `.agents/.github/agents/technical-lead.agent.md` |
| `templates/agent_overlays/ats-format-lead.overlay.md` | `.agents/.github/agents/ats-format-lead.agent.md` |
| `templates/agent_overlays/qa-auditor.overlay.md` | `.agents/.github/agents/qa-auditor.agent.md` |
| `templates/application_context.fragments.md` | `.agents/application_context.template.md` and `inputs/application_context.md` |
| `topology/server_manifest.yaml` | new operational artifact (no v1 equivalent) |
| `scripts/check_scope.py` | new optional verifier (no v1 equivalent) |

The overlays are **additive splice blueprints**, not full replacements for
the existing `.agent.md` files.

## What is NOT in this version

See [spec/99_rollout_and_parked.md](spec/99_rollout_and_parked.md) §"Intentionally parked".
Notably:

- Bundled coordination shim (use stock `agent_sync`)
- Forking `agent_sync` to allow multiple writers per server
- Numeric tournament scoring for QA
- Splitting `ats-format-lead` by target system
- **Pre-generation red-team pass (formerly A3) — deleted; pre-Phase-8 gap
  review is owned by `apex-user-feedback-revision` (Phase 7.5).**

## Authority

When this spec and the live `.agents/` files disagree, the live files win
until the implementation task is run. This folder is a proposal, not a
contract.

For the runtime semantics of `agent_sync` itself, the authority is
`.agents/skills/apex-agent-sync-protocol/SKILL.md` and the upstream
`server_v6.py`.
