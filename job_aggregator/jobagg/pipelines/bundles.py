"""Canonical per-organization output bundles."""

from __future__ import annotations

import csv
import shutil
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from jobagg.db import JobDatabase
from jobagg.models import OrganizationSource, SyncResult
from jobagg.pipelines.exports import export_jobs
from jobagg.pipelines.sync_source import sync_source, sync_source_with_selective_details
from jobagg.robots import RobotsPolicy

EXPECTED_BUNDLE_SUFFIXES = {
    "_jobs.sqlite3",
    "_jobs_current.csv",
    "_jobs_current.json",
    "_jobs_history.csv",
    "_jobs_history.json",
}

_SOURCE_ID_SUFFIXES = (
    "_workday",
    "_taleo",
    "_pageup",
    "_oracle_hcm",
    "_successfactors",
    "_smartrecruiters",
    "_custom_html",
    "_avature",
    "_workable",
    "_inspira",
    "_csod",
    "_uvp",
)


@dataclass(slots=True)
class BundleResult:
    source: OrganizationSource
    slug: str
    file_slug: str
    paths: dict[str, Path]
    sync_result: SyncResult

    @property
    def ok(self) -> bool:
        return not self.sync_result.errors


@dataclass(slots=True)
class BundleValidation:
    missing_files: dict[str, list[str]]
    duplicate_external_ids: dict[str, list[tuple[str, int]]]
    duplicate_apply_urls: dict[str, list[tuple[str, int]]]

    @property
    def ok(self) -> bool:
        return not (
            self.missing_files
            or self.duplicate_external_ids
            or self.duplicate_apply_urls
        )


def source_output_slug(source: OrganizationSource) -> str:
    configured = source.extra.get("output_slug") if source.extra else None
    if configured:
        return str(configured).replace("-", "_")
    slug = source.id
    for suffix in _SOURCE_ID_SUFFIXES:
        if slug.endswith(suffix):
            slug = slug[: -len(suffix)]
            break
    return slug.replace("-", "_")


def source_output_paths(output_dir: str | Path, slug: str) -> dict[str, Path]:
    output = Path(output_dir)
    return {
        "db": output / f"{slug}_jobs.sqlite3",
        "current_json": output / f"{slug}_jobs_current.json",
        "current_csv": output / f"{slug}_jobs_current.csv",
        "history_json": output / f"{slug}_jobs_history.json",
        "history_csv": output / f"{slug}_jobs_history.csv",
    }


def write_source_bundle(
    source: OrganizationSource,
    *,
    output_dir: str | Path,
    policy: RobotsPolicy,
    file_slug: str | None = None,
    seed_db_path: str | Path | None = None,
    selective_details: bool = True,
    deadline_refresh_days: int = 14,
    refresh_all_details: bool = False,
    close_missing: bool = True,
    missing_run_threshold: int = 3,
) -> BundleResult:
    """Sync one source into a five-file bundle."""

    slug = source_output_slug(source)
    paths = source_output_paths(output_dir, file_slug or slug)
    if seed_db_path is not None and Path(seed_db_path).is_file() and not paths["db"].exists():
        paths["db"].parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(seed_db_path, paths["db"])
    db = JobDatabase(paths["db"])
    db.initialize()
    if selective_details:
        result = sync_source_with_selective_details(
            source,
            db=db,
            policy=policy,
            deadline_refresh_days=deadline_refresh_days,
            refresh_all_details=refresh_all_details,
            close_missing=close_missing,
            missing_run_threshold=missing_run_threshold,
        )
    else:
        result = sync_source(
            source,
            db=db,
            policy=policy,
            close_missing=close_missing,
            missing_run_threshold=missing_run_threshold,
        )
    export_bundle(db, paths=paths, source_id=source.id)
    return BundleResult(
        source=source,
        slug=slug,
        file_slug=file_slug or slug,
        paths=paths,
        sync_result=result,
    )


def export_bundle(db: JobDatabase, *, paths: dict[str, Path], source_id: str) -> None:
    export_jobs(
        db,
        output_path=paths["current_json"],
        output_format="json",
        source_id=source_id,
        status="open",
    )
    export_jobs(
        db,
        output_path=paths["current_csv"],
        output_format="csv",
        source_id=source_id,
        status="open",
    )
    export_jobs(
        db,
        output_path=paths["history_json"],
        output_format="json",
        source_id=source_id,
    )
    export_jobs(
        db,
        output_path=paths["history_csv"],
        output_format="csv",
        source_id=source_id,
    )


def select_canonical_results(results: list[BundleResult]) -> tuple[list[BundleResult], list[BundleResult]]:
    """Return one result per organization slug and any duplicate source variants."""

    by_slug: dict[str, list[tuple[int, BundleResult]]] = {}
    for index, result in enumerate(results):
        by_slug.setdefault(result.slug, []).append((index, result))

    selected: list[BundleResult] = []
    duplicates: list[BundleResult] = []
    for grouped in by_slug.values():
        grouped.sort(
            key=lambda item: (
                item[1].ok,
                item[1].sync_result.fetched,
                -item[0],
            ),
            reverse=True,
        )
        selected.append(grouped[0][1])
        duplicates.extend(item[1] for item in grouped[1:])
    selected.sort(key=lambda item: item.slug)
    duplicates.sort(key=lambda item: (item.slug, item.source.id))
    return selected, duplicates


