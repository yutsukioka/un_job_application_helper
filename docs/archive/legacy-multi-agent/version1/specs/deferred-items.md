# Deferred Items

These items are intentionally excluded from v1.

## 1. Candidate-Specific Persistent `lessons.md`

Deferred because the current guardrails treat the system as stateless across
sessions and do not permit saving or reusing candidate-specific memory merely by
moving it outside the Git repo.

Required before adoption:

- explicit guardrail or policy change
- precise definition of allowable content
- storage and recall rules that do not silently override current-session inputs

## 2. Promoting `deterministic-skill-router` to a Runtime Gate

Deferred because the current orchestrator flow is already explicitly sequenced.
V1 keeps the router diagnostic-only unless a later design shows a concrete
runtime need.

## 3. Any `agent_sync` Fork or Multi-Writer-Per-Server Design

Deferred because v1 is explicitly built around stock `agent_sync`.

Out of scope:

- multiple simultaneous implementers on one server
- server-enforced advisor message policy
- custom branch or merge semantics inside the coordination server

## 4. Direct Backport or Cutover into `.agents/`

Deferred because `multi-agent-version1/` is an isolated staging package only.

Out of scope:

- replacing the live `.agents/` workspace
- copying v1 docs into `.agents/`
- changing existing runtime prompts, skills, or agent definitions as part of
  this revision
