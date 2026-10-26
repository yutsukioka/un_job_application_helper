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
config's directory. The command prints JSON to standard output. To save a report,
redirect it to a new private file **outside production**, for example:

```sh
(umask 077; set -C
 python3 -B packages/jobagg/scripts/inspect_pending_publication.py \
   --config /absolute/path/to/dispatcher.proposed.json \
   > /private/tmp/jobagg-pending-inspection.json)
```

The command itself creates no report files, directories, checkpoints, maintenance
markers or owner locks. It does not contact providers, read historical payloads,
compute content hashes, invoke publication, acknowledge work, release holds, or
change cron/configuration/authority. Keep reports private: source names and an
optional `--sample 10` may expose internal unit identities and versions.

## Read behavior and limits

SQLite opens with `mode=ro`, `query_only=ON`, zero busy wait, and an authorizer
restricted to the four native metadata tables and read operations. It never uses
`immutable=1` on a live WAL database, since that could miss committed WAL state.
For a WAL database it requires readable, pre-existing WAL and SHM sidecars; missing
sidecars cause an input error rather than a deliberate sidecar-creation attempt.
As with any normal SQLite reader, brief SQLite reader locks and shared-memory
bookkeeping are possible. This is not the JobAgg owner lock or an application write;
read-only access still consumes some I/O and cannot promise zero runtime impact.
Do not change filesystem permissions, checkpoint the live database, or copy the
production database to work around an access error.

The pending index supplies ordered pages, joined to exact native dirty versions.
Each page ends its read transaction before processing and a 25 ms pause. There is
no whole-scan snapshot pinning the WAL. Defaults: 100 rows/page, 10,000 observed
units, five seconds, no identity sample. Maximums: 500 rows/page, 100,000 units,
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

Even a complete report has `publication_verified=false` and
`acknowledgement_authorized=false`. It does not inspect destination stores or export
readback, prove main-text completeness, certify safe acknowledgement, or produce an
executable write plan. A later reconciliation stage would need exact current-version
publication evidence and the normal owner-held publication gates; this tool grants
no permission to clear pending work.

## Validation

Focused tests use tiny synthetic databases, including a concurrent WAL writer,
changing metadata without a revision increment, bounded partial enumeration,
malformed authority/version metadata, and denied SQL mutations. No live production
run, provider access, or A8 validation is part of this change.

```sh
python3 -B -m pytest -q packages/jobagg/tests/test_inspect_pending_publication.py
```
