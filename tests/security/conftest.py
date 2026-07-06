from __future__ import annotations

from pathlib import Path

import pytest


SECURITY_TEST_ROOT = Path(__file__).resolve().parent


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    for item in items:
        try:
            Path(str(item.path)).resolve().relative_to(SECURITY_TEST_ROOT)
        except ValueError:
            continue
        item.add_marker(pytest.mark.security)
