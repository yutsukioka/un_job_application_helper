"""Export normalized jobs."""

from __future__ import annotations

import csv
import json
from pathlib import Path

from jobagg.db import JobDatabase


EXPORT_FIELDS = [
    "source_id",
    "ats_family",
    "external_id",
    "title",
    "location",
    "department",
    "employment_type",
    "posted_at",
    "closes_at",
    "status",
    "apply_url",
    "source_url",
    "last_seen_at",
]


def export_jobs(
    db: JobDatabase,
    *,
    output_path: str | Path,
    output_format: str = "json",
    source_id: str | None = None,
    status: str | None = None,
) -> None:
    rows = list(db.iter_jobs(source_id=source_id, status=status))
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if output_format == "json":
        path.write_text(json.dumps(rows, indent=2, ensure_ascii=True), encoding="utf-8")
        return
    if output_format == "csv":
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=EXPORT_FIELDS, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)
        return
    raise ValueError(f"Unsupported export format: {output_format}")
