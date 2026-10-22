"""Synthetic local history growth, fixed active work; no production scale claim."""

import hashlib
import json
import os
from pathlib import Path
import sqlite3
import time

from jobagg import accepted_evidence
from jobagg.publication_projection import build_projection
from test_live_publication import setup as make_fixture, add_detail
from test_publication_work import prepare, select


class Meter(sqlite3.Connection):
    steps = 0

    def set_progress_handler(self, callback, n):
        def measured():
            self.steps += 1
            return int(bool(callback and self.steps % max(1, n) == 0 and callback()))

        super().set_progress_handler(measured, 1)


def test_fixed_selected_work_does_not_copy_or_hash_retained_versions(tmp_path):
    samples = []
    for size in (0, 1024, 8192):
        root = tmp_path / str(size)
        root.mkdir()
        fixture = make_fixture.__wrapped__(root)
        add_detail(fixture)
        prepare(fixture)
        with fixture["worker"].connect() as conn:
            original = dict(conn.execute("SELECT * FROM jobagg_accepted_evidence").fetchone())
            for index in range(size):
                ref = json.loads(original["reference_json"])
                ref["proof_sha256"] = hashlib.sha256(str(index).encode()).hexdigest()
                ref["observation"]["checked_at"] -= index + 1
                ref["artifact_path"] = "/synthetic-unselected-history/" + str(index)
                ref["attempt_id"] = "historical-" + str(index)
                version = accepted_evidence.digest(
                    {key: value for key, value in ref.items() if key != "artifact_path"}
                )
                conn.execute(
                    "INSERT INTO jobagg_accepted_evidence VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    tuple(
                        version
                        if column == "version"
                        else json.dumps(ref, sort_keys=True)
                        if column == "reference_json"
                        else ref.get(column, original[column])
                        for column in original
                    ),
                )
                conn.execute(
                    "INSERT INTO remediation_attempts VALUES(?,?,?,?,?,?,?,?)",
                    (
                        ref["attempt_id"],
                        original["task_id"],
                        original["source_id"],
                        "detail",
                        0,
                        1,
                        "failed",
                        "{}",
                    ),
                )
            selected = select(fixture, conn)
        for temperature in ("fresh_connection", "warm_connection"):
            origin = sqlite3.connect(fixture["worker"].path, factory=Meter)
            destination = sqlite3.connect(root / (temperature + ".sqlite3"))
            statements = []
            origin.set_trace_callback(statements.append)
            started = time.monotonic()
            receipt = build_projection(
                origin,
                destination,
                selection=selected,
                deadline_at=time.monotonic() + 10,
                max_bytes=2_000_000,
            )
            elapsed = time.monotonic() - started
            assert (
                destination.execute("SELECT count(*) FROM jobagg_accepted_evidence").fetchone()[0]
                == 1
            )
            assert (
                destination.execute("SELECT count(*) FROM remediation_attempts").fetchone()[0] == 1
            )
            assert not any("count(*) FROM jobs" in query for query in statements)
            assert receipt["manifest"]["diagnostics_scope"] == "selected_dependencies_only"
            samples.append(
                {
                    "history_versions": size,
                    "active_jobs": 1,
                    "connection": temperature,
                    "sql_vm_instructions": origin.steps,
                    "sql_statements": len(statements),
                    "seconds": elapsed,
                    "lineage_components": receipt.get("timings", {}).get("lineage_components", {}),
                    "projection_bytes": (root / (temperature + ".sqlite3")).stat().st_size,
                }
            )
            origin.close()
            destination.close()
    target = os.environ.get("JOBAGG_M1_HISTORY_REPORT")
    if target:
        Path(target).write_text(
            json.dumps(
                {
                    "scope": "synthetic local storage; application connection cold/warm; OS cache not flushed; no external-volume or production gate claim",
                    "samples": samples,
                },
                indent=2,
            )
            + "\n"
        )
