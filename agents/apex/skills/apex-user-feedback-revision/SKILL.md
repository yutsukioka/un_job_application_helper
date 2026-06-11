---
name: apex-user-feedback-revision
description: >-
  Create a structured feedback loop after `apex-orchestrator-report` by extracting
  evidence gaps, mitigation strategies, and metrics/specifics needed from the
  Phase 1-7 strategy report, then evaluating user ad-hoc additions or edited
  strategy-report content for grounding, consistency, and integration readiness.
  Produces a review artifact and, only when explicitly authorized, a controlled
  integration patch for Phase 8 regeneration.
---

# apex-user-feedback-revision

## Purpose

This skill introduces an explicit evidence-review gate between the Phase 1-7
strategy report and Phase 8 document generation.

It prevents the system from treating user additions as a "golden record" by
separating three tasks:

1. Surface what is still missing from the strategy report.
2. Evaluate user-supplied updates, whether typed in chat or added directly
   into a revised `phase1_7_strategy_report*.md`.
3. Produce a controlled patch file only when the user explicitly wants those
   updates applied to downstream document generation.

This skill is a review and validation utility. It does not regenerate Phase 8
documents by itself.

## Shared definitions

Apply the expert lens, collaboration rules, guardrails, quality loop protocol,
and guiding principles defined in `apex-guardrails`.

Default format profile: `strategy_markdown`.

## When to use

Use this skill in either of these situations:

- Immediately after `apex-orchestrator-report`, before Phase 8 generation, to
  surface missing proof and metrics the user can improve.
- When the user provides ad-hoc additions in chat and wants the system to
  evaluate them before regenerating outputs.
- When the user edits `phase1_7_strategy_report*.md` and wants the system to
  assess those edits before Phase 8 regeneration.
- When the workflow needs a controlled "review -> confirm -> regenerate" loop
  rather than blind propagation of user text.

Recommended placement:
- Phase 7.5, between `apex-orchestrator-report` and Phase 8 document generation.

## Inputs

### Required
One of:
- `STRATEGY_REPORT_PATH`
- a discoverable `phase1_7_strategy_report*.md` file

### Optional
- `BASELINE_STRATEGY_REPORT_PATH`:
  explicit pre-edit version, when a revised report should be compared against an
  older baseline
- `REVISED_STRATEGY_REPORT_PATH`:
  user-edited strategy report file
- `USER_ADHOC_ENTRY_TEXT`:
  free-form user additions pasted in chat
- `USER_INTENT_APPLY_UPDATES`:
  `YES` or `NO` (default: `NO`)
- `TARGET_SCOPE`:
  restrict analysis to specific requirements, sections, or questions

## Default file discovery

If no explicit path is provided, select files deterministically:

1. Search `private/output/` recursively for `phase1_7_strategy_report*.md` and choose
   the most recently modified file.
2. If none exist, search `generated/` recursively.
3. If none exist, search the repository root.
4. If no strategy report exists, stop and recommend running
   `apex-orchestrator-report` first.

If both a baseline and revised report are available, use the baseline for
comparison and the revised file as the candidate update source.

## What to extract from the strategy report

Extract these items grouped by requirement where possible:

### Per requirement
- Requirement label or requirement text
- `Gap / Missing proof:`
- `Mitigation strategies:`

### Consolidated section
- `## Metrics & Specifics Needed`

Apply robust heading matching if capitalization varies, but preserve the
substance of the original report.

## Non-golden-record policy (hard rule)

User additions are never treated as automatically authoritative.

Do not apply or propagate user ad-hoc additions into regenerated outputs unless:
- the user explicitly asks to apply, regenerate, revise, or update documents; or
- `USER_INTENT_APPLY_UPDATES: YES` is provided.

If apply intent is not explicit:
- default `USER_INTENT_APPLY_UPDATES` to `NO`;
- evaluate the updates;
- produce a review artifact only;
- do not create an integration patch for downstream generators.

## Candidate Assertion Ledger

When `USER_ADHOC_ENTRY_TEXT` is provided, or when a revised strategy report
contains new or changed substantive content, split the changes into atomic
claims and evaluate each one as a **Candidate Assertion**.

Each Candidate Assertion must be classified on these axes:

### Claim type
- `CORRECTION`: fixes an existing date, title, number, or wording error
- `ADDITION`: introduces a new metric, scope detail, tool, stakeholder, result,
  responsibility, or achievement
- `REPHRASE_ONLY`: wording change that does not change facts
- `INSTRUCTION`: user request about what to regenerate or how to handle content
- `OPINION_OR_FRAMING`: narrative preference, tone preference, or emphasis choice

