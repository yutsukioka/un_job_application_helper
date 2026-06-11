# Multi-Agent Version 1

This folder is an isolated planning and staging workspace for the first
multi-agent architecture revision of ApexStrategist.

It is intentionally separate from the live `.agents/` runtime. Nothing in this
folder modifies, replaces, or backports into `.agents/` during this revision.

## Purpose

- Consolidate the current multi-agent design discussion into one canonical v1
  specification.
- Define the runbooks, interfaces, templates, and deferred items needed for a
  later implementation pass.
- Preserve compatibility assumptions with stock `agent_sync` without copying or
  forking the runtime here.

## What This Folder Contains

- `canonical/multi-agent-v1-spec.md`
  The only canonical v1 plan document.
- `runbooks/`
  Operational sequences for launch order and the strategy/document folds.
- `specs/`
  Decision-level interface and behavior contracts.
- `templates/`
  Reusable templates for manifests and exported notes.
- `validators/README.md`
  Future validator scope only. No validators are implemented in v1.

## Source Consolidation

This workspace consolidates the decisions from the two current draft documents
under `.agents/`:

- `.agents/# ApexStrategist — Consolidated Multi-Ag.md`
- `.agents/You're right — I missed the agent_sync c.md`

Their contents are merged into the canonical spec and supporting docs here.
They are not copied verbatim into `multi-agent-version1/`.

## What This Folder Does Not Do

- It does not fork `agent_sync`.
- It does not create a code fork of `.agents/`.
- It does not define a cutover or backport plan into `.agents/`.
- It does not implement validators, hooks, or runtime wrappers.

## Recommended Reading Order

1. `canonical/multi-agent-v1-spec.md`
2. `runbooks/launch-order.md`
3. `runbooks/strategy-fold.md`
4. `runbooks/document-fold.md`
5. `specs/server-topology.md`
6. `specs/phase-rules.md`
7. `specs/advisor-export-contract.md`
8. `specs/run-mode-and-budgets.md`
9. `specs/deferred-items.md`
