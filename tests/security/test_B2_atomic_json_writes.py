from __future__ import annotations

import sys
import threading
import time
from pathlib import Path
from typing import Callable


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "packages" / "jobagg"))
sys.path.insert(0, str(ROOT / "services" / "job-api"))

from job_api import tracker  # noqa: E402
from job_api.models import ApplicationRecord  # noqa: E402
from jobagg.atomic_json_store import AtomicJsonStore
from jobagg.filters import saved_searches  # noqa: E402
from jobagg.filters.schemas import VacancySearchRequest  # noqa: E402


def _run_threads(count: int, worker: Callable[[int], None]) -> None:
    barrier = threading.Barrier(count)
    errors: list[BaseException] = []
    lock = threading.Lock()

    def wrapped(index: int) -> None:
        try:
            barrier.wait(timeout=5)
            worker(index)
        except BaseException as exc:
            with lock:
                errors.append(exc)

    threads = [threading.Thread(target=wrapped, args=(index,)) for index in range(count)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
    assert not any(thread.is_alive() for thread in threads)
    assert errors == []


def test_B2_tracker_concurrent_upserts_preserve_all_records(
    tmp_path: Path,
    monkeypatch,
) -> None:
    path = tmp_path / "tracker.json"
    original_write = AtomicJsonStore._write_unlocked

    def slow_write(store, document):
        time.sleep(0.01)
        return original_write(store, document)

    monkeypatch.setattr(AtomicJsonStore, "_write_unlocked", slow_write)

    def worker(index: int) -> None:
        tracker.upsert_record(
            path,
            ApplicationRecord(
                id=f"record-{index}",
                job_key=f"source:{index}",
                status="saved",
            ),
        )

    _run_threads(50, worker)

    records = tracker.list_records(path)
    assert len(records) == 50
    assert {record.id for record in records} == {f"record-{index}" for index in range(50)}


def test_B2_saved_search_concurrent_writes_preserve_all_searches(
    tmp_path: Path,
    monkeypatch,
) -> None:
    path = tmp_path / "saved_searches.json"
    original_write = AtomicJsonStore._write_unlocked

    def slow_write(store, document):
        time.sleep(0.01)
        return original_write(store, document)

    monkeypatch.setattr(AtomicJsonStore, "_write_unlocked", slow_write)

    def worker(index: int) -> None:
        saved_searches.save_search(
            path,
            name=f"search-{index}",
            request=VacancySearchRequest(text=f"term-{index}"),
            overwrite=True,
        )

    _run_threads(50, worker)

    searches = saved_searches.load_saved_searches(path)
    assert len(searches) == 50
    assert set(searches) == {f"search-{index}" for index in range(50)}
