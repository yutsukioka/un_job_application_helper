"""Persistence helpers for CLI saved searches."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, fields
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

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
    searches = data.get("saved_searches") or {}
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
    data = _load_store(store_path)
    searches = data.setdefault("saved_searches", {})
    if name in searches and not overwrite:
        raise ValueError(f"Saved search {name!r} already exists; use --overwrite to replace it")
    now = datetime.now(tz=UTC).isoformat()
    created_at = searches.get(name, {}).get("created_at", now) if isinstance(searches.get(name), dict) else now
    search = SavedSearch(
        name=name,
        description=description,
        request=request,
        created_at=created_at,
        updated_at=now,
    )
    searches[name] = search.to_dict()
    _write_store(store_path, data)
    return search


def get_saved_search(path: str | Path, name: str) -> SavedSearch:
    searches = load_saved_searches(path)
    try:
        return searches[name]
    except KeyError as exc:
        raise KeyError(f"No saved search named {name!r}") from exc


def remove_saved_search(path: str | Path, name: str) -> bool:
    store_path = Path(path)
    data = _load_store(store_path)
    searches = data.setdefault("saved_searches", {})
    if name not in searches:
        return False
    del searches[name]
    _write_store(store_path, data)
    return True


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
    if not path.exists():
        return {"version": STORE_VERSION, "saved_searches": {}}
    data = json.loads(path.read_text(encoding="utf-8") or "{}")
    if not isinstance(data, dict):
        raise ValueError(f"Saved searches file must contain a JSON object: {path}")
    data.setdefault("version", STORE_VERSION)
    data.setdefault("saved_searches", {})
    return data


def _write_store(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, indent=2, sort_keys=True, ensure_ascii=True) + "\n",
        encoding="utf-8",
    )
