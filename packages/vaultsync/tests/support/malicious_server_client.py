"""C24 process adapter. Synthetic test state only; no network credentials."""

import base64
import hashlib
import json
import os
import sys
import time
from pathlib import Path

from vaultsync.authenticated_state_view import StateViewError
from vaultsync.sync_queue import (
    DurableEncryptedInbox,
    DurableEncryptedOutbox,
    EncryptedPatchOperation,
)
from vaultsync.sync_recovery import GuardedSyncState


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def publish(output, value):
    temporary = Path(str(output) + ".pending")
    with temporary.open("wb") as stream:
        stream.write(canonical(value))
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, output)


def run(mode, directory, plan_path, output):
    plan = json.loads(Path(plan_path).read_bytes())
    results = {}
    for name, scenario in plan["scenarios"].items():
        root = Path(directory) / name
        root.mkdir(parents=True, exist_ok=True)
        key = hashlib.sha256(b"c24-synthetic-client-storage").digest()
        c = GuardedSyncState(
            root / "accepted-recovery.state",
            encryption_key=key,
            **{**plan["context"], "trusted_signer": base64.b64decode(plan["public_b64"])},
        )
        inbox = DurableEncryptedInbox(root / "inbox.state", encryption_key=key)
        outbox = DurableEncryptedOutbox(root / "outbox.state", encryption_key=key)
        if mode == "prepare":
            c.initialize()
        categories = []
        before = c.checkpoint()
        actions = (
            scenario["baseline"]
            if mode == "prepare"
            else scenario["attack"]
            if mode == "attack"
            else []
        )
        for action in actions:

            def interrupt(point, action=action):
                if action.get("stop_after") == point:
                    publish(output, {"interrupted_after": point})
                    while True:
                        time.sleep(60)

            try:
                if "peer" in action:
                    peer = json.loads(Path(action["peer"]).read_bytes())[name]["history"]
                    c.compare_evidence(peer)
                    categories.append("COMPATIBLE_PREFIX_NOT_FRESHNESS")
                else:
                    p = plan["packets"][action["packet"]]
                    accepted = c.ingest(
                        p["view"], p["registry"], p["collection"], base64.b64decode(p["opaque_b64"])
                    )
                    interrupt("admission")
                    # A valid idempotent admission can still have unfinished queue receipts.
                    op = EncryptedPatchOperation.from_dict(p["operation"])
                    outbox.enqueue(op)
                    interrupt("outbox")
                    pending = inbox.pending_operations()
                    if pending and pending != (op,):
                        raise RuntimeError("C24 unexpected pending receipt")
                    if inbox.cursor != p["view"]["root"] and not pending:
                        inbox.stage_page(
                            expected_cursor=inbox.cursor,
                            next_cursor=p["view"]["root"],
                            operations=[op],
                        )
                    interrupt("inbox")
                    while inbox.apply_next(lambda _: None) is not None:
                        pass
                    interrupt("receipt")
                    outbox.confirm_remote_acceptance(op.operation_id)
                    categories.append("ACCEPTED" if accepted else "IDEMPOTENT")
            except StateViewError as error:
                categories.append(str(error))
        calls = []
        try:
            c.automatic_sync(lambda calls=calls: calls.append(True))
        except StateViewError as error:
            assert str(error) == "ATLAS_RECOVERY_PENDING"
        checkpoint, recovery, evidence = c.checkpoint(), c.recovery(), c.evidence()
        results[name] = {
            "before": before,
            "checkpoint": checkpoint,
            "recovery": recovery,
            "history": c.export_evidence(),
            "evidence": evidence,
            "categories": categories,
            "automatic_sync_fenced": not calls,
            "cursor": inbox.cursor,
            "pending_outbox": len(outbox.pending_operations()),
            "state_sha256": hashlib.sha256(canonical(checkpoint)).hexdigest(),
            "recovery_sha256": hashlib.sha256(canonical(recovery)).hexdigest(),
        }
    publish(output, results)
    if mode != "inspect":
        while True:
            time.sleep(60)


if __name__ == "__main__":
    run(*sys.argv[1:])
