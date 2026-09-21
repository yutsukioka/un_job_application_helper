"""Preview/export an explicit quiescent manual-run policy migration, no network.

Only --write takes the shared owner and writes a new reviewable bootstrap file.
The reviewed manual directory must be the complete prior writer history for the
chosen source scope. This is not an automatic assertion about unrelated writers.
"""

import argparse
from datetime import UTC, datetime
import hashlib
import gzip
import json
from pathlib import Path
import sqlite3
import time
from urllib.parse import urlsplit

from jobagg.pipelines.sync_source import load_sources
from jobagg.remediation_worker import dump, epoch, sha, shared_owner


def reconcile_attachment_holds(run, hosts, evidence):
    """Do not lose a separate attachment hold or reapply a proved historical one."""
    path = Path(run) / "evidence/attachments/host_blocks.json"
    if not path.exists():
        return
    blocks = json.loads(path.read_text())
    evidence[str(path)] = sha(path)
    for host, block in blocks.items():
        key = "host-" + hashlib.sha256(host.encode()).hexdigest()[:24]
        state = hosts.get(key)
        if state is None:
            raise ValueError("Attachment host hold lacks shared-state mirror: " + host)
        if state.get("stopped") is True:
            continue  # A current stricter shared hold is retained unchanged.
        basis = state.get("historical_stop_review_basis") or {}
        if (
            state.get("historical_stop_reviewed") is not True
            or state.get("inherited_stop") != block
        ):
            raise ValueError("Attachment/shared host hold conflict requires review: " + host)
        transition_path = Path(basis["transition_path"])
        if sha(transition_path) != basis["transition_sha256"]:
            raise ValueError("Historical attachment host release proof changed")
        transition = json.loads(transition_path.read_text())
        prior = Path(transition["prior_host_state_path"])
        browser = Path(transition["browser"]["path"])
        if (
            sha(prior) != transition["prior_host_state_sha256"]
            or sha(browser) != transition["browser"]["sha256"]
        ):
            raise ValueError("Historical host/browser review preimages changed")
        previous = json.loads(prior.read_text())
        if previous.get("stopped") is not True or previous.get("inherited_evidence") != block:
            raise ValueError("Historical release does not bind this exact attachment stop")
        metadata = Path(basis["fresh_success_capture"])
        capture = json.loads(metadata.read_text())
        artifact = Path(capture["artifact"])
        body = gzip.decompress(artifact.read_bytes())
        if (
            capture.get("status_code") != 200
            or capture.get("body_captured") is not True
            or capture.get("url") != transition["allowed_public_url"]
            or capture.get("response_url") != transition["allowed_public_url"]
            or urlsplit(capture["response_url"]).hostname != host
            or capture.get("body_sha256") != basis["fresh_success_body_sha256"]
            or hashlib.sha256(body).hexdigest() != capture["body_sha256"]
            or epoch(capture["started_at"]) < epoch(transition["authorized_at"])
        ):
            raise ValueError("Historical release lacks its exact successful capture")
        for proof in (transition_path, prior, browser, metadata, artifact):
            evidence[str(proof)] = sha(proof)


