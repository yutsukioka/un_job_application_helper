"""Local-only consolidation of per-organization output bundles."""

from __future__ import annotations

import csv
import sqlite3
from contextlib import closing
from dataclasses import dataclass, field
from pathlib import Path

from jobagg.db import JobDatabase
from jobagg.pipelines.exports import export_jobs


CONSOLIDATED_SLUG = "all"

CONSOLIDATED_TABLES = (
    ("jobs", "job_key"),
    ("vacancy_source_features", "vacancy_id"),
    ("vacancy_classifications", "vacancy_id"),
    ("classification_overrides", "vacancy_id, field_name"),
    ("vacancy_locations", None),
    ("change_events", None),
    ("vacancy_snapshots", None),
    ("source_runs", None),
    ("source_run_diagnostics", "source_run_id"),
)

AUTOINCREMENT_COLUMNS = {
    "change_events": {"id"},
    "vacancy_snapshots": {"id"},
    "source_runs": {"id"},
    "vacancy_locations": {"id"},
}


@dataclass(slots=True)
class ConsolidationResult:
    output_dir: Path
    db_path: Path
    current_json_path: Path
    current_csv_path: Path
    history_json_path: Path
    history_csv_path: Path
    source_db_paths: list[Path]
    table_rows: dict[str, int] = field(default_factory=dict)
    status_counts: dict[str, int] = field(default_factory=dict)
    organization_counts: list[dict[str, object]] = field(default_factory=list)

    @property
    def history_count(self) -> int:
        return sum(self.status_counts.values())

    @property
    def current_count(self) -> int:
        return self.status_counts.get("open", 0)


def consolidated_output_paths(output_dir: str | Path, slug: str = CONSOLIDATED_SLUG) -> dict[str, Path]:
    output = Path(output_dir)
    return {
        "db": output / f"{slug}_jobs.sqlite3",
        "current_json": output / f"{slug}_jobs_current.json",
        "current_csv": output / f"{slug}_jobs_current.csv",
        "history_json": output / f"{slug}_jobs_history.json",
        "history_csv": output / f"{slug}_jobs_history.csv",
    }


def consolidate_bundle_databases(
    *,
    output_dir: str | Path,
    slug: str = CONSOLIDATED_SLUG,
) -> ConsolidationResult:
    """Merge existing per-organization SQLite bundles into one all-jobs bundle.

    This function performs no network fetch. It reads only existing
    ``*_jobs.sqlite3`` files under ``output_dir`` and skips the destination
    ``{slug}_jobs.sqlite3`` file when re-run.
    """

    output = Path(output_dir)
    paths = consolidated_output_paths(output, slug)
    source_db_paths = _source_databases(output, destination=paths["db"])
    if not source_db_paths:
        raise ValueError(f"No source *_jobs.sqlite3 files found in {output}")

    temp_db = paths["db"].with_name(f".{paths['db'].name}.tmp")
    _remove_sqlite_artifacts(temp_db)

    db = JobDatabase(temp_db)
    db.initialize()

    table_rows: dict[str, int] = {table: 0 for table, _ in CONSOLIDATED_TABLES}
    with closing(sqlite3.connect(temp_db)) as dest:
        dest.row_factory = sqlite3.Row
        dest.execute("PRAGMA foreign_keys=ON")
        dest.execute("BEGIN")
        try:
            for source_path in source_db_paths:
                with closing(_connect_source(source_path)) as source:
                    source.row_factory = sqlite3.Row
                    source_run_id_map: dict[int, int] = {}
                    for table, conflict_target in CONSOLIDATED_TABLES:
                        if table == "source_runs":
                            copied, source_run_id_map = _copy_source_runs(source, dest)
                        elif table == "source_run_diagnostics":
                            copied = _copy_source_run_diagnostics(
                                source,
                                dest,
                                source_run_id_map,
                            )
                        else:
                            copied = _copy_table(
                                source,
                                dest,
                                table=table,
                                conflict_target=conflict_target,
                            )
                        table_rows[table] += copied
            dest.commit()
        except Exception:
            dest.rollback()
            raise

    _checkpoint_and_compact(temp_db)
    _remove_sqlite_artifacts(paths["db"])
    temp_db.replace(paths["db"])
    _remove_sqlite_sidecars(temp_db)

    all_db = JobDatabase(paths["db"])
    export_jobs(
        all_db,
        output_path=paths["current_json"],
        output_format="json",
        status="open",
    )
    export_jobs(
        all_db,
        output_path=paths["current_csv"],
        output_format="csv",
        status="open",
    )
    export_jobs(
        all_db,
        output_path=paths["history_json"],
        output_format="json",
    )
    export_jobs(
        all_db,
        output_path=paths["history_csv"],
        output_format="csv",
    )

    status_counts = _status_counts(paths["db"])
    organization_counts = _organization_counts(paths["db"])
    _checkpoint_and_compact(paths["db"])
    _remove_sqlite_sidecars(paths["db"])

    return ConsolidationResult(
        output_dir=output,
        db_path=paths["db"],
        current_json_path=paths["current_json"],
        current_csv_path=paths["current_csv"],
        history_json_path=paths["history_json"],
        history_csv_path=paths["history_csv"],
        source_db_paths=source_db_paths,
        table_rows=table_rows,
        status_counts=status_counts,
        organization_counts=organization_counts,
    )


