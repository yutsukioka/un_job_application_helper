# PR #143 authorized production preflight — 24 October 2026

Observed **2026-10-24T20:04:31.605861+03:00**, Africa/Nairobi. The operator's
instruction, “Authorize final production. Proceed to the production,” clears the
activation authorization gate. It does not supply the missing technical evidence
required by the [deployment gates](pr143-production-deployment-gates-2026-10-24.md)
or change the [fixed 100 GB budget](fixed-storage-budget.md).
**The cutover was withheld; production is still fenced and readiness is false.**

## Passed boundary and candidate checks

All seven CI checks passed for runtime candidate
`cf47ca72510d1372eb5c727b26417a82fe5233ef`; PR #143 has no inline review threads.
The PR remains draft and unmerged. Its immutable source still has 180 package
Python modules with implementation SHA-256
`f9b8ecbce83be2ef854ef8fc4afd13646f01b9a300129658cef1870bb65a13d1`.
This report changes no runtime module, setting or seal. Passing CI is not a
production migration or admitted-cycle receipt.

At **20:02:51 EAT**, a fresh process census found no matching worker, publisher or
rehearsal writer. The actual shared owner was available and held only for the
read-only preflight; active JobAgg cron entries were absent. Production
configuration, native maintenance marker, health and publication-gate hashes
matched the preserved boundary. M0's 152-module implementation was unchanged.
The gate remains complete/published with database transactions complete.

The persistent SSD volume UUID matches the prior attestation, but the device
number is now 16777259 rather than 16777252. Owner inode 4728 is unchanged.
This verifies the observed mount, not renewal of policy/publication/dependency
receipts. Their explicit bounded remount re-attestation remains necessary.

The unchanged health record also reports `accepted_work_pending=true`,
`pending_accepted_run_id=null` and `invocation_alive=true`, while `active_attempts`
is empty and the latest finished timestamp follows the invocation timestamp.
The legacy dispatcher state reports incomplete worker status and published
publication status. No active process was found, but these flags cannot certify
an empty accepted-work boundary. Resolve the exact durable accepted/pending
controls during handoff; do not edit the flags to imply a clean boundary.

## Complete measured-artifact dependency intersection

A bounded, owner-held, read-only census examined all **18,546** previously
measured SSD artifacts against **50,067** copied native catalogue dependencies,
174 publication generations and 2,368 dispatcher runs. It hashed 255 protection
controls and identified 82 protected generation prefixes. The check took
**14.178 seconds** and read no artifact payload or HDD payload.

Every selected artifact still exists with its previously measured inode, size,
mtime and ctime; only its device field differs after the remount. No selected
path was an alias or hardlink. This metadata observation is not a fresh payload
hash certification or permission to bypass the remount review.

| Artifact family | Catalogue-dependent files | Previous charged bytes |
| --- | ---: | ---: |
| Plans | 174 | 15,661,223,936 |
| Beforeimages | 9,626 | 17,408,786,432 |
| Exports | 4,434 | 273,505,406,976 |
| **Total** | **14,234** | **306,575,417,344** |

Those catalogue-dependent files contain **306,543,299,651 logical bytes** now.
This subset alone exceeds the complete 100,000,000,000-byte active ceiling,
without other essential data, protected hot copies, runtime support or scratch.
Some files also have generation/recovery protection; these are overlapping gates,
not additional file counts. The previously reported 442,900,754,002-byte native
scope is still a historical lower-bound inventory, not a newly completed census
of all candidate/staging/HDD roots.

All 14,234 dependency rows have a NULL catalogue hash. This is an explicit
unverified-content state in the bounded historical import, **not evidence that
the retained bytes are corrupt**. Separate original producer verification already
bound all selected plans/exports, and the later strict plan pass bound all 9,626
beforeimages. Those independent proofs must be incorporated through an explicit
producer-backed migration. A hash computed from today's file or a boolean claim
of reader compatibility cannot substitute for that bridge.

The current `historical_storage.attest_catalogue` deliberately refuses a NULL
original catalogue hash. Its offline grant reader does not migrate ordinary
native consumers. The additive executor retains every original; the guarded
removal executor rejects catalogue dependencies and has **no production mode**.
Consequently the current implementation cannot retire this necessary subset or
establish the requested budget. This is a demonstrated runtime/migration gap,
not a missing final permission or a failing CI test.

## Required technical closure before retrying cutover

1. Implement and review explicit original producer-backed representation bindings
   for the catalogue's NULL-hash entries. Preserve original identities, NULL
   provenance, dispositions, held history and all charged attempts. Adapt every
   applicable native/offline reader and restore path; never bulk-fill NULL hashes
   or silently replace canonical paths with aliases.
2. Validate a separately sealed production-root migration and compatible rollback
   procedure. Repeat real historical archival/removal on an isolated final layout,
   retaining current/previous, unresolved and recovery dependencies. The two-file
   synthetic component rehearsal is not this proof. The accepted native rehearsal
   batch remains preserved without reconfiguration or rerun.
3. Complete the writer/root registry and actual installed/peak category census.
   Enforce every category, the total 100 GB ceiling and 20 GiB physical reserve
   after restore/next-write growth. Preview compressed sizes and logical removal
   credit grant no installed capacity.
4. Complete the exact-layout fresh admitted-cycle, stock dispatcher watchdog,
   recovery, owner/maintenance handoff and rollback rehearsal. Validate the measured
   phase/publication/audit settings and exact pending-work boundary. Renew physical
   bindings explicitly, then review/merge and stage the accepted immutable source
   and coherent production configuration.
5. Only after these gates pass, perform the already authorized owner-held production
   handoff, remove only the operation's unchanged marker, run the bounded first
   production cycle and verify acceptance/commits/exports/readback separately.
   Refence on uncertainty; retain M0 and all legitimate intervening data.

R2/R4 preserve historical holds, useful bytes, ownership and recovery evidence;
R5/R6 require bounded migration and exact release/configuration validation;
R7 identifies a concrete missing deployment requirement; R8 separates authorization,
CI, metadata observations, producer certification, rehearsal and activation.

Private preflight, catalogue-intersection cases/protection and authorization
receipts remain outside Git in the existing preparation directory. No production
payload/configuration/selector was changed, no original was retired, no HDD payload
was copied, and no production worker or publisher was started.
