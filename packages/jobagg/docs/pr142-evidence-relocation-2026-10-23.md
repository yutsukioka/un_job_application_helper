# PR #142 evidence relocation and in-place verification

Observed 23 October 2026. Isolated candidate only; production is not activated.
The immutable merged PR #142 release and both M0 baseline records are unchanged.
The runtime follow-up is committed as `8c1a2e875711979570baaa4fba1023f88a4532e8`
on `codex/pr142-release-rehearsal`. GitHub still reports PR #142 as merged;
these local commits are not an update to an open PR or a production release.

## Independent integrity record and result

The live SSD policy bootstrap was reviewed on 14 September 2026 at
07:33:16 UTC; its marker was initialized at 07:36:03 UTC. Its SHA-256 remains
`cafc6fecac9bb38bf85a891011af3d3e95b1b019e20ebad0f3b61fdc4a10196b`,
exactly matching the live marker. This independent record predates the
15 October HDD backup. It contains the original expected hashes of all 469
referenced files; no expected hash was derived from the HDD being tested.

All **469/469 files, totaling 3,142,043,607 bytes**, were read **in place** on the
HDD and matched those original hashes through the native import evidence phase.
No HDD payload was copied to SSD or production, and the sole HDD copy was not
modified. The subset verification receipt was recorded at **13:34:42 EAT**.
This certifies equivalence to the bootstrap-recorded contents for this subset;
it does not certify the entire backup, whose available control journal still
records an interrupted/unverified backup run. Copying alone would establish
neither the original contents nor a successful integrity comparison.

Private receipt:
`/Users/yutsukioka2/git/un_job_application_helper-releases/pr142-42405d942f66-3616569f42bc-preparation/evidence-inplace-verification.json`.
The complete path mapping and data remain outside Git.

## Runtime change and preserved contracts

`policy_evidence.prepare` is an explicit owner-held preparation API. It admits
an exact, complete mapping from original bootstrap references to regular files
under one reviewed root. It preserves bootstrap/marker bytes and original
expected hashes. It does not read payloads, claim verification, migrate an
existing native authority, or run at worker startup.

The mapping is sealed separately. A stopped import handoff archives the original
journal, binds the map hash into its contract, and resets only the evidence audit
cursor so a previously checked old locator cannot certify a new target. Existing
quota reservations, replay progress, source holds and cooldowns are retained.
Interruption resumes against the identical map. The map becomes an immutable
control in the completed native authority head. An unadmitted map beside an
existing authority is rejected.

Bootstrap and subsequent bounded audits resolve selected evidence through this
map and still compare the original expected hashes. Identity changes, aliases,
wrong scopes, byte growth, changed content and changed map controls fail closed.
The explicit relocation envelope is 1,024 files, 512 MiB per file and 4 GiB total,
with chunk-level deadline checks. These are preparation/audit bounds, not changes
to provider quotas or fetching limits. Thirty-one observed legacy files exceed
the old generic 16 MiB audit cap; only explicitly admitted mapped evidence uses
its exact recorded size within the new envelope. Unmapped evidence retains the
existing audit behavior.

The exact candidate includes **168** package Python modules, including the new
resolver, with no exclusions:

- Full implementation: `0faaf5c20c57315d3eb16f7aee0b943eef9a744a29e9d88e42978e36a3a4dfcf`.
- Explicit successor scope: `013073b79d6722602bf77dd61a4e64689ca604a25c6cddf14a78c543acd87f1e`.

The old 167-module immutable PR #142 archive remains unchanged. This seal
identifies the reviewed follow-up candidate; it grants no production activation.
Retry predecessors and provider/source/parser/transport/concurrency/retention
settings are unchanged. No network probe, hold reset or schedule change occurred.

## Rehearsal and tests

The existing isolated snapshot was reused. The maintenance marker stayed in
place, and the actual existing owner lock was held without changing its inode or
bytes. An OS sandbox denied network and all production writes; the HDD was read
only. Policy bootstrap completed in **134.54 seconds**, preserving and verifying
all **71,888** reservation events across **49** historical sources. The resulting
native authority has **72,357** evidence records and its quick integrity check
returned `ok`. The native worker runtime authority was not activated.

The latest exact worker exit imported **22** accepted attempt identities. A
bounded diagnostic cohort of **11 detail units and 5 listing units** produced a
**1,200,128-byte projection in 0.032 seconds**; read-only planning took **3.673
seconds**. The copied-target publication completed in **51.814 seconds**, with
zero changed detail jobs, five listing frames, complete database transactions
and verified readback. All 11 detail rows were already unchanged. This is one
specific cohort, not worst-case 40-job capacity or an all-source freshness claim.

After this scoped rehearsal, six accepted listing identities remain unresolved,
and the publisher's full-scope native export boundary remains null. The output
explicitly reports `pending_backlog_empty=false`. These facts are retained, not
converted into an empty-backlog or deployment-ready certificate.

Full package run on the unchanged candidate: **3,808 passed, 40 failed, 9 skipped**
in 452.04 seconds. All 40 failures were browser setup failures caused by omitting
the staged `PLAYWRIGHT_BROWSERS_PATH`. With that environment corrected, the same
40 cases passed in 108.82 seconds. Thus **3,848 distinct tests passed after the
environment correction; 9 skipped**, across the full run and failed-case rerun.
This is not represented as a single clean full-suite invocation. The earlier
37-test staging/policy set overlaps this package scope and is not added to it.
Skips retain the existing ignored CCOG/private browser-capture limitations.
No source code changed between these runs. Focused corruption, large-file,
interruption, wrong-owner, map tampering, cursor reset and growth-bound tests
exercise synthetic files independently of HDD backup quality.

## Remaining deployment gates and next reviewed increment

Evidence discovery, in-place integrity verification and copied policy migration
now pass. Deployment readiness remains **false**. Remaining gates are:

1. A complete reviewed inventory/import of legacy manual, browser, dispatcher and
   recovery controls. The configured dispatcher root alone has 2,368 run
   directories and 2,182 acceptance reports; 51 reports lack task-interval
   identities. The one extra directory entry is `.DS_Store`, not a corrupt run.
   None of this inventory is presented as a complete all-writer certificate.
2. Exact treatment of the six outstanding listing identities and a full-scope
   current export certificate. The successful five-frame rehearsal does not close
   unrelated or older acceptance/publication work.
3. Reviewed phase, publication-admission and policy-audit parameters. One measured
   cohort does not establish every viable unit or worst-case export cost; draft
   fields remain null. The proposed 840-second supervisor entry ceiling remains
   an explicit change from the legacy 1,200-second total, requiring review.
4. Stage/review the successor code/configuration set. The exact merged PR #142
   archive cannot provide this new resolver, and GitHub still marks it merged.

A further substantial bootstrap/reconciliation change must be consulted on
before implementation. It must preserve original controls, record uncertain
legacy runs as explicit held/repair work, and never manufacture missing attempt
identities, successful exits, historical publication proof or a full export
certificate. Its review should cover paged import interruption, contradictory
controls, superseded accepted occurrences, genuine pending work and export
readback across all configured destinations. No automatic blanket closure,
provider request or production activation is authorized by this note.

## Final unchanged boundary

At **13:42:53 EAT on 23 October 2026**, an owner-held final check verified all
72,362 original production snapshot files unchanged by identity and hash. The
active configuration, native maintenance marker and existing owner identity
(device 16777257, inode 4728) matched exactly. All 71,888 imported event payloads
still matched their retained legacy JSON. All 1,933 files in the original
immutable release matched its manifest and remained read-only. Production schema,
schedule and code were not changed. The owner was released with the maintenance
fence still present.
