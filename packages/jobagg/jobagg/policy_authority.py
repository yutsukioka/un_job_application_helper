"""Explicitly migrated, indexed all-writer quota authority.

Legacy JSON is retained as evidence, never a warm-start admission source. The
external head is a conservative restore fence: interrupted transactions or a
stale database cannot silently become an empty quota window. A reviewed repair
may reconcile a pending head; ordinary startup never rebuilds it from history.
"""

from contextlib import closing, contextmanager, nullcontext
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import time
import uuid

from jobagg.atomic_files import atomic_write_text
from jobagg.publication_fence import _owner

SCHEMA = "jobagg-policy-authority-1"


def _dump(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _schema_hash(conn):
    return hashlib.sha256(
        _dump(
            [
                tuple(row)
                for row in conn.execute("SELECT name,type,sql FROM sqlite_master ORDER BY name")
            ]
        ).encode()
    ).hexdigest()


def _signature(path):
    path = Path(path)
    if path.resolve() != path or not path.is_file():
        raise ValueError("policy_authority_path_missing_or_aliased")
    info = path.stat()
    return [info.st_dev, info.st_ino]


class PolicyAuthority:
    def __init__(self, policy):
        self.policy = policy
        self.path = policy.root / "authority.sqlite3"
        self.head = policy.root / "authority.json"
        self.owner_fd = None
        self.storage = None
        self.check_deadline = lambda: None

    @contextmanager
    def connect(self, *, write=False):
        self.check_deadline()
        if not self.head.exists():
            raise ValueError("policy_authority_missing_explicit_migration_required")
        head = json.loads(self.head.read_text())
        if (
            head.get("schema") != SCHEMA
            or head.get("pending") is not False
            or head.get("database_identity") != _signature(self.path)
            or head.get("owner_identity") != _signature(self.policy.owner)
        ):
            raise ValueError("policy_authority_restore_or_pending_repair_required")
        with closing(sqlite3.connect(self.path.as_uri() + "?mode=rw", uri=True, timeout=1)) as conn:
            conn.row_factory = sqlite3.Row
            conn.create_function("jobagg_policy_write_allowed", 0, lambda: 0)
            if _schema_hash(conn) != head.get("schema_sha256"):
                raise ValueError("policy_authority_schema_or_fence_changed")
            if time.time() < head.get("last_wall_at", 0):
                raise ValueError("policy_clock_regressed_review_required")
            conn.execute("PRAGMA synchronous=FULL")
            conn.set_progress_handler(lambda: self._expired(), 256)
            value = dict(conn.execute("SELECT * FROM authority WHERE id=1").fetchone())
            if (
                value["epoch"] != head["epoch"]
                or value["revision"] != head["revision"]
                or value["transaction_id"] != head["transaction_id"]
            ):
                raise ValueError("policy_authority_revision_or_restore_mismatch")
            self._verify_boundary(head)
            if write:
                if self.owner_fd is None:
                    raise ValueError("policy_authority_live_owner_required")
                _owner(self.owner_fd, self.policy.owner)
                conn.create_function(
                    "jobagg_policy_write_allowed",
                    0,
                    lambda: int(os.fstat(self.owner_fd).st_ino == self.policy.owner.stat().st_ino),
                )
                with (
                    self.storage.policy_database_write(conn, self.path)
                    if self.storage is not None
                    else nullcontext()
                ):
                    transaction_id = uuid.uuid4().hex
                    # Durable pending head precedes any SQL mutation. Failure is a
                    # repair state even if SQLite rolled back: no automatic refund.
                    atomic_write_text(
                        self.head,
                        _dump({**head, "pending": True, "next_transaction_id": transaction_id}),
                    )
                    try:
                        with conn:
                            conn.execute(
                                "UPDATE authority SET revision=revision+1,transaction_id=? WHERE id=1",
                                (transaction_id,),
                            )
                            yield conn
                        head.update(
                            revision=value["revision"] + 1,
                            transaction_id=transaction_id,
                            last_wall_at=time.time(),
                        )
                        atomic_write_text(self.head, _dump(head))
                    except BaseException:
                        raise
            else:
                yield conn
        self.check_deadline()

    def _expired(self):
        try:
            self.check_deadline()
        except (TimeoutError, ValueError):
            return 1
        return 0

    def _verify_boundary(self, head):
        from jobagg.publication_fence import _file_identity

        for path, identity in head.get("immutable_controls", {}).items():
            if _file_identity(path) != identity:
                raise ValueError("policy_bootstrap_control_changed")
        if (self.policy.root / "attempt_index.json").read_text() != _dump(
            {"schema": SCHEMA, "old_writers_fenced": True}
        ):
            raise ValueError("policy_legacy_writer_fence_changed")
        # Directory metadata detects a noncooperating new reservation. Cold
        # evidence content is checked by bounded catalogue audit, not this test.
        for name, proof in head["legacy_boundaries"].items():
            info = (self.policy.root / name).stat()
            if [info.st_dev, info.st_ino, info.st_mtime_ns, info.st_ctime_ns] != proof:
                raise ValueError("policy_legacy_boundary_changed")

    def revision(self):
        with self.connect() as conn:
            return conn.execute("SELECT revision FROM authority WHERE id=1").fetchone()[0]

    def due(self, source_id, now, *, minimum, jitter, batch, pause):
        with self.connect() as conn:
            summary = conn.execute(
                "SELECT * FROM sources WHERE source_id=?", (source_id,)
            ).fetchone()
            if summary is None or summary["n"] == 0:
                return now
            if summary["pause"] != pause:
                raise ValueError("policy_contract_changed_reviewed_projection_migration_required")
            last = conn.execute(
                "SELECT effective_end FROM attempts WHERE source_id=? AND kind='detail' ORDER BY sequence DESC LIMIT 1",
                (source_id,),
            ).fetchone()[0]
            gap = conn.execute(
                "SELECT sequence FROM attempts WHERE source_id=? AND kind='detail' AND batch_break=1 ORDER BY sequence DESC LIMIT 1",
                (source_id,),
            ).fetchone()
            count = summary["n"] - (gap[0] if gap else 1) + 1
            due = last + minimum + jitter
            if batch and count >= batch:
                due = max(due, last + pause)
            if source_id == "unicef_pageup":
                # The tenth newest end is the exact legacy overfull-window gate.
                tenth = conn.execute(
                    "SELECT effective_end FROM attempts WHERE source_id=? AND kind='detail' AND effective_end>? ORDER BY effective_end DESC LIMIT 1 OFFSET 9",
                    (source_id, now - 3600),
                ).fetchone()
                if tenth:
                    due = max(due, tenth[0] + 3600)
            return max(now, due)

    def reserve(self, event):
        with self.connect(write=True) as conn:
            _insert(conn, event)

    def finish(self, attempt_id, status):
        with self.connect(write=True) as conn:
            row = conn.execute(
                "SELECT * FROM attempts WHERE attempt_id=?", (attempt_id,)
            ).fetchone()
            if row is None:
                raise ValueError("policy_finish_without_reservation")
            end = time.time()
            if end < row["started_at"]:
                raise ValueError("policy_clock_regressed_review_required")
            event = json.loads(row["event_json"])
            if event.get("finished_at") is not None:
                if event.get("status") != status:
                    raise ValueError("policy_conflicting_duplicate_finish")
                return
            event.update(finished_at=end, status=status)
            conn.execute(
                "UPDATE attempts SET effective_end=?,event_json=? WHERE attempt_id=?",
                (end, _dump(event), attempt_id),
            )
            if row["kind"] == "detail":
                following = conn.execute(
                    "SELECT attempt_id,started_at FROM attempts WHERE source_id=? AND kind='detail' AND sequence=?",
                    (row["source_id"], row["sequence"] + 1),
                ).fetchone()
                pause = conn.execute(
                    "SELECT pause FROM sources WHERE source_id=?", (row["source_id"],)
                ).fetchone()[0]
                if following:
                    conn.execute(
                        "UPDATE attempts SET batch_break=? WHERE attempt_id=?",
                        (
                            int(bool(pause and following["started_at"] - end >= pause)),
                            following["attempt_id"],
                        ),
                    )

    def audit(self, *, limit, now, maximum_lag_seconds, reviewed_catchup=False):
        """Incremental exact evidence audit; persistent cursor, explicit latency.

        Quota rows are retained even when evidence is damaged. Failed or overdue
        audit prevents admission; it never empties the source's charge history.
        """
        if type(limit) is not int or limit <= 0 or maximum_lag_seconds <= 0:
            raise ValueError("finite_policy_audit_contract_required")
        # Overdue admission is a read-only hold, not an uncertain write. An
        # explicitly invoked catch-up still checks finite pages and retains all
        # charges; it never marks a partial audit complete.
        with self.connect() as conn:
            completed = conn.execute(
                "SELECT audit_completed_at FROM authority WHERE id=1"
            ).fetchone()[0]
        if now - completed > maximum_lag_seconds and not reviewed_catchup:
            raise ValueError("policy_audit_overdue_explicit_repair_required")
        with self.connect(write=True) as conn:
            control = conn.execute(
                "SELECT audit_cursor,audit_completed_at FROM authority WHERE id=1"
            ).fetchone()
            rows = conn.execute(
                "SELECT path,sha256 FROM evidence WHERE path>? ORDER BY path LIMIT ?",
                (control["audit_cursor"], limit),
            ).fetchall()
            for row in rows:
                self.check_deadline()
                if _sha(row["path"]) != row["sha256"]:
                    raise ValueError("policy_evidence_missing_or_changed_charges_retained")
            more = (
                rows
                and conn.execute(
                    "SELECT 1 FROM evidence WHERE path>? LIMIT 1", (rows[-1]["path"],)
                ).fetchone()
            )
            conn.execute(
                "UPDATE authority SET audit_cursor=?,audit_completed_at=? WHERE id=1",
                (rows[-1]["path"] if more else "", control["audit_completed_at"] if more else now),
            )
        return len(rows)


def _insert(conn, event):
    prior = conn.execute(
        "SELECT event_json FROM attempts WHERE attempt_id=?", (event["attempt_id"],)
    ).fetchone()
    if prior:
        if prior[0] == _dump(event):
            return
        raise ValueError("policy_conflicting_duplicate_identity")
    source, kind, start = event["source_id"], event["kind"], event["started_at"]
    summary = conn.execute("SELECT * FROM sources WHERE source_id=?", (source,)).fetchone()
    if summary is None:
        raise ValueError("policy_source_outside_migrated_scope")
    if kind == "detail":
        last = conn.execute(
            "SELECT started_at,effective_end FROM attempts WHERE source_id=? AND kind='detail' ORDER BY sequence DESC LIMIT 1",
            (source,),
        ).fetchone()
        if last and start < last["started_at"]:
            raise ValueError("policy_clock_or_import_order_regressed")
        sequence = summary["n"] + 1
        batch_break = int(
            not last or bool(summary["pause"] and start - last["effective_end"] >= summary["pause"])
        )
        conn.execute("UPDATE sources SET n=n+1 WHERE source_id=?", (source,))
    else:
        sequence, batch_break = None, 0
    end = event.get("finished_at") or event.get("eligibility_accounting_until") or start
    conn.execute(
        "INSERT INTO attempts VALUES(?,?,?,?,?,?,?,?)",
        (event["attempt_id"], source, kind, sequence, start, end, batch_break, _dump(event)),
    )


def migrate(policy, *, owner_fd, epoch, pauses, reviewed_writers, check_deadline=lambda: None):
    """Compatibility driver for the explicit checkpointed preparation API.

    Each step durably commits a finite page; a caller's migration deadline can
    stop/restart this driver. No worker startup calls this historical operation.
    """
    from jobagg.policy_migration import step

    while True:
        progress = step(
            policy,
            owner_fd=owner_fd,
            epoch=epoch,
            pauses=pauses,
            reviewed_writers=reviewed_writers,
            limit=256,
            check_deadline=check_deadline,
        )
        if progress["complete"]:
            return PolicyAuthority(policy)