def write_organization_summary(result: ConsolidationResult, output_path: str | Path) -> None:
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["org_id", "source_id", "ats_family", "current", "history", "missing", "closed"],
        )
        writer.writeheader()
        writer.writerows(result.organization_counts)


def _source_databases(output_dir: Path, *, destination: Path) -> list[Path]:
    return sorted(
        path
        for path in output_dir.glob("*_jobs.sqlite3")
        if path.is_file() and path.resolve() != destination.resolve()
    )


def _connect_source(path: Path) -> sqlite3.Connection:
    uri = f"{path.resolve().as_uri()}?mode=ro"
    try:
        return sqlite3.connect(uri, uri=True)
    except sqlite3.OperationalError:
        # Some fetched bundle databases are WAL-mode files without their sidecar
        # WAL/SHM files. Immutable mode opens those files read-only without
        # attempting to create sidecars, preserving the fetched artifacts.
        immutable_uri = f"{path.resolve().as_uri()}?immutable=1"
        return sqlite3.connect(immutable_uri, uri=True)


def _copy_table(
    source: sqlite3.Connection,
    dest: sqlite3.Connection,
    *,
    table: str,
    conflict_target: str | None,
) -> int:
    if not _table_exists(source, table):
        return 0
    dest_columns = _copyable_columns(dest, table)
    source_columns = set(_copyable_columns(source, table))
    columns = [column for column in dest_columns if column in source_columns]
    if not columns:
        return 0

    select_sql = f"SELECT {', '.join(columns)} FROM {table}"
    rows = source.execute(select_sql).fetchall()
    if not rows:
        return 0

    placeholders = ", ".join("?" for _ in columns)
    conflict_sql = ""
    if conflict_target is not None:
        conflict_sql = f" ON CONFLICT({conflict_target}) DO NOTHING"
    insert_sql = (
        f"INSERT INTO {table} ({', '.join(columns)}) "
        f"VALUES ({placeholders}){conflict_sql}"
    )
    dest.executemany(insert_sql, [tuple(row[column] for column in columns) for row in rows])
    return len(rows)