def prepare(legacy_run, registry, shared_lock):
    run = Path(legacy_run).resolve()
    expected_owner = run.parent / "remediation" / "manual-fetch-owner.lock"
    if Path(shared_lock).resolve() != expected_owner:
        raise ValueError("Legacy export requires its actual manual-fetch writer owner")
    registry = Path(registry).resolve()
    manifest = run / "manifest.json"
    original_manifest = json.loads(manifest.read_text())
    if Path(original_manifest["run_dir"]).resolve() != run:
        raise ValueError("Manual manifest does not bind this original directory")
    scope = original_manifest["scope"]
    enabled = {s.id for s in load_sources(registry) if s.enabled}
    by_id = {s["id"]: s for s in scope}
    if not enabled.issubset(by_id):
        raise ValueError("Prior run manifest does not cover configured enabled source scope")
    evidence = {str(manifest): sha(manifest), str(registry): sha(registry)}
    events, reservation_ids, seen_log_reservations = {}, {}, set()
    now = time.time()
    for source_id in sorted(enabled):
        reservations = run / "locks" / f"{source_id}-detail-hourly-reservations.json"
        if reservations.exists():
            payload = json.loads(reservations.read_text())
            if payload["source_id"] != source_id:
                raise ValueError("Prior reservation source identity mismatch")
            evidence[str(reservations)] = sha(reservations)
            for value in payload["reservations"]:
                rid = value["reservation_id"]
                if rid in events:
                    raise ValueError("Duplicate prior reservation")
                reservation_ids[rid] = value
                events[rid] = {
                    "attempt_id": rid,
                    "source_id": source_id,
                    "started_at": epoch(value["reserved_at"]),
                    "finished_at": None,
                    "external_id": value["external_id"],
                    "original": value,
                    # Unknown completion is never retimed as an observation.
                    # Hold a full additional hour from migration conservatively.
                    "eligibility_accounting_until": now,
                }
        for path in sorted((run / "sources" / source_id).glob("*/details.jsonl")):
            evidence[str(path)] = sha(path)
            for index, line in enumerate(path.read_text().splitlines()):
                value = json.loads(line)
                start = epoch(value["started_at"])
                end = epoch(value["finished_at"]) if value.get("finished_at") else None
                rid = value.get("reservation_id")
                if rid:
                    original = reservation_ids.get(rid)
                    if (
                        rid in seen_log_reservations
                        or not original
                        or original["reserved_at"] != value["started_at"]
                        or str(original["external_id"]) != str(value["listing_identity"])
                    ):
                        raise ValueError(
                            "Prior detail log does not bind exactly one original reservation"
                        )
                    seen_log_reservations.add(rid)
                else:
                    rid = hashlib.sha256((str(path) + ":" + str(index)).encode()).hexdigest()
                events[rid] = {
                    "attempt_id": rid,
                    "source_id": source_id,
                    "started_at": start,
                    "finished_at": end,
                    "original_log": {"path": str(path), "line": index + 1},
                    **({"eligibility_accounting_until": now} if end is None else {}),
                }
    hosts = {}
    for path in sorted((run / "locks").glob("host-*.json")):
        state = json.loads(path.read_text())
        evidence[str(path)] = sha(path)
        clock = path.with_suffix(".lock")
        if clock.exists():
            evidence[str(clock)] = sha(clock)
            state["last_request_at"] = max(
                float(state.get("last_request_at", 0)), float(clock.read_text().strip() or 0)
            )
        hosts[path.stem] = state
    reconcile_attachment_holds(run, hosts, evidence)
    holds = {}
    for source_id in sorted(enabled):
        db = run / "output" / (by_id[source_id]["slug"] + "_jobs.sqlite3")
        if not db.is_file():
            raise ValueError("Missing prior source database for circuit inspection")
        with sqlite3.connect(db.as_uri() + "?mode=ro", uri=True) as conn:
            conn.row_factory = sqlite3.Row
            rows = [
                dict(r)
                for r in conn.execute(
                    "SELECT * FROM source_circuit_breakers WHERE source_id=? AND state IN ('open','half_open')",
                    (source_id,),
                )
            ]
        if rows:
            holds[source_id] = {"reason": "Prior source circuit remains open", "rows": rows}
        for path in (db, Path(str(db) + "-wal")):
            if path.exists():
                evidence[str(path)] = sha(path)
    # Hash all read evidence again while the shared writer is held by --write.
    if any(sha(path) != expected for path, expected in evidence.items()):
        raise ValueError("Prior policy evidence changed during migration snapshot")
    return {
        "schema_version": 1,
        "shared_lock": str(Path(shared_lock).absolute()),
        "reviewed_at": datetime.now(UTC).isoformat(),
        "scope_source_ids": sorted(enabled),
        "prior_writers_reviewed": True,
        "no_unmigrated_policy_state": True,
        "review_note": "Operator explicitly selected this complete prior run under the same writer owner. Unmatched reserved attempts retain original starts and receive conservative accounting through migration time; that is not a new source observation.",
        "detail_attempts": list(events.values()),
        "host_states": {},
        "host_states_by_digest": hosts,
        "source_holds": holds,
        "evidence": [{"path": path, "sha256": value} for path, value in sorted(evidence.items())],
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--legacy-run", required=True, type=Path)
    parser.add_argument("--registry", required=True, type=Path)
    parser.add_argument("--shared-lock", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument(
        "--write",
        action="store_true",
        help="Confirm this is the complete reviewed prior writer scope; create an immutable proposal",
    )
    args = parser.parse_args(argv)
    if not args.write:
        print(
            dump(
                {
                    "status": "preview",
                    "network_requests": 0,
                    "writes": 0,
                    "legacy_run": str(args.legacy_run),
                    "output": str(args.output),
                    "requires": "Quiescent shared owner and explicit review that all prior writers are represented",
                }
            )
        )
        return 0
    with shared_owner(args.shared_lock):
        if args.output.exists():
            raise ValueError("Bootstrap proposal already exists; preserve it and choose a new path")
        value = prepare(args.legacy_run, args.registry, args.shared_lock)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("x") as out:
            out.write(dump(value) + "\n")
        print(
            dump(
                {
                    "status": "prepared_for_review",
                    "path": str(args.output),
                    "sha256": sha(args.output),
                    "attempts": len(value["detail_attempts"]),
                    "hosts": len(value["host_states_by_digest"]),
                }
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
