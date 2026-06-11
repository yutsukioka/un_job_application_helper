"""Vacancy classification and filter support."""

from __future__ import annotations

from typing import Any


def classify_and_store(*args: Any, **kwargs: Any) -> Any:
    from jobagg.classification.pipeline import classify_and_store as _classify_and_store

    return _classify_and_store(*args, **kwargs)


def classify_database(*args: Any, **kwargs: Any) -> Any:
    from jobagg.classification.pipeline import classify_database as _classify_database

    return _classify_database(*args, **kwargs)


def classify_job(*args: Any, **kwargs: Any) -> Any:
    from jobagg.classification.pipeline import classify_job as _classify_job

    return _classify_job(*args, **kwargs)

__all__ = ["classify_and_store", "classify_database", "classify_job"]
