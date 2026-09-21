"""Concurrent organization work must retain every durable quota reservation."""
from concurrent.futures import ThreadPoolExecutor
import json
import threading
import time

from jobagg.pipelines.worker_policy import SharedPolicy
from test_worker_policy import bootstrap


def test_parallel_policy_instances_keep_complete_attempt_index(tmp_path):
    original, _, _ = bootstrap(tmp_path)
    original.initialize()
    policies = [SharedPolicy(original.owner, ["example"]) for _ in range(8)]
    barrier = threading.Barrier(8)

    def record(index):
        policy = policies[index]
        barrier.wait(timeout=10)
        for number in range(5):
            identity = f"{index}-{number}"
            policy.reserve(identity, "example", "detail", time.time(), tmp_path, identity)
            policy.finish(identity, "done")
            # An observer must never see a partially updated reservation/index.
            assert policy.events("example")

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(record, range(8)))
    events = original.events("example")
    assert len(events) == 40
    assert len({event["attempt_id"] for event in events}) == 40
    assert all(event["status"] == "done" for event in events)
    index = json.loads((original.root / "attempt_index.json").read_text())
    assert set(index) == {path.name for path in (original.root / "attempts").glob("*.json")}
    assert len(index) == 40


def test_simultaneous_duplicate_reservation_is_charged_once(tmp_path):
    original, _, _ = bootstrap(tmp_path)
    original.initialize()
    barrier = threading.Barrier(8)

    def reserve(_):
        policy = SharedPolicy(original.owner, ["example"])
        barrier.wait(timeout=10)
        try:
            policy.reserve("one", "example", "detail", time.time(), tmp_path, "job")
            return "reserved"
        except ValueError as exc:
            assert "already exists" in str(exc)
            return "duplicate"

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(reserve, range(8)))
    assert results.count("reserved") == 1
    assert results.count("duplicate") == 7
    assert len(original.events("example")) == 1
