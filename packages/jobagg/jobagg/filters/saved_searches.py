"""Persistence helpers for CLI saved searches."""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, fields
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from jobagg.atomic_json_store import AtomicJsonStore, AtomicJsonStoreError
from jobagg.filters.schemas import VacancySearchRequest

STORE_VERSION = 1
_STORE_KEYS = frozenset({"version", "saved_searches"})
_SAVED_SEARCH_KEYS = frozenset(
    {"name", "description", "created_at", "updated_at", "request"}
)
_REQUEST_FIELDS = tuple(fields(VacancySearchRequest))
_REQUEST_KEYS = frozenset(field.name for field in _REQUEST_FIELDS)
_REQUEST_DEFAULTS = VacancySearchRequest()
_LEGACY_REQUEST_KEYS = frozenset({"volunteer_kinds", "exclude_expired_open"})


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
        normalized = _normalize_store(data)
        searches = data["saved_searches"]
        if name not in searches:
            return False, normalized
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
        normalized = _normalize_store(data)
        searches = data["saved_searches"]
        if name not in searches:
            return "absent", normalized
        payload = searches[name]
        expected_payload = expected.to_dict()
        if expected.name != name:
            return "mismatch", normalized
        _validate_saved_search_payload(name, expected_payload)
        if _canonical_payload(payload) != _canonical_payload(expected_payload):
            return "mismatch", normalized
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
        name=payload["name"],
        description=payload["description"],
        request=request_from_dict(payload["request"]),
        created_at=payload["created_at"],
        updated_at=payload["updated_at"],
    )


def _load_store(path: Path) -> dict[str, Any]:
    def mutation(data: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        return data, _normalize_store(data)

    return _store(path).mutate(mutation)


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
    if set(data) - _STORE_KEYS:
        raise AtomicJsonStoreError("Saved-search JSON store is invalid.")
    version = data.get("version", STORE_VERSION)
    if type(version) is not int or version != STORE_VERSION:
        raise AtomicJsonStoreError("Saved-search JSON store is invalid.")
    searches = data.get("saved_searches", {})
    if not isinstance(searches, dict):
        raise AtomicJsonStoreError("Saved-search JSON store is invalid.")
    for name, payload in searches.items():
        _validate_saved_search_payload(name, payload, allow_legacy=True)


def _validate_saved_search_payload(
    name: Any,
    payload: Any,
    *,
    allow_legacy: bool = False,
) -> None:
    if not isinstance(name, str) or not isinstance(payload, dict):
        raise AtomicJsonStoreError("Saved-search JSON store is invalid.")
    if set(payload) != _SAVED_SEARCH_KEYS or payload["name"] != name:
        raise AtomicJsonStoreError("Saved-search JSON store is invalid.")
    if not isinstance(payload["name"], str):
        raise AtomicJsonStoreError("Saved-search JSON store is invalid.")
    if payload["description"] is not None and not isinstance(
        payload["description"], str
    ):
        raise AtomicJsonStoreError("Saved-search JSON store is invalid.")
    if not isinstance(payload["created_at"], str) or not isinstance(
        payload["updated_at"], str
    ):
        raise AtomicJsonStoreError("Saved-search JSON store is invalid.")
    _validate_request_payload(payload["request"], allow_legacy=allow_legacy)


def _validate_request_payload(payload: Any, *, allow_legacy: bool = False) -> None:
    if not isinstance(payload, dict):
        raise AtomicJsonStoreError("Saved-search JSON store is invalid.")
    payload_keys = set(payload)
    missing = _REQUEST_KEYS - payload_keys
    if payload_keys - _REQUEST_KEYS or (
        missing and (not allow_legacy or not missing <= _LEGACY_REQUEST_KEYS)
    ):
        raise AtomicJsonStoreError("Saved-search JSON store is invalid.")
    for field in _REQUEST_FIELDS:
        if field.name not in payload:
            continue
        value = payload[field.name]
        default = getattr(_REQUEST_DEFAULTS, field.name)
        if isinstance(default, list):
            valid = isinstance(value, list) and all(
                isinstance(item, str) for item in value
            )
        elif isinstance(default, bool):
            valid = isinstance(value, bool)
        elif isinstance(default, int):
            valid = type(value) is int
        elif isinstance(default, float):
            valid = (
                type(value) in {int, float}
                and not isinstance(value, bool)
                and math.isfinite(value)
            )
        elif isinstance(default, str):
            valid = isinstance(value, str)
        else:
            valid = value is None or isinstance(value, str)
        if not valid:
            raise AtomicJsonStoreError("Saved-search JSON store is invalid.")


def _canonical_payload(payload: dict[str, Any]) -> bytes:
    try:
        return json.dumps(
            payload,
            ensure_ascii=True,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError):
        raise AtomicJsonStoreError("Saved-search JSON store is invalid.") from None


def _normalize_store(data: dict[str, Any]) -> bool:
    changed = False
    if "version" not in data:
        data["version"] = STORE_VERSION
        changed = True
    if "saved_searches" not in data:
        data["saved_searches"] = {}
        changed = True
    for payload in data["saved_searches"].values():
        request = payload["request"]
        for key in _LEGACY_REQUEST_KEYS:
            if key in request:
                continue
            default = getattr(_REQUEST_DEFAULTS, key)
            request[key] = list(default) if isinstance(default, list) else default
            changed = True
        _validate_request_payload(request)
    return changed
