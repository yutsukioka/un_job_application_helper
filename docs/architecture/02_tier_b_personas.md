# Tier B — Persona definition and model assignment (deep dive)

> Companion to [00_consolidated_plan.md §"Tier B"](00_consolidated_plan.md).

## B1. Per-section default-lead table

The canonical table is in [00_consolidated_plan.md §B1](00_consolidated_plan.md).
A copy in a form C1/C2 prompts can quote verbatim is in the
[archived v2 template](../archive/legacy-multi-agent/version2/templates/per_section_default_leads.md).

### Override discipline

- Any non-lead agent may flag a defect on a section, but may not rewrite
  it. The flag must cite either:
  - a JD line number (e.g., `JD L23: "demonstrated experience supervising
    multidisciplinary teams"`), or
  - a strategy-report section ID (e.g., `phase1_2 §3 requirement #4`).
- Overrides without evidence are dropped by `qa-auditor` during consensus.

## B2. Disagreement log

`_discussion/disagreement_log.md` schema:

```markdown
## Disagreement <N> — <one-line summary>
- Section: <artifact + section>
- Lead's choice: <text>
- Alternative(s):
  - <agent>: <text> — rationale: <evidence cite>
- Resolution: <merged | both surfaced | deferred to user>
- Decided by: qa-auditor on <date>
```

## B3. The three new sections to splice into each authoring `.agent.md`

Concrete overlay text per agent is in the
[archived v2 overlays](../archive/legacy-multi-agent/version2/templates/agent_overlays/). This section
documents the **structure** of those overlays.

### `## Context Scoping`

A bullet list describing which sections of `application_context.md` and
which earlier-phase artifacts the agent reads vs. ignores. Forces
divergence by hiding context selectively.

### `## Voice & Emphasis`

A short paragraph describing register, sentence shape, and what the
agent prioritizes when given a writing choice.

### `## Advisor Mode`

A subsection that activates only when the agent is co-resident on a
server but is NOT the writer. Must specify, per phase:

| Phase | Allowed | Forbidden |
|---|---|---|
| IMPLEMENT | (silent) | any tool call |
| TEST | `send`, `broadcast` (with `ADVISOR_TO=<writer>` prefix) | `test-result`, file writes |
| DISCUSS | one structured `discuss`, `discuss-done` (without `--next-impl`) | `discuss-done --next-impl`, file writes |

`qa-auditor` gets a separate overlay
([archived qa-auditor overlay](../archive/legacy-multi-agent/version2/templates/agent_overlays/qa-auditor.overlay.md))
spelling out its **canonical tester** role on author servers (calls
`test-result` and `discuss-done --next-impl`) vs. its **writer** role on
consensus servers C1/C2.

## B4. Mixed-model assignment

See [00_consolidated_plan.md §B4](00_consolidated_plan.md) for the recommended mapping.

Operational notes:

- In VS Code Copilot: each agent runs in a separate chat tab with a
  specific model selected; co-resident advisors are additional chat
  tabs pointing at the same `agent_sync` server port.
- In Codex/Claude Code: declare the preferred model in each skill's
  `agents/apex/skills/<skill>/agents/openai.yaml` adapter.
- Mixed-model setups depend on D2 (structured handoff schema) for
  reliable cross-model parsing.
