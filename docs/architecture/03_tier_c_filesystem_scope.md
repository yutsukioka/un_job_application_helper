# Tier C — Filesystem & scope discipline (deep dive)

> Companion to [00_consolidated_plan.md §"Tier C"](00_consolidated_plan.md).

## C1. Per-author draft folder layout

Canonical tree is in [00_consolidated_plan.md §C1](00_consolidated_plan.md).

### Advisor-note persistence runbook

This is the operational sequence the writer or `qa-auditor` must follow
to ensure no advisor message is lost.

#### TEST-phase persistence (live consume)

`agent_sync` `listen` is **destructive**. Once a message is read via
`listen`, it is removed from the per-agent mailbox and there is **no
retrospective send/broadcast retrieval API** in stock `server_v6.py`.

**Sequence (per TEST phase):**

```text
1. Advisors call send / broadcast.
2. WRITER (or qa-auditor) loop:
     while messages pending:
        msg = listen()                        # DESTRUCTIVE
        append msg to _discussion/advisor_notes_<server>.md
3. qa-auditor calls test-result -> advances to DISCUSS.
```

If the writer/qa-auditor forgets step 2, the advisor message is lost
permanently. The runbook therefore mandates the pre-shutdown check:

> Before issuing SHUTDOWN to any author server, verify that
> `_discussion/advisor_notes_<server>.md` exists and is non-empty.

#### DISCUSS-phase persistence (recoverable via get-discussion)

DISCUSS messages are held in `S.discussion` and exposed via
`get-discussion`. They are NOT persisted to `logs/v6/`.

**Sequence (per DISCUSS phase):**

```text
1. Each advisor + qa-auditor calls discuss exactly once.
2. WRITER (or qa-auditor) calls get-discussion ONCE near end of phase.
3. WRITER (or qa-auditor) appends the result to
   _discussion/advisor_notes_<server>.md.
4. Non-qa agents call discuss-done (without --next-impl).
5. qa-auditor calls discuss-done --next-impl <writer | shutdown>.
```

#### Ownership

| File | Written by | Never written by |
|---|---|---|
| `_discussion/advisor_notes_<server>.md` | WRITER or `qa-auditor` (declared owners on the server) | any advisor |
| `_discussion/round*_consensus.md` | `qa-auditor` (consensus server writer) | anyone else |
| `_discussion/disagreement_log.md` | `qa-auditor` | anyone else |
| `<agent>/option*.md`, `<agent>/phase1_7_strategy_report.md` | declared WRITER on the matching server | any other agent |
| canonical flat-path outputs | `qa-auditor` on C1 / C2 | author-fold writers |

Mechanical enforcement is via `write_scope.allowed_paths` (see C2 and the
[archived v2 scope checker](../archive/legacy-multi-agent/version2/scripts/check_scope.py)).

## C2. `write_scope` blocks by server role

Canonical YAML in [00_consolidated_plan.md §C2](00_consolidated_plan.md).
Distilled rules:

### Writer (on its own author server)

```yaml
write_scope:
  allowed_paths:
    - <draft-subfolder>/**
    - _discussion/advisor_notes_<server>.md   # writer co-owns this with qa-auditor
  forbidden_paths:
    - other agents' draft subfolders
    - canonical flat-path outputs
    - _discussion/round*_consensus.md
    - _discussion/disagreement_log.md
```

### Advisor (on any server)

```yaml
write_scope:
  allowed_paths: []                     # MECHANICALLY ENFORCED
  # Below: prompt-level convention only, NOT runtime-enforced
  allowed_messages: [send, broadcast, discuss]
  forbidden_actions: [test-result, "discuss-done --next-impl", "any file write"]
  message_prefix_required: "ADVISOR_TO=<writer-name>"
```

### `qa-auditor` (canonical tester on author server)

```yaml
write_scope:
  allowed_paths:
    - _discussion/advisor_notes_<server>.md
  forbidden_paths: [draft subfolders, canonical flat-path outputs]
  # Prompt-level convention:
  allowed_phase_actions: [test-result, "discuss-done --next-impl"]
```

### `qa-auditor` (writer on consensus server C1 / C2)

```yaml
write_scope:
  allowed_paths:
    - <canonical flat-path outputs for that consensus round>
    - _discussion/round<N>_consensus.md
    - _discussion/disagreement_log.md
  forbidden_paths:
    - draft subfolders
    - prep artifacts (frozen at this point)
```

### Field enforcement summary

| Field | Mechanically enforced? |
|---|---|
| `allowed_paths` | YES — by the archived v2 `docs/archive/legacy-multi-agent/version2/scripts/check_scope.py` stub |
| `forbidden_paths` | YES — by the archived v2 `docs/archive/legacy-multi-agent/version2/scripts/check_scope.py` stub |
| `allowed_messages` | NO — prompt-level convention only |
| `forbidden_actions` | NO — prompt-level convention only |
| `message_prefix_required` | NO — prompt-level convention only |
| `allowed_phase_actions` | NO — prompt-level convention only |

The non-mechanically-enforced fields are still useful: they appear in
the agent prompt and in the `_discussion/disagreement_log.md` audit
trail, but stock `agent_sync` itself does not gate on them.

## C3. Optional `check_scope.py` verifier

Stub in the
[archived v2 scope checker](../archive/legacy-multi-agent/version2/scripts/check_scope.py).

### Expectations

- Reads `topology/server_manifest.yaml` to learn each server's writer
  and allowed paths.
- Inspects `git diff --name-only` (or a passed-in changeset) against
  the run's expected writer per file.
- Fails with a clear message if any file was written outside the
  writer's `allowed_paths`.
- Explicitly checks that no `_discussion/advisor_notes_*.md` was
  written by an agent declared as `advisor` on that server.
- Idempotent; safe to invoke as a pre-commit hook.

Implementation is **deferred** — this spec ships only the stub
(signature + docstring).
