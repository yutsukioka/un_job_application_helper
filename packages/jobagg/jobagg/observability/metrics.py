"""Tiny in-process metrics collector for sync runs."""

from __future__ import annotations

from collections import Counter
from contextlib import contextmanager
from dataclasses import dataclass, field
from time import perf_counter


@dataclass(slots=True)
class Metrics:
    counters: Counter[str] = field(default_factory=Counter)
    timings: dict[str, float] = field(default_factory=dict)

    def increment(self, name: str, value: int = 1) -> None:
        self.counters[name] += value

    @contextmanager
    def timer(self, name: str):
        start = perf_counter()
        try:
            yield
        finally:
            self.timings[name] = self.timings.get(name, 0.0) + (perf_counter() - start)

