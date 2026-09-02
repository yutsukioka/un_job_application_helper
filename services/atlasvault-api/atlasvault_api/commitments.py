"""Bounded append admission, not a freshness oracle or fork-resolution authority."""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Annotated, Literal

from cryptography.exceptions import InvalidSignature
from pydantic import BaseModel, ConfigDict, Field
from vaultsync.authenticated_state_view import EMPTY_REGISTRY, LIMIT, ZERO, _verified

Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
Identifier = Annotated[
    str, Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._~-]+$")
]
Counter = Annotated[int, Field(strict=True, ge=1, le=9007199254740991)]


class StateViewModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    format: Literal["atlasvault-authenticated-state-view"]
    version: Literal[2]
    account_id: Identifier
    vault_id: Identifier
    sequence: Counter
    previous_root: Digest
    collection_root: Digest
    registry_root: Digest
    previous_registry_root: Digest
    key_epoch: Counter
    root: Digest
    signature_b64: Annotated[
        str, Field(min_length=88, max_length=88, pattern=r"^[A-Za-z0-9+/]+==$")
    ]


class CommitmentAppendResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    commitment: StateViewModel
    appended: bool


class CommitmentConflict(ValueError):
    def __init__(self):
        super().__init__("Commitment conflict.")


class CommitmentLog:
    """SQLite serializes independent connections; the service is still single-instance."""

    def __init__(self, path: Path | str = ":memory:"):
        self._lock = threading.RLock()
        self._db = sqlite3.connect(
            str(path), timeout=10, check_same_thread=False, isolation_level=None
        )
        self._db.execute("PRAGMA synchronous=FULL")
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS commitments (account TEXT NOT NULL, vault TEXT NOT NULL, sequence INTEGER NOT NULL, root TEXT NOT NULL, authority BLOB NOT NULL, body TEXT NOT NULL, PRIMARY KEY(account,vault,sequence), UNIQUE(account,vault,root))"
        )

    def read(self, account_id, vault_id):
        with self._lock:
            return [
                json.loads(row[0])
                for row in self._db.execute(
                    "SELECT body FROM commitments WHERE account=? AND vault=? ORDER BY sequence",
                    (account_id, vault_id),
                )
            ]

    def append(self, account_id, vault_id, view, registry, public, initial_epoch):
        with self._lock:
            try:
                self._db.execute("BEGIN IMMEDIATE")
                rows = self._db.execute(
                    "SELECT authority,body FROM commitments WHERE account=? AND vault=? ORDER BY sequence",
                    (account_id, vault_id),
                ).fetchall()
                authority = bytes(rows[0][0]) if rows else public
                value = _verified(view, authority)
                if value["account_id"] != account_id or value["vault_id"] != vault_id:
                    raise CommitmentConflict()
                body = json.dumps(value, sort_keys=True, separators=(",", ":"))
                history = [json.loads(row[1]) for row in rows]
                for old in history:
                    if old["root"] == value["root"]:
                        if (
                            json.dumps(old, sort_keys=True, separators=(",", ":"))
                            != body
                        ):
                            raise CommitmentConflict()
                        self._db.execute("COMMIT")
                        return False
                last = history[-1] if history else None
                if (
                    value["sequence"] != len(history) + 1
                    or value["previous_root"] != (last["root"] if last else ZERO)
                    or value["previous_registry_root"]
                    != (last["registry_root"] if last else EMPTY_REGISTRY)
                    or value["registry_root"] != registry
                    or value["key_epoch"]
                    != (last["key_epoch"] if last else initial_epoch)
                    or len(history) >= LIMIT
                    or self._db.execute("SELECT COUNT(*) FROM commitments").fetchone()[
                        0
                    ]
                    >= 8192
                ):
                    raise CommitmentConflict()
                self._db.execute(
                    "INSERT INTO commitments VALUES(?,?,?,?,?,?)",
                    (
                        account_id,
                        vault_id,
                        value["sequence"],
                        value["root"],
                        authority,
                        body,
                    ),
                )
                self._db.execute("COMMIT")
                return True
            except (ValueError, TypeError, KeyError, InvalidSignature, sqlite3.Error):
                raise CommitmentConflict() from None
            finally:
                if self._db.in_transaction:
                    self._db.execute("ROLLBACK")
