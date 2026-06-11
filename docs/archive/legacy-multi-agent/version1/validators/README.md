# Validators (Planned, Not Implemented)

This folder reserves space for future mechanical checks that sit outside stock
`agent_sync`.

V1 does not implement any validator scripts.

## Planned Validator Scope

- `check_scope.py`
  - verify that only the active writer touched the expected draft subtree
  - verify that advisors did not produce file writes
- server-manifest validation
  - ensure required manifest fields are present
  - ensure default ports and roles are assigned coherently
- export-presence validation
  - ensure `_discussion/advisor_notes_<SERVER>.md` exists before author-server
    shutdown is considered complete
- run-mode consistency checks
  - ensure active servers match the selected writer sets in `## RUN_MODE`

## Intent

These validators are future implementation helpers only. They are not part of
the v1 skeleton deliverable.
