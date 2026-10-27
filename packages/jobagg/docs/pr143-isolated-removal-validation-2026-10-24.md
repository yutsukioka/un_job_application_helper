# PR #143 isolated archive removal validation — 24 October 2026

Observed 2026-10-24T17:02:09.096307+03:00 (Africa/Nairobi). User authorization permits guarded removal
in isolated copies only. Production activation, retirement and readiness remain
false. The [implementation contract](pr143-isolated-archive-removal-2026-10-24.md)
describes eligibility, retained-source proof and restart accounting.

## Results

- Complete JobAgg suite: **4125 passed, 9 skipped** in **681.51 seconds**
  using the pinned Python/browser environment and final implementation seal.
- Final affected archive/removal/storage groups: **129 passed**, 16.90 seconds.
  These overlap the full suite and must not be added as an independent total.
  They also reran the deterministic-capacity fixture repair added after the full
  suite had collected its original fixture definition. Runtime bytes did not
  change during that run.
- Removal group before that fixture repair: **37 passed**, 10.95 seconds. These
  are also included in the affected groups; no separate total is implied.
- Low-capacity fixture reproduction: **53 passed**, 13.42 seconds, with only
  1 GiB reported free and the runtime reserve asserted at 20 GiB. This overlaps
  the affected groups; the independent SSD rehearsal uses real capacity.
- Tracked dispatcher suite: **168 passed, 17 subtests passed**, 26.52 seconds.
- Changed-file Ruff and whitespace checks passed. The explicit seal comparison
  covers every package Python module, including isolated maintenance operations.

Implementation SHA-256: `f9b8ecbce83be2ef854ef8fc4afd13646f01b9a300129658cef1870bb65a13d1`.
Reviewed successor scope SHA-256: `5c62ee699fe53e5f1ab33f05058a5aff68be6ad05f6056783da0baf564ae5fab`.
Module count: **180**.

No provider was probed. Synthetic isolated fixtures retain their source payloads,
protect current/previous publication inputs, and reconstruct exact compressed-only
bytes. Native tests preserve idle authority and reject admitted work. Unknown
historical exits, catalogue dependencies and recovery variants remain held; no
proof, observation, attempt charge or native source head is rewritten.

The preceding PR head's six Linux failures were positive archive fixtures reaching
the real 20 GiB reserve gate on the hosted runner. The final fixture models
capacity only for small synthetic tests and retains low-space negatives. Actual
runtime admission limits were not lowered. CI on the new pushed head must finish
independently; prior checks cannot certify these changes.

## Preservation and deployment limit

The private read-only check at 2026-10-24T16:56:00.053373+03:00 verified unchanged
production maintenance/configuration/health/publication-gate contents, unchanged
M0 (152 modules) and the unchanged accepted rehearsal marker. The owner was
available. The previously observed SSD device-identity change remains a separate
re-attestation gate; it is not silently accepted by these tests.

After commit, stage a separate immutable source candidate and run the bounded
synthetic isolated-only removal/restart component on that exact source. Keep its
private receipt and fresh preservation check with preparation records. That
component does not migrate the accepted native history copy or constitute the
final admitted-cycle rehearsal, production storage admission, settings approval or
production authority handoff. No HDD payload copying is required.
