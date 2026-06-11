---
name: apex-agent-sync-protocol
description: >-
  Coordination contract for running the ApexStrategist multi-agent workflow
  on top of stock agent_sync. Defines phase semantics, role boundaries,
  canonical round ownership, message schemas, human-decision handling,
  liveness rules, and post-generation independent evaluation rounds.
---

# apex-agent-sync-protocol

## Purpose

This skill governs how the ApexStrategist team coordinates when using the
`agent_sync` server. It does not replace `apex-guardrails`; it defines the
team protocol around it.

Use this skill whenever:
- multiple peer agents are running in parallel,
- the team is using `agent_sync`,
- the work is split across IMPLEMENT / TEST / DISCUSS / SHUTDOWN,
- or post-generation independent evaluation is being run.

---

## Core principle

**One shared team. One current implementer. One canonical tester. One
canonical closer for next-implementer selection.**

This protocol intentionally adapts to stock `agent_sync` behavior:
- the first `test-result` advances TEST -> DISCUSS
- the final `discuss-done` call determines the next implementer

Therefore, the team must not treat all `discuss-done --next-impl` calls as
votes. The protocol below makes the behavior deterministic.

---

## Standard team topology

### Core authoring agents
- screening-lead
- technical-lead
- ats-format-lead
- qa-auditor

### Post-generation independent evaluation agents
- independent-panel-evaluator
- independent-shortlisting-redteam

---

## Canonical round ownership

### Round 1 — screening-lead
Purpose:
- read `inputs/application_context.md`
- validate or run term extraction
- copy the history snapshot
- reset the target output folder
- produce:
  - `phase1_2_core_requirements.md`
  - `classification_proposal.md`
  - `metric_ledger.md`

### Human decision gate
A vacancy-type confirmation or override must be obtained before Mode B
continues.

### Round 2 — technical-lead
Purpose:
- consume the confirmed vacancy type and functional register
- produce:
  - `ccog_reference_resolved.md`

### Round 3 — screening-lead
Purpose:
- produce:
  - `phase1_7_strategy_report.md`

### Round 4 — ats-format-lead
Purpose:
- produce candidate-facing documents, for example:
  - `option1_admin_profile.md`
  - `option2_cv.md`
  - `option3_cover_letter.md`
  - optional `option4_qualification_answers.md`
  - optional `option7_motivation_statement.md`

### Round 5+ — narrow fix rounds
Purpose:
- fix only blocking defects
- owner is the owner of the affected artifact unless a human explicitly
  reassigns the round

### Evaluation Round E1 — independent-panel-evaluator
Activation rule:
- at least one candidate-facing output exists
- not applicable to Phase 1 planning-only artifacts

Purpose:
- produce:
  - `independent_panel_evaluation.md`

### Evaluation Round E2 — independent-shortlisting-redteam
Activation rule:
- after E1 or in parallel with E1 if desired
- not applicable to Phase 1 planning-only artifacts

Purpose:
- produce:
  - `independent_shortlisting_risk_review.md`

---

## v2 ensemble topology (RUN_MODE-gated)

This section applies **only when `## RUN_MODE` in
`inputs/application_context.md` has a non-empty `ENSEMBLE_PHASE_1_7` or
`ENSEMBLE_PHASE_8` list**. Otherwise the canonical linear rounds above
apply.

### Activation
- Read `## RUN_MODE` from the context pack.
- If `MODE: ensemble_v2` (or either ensemble list is non-empty), use the
  per-server prompts under `.agents/prompts/v2/` and the launcher at
  `.agents/scripts/launch_v2_servers.sh`.
- Per-server registry, ports, writer/advisors and canonical tester are
  defined in `.agents/topology/server_manifest.yaml`. The launcher
  injects `AGENTS_LIST` per server based on this manifest.

### Servers (writer / canonical tester)
- `P0a` 9800 — screening-lead writer / qa-auditor canonical tester
- `P0b` 9801 — technical-lead writer / qa-auditor canonical tester
- `S1`  9811 — screening-lead writer / qa-auditor canonical tester
- `S2`  9812 — technical-lead writer / qa-auditor canonical tester
- `S3`  9813 — ats-format-lead writer / qa-auditor canonical tester
- `C1`  9820 — qa-auditor writer (consensus, no separate canonical tester)
- `D1`  9831 — screening-lead writer / qa-auditor canonical tester
- `D2`  9832 — technical-lead writer / qa-auditor canonical tester
- `D3`  9833 — ats-format-lead writer / qa-auditor canonical tester
- `C2`  9840 — qa-auditor writer (consensus, no separate canonical tester)
- `E1`  9851 — independent-panel-evaluator writer (isolated, self-evaluating)
- `E2`  9852 — independent-shortlisting-redteam writer (isolated, self-evaluating)
- `R1`  9860 — screening-lead writer / qa-auditor canonical tester
- `R2`  9861 — qa-auditor writer (remediation consensus, no separate canonical tester)

