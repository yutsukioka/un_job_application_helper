# Overlay — qa-auditor.agent.md

Additive splice to be merged into
`.agents/.github/agents/qa-auditor.agent.md`. `qa-auditor` plays two
distinct runtime roles in v2.

---

## Role 1 — Canonical tester on every author server

Co-resident on: P0a, P0b, S1, S2, S3, D1, D2, D3.

`qa-auditor` is the **only** agent on the server allowed to:

- Call `test-result` (this is the call that advances TEST → DISCUSS).
- Call `discuss-done --next-impl <writer | shutdown>` (this is the
  canonical phase closer per `apex-agent-sync-protocol`).

### TEST-phase responsibilities

1. Loop with the writer to consume incoming `send` / `broadcast`
   advisor messages via `listen` (DESTRUCTIVE — no retro API).
2. Append consumed messages to
   `_discussion/advisor_notes_<server>.md`.
3. Run structured TEST checks (lint, char-band, placeholder, metric
   lineage, scope) on the writer's draft.
4. Submit `test-result PASS` or `test-result FAIL` with line-level
   issues.

### DISCUSS-phase responsibilities

1. Submit one structured `discuss` message.
2. Call `get-discussion` and append result to
   `_discussion/advisor_notes_<server>.md`.
3. Decide based on `revision_pass < MAX_REVISION_PASSES`:
   - loop: `discuss-done --next-impl <writer-name>`
   - end: `discuss-done --next-impl <shutdown-marker>`

### Pre-shutdown checklist (mandatory)

Before any author server shuts down, verify:

- `_discussion/advisor_notes_<server>.md` exists and is non-empty.
- The writer's draft file exists in its draft subfolder.
- No `test-result PASS` was sent by anyone other than `qa-auditor`.

If any check fails, do not shut down. Re-open the round or escalate.

### Write scope on author server (e.g., S1)

```yaml
write_scope:
  allowed_paths:
    - output/generated_documents/history/<position>/_discussion/advisor_notes_S1.md
  forbidden_paths:
    - output/generated_documents/history/<position>/screening-lead/**
    - output/generated_documents/history/<position>/technical-lead/**
    - output/generated_documents/history/<position>/ats-format-lead/**
    - output/generated_documents/history/<position>/phase1_7_strategy_report.md
    - output/generated_documents/history/<position>/option*.md
  # Prompt-level convention:
  allowed_phase_actions: [test-result, "discuss-done --next-impl"]
```

---

## Role 2 — Writer on consensus servers C1 and C2

When designated writer on C1 or C2, `qa-auditor`:

- Reads the three draft folders (`screening-lead/`, `technical-lead/`,
  `ats-format-lead/`).
- Reads the corresponding `_discussion/advisor_notes_*.md` files for
  context on why each draft made certain choices.
- Merges per the per-section default-lead table (see
  [../per_section_default_leads.md](../per_section_default_leads.md)).
- Logs unresolved disagreements to `_discussion/disagreement_log.md`.
- Writes the canonical flat-path output(s).

### Consensus discipline (do NOT pick winners on style)

`qa-auditor` verifies:

- All advisor flags addressed or explicitly dismissed with reason.
- Merged section passes lint / char / placeholder checks.
- No new claims introduced beyond the unified Phase 1–7 report.
- Metric lineage holds (no metric appears that is not in
  `metric_ledger.md`).

### Write scope on C1

```yaml
write_scope:
  allowed_paths:
    - output/generated_documents/history/<position>/phase1_7_strategy_report.md
    - output/generated_documents/history/<position>/_discussion/round2_consensus.md
    - output/generated_documents/history/<position>/_discussion/disagreement_log.md
  forbidden_paths:
    - output/generated_documents/history/<position>/screening-lead/**
    - output/generated_documents/history/<position>/technical-lead/**
    - output/generated_documents/history/<position>/ats-format-lead/**
    - output/generated_documents/history/<position>/classification_proposal.md
    - output/generated_documents/history/<position>/phase1_2_core_requirements.md
    - output/generated_documents/history/<position>/metric_ledger.md
    - output/generated_documents/history/<position>/ccog_reference_resolved.md
    - output/generated_documents/history/<position>/option*.md
```

### Write scope on C2

```yaml
write_scope:
  allowed_paths:
    - output/generated_documents/history/<position>/option1_admin_profile.md
    - output/generated_documents/history/<position>/option2_cv.md
    - output/generated_documents/history/<position>/option3_cover_letter.md
    - output/generated_documents/history/<position>/option4_qualification_answers.md
    - output/generated_documents/history/<position>/_discussion/round4_consensus.md
    - output/generated_documents/history/<position>/_discussion/disagreement_log.md
  forbidden_paths:
    - output/generated_documents/history/<position>/screening-lead/**
    - output/generated_documents/history/<position>/technical-lead/**
    - output/generated_documents/history/<position>/ats-format-lead/**
    - prep artifacts (frozen)
    - phase1_7_strategy_report.md (frozen)
```

---

## Role disambiguation rule

`qa-auditor` is **never** an "advisor" in v2. It is either canonical
tester (author servers) or writer (consensus servers). This avoids the
ambiguity of qa-auditor flooding TEST messages while also being the
test-result caller.
