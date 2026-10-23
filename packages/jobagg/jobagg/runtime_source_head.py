"""External source transaction head; ordinary SQLite restores cannot erase work.

The head precedes SQL mutation. Only an exact committed transaction identifier
can satisfy it. A rollback/unknown write remains an explicit repair state; a
later tick cannot turn that uncertainty into an empty accepted queue.
"""

from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
import re
from pathlib import Path
import uuid

from jobagg.atomic_files import atomic_write_text


def owner(fd, path):
    path = Path(path)
    expected, actual = path.stat(), os.fstat(fd)
    if path.resolve() != path or (expected.st_dev, expected.st_ino) != (
        actual.st_dev,
        actual.st_ino,
    ):
        raise ValueError("runtime_actual_owner_descriptor_differs")
    with path.open("r+") as probe:
        try:
            fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        else:
            raise ValueError("runtime_actual_owner_not_held")


def _state(conn):
    cursor = conn.execute("SELECT * FROM jobagg_runtime_state WHERE id=1")
    row = cursor.fetchone()
    return dict(zip([item[0] for item in cursor.description], row))


def _database(conn):
    path = Path(conn.execute("PRAGMA database_list").fetchone()[2]).absolute()
    if path.resolve() != path or not path.is_file():
        raise ValueError("runtime_source_database_alias_or_not_durable")
    info = path.stat()
    return {"path": str(path), "device": info.st_dev, "inode": info.st_ino}


def _head_root(conn, root):
    root = Path(root).absolute()
    database = Path(_database(conn)["path"])
    if (
        root.resolve() != root
        or not root.is_dir()
        or root == database.parent
        or root.is_relative_to(database.parent)
        or database.parent.is_relative_to(root)
    ):
        raise ValueError("runtime_source_head_root_must_be_external_and_direct")
    info = root.stat()
    if info.st_uid != os.geteuid() or info.st_mode & 0o022:
        raise ValueError("runtime_source_head_root_requires_private_operator_ownership")
    return root, {"device": info.st_dev, "inode": info.st_ino}


def path_for(conn, owner_path, *, head_root=None):
    database = _database(conn)
    key = hashlib.sha256(database["path"].encode()).hexdigest()
    name = Path(owner_path).name + ".source-" + key + ".json"
    if head_root is not None:
        return _head_root(conn, head_root)[0] / name
    return Path(str(owner_path) + ".source-" + key + ".json")


def validate(conn, value=None):
    value = value or _state(conn)
    bootstrap = json.loads(value["bootstrap_json"])
    path = bootstrap.get("restore_head_path")
    if path is None:
        return None  # Legacy routing/profile schemas keep their old contract.
    path = Path(path)
    if bootstrap.get("restore_head_root") is not None:
        root, identity = _head_root(conn, bootstrap["restore_head_root"])
        if identity != bootstrap.get("restore_head_root_identity") or path != path_for(
            conn, value["owner_path"], head_root=root
        ):
            raise ValueError("runtime_source_head_root_identity_changed")
    if path.resolve() != path or path.is_symlink() or path.stat().st_size > 65536:
        raise ValueError("runtime_source_head_missing_aliased_or_oversized")
    head = json.loads(path.read_text())
    if (
        head.get("schema") != "runtime-source-head-1"
        or type(head.get("minimum_revision")) is not int
        or head["minimum_revision"] < 0
        or not isinstance(head.get("transaction_id"), str)
        or re.fullmatch(r"[a-f0-9]{32}", head["transaction_id"]) is None
        or head.get("epoch") != value["epoch"]
        or head.get("database") != _database(conn)
        or head.get("owner_identity") != bootstrap["owner_identity"]
        or head.get("transaction_id") != value["source_transaction_id"]
        or head.get("minimum_revision", -1) > value["revision"]
    ):
        raise ValueError("runtime_source_restore_or_uncertain_transaction_requires_repair")
    return head