def publish_canonical_results(
    results: list[BundleResult],
    *,
    output_dir: str | Path,
    archive_dir: str | Path | None = None,
    prune_output_dir: bool = True,
) -> tuple[list[BundleResult], list[BundleResult]]:
    """Move staged bundles into canonical output paths.

    If multiple sources map to the same organization slug, the successful source
    with the largest fetched count wins. Non-selected variants are moved to the
    archive when one is provided.
    """

    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    archive = Path(archive_dir) if archive_dir is not None else None
    selected, duplicates = select_canonical_results(results)
    expected_names = {
        path.name
        for result in selected
        for path in source_output_paths(output, result.slug).values()
    }
    if prune_output_dir and archive is not None:
        _archive_noncanonical_files(output, expected_names, archive)

    for result in selected:
        destination_paths = source_output_paths(output, result.slug)
        for key, source_path in result.paths.items():
            _replace_file(source_path, destination_paths[key], archive)

    if archive is not None:
        for result in duplicates:
            duplicate_dir = archive / "duplicate_sources" / result.source.id
            duplicate_dir.mkdir(parents=True, exist_ok=True)
            for source_path in result.paths.values():
                if source_path.exists():
                    shutil.move(str(source_path), _unique_path(duplicate_dir / source_path.name))
    return selected, duplicates


def validate_bundle_dir(output_dir: str | Path, slugs: set[str] | None = None) -> BundleValidation:
    output = Path(output_dir)
    missing_files: dict[str, list[str]] = {}
    duplicate_external_ids: dict[str, list[tuple[str, int]]] = {}
    duplicate_apply_urls: dict[str, list[tuple[str, int]]] = {}

    if slugs is None:
        slugs = {
            path.name[: -len("_jobs.sqlite3")]
            for path in output.glob("*_jobs.sqlite3")
            if path.is_file()
        }

    for slug in sorted(slugs):
        paths = source_output_paths(output, slug)
        missing = [
            suffix
            for suffix in sorted(EXPECTED_BUNDLE_SUFFIXES)
            if not (output / f"{slug}{suffix}").is_file()
        ]
        if missing:
            missing_files[slug] = missing
            continue
        with sqlite3.connect(paths["db"]) as conn:
            duplicate_external_ids[slug] = _duplicate_rows(
                conn,
                "external_id",
            )
            duplicate_apply_urls[slug] = _duplicate_rows(
                conn,
                "apply_url",
            )

    duplicate_external_ids = {
        slug: rows for slug, rows in duplicate_external_ids.items() if rows
    }
    duplicate_apply_urls = {
        slug: rows for slug, rows in duplicate_apply_urls.items() if rows
    }
    return BundleValidation(
        missing_files=missing_files,
        duplicate_external_ids=duplicate_external_ids,
        duplicate_apply_urls=duplicate_apply_urls,
    )


def write_summary(
    results: list[BundleResult],
    duplicates: list[BundleResult],
    *,
    output_path: str | Path,
) -> None:
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, delimiter="\t")
        writer.writerow(
            [
                "slug",
                "source_id",
                "status",
                "fetched",
                "inserted",
                "updated",
                "unchanged",
                "missing",
                "closed",
                "error",
            ]
        )
        for result in sorted(results, key=lambda item: (item.slug, item.source.id)):
            sync = result.sync_result
            writer.writerow(
                [
                    result.slug,
                    result.source.id,
                    "OK" if not sync.errors else "ERROR",
                    sync.fetched,
                    sync.inserted,
                    sync.updated,
                    sync.unchanged,
                    sync.missing,
                    sync.closed,
                    "; ".join(sync.errors),
                ]
            )
        for result in duplicates:
            sync = result.sync_result
            writer.writerow(
                [
                    result.slug,
                    result.source.id,
                    "ARCHIVED_DUPLICATE_SOURCE",
                    sync.fetched,
                    sync.inserted,
                    sync.updated,
                    sync.unchanged,
                    sync.missing,
                    sync.closed,
                    "; ".join(sync.errors),
                ]
            )


def _duplicate_rows(conn: sqlite3.Connection, column: str) -> list[tuple[str, int]]:
    cursor = conn.execute(
        f"""
        SELECT {column}, COUNT(*)
        FROM jobs
        WHERE {column} IS NOT NULL AND {column} != ''
        GROUP BY {column}
        HAVING COUNT(*) > 1
        """
    )
    return [(str(value), int(count)) for value, count in cursor.fetchall()]


def _archive_noncanonical_files(output: Path, expected_names: set[str], archive: Path) -> None:
    archive_root = archive / "previous_output_root"
    archive_root.mkdir(parents=True, exist_ok=True)
    for item in sorted(output.iterdir()):
        if not item.is_file() or item.name in expected_names:
            continue
        shutil.move(str(item), _unique_path(archive_root / item.name))


def _replace_file(source: Path, destination: Path, archive: Path | None) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists() and archive is not None:
        archive_root = archive / "previous_output_root"
        archive_root.mkdir(parents=True, exist_ok=True)
        shutil.move(str(destination), _unique_path(archive_root / destination.name))
    source.replace(destination)


def _unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    stem = path.stem
    suffix = path.suffix
    for index in range(1, 10_000):
        candidate = path.with_name(f"{stem}_{index}{suffix}")
        if not candidate.exists():
            return candidate
    raise RuntimeError(f"Could not allocate unique archive path for {path}")
