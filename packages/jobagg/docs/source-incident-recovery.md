# Captured source incident recovery

This change addresses the October 2026 IOM, IFAD, UNV and WHO incidents. It does
not authorize activation or a provider request. Follow the
[change and review policy](change-and-review-policy.md) for a separately approved
activation. The implementation was prepared against release
`9e3729ee0c380863c7b9071dd7e4ab24d7cc9518`, whose package fingerprint is
`ca42922ee7982fdc51cd05a3a1d3e9911f23760feaa0bba6e1b3b049e40e30e0`.

## Behavior and boundaries

| Evidence | Result | Boundary |
| --- | --- | --- |
| Exact UNV assignment GET, successful JSON with `value: null` | Vacancy detail empty; reconcile through the existing inventory lifecycle | Wrong identity, malformed/ambiguous JSON and missing current capture bindings retain their guards |
| Exact WHO detail GET, active unavailable template | Vacancy unavailable pending independently complete inventory | Generic text, inactive templates, access challenges and a different vacancy are not accepted |
| Historical IOM listing maintenance 503, IFAD listing maintenance 503, or UNV category-support 502 | Explicit selected-task recovery can restore pending eligibility | Only captured matching routes, status, body, source, phase, failed attempt and original listing are supported |

UNV absence requires a newer independently complete census. WHO and IFAD still
lack an independent complete-inventory contract; this change does not infer one
from observed row counts. Unavailable and not observed never mean closed. Useful
accepted descriptions, observations and archives survive all classifications.
The existing unavailable lifecycle coalesces its listing follow-up and retains
the existing retry ceilings. It does not repeatedly fetch unavailable details
while inventory proof is missing.

Changing the full package fingerprint formerly made otherwise identical typed
failures eligible again and reset semantic retry counts. Retry comparison now
also recognizes the exact predecessor fingerprint above. This applies only to
retry decisions and counters: startup integrity, current receipts, registry,
robots, owner, version and semantic payload still use their exact bindings.
Arbitrary older implementations are not accepted. Future parser changes must
review whether this predecessor remains semantically equivalent; do not expand
the alias list as a general workaround.

## Read-only preview

`python -m jobagg.source_incident_repair` accepts an explicit JSON selection:

```json
{"selections": [{"task_id": "<exact task key>", "action": "retry_captured_transient"}]}
```

Supported actions are `retry_captured_transient`, `unv_absent` and
`who_unavailable`. There are at most 19 unique selections. The incident scope is
one IOM listing, one IFAD listing, one UNV captured support 502, eight UNV null
assignments and eight WHO unavailable details. Other historical detail failures
and reviewed deadline holds are excluded.

```sh
python -m jobagg.source_incident_repair \
  --workspace "$worker_workspace" \
  --shared-lock "$shared_owner" \
  --dispatcher-config "$dispatcher_config" \
  --selection "$reviewed_selection" \
  --expected-old-implementation ca42922ee7982fdc51cd05a3a1d3e9911f23760feaa0bba6e1b3b049e40e30e0 \
  --max-seconds 120 --report "$local_preview"
```

Run from the reviewed candidate package with its configured Python environment.
The explicit old-seal preview is always nonexecutable. It opens the worker DB
read-only, makes no requests, and creates only the requested local report. It
binds the actual dispatcher, worker/publisher code, shared owner, workspace seal,
registry, robots, complete publication gate, original attempts, captures, current
source/circuit/host state and quota history. A report must be outside production
state and configuration paths.

The UNV census is independently reproduced from its archived pages, with exact
source-state agreement and a timestamp newer than the failure. Null assignments
must be absent; the support-502 assignment must still be present. Legacy UNV
captures use validated original task/listing/attempt provenance; the utility
does not synthesize missing source-binding metadata.

The verification budget is cooperative: the utility checks it between stages
and tasks, and limits capture counts and sizes. The existing independent
`verify_listing` call has no inner deadline callback; `--max-seconds` is not a
hard preemption guarantee for that call. Exhaustion fails the plan rather than
accepting a partial census. This pre-existing enforcement gap remains explicit.

## Separately approved activation and recovery

1. Freeze and identify the reviewed release, full package fingerprint, unchanged
   registry/robots and exact selection. Check the actual configured worker and
   publisher paths. Do not activate from a dirty or incoherent checkout.
2. Use the established shared-owner and maintenance procedure to drain active
   work. Require the publication gate to be complete and published. Preserve all
   database files, archives, policy history, holds and source schedules.
3. Activate and explicitly reseal the matching reviewed code/configuration using
   the existing activation procedure. This utility never reseals a workspace.
4. Prepare a fresh plan with the same arguments but **without**
   `--expected-old-implementation`. Require `runtime.executable: true`, the exact
   intended before/after task images and unchanged preservation hashes. The
   plan expires after 900 seconds. Any changed evidence or state requires a new
   reviewable preview, not a forced apply.
5. Only after approval, execute the reviewed plan:

   ```sh
   python -m jobagg.source_incident_repair \
     --apply-plan "$fresh_plan" --execute --max-seconds 120 \
     --report "$local_apply_report"
   ```

   Apply reacquires the actual shared owner and verifies gates and evidence
   again under one SQLite transaction. Only selected task status, eligibility,
   receipt and the durable repair journal can change. Attempts, claim history,
   deadlines, jobs, observations, quotas and schedules remain unchanged.
6. Confirm the expected result: three tasks pending, eight UNV tasks not observed
   and eight WHO tasks unavailable pending inventory; no description replacement
   and no closure inference. Pending eligibility retains existing task, host,
   source, pacing, quota, Retry-After and recovery gates. The utility issues zero
   requests and grants no budget increase. Existing durable claims check those
   gates again when normal work resumes.
7. Any provider validation or dispatch needs its own approved exact task scope,
   existing request/time ceilings and stop conditions. A pending task is not
   evidence of successful recovery. Verify complete listing evidence, accepted
   main text, publication projection and database readback separately. Keep OSCE
   inventory and its accepted release behavior in the regression scope.

Stop on a changed identity/scope, access challenge, TLS/URL-policy regression,
text/archive loss, unexpected mutation, fingerprint mismatch or unresolved
publication transaction. A failed apply rolls back its whole transaction. After
an uncertain commit inspect the journal and exact after-images before repeating;
an unchanged, unexpired plan can report `already_applied`. If normal work has
advanced, preserve that data and review a new recovery plan. Code rollback must
restore a coherent previously reviewed code/configuration/fingerprint set; never
restore an old database over legitimate intervening data.

## Verification

The new tests cover strict classifiers, accepted historical details, captured
transient success/negative/boundary cases, legacy provenance, latest complete
census validation, deadline and host gates, exact retry compatibility, persistent
counter ceilings, text/publication preservation, preview/apply drift, atomic
rollback, idempotent journal readback and code/configuration coherence. Tests use
sanitized fixtures and temporary databases. Private production archives are not
included in Git. Offline test success does not establish current provider access.