def activate(conn, *, owner_fd, head_root=None):
    value = _state(conn)
    owner(owner_fd, value["owner_path"])
    bootstrap = json.loads(value["bootstrap_json"])
    path = path_for(conn, value["owner_path"], head_root=head_root)
    if os.path.lexists(path):
        raise ValueError("runtime_source_head_exists_reviewed_epoch_handoff_required")
    token = uuid.uuid4().hex
    bootstrap["restore_head_path"] = str(path)
    if head_root is not None:
        root, identity = _head_root(conn, head_root)
        bootstrap.update(restore_head_root=str(root), restore_head_root_identity=identity)
    payload = (
        json.dumps(
            {
                "schema": "runtime-source-head-1",
                "epoch": value["epoch"],
                "database": _database(conn),
                "owner_identity": bootstrap["owner_identity"],
                "transaction_id": token,
                "minimum_revision": value["revision"],
            },
            sort_keys=True,
        )
        + "\n"
    )
    # An interrupted initial write remains explicit uncertainty. Never replace
    # an existing head, including another operation's dangling symlink.
    descriptor = os.open(
        path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600
    )
    with os.fdopen(descriptor, "w") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())
    directory = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)
    conn.execute(
        "UPDATE jobagg_runtime_state SET bootstrap_json=?,source_transaction_id=? WHERE id=1",
        (json.dumps(bootstrap, sort_keys=True), token),
    )


@contextmanager
def grant(
    conn, *, owner_fd, control=False, writer_check=lambda: False, commit_guard=True, authorizer=None
):
    value = _state(conn)
    owner(owner_fd, value["owner_path"])
    head = validate(conn, value)
    touched = False
    internal = False
    committed = False
    initial_changes = conn.total_changes

    def verify():
        actual, expected = os.fstat(owner_fd), Path(value["owner_path"]).stat()
        if (actual.st_dev, actual.st_ino) != (expected.st_dev, expected.st_ino):
            raise ValueError("runtime_actual_owner_identity_changed")
        current = _state(conn)
        if current["epoch"] != value["epoch"]:
            raise ValueError("runtime_owner_epoch_changed")
        return current

    def note():
        nonlocal touched, internal
        if touched or head is None:
            return
        current = verify()
        validate(conn, current)
        token = uuid.uuid4().hex
        new = {**head, "transaction_id": token, "minimum_revision": current["revision"]}
        atomic_write_text(
            Path(json.loads(current["bootstrap_json"])["restore_head_path"]),
            json.dumps(new, sort_keys=True) + "\n",
        )
        touched = True
        internal = True
        try:
            conn.execute(
                "UPDATE jobagg_runtime_state SET source_transaction_id=? WHERE id=1", (token,)
            )
        finally:
            internal = False

    def mutation():
        if committed or not (control or writer_check()):
            return 0
        verify()
        if not internal:
            note()
        return 1

    def control_allowed():
        if not control or committed:
            return 0
        verify()
        note()
        return 1

    conn.create_function("jobagg_runtime_write_allowed", 0, mutation)
    conn.create_function("jobagg_runtime_mutation_allowed", 0, mutation)
    conn.create_function("jobagg_runtime_control_allowed", 0, control_allowed)
    if commit_guard:

        def authorize(action, first, second, database, trigger):
            nonlocal committed
            if (
                authorizer is not None
                and authorizer(action, first, second, database, trigger)
                != __import__("sqlite3").SQLITE_OK
            ):
                return __import__("sqlite3").SQLITE_DENY
            if (
                action == __import__("sqlite3").SQLITE_TRANSACTION
                and first == "COMMIT"
                and (touched or conn.total_changes > initial_changes)
            ):
                try:
                    owner(owner_fd, value["owner_path"])
                    verify()
                    if not control and not writer_check():
                        raise ValueError("runtime_writer_phase_changed_before_commit")
                    validate(conn)
                    committed = True
                except (ValueError, OSError):
                    return __import__("sqlite3").SQLITE_DENY
            return __import__("sqlite3").SQLITE_OK

        conn.set_authorizer(authorize)
    try:
        yield
    finally:
        for name in (
            "jobagg_runtime_write_allowed",
            "jobagg_runtime_mutation_allowed",
            "jobagg_runtime_control_allowed",
        ):
            conn.create_function(name, 0, lambda: 0)
