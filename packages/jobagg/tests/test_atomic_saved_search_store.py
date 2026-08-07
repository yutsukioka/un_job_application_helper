from __future__ import annotations

import importlib
import json
import threading
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from jobagg.filters import saved_searches
from jobagg.filters.saved_searches import SavedSearch
from jobagg.filters.schemas import VacancySearchRequest


def _save(
    path: Path,
    *,
    description: str = "version-a",
    text: str = "programme",
) -> SavedSearch:
    return saved_searches.save_search(
        path,
        name="programme",
        description=description,
        request=VacancySearchRequest(text=text),
        overwrite=True,
    )


def _compare_and_remove(path: Path, expected: SavedSearch) -> str:
    return saved_searches.compare_and_remove_saved_search(
        path,
        name=expected.name,
        expected=expected,
    )


def _atomic_store_module() -> Any:
    return importlib.import_module("jobagg.atomic_json_store")


def test_exact_saved_search_compare_and_remove_is_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "saved-searches.json"
    expected = _save(path)

    assert _compare_and_remove(path, expected) == "deleted"
    assert _compare_and_remove(path, expected) == "absent"
    assert saved_searches.load_saved_searches(path) == {}


@pytest.mark.parametrize(
    "changed",
    [
        lambda item: replace(item, description="version-b"),
        lambda item: replace(item, request=VacancySearchRequest(text="finance")),
        lambda item: replace(item, created_at="2099-01-01T00:00:00+00:00"),
        lambda item: replace(item, updated_at="2099-01-01T00:00:00+00:00"),
    ],
    ids=["description", "request", "created-at", "updated-at"],
)
def test_saved_search_compare_mismatch_preserves_current_record(
    tmp_path: Path,
    changed: Any,
) -> None:
    path = tmp_path / "saved-searches.json"
    current = _save(path)

    assert _compare_and_remove(path, changed(current)) == "mismatch"
    assert saved_searches.get_saved_search(path, current.name) == current


def test_saved_search_store_rejects_malformed_json_without_rewriting(tmp_path: Path) -> None:
    path = tmp_path / "saved-searches.json"
    malformed = b'{"saved_searches":'
    path.write_bytes(malformed)

    with pytest.raises(ValueError, match="invalid"):
        _save(path)

    assert path.read_bytes() == malformed


def test_saved_search_store_rejects_non_object_without_rewriting(tmp_path: Path) -> None:
    path = tmp_path / "saved-searches.json"
    malformed = b"[]"
    path.write_bytes(malformed)

    with pytest.raises(ValueError, match="object"):
        _save(path)

    assert path.read_bytes() == malformed


def test_pre_replace_failure_preserves_old_file_and_cleans_temp(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "saved-searches.json"
    original = _save(path)
    original_bytes = path.read_bytes()
    atomic_store = _atomic_store_module()

    def fail_replace(_source: object, _destination: object) -> None:
        raise OSError("injected replace failure")

    monkeypatch.setattr(atomic_store.os, "replace", fail_replace)

    with pytest.raises(ValueError, match="write failed"):
        _save(path, description="version-b")

    assert path.read_bytes() == original_bytes
    assert saved_searches.get_saved_search(path, original.name) == original
    assert list(tmp_path.glob(f".{path.name}.*.tmp")) == []


def test_mutation_lock_is_released_after_write_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "saved-searches.json"
    _save(path)
    atomic_store = _atomic_store_module()
    real_replace = atomic_store.os.replace

    def fail_replace(_source: object, _destination: object) -> None:
        raise OSError("injected replace failure")

    monkeypatch.setattr(atomic_store.os, "replace", fail_replace)
    with pytest.raises(ValueError, match="write failed"):
        _save(path, description="failed")

    monkeypatch.setattr(atomic_store.os, "replace", real_replace)
    saved = _save(path, description="recovered")
    assert saved_searches.get_saved_search(path, saved.name) == saved


def test_saved_search_update_and_conditional_delete_share_one_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "saved-searches.json"
    expected = _save(path)
    atomic_store = _atomic_store_module()
    update_loaded = threading.Event()
    delete_attempted = threading.Event()
    real_read = atomic_store.AtomicJsonStore._read_unlocked
    real_acquire = atomic_store._acquire_platform_lock

    def gated_read(store: object) -> object:
        data = real_read(store)
        if threading.current_thread().name == "saved-search-update":
            update_loaded.set()
            assert delete_attempted.wait(5)
        return data

    def observed_acquire(lock_file: object) -> None:
        if threading.current_thread().name == "saved-search-delete":
            delete_attempted.set()
        real_acquire(lock_file)

    monkeypatch.setattr(atomic_store.AtomicJsonStore, "_read_unlocked", gated_read)
    monkeypatch.setattr(atomic_store, "_acquire_platform_lock", observed_acquire)
    outcomes: list[str] = []

    update = threading.Thread(
        name="saved-search-update",
        target=lambda: _save(path, description="version-b"),
    )
    delete = threading.Thread(
        name="saved-search-delete",
        target=lambda: outcomes.append(_compare_and_remove(path, expected)),
    )
    update.start()
    assert update_loaded.wait(5)
    delete.start()
    update.join(5)
    delete.join(5)

    assert not update.is_alive()
    assert not delete.is_alive()
    assert outcomes == ["mismatch"]
    assert saved_searches.get_saved_search(path, expected.name).description == "version-b"
    json.loads(path.read_text(encoding="utf-8"))


def test_delete_then_later_save_remains_valid(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "saved-searches.json"
    expected = _save(path)
    atomic_store = _atomic_store_module()
    delete_loaded = threading.Event()
    save_attempted = threading.Event()
    real_read = atomic_store.AtomicJsonStore._read_unlocked
    real_acquire = atomic_store._acquire_platform_lock

    def gated_read(store: object) -> object:
        data = real_read(store)
        if threading.current_thread().name == "saved-search-delete":
            delete_loaded.set()
            assert save_attempted.wait(5)
        return data

    def observed_acquire(lock_file: object) -> None:
        if threading.current_thread().name == "saved-search-save":
            save_attempted.set()
        real_acquire(lock_file)

    monkeypatch.setattr(atomic_store.AtomicJsonStore, "_read_unlocked", gated_read)
    monkeypatch.setattr(atomic_store, "_acquire_platform_lock", observed_acquire)
    outcomes: list[str] = []

    delete = threading.Thread(
        name="saved-search-delete",
        target=lambda: outcomes.append(_compare_and_remove(path, expected)),
    )
    save = threading.Thread(
        name="saved-search-save",
        target=lambda: _save(path, description="later-save"),
    )
    delete.start()
    assert delete_loaded.wait(5)
    save.start()
    delete.join(5)
    save.join(5)

    assert not delete.is_alive()
    assert not save.is_alive()
    assert outcomes == ["deleted"]
    assert saved_searches.get_saved_search(path, expected.name).description == "later-save"
    json.loads(path.read_text(encoding="utf-8"))


def test_atomic_store_has_posix_and_windows_lock_paths() -> None:
    source = Path(_atomic_store_module().__file__).read_text(encoding="utf-8")

    assert "fcntl.flock" in source
    assert "msvcrt.locking" in source
    assert "LK_LOCK" in source
    assert "LK_UNLCK" in source
    assert "mutation.lock" in source