def _copy_source_runs(
    source: sqlite3.Connection,
    dest: sqlite3.Connection,
) -> tuple[int, dict[int, int]]:
    if not _table_exists(source, "source_runs"):
        return 0, {}
    columns = [column for column in _copyable_columns(dest, "source_runs") if column != "id"]
    source_columns = set(_copyable_columns(source, "source_runs"))
    columns = [column for column in columns if column in source_columns]
    if not columns:
        return 0, {}
    rows = source.execute(f"SELECT id, {', '.join(columns)} FROM source_runs").fetchall()
    if not rows:
        return 0, {}
    placeholders = ", ".join("?" for _ in columns)
    insert_sql = f"INSERT INTO source_runs ({', '.join(columns)}) VALUES ({placeholders})"
    id_map: dict[int, int] = {}
    for row in rows:
        cursor = dest.execute(insert_sql, tuple(row[column] for column in columns))
        id_map[int(row["id"])] = int(cursor.lastrowid)
    return len(rows), id_map


def _copy_source_run_diagnostics(
    source: sqlite3.Connection,
    dest: sqlite3.Connection,
    source_run_id_map: dict[int, int],
) -> int:
    if not source_run_id_map or not _table_exists(source, "source_run_diagnostics"):
        return 0
    columns = [column for column in _copyable_columns(dest, "source_run_diagnostics")]
    source_columns = set(_copyable_columns(source, "source_run_diagnostics"))
    columns = [column for column in columns if column in source_columns]
    if not columns:
        return 0
    rows = source.execute(f"SELECT {', '.join(columns)} FROM source_run_diagnostics").fetchall()
    if not rows:
        return 0
    placeholders = ", ".join("?" for _ in columns)
    insert_sql = (
        f"INSERT OR REPLACE INTO source_run_diagnostics ({', '.join(columns)}) "
        f"VALUES ({placeholders})"
    )
    copied = 0
    for row in rows:
        old_run_id = int(row["source_run_id"])
        new_run_id = source_run_id_map.get(old_run_id)
        if new_run_id is None:
            continue
        values = []
        for column in columns:
            values.append(new_run_id if column == "source_run_id" else row[column])
        dest.execute(insert_sql, tuple(values))
        copied += 1
    return copied


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table,),
    ).fetchone()
    return row is not None


def _copyable_columns(conn: sqlite3.Connection, table: str) -> list[str]:
    excluded = AUTOINCREMENT_COLUMNS.get(table, set())
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    columns = []
    for row in rows:
        name = row["name"] if isinstance(row, sqlite3.Row) else row[1]
        if name not in excluded:
            columns.append(name)
    return columns


def _status_counts(path: Path) -> dict[str, int]:
    with closing(sqlite3.connect(path)) as conn:
        return {
            str(status): int(count)
            for status, count in conn.execute(
                "SELECT status, COUNT(*) FROM jobs GROUP BY status ORDER BY status"
            )
        }


def _organization_counts(path: Path) -> list[dict[str, object]]:
    with closing(sqlite3.connect(path)) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT
                org_id,
                source_id,
                ats_family,
                SUM(CASE WHEN status = 'open' THEN 1 ELSE 0 END) AS current,
                COUNT(*) AS history,
                SUM(CASE WHEN status = 'missing' THEN 1 ELSE 0 END) AS missing,
                SUM(CASE WHEN status = 'closed' THEN 1 ELSE 0 END) AS closed
            FROM jobs
            GROUP BY org_id, source_id, ats_family
            ORDER BY org_id, source_id
            """
        ).fetchall()
        return [dict(row) for row in rows]


def _checkpoint_and_compact(path: Path) -> None:
    with closing(sqlite3.connect(path, isolation_level=None)) as conn:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchall()
        conn.execute("PRAGMA journal_mode=DELETE").fetchall()


def _remove_sqlite_artifacts(path: Path) -> None:
    for artifact in (path, *_sqlite_sidecars(path)):
        if artifact.exists():
            artifact.unlink()


def _remove_sqlite_sidecars(path: Path) -> None:
    for sidecar in _sqlite_sidecars(path):
        if sidecar.exists():
            sidecar.unlink()


def _sqlite_sidecars(path: Path) -> tuple[Path, Path]:
    return (
        path.with_name(f"{path.name}-wal"),
        path.with_name(f"{path.name}-shm"),
    )
