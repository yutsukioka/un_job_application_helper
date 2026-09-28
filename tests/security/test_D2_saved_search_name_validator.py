from __future__ import annotations

import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "packages" / "jobagg"))
sys.path.insert(0, str(ROOT / "services" / "job-api"))

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402
from job_api.app import create_app  # noqa: E402
from job_api.config import ApiSettings  # noqa: E402
from jobagg.filters.saved_searches import save_search  # noqa: E402
from jobagg.filters.schemas import VacancySearchRequest  # noqa: E402


INVALID_NAMES = [
    "finance/procurement",
    "finance\\procurement",
    "finance\nprocurement",
    "x" * 129,
]


def _settings(tmp_path: Path) -> ApiSettings:
    return ApiSettings(
        repo_root=ROOT,
        db_path=tmp_path / "missing.sqlite3",
        saved_searches_path=tmp_path / "saved_searches.json",
        tracker_path=tmp_path / "tracker.json",
    )


@pytest.mark.parametrize("name", INVALID_NAMES)
def test_D2_saved_search_api_rejects_invalid_names(tmp_path: Path, name: str, loopback_client) -> None:
    client = loopback_client(create_app(_settings(tmp_path)))

    response = client.post(
        "/api/saved-searches",
        json={"name": name, "request": {"text": "finance"}},
    )

    assert response.status_code == 422
    assert not (tmp_path / "saved_searches.json").exists()


@pytest.mark.parametrize("name", INVALID_NAMES)
def test_D2_saved_search_store_rejects_invalid_names(tmp_path: Path, name: str) -> None:
    with pytest.raises(ValueError, match="Saved search name"):
        save_search(
            tmp_path / "saved_searches.json",
            name=name,
            request=VacancySearchRequest(text="finance"),
            overwrite=True,
        )