### Evidence status
- `SUPPORTED`: explicitly grounded in existing source inputs or clearly present
  in the revised strategy report as user-provided source text
- `UNSUPPORTED_BUT_PLAUSIBLE`: not contradicted, but not independently grounded
  elsewhere
- `CONFLICTING`: contradicts dates, titles, metrics, or scope already present in
  source inputs
- `AMBIGUOUS`: unclear timeframe, scope, unit, organization, role anchor, or
  action verb meaning

### Integration decision
- `OK_TO_INTEGRATE`: supported and non-conflicting
- `INTEGRATE_WITH_CONFIRM_TAG`: plausible but requires explicit confirmation in
  downstream text
- `HOLD_AS_PLACEHOLDER`: too ambiguous to integrate cleanly; keep as placeholder
- `DO_NOT_INTEGRATE`: conflicting and must be resolved first

### Additional evaluation checks
For each atomic claim, also check:
- role/organization anchoring
- timeframe anchoring
- metric unit clarity
- duplicate/restatement risk
- action-verb integrity (for example, do not flatten `oversaw` into `managed`
  without support)
- overlap / possible double-counting risk when a claim introduces numeric scale

## Revised report handling

If `REVISED_STRATEGY_REPORT_PATH` is provided, compare it against the baseline
strategy report when available.

Treat newly added or materially changed content inside:
- requirement sections,
- `Gap / Missing proof`,
- `Mitigation strategies`,
- `## Metrics & Specifics Needed`,
- and any clearly user-added notes

as Candidate Assertions subject to the same evaluation rules as chat-based
ad-hoc entries.

## Outputs

Always write:
- `private/output/0x_user_feedback_revision.md`

Write only when `USER_INTENT_APPLY_UPDATES: YES`:
- `private/inputs/user_feedback_updates.md`

### Artifact 1: `private/output/0x_user_feedback_revision.md`

Required structure:

```text
# User Feedback Revision Packet

## 1) What is missing from the strategy report
### A) Gaps by requirement
- Requirement: ...
  - Gap / Missing proof:
    - ...
  - Mitigation strategies:
    - ...

### B) Metrics & specifics needed (consolidated)
- ...

## 2) User additions / edits evaluated
### A) Candidate Assertion Ledger
| ID | Claim | Source | Type | Evidence status | Conflict check | Integration decision | What the system needs |
|----|-------|--------|------|-----------------|----------------|----------------------|-----------------------|

### B) Integration-safe phrasing (optional)
- Claim ID X -> safe phrasing with placeholders or confirm-tags

## 3) Regeneration guidance
- What can be applied now
- What still needs confirmation
- What must not be integrated yet
- Next-step instruction for Phase 8 regeneration
```

### Artifact 2: `private/inputs/user_feedback_updates.md`

Create this file only when `USER_INTENT_APPLY_UPDATES: YES`.

Required structure:

```text
# User Feedback Updates (Integration Patch)

## APPROVED_UPDATES
- <atomic fact / metric / scope / correction with role, organization, and timeframe where possible>

## UPDATES_REQUIRING_CONFIRMATION
- <atomic claim> -> [Confirm: exact item to verify]

## HOLD_AS_PLACEHOLDER
- <claim> -> [Placeholder guidance]

## DO_NOT_INTEGRATE_UNTIL_RESOLVED
- <conflict summary>

## NOTES_FOR_GENERATORS
- Use only APPROVED_UPDATES as additive factual material.
- Convert confirmation-needed items into explicit [Confirm ...] tags when surfaced.
- Do not use DO_NOT_INTEGRATE_UNTIL_RESOLVED items in generated outputs.
- Do not upgrade action verbs or aggregate metrics unless the evidence supports it.
```

## Rules

- Do not invent facts or silently reconcile contradictions.
- Do not treat user ad-hoc text as authoritative by default.
- Do not regenerate Phase 8 documents in this skill.
- Keep claims atomic: one fact, correction, metric, or scope detail per line.
- Preserve placeholders or confirm-tags when grounding is incomplete.
- If the user only asks for evaluation, do not create the integration patch.

## Steps

1. Locate the applicable strategy report(s).
2. Extract the missing-proof sections and the consolidated metrics-needed list.
3. If a revised strategy report exists, compare it to the baseline and isolate
   changed or newly added substantive claims.
4. If chat-based ad-hoc entry exists, split it into atomic Candidate Assertions.
5. Evaluate each Candidate Assertion using the ledger categories and checks.
6. Write `private/output/0x_user_feedback_revision.md`.
7. If `USER_INTENT_APPLY_UPDATES: YES`, also write
   `private/inputs/user_feedback_updates.md`.
8. Return a concise completion summary with the artifact path(s) written.

