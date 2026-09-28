from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "packages" / "jobagg"))
sys.path.insert(0, str(ROOT / "services" / "job-api"))

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402
from job_api.app import create_app  # noqa: E402
from job_api.config import ApiSettings  # noqa: E402
from jobagg.scoring import StrategyPathError, resolve_strategy_signals_path  # noqa: E402


@dataclass
class FakeSearchResponse:
    total: int = 0
    limit: int = 50
    offset: int = 0
    results: list[dict[str, Any]] = field(default_factory=list)
    facets: dict[str, dict[str, int]] = field(default_factory=dict)
    unclassified_count: int = 0


def _write(path: Path, text: str = '{"terms": ["admin"]}') -> Path:
    path.write_text(text, encoding="utf-8")
    return path


def test_A3_score_against_allows_regular_file_inside_root(tmp_path: Path) -> None:
    root = tmp_path / "strategies"
    root.mkdir()
    allowed = _write(root / "signals.json")

    assert resolve_strategy_signals_path("signals.json", root=root) == allowed.resolve()


@pytest.mark.parametrize(
    ("case_name", "supplied_path"),
    [
        ("absolute outside root", "outside.json"),
        ("parent traversal", "../outside.json"),
        ("file URL", "file:/tmp/outside.json"),
    ],
)
def test_A3_score_against_rejects_unsafe_path_cases(
    tmp_path: Path,
    case_name: str,
    supplied_path: str,
) -> None:
    root = tmp_path / "strategies"
    root.mkdir()
    outside = _write(tmp_path / "outside.json")
    if case_name == "absolute outside root":
        supplied_path = str(outside)

    with pytest.raises(StrategyPathError):
        resolve_strategy_signals_path(supplied_path, root=root)


def test_A3_score_against_rejects_symlink_escape(tmp_path: Path) -> None:
    root = tmp_path / "strategies"
    root.mkdir()
    outside = _write(tmp_path / "outside.json")
    link = root / "linked.json"
    try:
        os.symlink(outside, link)
    except OSError as exc:
        pytest.skip(f"symlink unavailable: {exc}")

    with pytest.raises(StrategyPathError):
        resolve_strategy_signals_path("linked.json", root=root)


def test_A3_score_against_rejects_non_regular_file(tmp_path: Path) -> None:
    root = tmp_path / "strategies"
    root.mkdir()
    directory = root / "directory"
    directory.mkdir()

    with pytest.raises(StrategyPathError):
        resolve_strategy_signals_path("directory", root=root)


def test_A3_score_against_rejects_oversized_file(tmp_path: Path) -> None:
    root = tmp_path / "strategies"
    root.mkdir()
    large = _write(root / "large.json", "x" * 32)

    with pytest.raises(StrategyPathError):
        resolve_strategy_signals_path(large, root=root, max_bytes=16)


def test_A3_search_endpoint_rejects_score_against_outside_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    loopback_client,
) -> None:
    root = tmp_path / "strategies"
    root.mkdir()
    outside = _write(tmp_path / "outside.json")
    db_path = tmp_path / "all_jobs.sqlite3"
    db_path.touch()
    settings = ApiSettings(
        repo_root=ROOT,
        db_path=db_path,
        saved_searches_path=tmp_path / "saved_searches.json",
        tracker_path=tmp_path / "tracker.json",
    )

    import job_api.app as app_module

    monkeypatch.setattr(app_module, "search_collected_jobs", lambda *args, **kwargs: FakeSearchResponse())
    monkeypatch.setattr(
        app_module,
        "_decode_strategy_signals",
        lambda path: pytest.fail(f"unsafe path reached loader: {path}"),
    )
    monkeypatch.setenv("JOB_API_STRATEGY_ROOT", str(root))
    client = loopback_client(create_app(settings))

    response = client.post("/api/search", json={"score_against": str(outside)})

    assert response.status_code == 400
    assert response.json()["detail"] == "Strategy file is unavailable."
