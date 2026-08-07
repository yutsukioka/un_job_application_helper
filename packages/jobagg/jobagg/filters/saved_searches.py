"""Persistence helpers for CLI saved searches."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, fields
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from jobagg.atomic_json_store import AtomicJsonStore, AtomicJsonStoreError
from jobagg.filters.schemas import VacancySearchRequest

STORE_VERSION = 1


@dataclass(slots=True)
class SavedSearch:
    name: str
    request: VacancySearchRequest
    description: str | None = None
    created_at: str = ""
    updated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "request": request_to_dict(self.request),
        }


def load_saved_searches(path: str | Path) -> dict[str, SavedSearch]:
    data = _load_store(Path(path))
    searches = data["saved_searches"]
    return {
        name: _saved_search_from_dict(name, payload)
        for name, payload in searches.items()
        if isinstance(payload, dict)
    }


def list_saved_searches(path: str | Path) -> list[SavedSearch]:
    searches = load_saved_searches(path)
    return [searches[name] for name in sorted(searches)]


def save_search(
    path: str | Path,
    *,
    name: str,
    request: VacancySearchRequest,
    description: str | None = None,
    overwrite: bool = False,
) -> SavedSearch:
    store_path = Path(path)

    def mutation(data: dict[str, Any]) -> tuple[SavedSearch, bool]:
        _normalize_store(data)
        searches = data["saved_searches"]
        if name in searches and not overwrite:
            raise ValueError(f"Saved search {name!r} already exists; use --overwrite to replace it")
        now = datetime.now(tz=UTC).isoformat()
        current = searches.get(name)
        created_at = current.get("created_at", now) if isinstance(current, dict) else now
        search = SavedSearch(
            name=name,
            description=description,
            request=request,
            created_at=created_at,
            updated_at=now,
        )
        searches[name] = search.to_dict()
        return search, True

    return _store(store_path).mutate(mutation)


def get_saved_search(path: str | Path, name: str) -> SavedSearch:
    searches = load_saved_searches(path)
    try:
        return searches[name]
    except KeyError as exc:
        raise KeyError(f"No saved search named {name!r}") from exc


def remove_saved_search(path: str | Path, name: str) -> bool:
    store_path = Path(path)

    def mutation(data: dict[str, Any]) -> tuple[bool, bool]:
        _normalize_store(data)
        searches = data["saved_searches"]
        if name not in searches:
            return False, False
        del searches[name]
        return True, True

    return _store(store_path).mutate(mutation)


def compare_and_remove_saved_search(
    path: str | Path,
    *,
    name: str,
    expected: SavedSearch,
) -> Literal["deleted", "absent", "mismatch"]:
    store_path = Path(path)

    def mutation(
        data: dict[str, Any],
    ) -> tuple[Literal["deleted", "absent", "mismatch"], bool]:
        _normalize_store(data)
        searches = data["saved_searches"]
        if name not in searches:
            return "absent", False
        payload = searches[name]
        if not isinstance(payload, dict):
            return "mismatch", False
        current = _saved_search_from_dict(name, payload)
        if expected.name != name or current != expected:
            return "mismatch", False
        del searches[name]
        return "deleted", True

    return _store(store_path).mutate(mutation)


def request_to_dict(request: VacancySearchRequest) -> dict[str, Any]:
    data = asdict(request)
    for key, value in list(data.items()):
        if hasattr(value, "isoformat") and not isinstance(value, str):
            data[key] = value.isoformat()
    return data


def request_from_dict(data: dict[str, Any]) -> VacancySearchRequest:
    allowed = {field.name for field in fields(VacancySearchRequest)}
    payload = {key: value for key, value in data.items() if key in allowed}
    return VacancySearchRequest(**payload)


def _saved_search_from_dict(name: str, payload: dict[str, Any]) -> SavedSearch:
    return SavedSearch(
        name=str(payload.get("name") or name),
        description=payload.get("description"),
        request=request_from_dict(payload.get("request") or {}),
        created_at=str(payload.get("created_at") or ""),
        updated_at=str(payload.get("updated_at") or ""),
    )


def _load_store(path: Path) -> dict[str, Any]:
    data = _store(path).read()
    _normalize_store(data)
    return data


def _store(path: Path) -> AtomicJsonStore[dict[str, Any]]:
    return AtomicJsonStore(
        path,
        default_factory=lambda: {"version": STORE_VERSION, "saved_searches": {}},
        validator=_validate_store,
        encoder=lambda data: (
            json.dumps(data, indent=2, sort_keys=True, ensure_ascii=True) + "\n"
        ).encode("utf-8"),
    )


def _validate_store(data: Any) -> None:
    if not isinstance(data, dict):
        raise AtomicJsonStoreError("Saved-search JSON store must contain an object.")
    if "saved_searches" in data and not isinstance(data["saved_searches"], dict):
        raise AtomicJsonStoreError("Saved-search JSON store is invalid.")


def _normalize_store(data: dict[str, Any]) -> None:
    data.setdefault("version", STORE_VERSION)
    data.setdefault("saved_searches", {})
