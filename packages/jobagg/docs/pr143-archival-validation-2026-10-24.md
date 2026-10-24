# PR #143 archival increment validation — 24 October 2026

Observed 2026-10-24T15:26:52.424826+03:00 (Africa/Nairobi). The implementation is isolated on PR #143;
production activation and storage readiness are false.

## Results and scope

- Complete JobAgg suite: **4082 passed, 9 skipped**, 738.90 seconds,
  using the pinned Python/browser runtime and final implementation seal below.
- Final targeted archive groups: **36 passed**, 6.61 seconds. Six cases added
  after full-suite collection cover the three additional producer families and
  expiry/memory limits; they all ran in this focused group. This overlaps the
  full suite and must not be added to it as an independent total.
- Tracked dispatcher suite: **168 passed, 17 subtests passed**, 29.03 seconds.
- Changed-code/test Ruff checks, staged whitespace checks and explicit full-module
  seal comparison passed. Existing skipped cases remain skips; none is treated as
  evidence of fresh provider coverage or completed deployment.
- PR #143 had no inline review threads when inspected. Its prior head's CI was
  successful. A new push needs its own CI result; prior green checks do not
  certify this increment.

Full implementation SHA-256: `4ef66728aeaeaccea4419f49ba33812fc1bb05533686f853dcda0d9b2a2f611b`.
Reviewed successor scope SHA-256: `8bf1b55ca91c30a9208c27c95b6b04f79cd5b862c6dc9b82630171d24e660865`.
The fingerprint covers **179 package Python modules**, including
both new archival modules; no maintenance modules are excluded.

The tested contracts and remaining gates are in the
[implementation report](pr143-archival-implementation-2026-10-24.md). Synthetic
compressed-only fixtures remove only their own test originals. The operator page
executor retains every original, gives no removed-byte credit and cannot grant
production readiness. An immutable source candidate and additive component
rehearsal can be staged after commit; that component is not a native admitted-cycle
rehearsal or a capacity certificate.

## Preserved production boundary

The separate private check at 2026-10-24T15:19:25.006975+03:00 verified unchanged
production maintenance/configuration/health/publication-gate contents and unchanged
M0 (152 modules). The actual owner was available. The existing accepted rehearsal
maintenance marker was preserved. No HDD payload was copied and no production
source head or database was rewritten.

The mounted SSD's device number differs from the last receipt, while the owner
inode and control hashes match. That observation is not an authorized remount
re-attestation; retain old receipts and repeat the bounded volume/dependency review
before relying on device-bound activation receipts.

The production 100 GB allocation and category/peak-headroom gates, complete active
root/writer registry, runtime settings approval, guarded source handoff and exact
migrated-layout admitted-cycle/recovery/rollback rehearsal remain open. The native
accepted rehearsal batch is not reset or reconfigured to bypass those gates.
