"""Deterministic host-capacity assumptions for synthetic storage-cycle tests.

The production admission policy correctly refuses to operate below the
physical reserve on the actual host. CI runners commonly expose less free
space than that reserve, so tests that exercise the cycle with synthetic
fixtures must provide an explicit deterministic free-space baseline instead
of depending on the runner's disk size. Tests that verify reserve refusal
override this fixture with their own bounded values.
"""

import pytest

from jobagg import storage_cycle
from jobagg.storage_budget import PHYSICAL_RESERVE_BYTES, TOTAL_LIMIT


@pytest.fixture(autouse=True)
def synthetic_physical_free(monkeypatch):
    def ample(budget):
        return {
            root.path.stat().st_dev: PHYSICAL_RESERVE_BYTES + TOTAL_LIMIT
            for root in budget.roots
        }

    monkeypatch.setattr(storage_cycle, "_free_bytes", ample)