P0a runs first, then the human confirmation gate, then P0b.
S1/S2/S3 run in parallel and feed C1.
D1/D2/D3 run in parallel and feed C2.
E1/E2 run in parallel after C2 and feed R1.
R1 feeds R2.

### Phase 8 ensemble scope
Ensemble v2 generation currently covers Phase 8 Options 1-4 and Option 7.
Options 5, 6, and 8 fall back to v1 single-agent generation unless the user
explicitly expands the v2 D/C2 scopes.

### Closer rule (v2)
- All writer agents on author servers (P0a/P0b/S1/S2/S3/D1/D2/D3/R1) call
  `discuss-done` **without** `--next-impl` so the DISCUSS barrier
  advances.
- `qa-auditor` is the canonical tester on all author servers and uses
  `discuss-done --next-impl <writer>` to loop revision passes, or
  `discuss-done` (no `--next-impl`) followed by the separate
  `shutdown --reason "<server> complete"` command to end the stage.
- E1 and E2 have no advisors or canonical tester. Their writer may submit
  the self-checking `test-result` needed by stock `server_v6.py` to move
  TEST -> DISCUSS, then shut down.
- `discuss-done --next-impl shutdown` is **not** valid; the explicit
  `shutdown` command is required.

### Advisor notes ownership
For each server, `_discussion/advisor_notes_<server>.md` is owned by
`qa-auditor`. Advisors and writers may read it; only qa-auditor appends
or rewrites it.

---

## Phase semantics

## IMPLEMENT

### Rules
1. Exactly one implementer writes files.
2. Before writing, the implementer sends:
   `WRITE_SCOPE::<file1>|<file2>|...`
3. The implementer may write only the files inside the declared write scope.
4. Non-implementers are read-only:
   - no edits
   - no file creation
   - no deletions
   - no file-moving
   - no terminal write commands
5. Do not use another agent's identity for commands.

### Owned file rule
Each artifact has one owner for its round. If a defect is found later, the
owner of the affected artifact is the default fix owner.

---

## TEST

### Canonical tester rule
`qa-auditor` is the canonical tester and the only default agent allowed to
submit `test-result`.

Reason:
Stock `agent_sync` advances TEST -> DISCUSS on the first `test-result`.
To avoid premature phase transitions, other agents must send review notes
through `send`, `broadcast`, or `discuss`, but not call `test-result`
unless `qa-auditor` is unavailable and the human explicitly appoints a
fallback tester.

### TEST output contract
The canonical tester submits:
`TEST::<PASS|FAIL>::FILES=<...>::ISSUES=<...>::OWNER=<...>::ACTION=<...>`

The message should be concise but exact.

### Substantive advisor review rule
Before an author server can shut down, `_discussion/advisor_notes_<server>.md`
must contain at least one specific observation, issue, or suggestion from each
advisor on that server. A notes file made only of confirmations such as "no
blocker" or "ready for next step" fails pre-shutdown validation.

Use the deterministic helper:

```bash
python .agents/scripts/validate_advisor_notes.py \
  --advisor-notes output/generated_documents/history/<JOB_SLUG>/_discussion/advisor_notes_<SERVER>.md \
  --advisors "<advisor-1>,<advisor-2>" --json
```

Advisors may send several TEST-phase messages within `MAX_ADVISOR_MESSAGES`
when genuine review requires back-and-forth. DISCUSS still records one final
structured position per agent so the stock server barrier remains deterministic.

---

## DISCUSS

### Structured discuss rule
Every agent submits exactly one final structured discuss message for the round:

`ISSUE=<top issue> | FILE=<affected file or none> | OWNER=<file owner> | NEXT=<recommended implementer> | ACTION=<next concrete action> | BLOCKER=<yes/no>`

This is the final-position record, not the whole debate. Substantive
back-and-forth should happen during TEST via `send` / `broadcast`, and must be
persisted into advisor notes before QA closes the server.

### Canonical closer rule
To make stock `agent_sync` deterministic:
- all non-QA agents call `discuss-done` **without** `--next-impl`
- `qa-auditor` is the only default agent allowed to call:
  `discuss-done --next-impl <owner>`

This converts the server's last-writer-wins behavior into a stable team
rule.

### If qa-auditor is unavailable
A human may appoint one fallback closer for the round. Only that fallback
closer may use `--next-impl`.

---

## SHUTDOWN

