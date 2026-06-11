# CHANGELOG — multi-agent-version2

All notable changes between the original v1 multi-agent improvement plan
and this v2 spec are recorded here. The seven follow-up edits agreed
during design review are listed individually so the audit trail is
preserved.

## v2.0.0 — design spec (this folder)

### Edit 1 — Canonical tester co-residency on every author server
- Every author server (S1, S2, S3, D1, D2, D3) now lists `qa-auditor` as
  a co-resident agent with the **canonical tester** role.
- Rationale: stock `agent_sync` advances TEST → DISCUSS on the **first**
  `test-result`. `qa-auditor` must be physically present on the server to
  legally submit `test-result`. The writer cannot self-test in this
  workflow because writer-submitted `test-result` is reserved for clean
  passes only.
- Affects: Tier A1 server topology, Tier B1 default-leads, Tier C2
  write_scope, all overlay templates, `topology/server_manifest.yaml`.

### Edit 2 — Per-server phase loop made explicit
- The per-server loop is now spelled out as:
  - **IMPLEMENT** — writer-only file writes; advisors silent.
  - **TEST** — advisors send `send`/`broadcast` review notes; writer or
    `qa-auditor` consumes via `listen` (destructive); `qa-auditor`
    submits `test-result` to advance the phase.
  - **DISCUSS** — each advisor submits exactly one structured `discuss`
    per the apex-agent-sync-protocol rule; writer/qa-auditor calls
    `get-discussion`; `qa-auditor` closes with
    `discuss-done --next-impl <writer>` to either loop or shut down.
  - Cross-pollination reaches the writer in the **next IMPLEMENT pass**,
    bounded by `MAX_REVISION_PASSES`.
- Rationale: aligns with `apex-agent-sync-protocol` rules and the
  destructive nature of `listen` in stock `agent_sync`.

### Edit 3 — Advisor-note persistence rules (DISCUSS path)
- Tier C1 directory tree restored with explicit `_discussion/advisor_notes_<server>.md`
  files for S1, S2, S3, D1, D2, D3.
- DISCUSS-phase notes are persisted by the writer or `qa-auditor` calling
  `get-discussion` and writing the result to
  `_discussion/advisor_notes_<server>.md`.
- Files are **never** written by advisors; advisor `write_scope.allowed_paths`
  remains `[]`.

### Edit 4 — YAML enforcement scope clarified
- The advisor `write_scope` block fields `allowed_messages`,
  `forbidden_actions`, and `message_prefix_required` are explicitly
  labelled as **prompt-level convention**, not runtime-enforced by stock
  `agent_sync`.
- Only `allowed_paths` / `forbidden_paths` are mechanically checkable
  (via `scripts/check_scope.py`).

### Edit 5 — P0 split into P0a + P0b with human gate
- v1 P0 had `screening-lead` write the CCOG-resolved file via `technical-lead`
  "handoff", which violates single-writer-per-server.
- v2 splits prep into:
  - **P0a** — writer `screening-lead`; produces `classification_proposal.md`,
    `phase1_2_core_requirements.md`, `metric_ledger.md`.
  - **Human confirmation gate** on the classification proposal.
  - **P0b** — writer `technical-lead`; produces `ccog_reference_resolved.md`.
- Rationale: preserves single-writer-per-server and adds a real human
  decision point on vacancy classification.

### Edit 6 — Advisor-note persistence rules (TEST path)
- Stock `agent_sync` `listen` is destructive and there is **no
  retrospective send/broadcast retrieval API**.
- Therefore the writer or `qa-auditor` must consume and export incoming
  TEST-phase advisor messages **while the server is still live**, before
  shutdown. This is added to Tier C1 and the runbook as a mandatory step.

### Edit 7 — A3 deletion + softening of "during authoring"
- Tier A3 (Pre-generation red-team pass / Round 2.5) is **deleted**.
  Pre-Phase-8 gap review is owned exclusively by
  `apex-user-feedback-revision` (Phase 7.5) at rollout step 8.
- A4 → A3 (Author response round); A5 → A4 (Bounded critic-author
  revision).
- Foundation principle #3 wording softened from "during authoring" to
  **"within the same author-server review loop, before consensus"**.
  IMPLEMENT remains writer-only.
- Closing thesis bullet updated to "Cross-pollination within each
  author-server review loop".

### Folder posture
- Package lives at workspace root, outside `.agents/`. Not committed to
  the `.agents` git repository.

### Out of scope
- No edits to existing `.agents/` files. The actual application of these
  overlays is a separate, later task.
- `scripts/check_scope.py` is a **stub only** (signature + docstring).

## Pre-v2 baseline

For the original v1 plan, see the conversation that produced
`.agents/You're right — I missed the agent_sync c.md` (workspace-local,
not part of git). The v1 plan is **not** copied into this folder; only
v2 is canonical.
