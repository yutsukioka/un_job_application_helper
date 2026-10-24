"""Historical tooling fails closed before touching aliased production files."""

import importlib.util
from pathlib import Path

import pytest

from test_wrapper_reconciliation_operator import prepare_copy


@pytest.fixture
def operator(monkeypatch):
    scripts = Path(__file__).resolve().parents[1] / "scripts"
    monkeypatch.syspath_prepend(str(scripts))
    spec = importlib.util.spec_from_file_location(
        "tested_history_operator", scripts / "m1_reconcile_history.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    "seconds,count", [(float("nan"), 1), (float("inf"), 1), (0, 1), (5, 0), (5, 65)]
)
def test_finite_operator_bounds(operator, tmp_path, seconds, count):
    with pytest.raises(ValueError, match="finite"):
        operator.reconcile(
            tmp_path,
            tmp_path / "operation",
            tmp_path / "absent",
            "",
            seconds=seconds,
            max_records=count,
        )


@pytest.mark.parametrize("change", ["symlink", "hardlink", "config", "hash"])
def test_boundary_change_stops_before_any_production_database_open(operator, tmp_path, change):
    import json

    root, review, production, marker = prepare_copy(tmp_path)
    value = json.loads(review.read_text())
    value["schema"] = "historical-occurrence-reconciliation-review-1"
    review.write_text(json.dumps(value))
    expected = operator.sha(review)
    if change in {"symlink", "hardlink"}:
        p = root / "worker/jobs.sqlite3"
        p.unlink()
        if change == "symlink":
            p.symlink_to(production)
        else:
            p.hardlink_to(production)
    elif change == "config":
        (tmp_path / "production-config.json").write_text("{}")
    else:
        expected = "0" * 64
    before = production.read_bytes(), marker.read_bytes()
    with pytest.raises(ValueError):
        operator.reconcile(root, root / "operation", review, expected, seconds=5, max_records=1)
    assert (production.read_bytes(), marker.read_bytes()) == before


def test_per_record_budget_must_be_finite_and_within_page(operator, tmp_path):
    for limit in (float("nan"), float("inf"), 0, 6):
        with pytest.raises(ValueError, match="record_deadline"):
            operator.reconcile(
                tmp_path,
                tmp_path / "op",
                tmp_path / "absent",
                "",
                seconds=5,
                max_records=1,
                record_seconds=limit,
            )
