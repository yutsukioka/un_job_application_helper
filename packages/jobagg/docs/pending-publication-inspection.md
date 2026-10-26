# Inspect pending publication metadata

`packages/jobagg/scripts/inspect_pending_publication.py` is a standalone, standard-library
command for the first, read-only stage of reconciliation. It supports the M1
`publication-work-3` / `jobagg-runtime-authority-3` schema. Older databases fail closed;
this tool neither installs nor migrates those tables. It does not import the worker
package and does not require a new worker implementation seal.

Run from the repository root, using Python 3.11 or newer:

```sh
python3 -B packages/jobagg/scripts/inspect_pending_publication.py \
  --config /absolute/path/to/dispatcher.proposed.json \
  --max-seconds 5 --max-units 10000
```

Alternatively use `--database /absolute/path/to/jobs.sqlite3`. The config option reads
only `publication_worker_database`; a relative database path resolves against the
config's directory. Every invocation saves a full private report outside production,
including partial and error results. It prints a JSON summary containing `report_file` to Terminal.
All observed exact unit/version records are in the saved file, regardless of the
optional sample size. The summary omits the potentially long full record list.

On macOS the report is `/private/tmp/jobagg-pending-inspection-<random>/report.json`;
on Linux it is `/tmp/jobagg-pending-inspection-<random>/report.json`. The directory
has mode 0700 and the file mode 0600. Each invocation gets a new directory, so old
reports are not overwritten. The location uses the OS temp root rather than a
possibly production-owned `TMPDIR`. These are temporary files: retain an important
report privately elsewhere, outside production and Git, before OS cleanup.

To additionally save the Terminal summary, use a separate new private file:

```sh
(umask 077; set -C
 python3 -B packages/jobagg/scripts/inspect_pending_publication.py \
   --config /absolute/path/to/dispatcher.proposed.json \
   > /private/tmp/jobagg-pending-inspection.json)

(umask 077; set -C
python3 -B \
  /Users/yutsukioka2/.codex/worktrees/jobagg-reconciliation-pending/un_job_application_helper/packages/jobagg/scripts/inspect_pending_publication.py \
  --config /Users/yutsukioka2/git/un_job_application_helper/reports/jobagg-deterministic-fetch-2026-09-14/dispatcher.proposed.json \
  > /private/tmp/jobagg-pending-inspection.json)
```
The command creates only its private observer report and directory. It creates no
production checkpoints, maintenance markers or owner locks. It does not contact
providers, read historical payloads,
compute content hashes, invoke publication, acknowledge work, release holds, or
change cron/configuration/authority. Keep reports private: the saved report contains
internal unit identities and versions even when `--sample` is zero.

## Read behavior and limits

Both stages query private filesystem copy-on-write clones, opened with `mode=ro`,
`query_only=ON`, zero busy wait and a restricted SQL authorizer. They never open a
production SQLite connection and never use `immutable=1`. An idle WAL-mode source
without sidecars works: SQLite creates any necessary WAL/SHM bookkeeping only
beside the private clone. Existing WAL files are cloned too, so committed WAL-only
state is retained even if SHM is missing. No production owner or SQLite locks are
taken by the tool.

Capture checks source database/WAL/SHM/journal device, inode, size and nanosecond
mtime/ctime before and after cloning. A rollback journal or changing source rejects
the capture. Source signatures are checked again between pages. The clones are
removed when the connection closes, including error paths. Read access still uses
some I/O and cannot promise zero runtime impact.

Cloning uses macOS `clonefile` or Linux `FICLONE`; there is **no full byte-copy
fallback**. Unsupported filesystems produce a saved error. Working clones live in
a private `.jobagg-readonly-*` directory under the OS temp root if it is on the
source filesystem. Otherwise they live in a private directory at the source volume
root, outside the production application subtree. macOS production therefore uses
`/Volumes/MacbookAirM2/.jobagg-readonly-*`, removed after inspection, while JSON
reports remain under `/private/tmp`. Cloning shares data blocks; it does not copy
4 GB of database payload. The command needs permission to create its private
working directory, but no write permission to the production data directory.

The pending index supplies ordered pages, joined to exact native dirty versions.
Each page ends its read transaction before processing and a 25 ms pause. There is
no whole-scan snapshot pinning the WAL. Defaults: 100 rows/page, 10,000 observed
units, five seconds, no Terminal identity sample. Every observed record is saved.
Maximums: 500 rows/page, 100,000 units,
30 seconds and 100 sample identities. A SQLite progress handler bounds query work;
the time limit is cooperative, not a hard operating-system I/O deadline.