Enter SHUTDOWN only when:
- all required deliverables are complete,
- final QA has passed,
- post-generation evaluation rounds are complete or intentionally skipped,
- and there are no unresolved blocking issues.

---

## Human decision protocol

A human decision seen in only one local chat tab is not yet shared team
state.

### Canonicalization steps
1. The receiving agent rebroadcasts the decision in this format:

`DECISION::<FIELD>::<VALUE>::SOURCE=human`

Examples:
- `DECISION::VACANCY_TYPE::DEVELOPMENT_AGENCY::SOURCE=human`
- `DECISION::CHAR_LIMIT_OPTION1::2500::SOURCE=human`

2. The current artifact owner updates the canonical shared artifact(s):
- `classification_proposal.md` if relevant
- `metric_ledger.md` metadata / decision section if relevant

3. Only after steps 1 and 2 may other agents rely on the decision as
shared team state.

---

## Shared artifact protocol

### metric_ledger.md
This artifact is the canonical ledger for metrics, scope, approved
aggregations, and unresolved confirmations.

Rules:
- create it early in Round 1
- use it in all later rounds
- do not silently override it from downstream documents
- if corrected, append a change-log entry

### Other shared artifacts
- `classification_proposal.md` = vacancy framing and human decision trail
- `ccog_reference_resolved.md` = occupational/register guidance
- `phase1_7_strategy_report.md` = downstream phrasing and document strategy

---

## Independent evaluation protocol

Independent evaluation agents activate only after at least one
candidate-facing document exists.

### What they evaluate
Independent evaluators may read only:
- `<OUTDIR>/_discussion/independent_eval_input.md`
- final canonical candidate-facing option files:
  - `option1_admin_profile.md`
  - `option2_cv.md`
  - `option3_cover_letter.md`
  - `option4_qualification_answers.md`
  - `option7_motivation_statement.md`, if requested/generated

They must not read:
- full `inputs/application_context.md`
- candidate history
- `metric_ledger.md`
- `phase1_7_strategy_report.md`
- D1/D2/D3 drafts
- advisor notes
- consensus notes
- `panel_response.md`
- `remediation_plan.md`

### Hard independence rule
Independent evaluators:
- do not advocate for the candidate
- do not rewrite candidate-facing documents during evaluation
- do not soften criticism to maintain morale
- may recommend narrow fix rounds with the correct file owner

---

## Liveness and stale-session rules

1. Long rounds require periodic heartbeat messages.
2. Project default:
   - send a heartbeat roughly every 5 minutes during active long rounds
   - treat an implementer as stale after roughly 20 minutes with no sign of
     life unless the human states otherwise
3. If the implementer appears stale:
   - `qa-auditor` broadcasts a stale-session warning
   - the human may reopen the inactive chat tab or force a phase change
4. Agents must not proxy another agent's identity to finish a round.

---

## Emergency controls

### Allowed emergency action
A human may use or authorize:
- `set-phase`
- manual re-assignment by restarting the correct chat tab
- manual stop / shutdown

### Forbidden emergency shortcut
Agents must not:
- impersonate another agent for `impl-done`
- submit `discuss-done` for other agents
- manufacture consensus that did not happen

---

## Message templates

### Write scope
`WRITE_SCOPE::<file1>|<file2>|...`

### Human decision
`DECISION::<FIELD>::<VALUE>::SOURCE=human`

### Test result
`TEST::<PASS|FAIL>::FILES=<...>::ISSUES=<...>::OWNER=<...>::ACTION=<...>`

### Discuss
`ISSUE=<...> | FILE=<...> | OWNER=<...> | NEXT=<...> | ACTION=<...> | BLOCKER=<yes/no>`

### Final evaluation recommendation
`EVAL::<score>::SHORTLIST=<shortlisted|borderline|unlikely>::TOP_GAPS=<...>`

---

## Practical operating rules by role

### screening-lead
- owns Round 1 and Round 3
- owns vacancy framing, requirement mapping, and evidence-shortlist logic

### technical-lead
- owns Round 2
- owns CCOG resolution and technical register alignment

### ats-format-lead
- owns Round 4
- owns final candidate-facing document generation

### qa-auditor
- read-only by default
- canonical tester
- canonical next-implementer closer in DISCUSS
- may own narrow fix rounds only if the human explicitly assigns them

### independent-panel-evaluator
- read-only except its own evaluation report
- may not edit candidate-facing docs

### independent-shortlisting-redteam
- read-only except its own risk review report
- may not edit candidate-facing docs

---

## Usage

When this skill is loaded:
1. follow the canonical round map,
2. use the structured message templates,
3. obey the canonical tester / closer rules,
4. and keep the independent evaluation rounds separate from authoring.
