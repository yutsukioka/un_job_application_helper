# PR #143: retain the 100 GB ceiling with lossless storage

Observed 23 October 2026, Africa/Nairobi. The user selected lossless archival /
compression preparation, retaining the fixed 100,000,000,000-byte active ceiling.
This review authorizes no deletion, production compression, migration or activation.
M0, the production maintenance marker and all sole backup copies stay intact.

## Measured starting point

A metadata-only census charged **442,021,101,718 bytes** across **571,742 entries**
in the active production tree. Charge is the larger of logical and allocated bytes;
hard-linked inodes count once, APFS clones count separately. At least
**342,021,101,718 bytes plus working headroom** must leave the active charge.
Free SSD space and a compressed sample do not satisfy this gate.

| Artifact family | Census charge (bytes) | Preservation / reader constraint |
| --- | ---: | --- |
| 128 historical publication snapshots | 90,428,928,000 | Immutable SQLite images, exact original snapshot hashes; restore before SQLite opens them |
| 174 generation plans | 15,661,223,936 | Original JSON bytes and hashes; bounded historical readers currently open original paths |
| 9,626 beforeimage JSON files | 17,408,786,432 | Recovery and rollback must retain exact old values and archive associations |
| 2,232 newer export JSON files | 137,951,272,960 | Generation receipts bind original bytes; current sealed exports remain ordinary files |
| 2,202 prior export JSON files | 135,554,134,016 | Recovery before-exports; preserve exact generation references |

Families are selected by filename/location; these counts do not certify that every
artifact is eligible for retirement. The strict native inventory remains incomplete:
the active tree contains escaping links for `.venv/bin/python3.12` and
`dispatcher.log`. Explicitly register their actual managed targets and writers, or
prepare a reviewed relocation; silently excluding them is unacceptable.

Five read-only SSD measurements used gzip level 6. Each reconstructed the entire
original stream, compared its logical hash and removed its temporary measurement
files. Ratios were 0.134054 for one beforeimage, 0.204979 / 0.204972 for new / old
JSON exports, 0.157219 for a plan, and 0.236655 for a sealed historical snapshot.
Total measurement time was 19.829 seconds. These are **five samples**, not aggregate
capacity forecasts or reader approval. No HDD payload was copied.

A separate producer-receipt census found 8,618 export references representing
280,305,967,656 logical bytes, with 4,227 distinct expected content hashes totaling
140,889,636,482 bytes. Thus 139,416,331,174 bytes are potentially shared content.
This uses original producer hashes, not a fresh payload verification or permission
to replace files with hard links. Compression plus content sharing merits a full
measurement; neither potential saving alone proves the target can be met.

## Reader compatibility review

The existing [representation API](../jobagg/storage_compression.py) supports exact
logical-byte reconstruction and opt-in journaled retirement. Its existence does
not make ordinary file readers transparent consumers of compressed objects.

| Reader / writer boundary | Current finding | Required migration check |
| --- | --- | --- |
| Representation API and its standalone tests | Supports legacy, manifest and cold-archive representations | Exercise interrupted install, corrupt object, missing dictionary, full-stream verification and bounded restore |
| [Historical plan parser](../jobagg/historical_plans.py), [occurrence index](../jobagg/historical_occurrences.py) | Uses original canonical file paths and identity/hash pins | Stream through a reviewed bounded representation reader, or restore a selected original into bounded verified scratch; preserve logical producer paths |
| [Publication snapshot validation](../jobagg/publication_snapshot.py) | SQLite requires a real file and exact source/snapshot binding | Restore sealed immutable snapshots only, verify original bytes, re-attest physical identity explicitly; never compress active SQLite/WAL/SHM families |
| [Current artifact verifier](../jobagg/historical_artifacts.py), [accepted detail lineage](../jobagg/accepted_detail_lineage.py), listing/frame and document readers | Exact receipts, capture metadata and body hashes bind ordinary files | Add bounded logical readers and tests across capture → verifier → acceptance → projection; aliases alone cannot read an absent original |
| [Policy evidence audit](../jobagg/policy_evidence.py) | Explicit locations still point to ordinary files and original hashes | Preserve original bootstrap hash and reviewed locator; test audit against compressed-only evidence without copying HDD payloads |
| [Generation recovery](../jobagg/pipelines/live_publication.py) and before-export readers | Original JSON, beforeimages and export paths are reopened | Recover the same interrupted generation using compressed-only historical inputs; verify both destinations and all four export forms |
| Current destination databases and public exports | Native receipts bind database and sealed export identity | Keep live families hot; compressed archives cannot replace a current export beneath an existing identity receipt |
| Code rollback / prior deployed readers | Prior code does not universally understand the new manifest | Retain an exact bounded restore plan and compatible reader runtime; rehearse rollback without overwriting intervening live data |

**Reader compatibility is not approved.** No production retirement flag should be
set based on these sample tests. Installing a compressed representation while
retaining its original increases active usage until a verified, journaled handoff.

## Prepared migration sequence

1. Under the existing production marker and actual owner lock, seal a finite
   manifest of eligible immutable artifacts. Exclude active generations, current
   native databases and their sidecars, current public exports, unresolved claims,
   sole unverified fetched evidence and unresolved recovery dependencies. Preserve
   original logical paths, producer hashes, links and generation associations.
2. Complete the reader changes above in a separately reviewable increment. Test
   legacy-only, dual-representation and compressed-only modes, exact null/absent
   values, UTF-8 bytes, corruption, interruption, deadlines and restoration. Test
   recovery of an interrupted generation and code rollback using those readers.
3. Measure **every selected distinct content object** while reading it in place;
   compute a capacity plan from actual compressed sizes, categories, manifests,
   journals, scratch, restore reserve and the maximum simultaneous old/new files.
   Keep the HDD backup in place. Stop if measured savings cannot meet every fixed
   category ceiling and total ceiling with working headroom.
4. Prepare a bounded page executor: immutable input manifest, actual owner,
   original hash check, temporary compressed object, durable install intent,
   complete reconstructed-byte verification, atomic object/manifest install,
   restartable checkpoint and interruption tests. No original is removed in this
   preparation phase. Content sharing must pin every logical reference and
   preserve reference accounting; it is not an inferred deduplication grant.
5. Present the exact eligible inventory, measured before/after category charges,
   peak temporary allocation, reader-test results and rollback drill for explicit
   production migration authorization. Only then retire verified eligible
   originals one bounded journaled page at a time. A failed check preserves the
   original and stops that page; unresolved evidence never becomes disposable.
6. Repeat the complete strict inventory, including all registered sidecars,
   representation objects, manifests, journals and restore/scratch reservations.
   Require each fixed category below its ceiling, total at most 100 GB with the
   admitted next-write envelope, and at least the existing 20 GiB physical reserve.
   Run the complete admitted-cycle and recovery rehearsal with this exact layout.

The fixed categories remain essential 40 GB, publication 15 GB, evidence 10 GB,
diagnostics 5 GB and work 30 GB. This is cooperative accounting; do not describe
it as a filesystem quota. No category borrowing, scope exclusion or larger ceiling
is proposed. The storage gate remains a deployment blocker until the measured,
reader-compatible migration is authorized and completed.

Private review receipts (payloads remain private):
`pr143-compression-readonly-preview/receipt.json`,
`pr143-export-sharing-preview.json`, and the whole-tree metadata census.