The report records UTC timestamps, limits, authority epoch/revision/configuration,
publication contract, observed pending counts grouped by source, kind, reason and
origin operation, and the oldest observed pending timestamp. It counts **current
pending publication versions**, not vacancies fetched, files, historical versions,
requests, or all accepted/held work. Held and acknowledged rows are excluded.

`complete=true` and exit status 0 mean only that the pending metadata enumeration
finished without a detected intervening database commit. Across pages, changes to
SQLite's connection data version or the native boundary (including the source
transaction token) stop enumeration. Time/row limits, contention, missing or
incompatible schema and moving state produce `complete=false` and exit status 2.
Counts then describe only the observed prefix; zero is not an empty-queue proof.
There is no automatic retry or background process. A busy or moving result can be
inspected later without disturbing the current cron owner.

Even a complete Step 1 report has `publication_verified=false` and
`acknowledgement_authorized=false`. It does not inspect destination stores or export
readback, prove main-text completeness, certify safe acknowledgement, or produce an
executable write plan. It is input to the read-only Step 2 comparison below; neither
stage grants permission to clear pending work.

## Step 2: compare the saved inventory with the current queue

Yes, Step 2 requires the **saved Step 1 report**, including exact identities and
versions. The old schema-1 summary/sample is insufficient; rerun Step 1 to obtain a
schema-2 report. Use the exact `report_file` path printed by Step 1:

```sh
python3 -B packages/jobagg/scripts/compare_pending_publication.py \
  --inspection /private/tmp/jobagg-pending-inspection-REPLACE/report.json

python3 -B /Users/yutsukioka2/.codex/worktrees/jobagg-reconciliation-pending/un_job_application_helper/packages/jobagg/scripts/compare_pending_publication.py \
  --inspection /private/tmp/jobagg-pending-inspection.json
  
```

Step 2 reads that file (at most 32 MiB), records its SHA-256, checks the worker
file identity, epoch/configuration/publication-contract bindings, and rechecks the
saved units in short read-only pages. It does not import worker runtime code or
perform schema migration. It uses the same time/page/row limits and SQLite reader
protections as Step 1. Its detailed report is saved in a new private
`jobagg-pending-comparison-<random>/report.json` directory in the OS temp root;
Terminal shows `report_file` and category counts.

| Category | Meaning / next action |
| --- | --- |
| `already_acknowledged_in_native_queue` | Cron has already acknowledged that exact version. No additional queue write is proposed. This is a queue-state observation, not new destination verification. |
| `pending_requires_publication_or_verified_readback` | Same version remains pending. It needs ordinary publication or an independent destination/export readback proof before any acknowledgement could be considered. |
| `held_requires_review` | Same version is now held; retain the hold and review its reason. |
| `version_changed_reinspect` | Version changed; the old inspection cannot authorize action on the replacement. |
| `missing_requires_review` / `identity_changed_requires_review` | Missing or inconsistent unit identity; retain uncertainty, not an inferred successful publication or closure. |
| `unknown_disposition_requires_review` | Unrecognized state; do not infer eligibility or success. |

Cron may legitimately advance between the two commands. Revision, phase and
transaction-token changes **between Step 1 and Step 2** are therefore allowed;
epoch/configuration/contract/file-identity changes require a new Step 1 inspection.
A change **during Step 2** stops its comparison and marks it partial.

`complete=true` in Step 2 means all **saved input units** were compared without a
detected intervening commit. It never means the whole production backlog is zero
or all destination content matches. `input_inventory_complete=false` explicitly
preserves a partial Step 1 scope; Step 2 can still inspect those exact records.
The result always has `publication_verified=false` and
`acknowledgement_authorized=false`.

This increment implements queue-state comparison, not independent public row,
main-text, document and consolidated-export certification. It contains **no guarded
writer**. Pending metadata alone cannot identify a safe bulk acknowledgement set;
those destination proofs and normal owner-held gates remain prerequisites to any
later write stage. Do not use the category counts to update the queue manually.

## Validation

Focused tests use tiny synthetic databases, including a concurrent WAL writer,
changing metadata without a revision increment, bounded partial enumeration,
malformed authority/version metadata, denied SQL mutations, private saved output,
Step 1-to-Step 2 scope preservation, and exact-version comparison across natural
queue progress. These tests do not establish destination content completeness or A8.

```sh
python3 -B -m pytest -q packages/jobagg/tests/test_inspect_pending_publication.py
```
