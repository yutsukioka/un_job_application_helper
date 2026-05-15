"""Command-line entry point for job aggregation runs."""

from __future__ import annotations

import argparse
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from jobagg.db import JobDatabase
from jobagg.models import OrganizationSource
from jobagg.observability.logging import configure_logging, get_logger
from jobagg.pipelines.bundles import (
    publish_canonical_results,
    source_output_paths,
    source_output_slug,
    validate_bundle_dir,
    write_source_bundle,
    write_summary,
)
from jobagg.pipelines.exports import export_jobs
from jobagg.pipelines.sync_source import (
    load_sources,
    sync_all,
    sync_source_with_selective_details,
)
from jobagg.robots import RobotsPolicy, load_policy

LOGGER = get_logger(__name__)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="jobagg", description="Aggregate job postings by ATS family.")
    parser.add_argument("--db", default="output/jobagg.sqlite3", help="SQLite database path.")
    parser.add_argument("--verbose", action="store_true", help="Enable debug logging.")
    subcommands = parser.add_subparsers(dest="command", required=True)

    init_db = subcommands.add_parser("init-db", help="Create or migrate the SQLite schema.")
    init_db.set_defaults(handler=handle_init_db)

    sync = subcommands.add_parser("sync", help="Synchronize enabled sources from organizations.yaml.")
    sync.add_argument("--config", default="config/organizations.yaml", help="Organizations config path.")
    sync.add_argument("--robots-policy", default="config/robots_policy.yaml", help="Robots policy path.")
    sync.add_argument("--include-disabled", action="store_true", help="Run disabled sample sources too.")
    sync.set_defaults(handler=handle_sync)

    bundles = subcommands.add_parser(
        "sync-bundles",
        help="Sync sources into canonical per-organization SQLite/JSON/CSV bundles.",
    )
    bundles.add_argument("--config", default="config/organizations.yaml", help="Organizations config path.")
    bundles.add_argument("--robots-policy", default="config/robots_policy.yaml", help="Robots policy path.")
    bundles.add_argument(
        "--output-dir",
        default="output",
        help="Directory that will contain only canonical *_jobs.* bundles.",
    )
    bundles.add_argument(
        "--source-id",
        action="append",
        help="Only sync this source ID. Repeat to sync multiple sources.",
    )
    bundles.add_argument(
        "--skip-source-id",
        action="append",
        default=[],
        help="Skip this source ID. Repeat for multiple skips.",
    )
    bundles.add_argument(
        "--include-disabled",
        action="store_true",
        help="Include disabled real sources. Sample sources remain excluded unless --include-samples is set.",
    )
    bundles.add_argument(
        "--include-samples",
        action="store_true",
        help="Include sample_* sources when --include-disabled or --source-id is used.",
    )
    bundles.add_argument(
        "--ignore-robots-txt",
        action="store_true",
        help="One-off implementation test mode: disable robots.txt checks in memory for this run.",
    )
    bundles.add_argument(
        "--full-sync",
        action="store_true",
        help="Use each adapter's normal sync path instead of selective detail refresh.",
    )
    bundles.add_argument(
        "--deadline-refresh-days",
        type=int,
        default=14,
        help="When using selective detail refresh, refresh details for jobs closing within this many days.",
    )
    bundles.add_argument(
        "--missing-run-threshold",
        type=int,
        default=3,
        help="Mark a not-seen posting missing after this many consecutive successful source runs.",
    )
    bundles.add_argument(
        "--refresh-all-details",
        action="store_true",
        help="When using selective detail refresh, fetch every detail page this run.",
    )
    bundles.add_argument(
        "--keep-extra-output-files",
        action="store_true",
        help="Do not archive noncanonical files already in --output-dir.",
    )
    bundles.add_argument(
        "--no-archive",
        action="store_true",
        help="Do not archive overwritten, duplicate, or noncanonical output files.",
    )
    bundles.set_defaults(handler=handle_sync_bundles)

    export = subcommands.add_parser("export", help="Export persisted jobs.")
    export.add_argument("--format", choices=["json", "csv"], default="json", help="Export format.")
    export.add_argument("--output", required=True, help="Output file path.")
    export.add_argument("--source-id", help="Optional source filter.")
    export.add_argument("--status", choices=["open", "missing", "closed"], help="Optional job status filter.")
    export.set_defaults(handler=handle_export)

    refresh = subcommands.add_parser(
        "refresh-deadlines",
        help="Sync listings and fetch details only for jobs whose deadlines need checking.",
    )
    refresh.add_argument("--config", default="config/organizations.yaml", help="Organizations config path.")
    refresh.add_argument("--robots-policy", default="config/robots_policy.yaml", help="Robots policy path.")
    refresh.add_argument("--source-id", help="Only refresh one source ID.")
    refresh.add_argument(
        "--deadline-refresh-days",
        type=int,
        default=14,
        help="Refresh detail pages for jobs closing within this many days.",
    )
    refresh.add_argument(
        "--missing-run-threshold",
        type=int,
        default=3,
        help="Mark a not-seen posting missing after this many consecutive successful source runs.",
    )
    refresh.add_argument(
        "--refresh-all-details",
        action="store_true",
        help="Fetch every detail page this run; useful for occasional full audits.",
    )
    refresh.add_argument("--json-output", default="output/jobs_current.json", help="Stable JSON export path.")
    refresh.add_argument("--csv-output", default="output/jobs_current.csv", help="Stable CSV export path.")
    refresh.add_argument(
        "--history-json-output",
        default="output/jobs_history.json",
        help="Stable JSON export path for all jobs ever seen.",
    )
    refresh.add_argument(
        "--history-csv-output",
        default="output/jobs_history.csv",
        help="Stable CSV export path for all jobs ever seen.",
    )
    refresh.add_argument(
        "--separate-by-source",
        action="store_true",
        help="Write one SQLite DB and current/history export bundle per source.",
    )
    refresh.add_argument(
        "--output-dir",
        default="output",
        help="Directory for --separate-by-source output bundles.",
    )
    refresh.set_defaults(handler=handle_refresh_deadlines)
    return parser


def handle_init_db(args: argparse.Namespace) -> int:
    db = JobDatabase(args.db)
    db.initialize()
    LOGGER.info("Initialized database at %s", Path(args.db))
    return 0


def handle_sync(args: argparse.Namespace) -> int:
    db = JobDatabase(args.db)
    db.initialize()
    policy = load_policy(args.robots_policy)
    results = sync_all(
        config_path=args.config,
        db=db,
        policy=policy,
        include_disabled=args.include_disabled,
    )
    for result in results:
        if result.errors:
            LOGGER.error("%s errors=%s", result.source_id, "; ".join(result.errors))
        LOGGER.info(
            "%s fetched=%s inserted=%s updated=%s unchanged=%s missing=%s closed=%s",
            result.source_id,
            result.fetched,
            result.inserted,
            result.updated,
            result.unchanged,
            result.missing,
            result.closed,
        )
    return 1 if any(result.errors for result in results) else 0


def handle_sync_bundles(args: argparse.Namespace) -> int:
    policy = load_policy(args.robots_policy)
    if args.ignore_robots_txt:
        policy = RobotsPolicy(
            user_agent=policy.user_agent,
            honor_robots_txt=False,
            request_timeout_seconds=policy.request_timeout_seconds,
            min_delay_seconds=policy.min_delay_seconds,
            max_pages_per_source=policy.max_pages_per_source,
            max_jobs_per_source=policy.max_jobs_per_source,
            domains={},
        )

    sources = _bundle_sources(args)
    if not sources:
        LOGGER.error("No sources selected for sync-bundles")
        return 1

    output_dir = Path(args.output_dir)
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    archive_dir = None if args.no_archive else _archive_dir_for(output_dir)
    all_results = []
    with tempfile.TemporaryDirectory(prefix="jobagg_bundles_", dir=output_dir.parent) as staging:
        staging_dir = Path(staging)
        for source in sources:
            result = write_source_bundle(
                source,
                output_dir=staging_dir,
                policy=policy,
                file_slug=source.id,
                seed_db_path=source_output_paths(output_dir, source_output_slug(source))["db"],
                selective_details=not args.full_sync,
                deadline_refresh_days=args.deadline_refresh_days,
                refresh_all_details=args.refresh_all_details,
                missing_run_threshold=args.missing_run_threshold,
            )
            all_results.append(result)
            sync = result.sync_result
            if sync.errors:
                LOGGER.error("%s errors=%s", source.id, "; ".join(sync.errors))
            LOGGER.info(
                "%s slug=%s fetched=%s inserted=%s updated=%s unchanged=%s missing=%s closed=%s",
                source.id,
                result.slug,
                sync.fetched,
                sync.inserted,
                sync.updated,
                sync.unchanged,
                sync.missing,
                sync.closed,
            )

        selected, duplicates = publish_canonical_results(
            all_results,
            output_dir=output_dir,
            archive_dir=archive_dir,
            prune_output_dir=not args.keep_extra_output_files and not args.source_id,
        )
        if archive_dir is not None:
            write_summary(
                selected,
                duplicates,
                output_path=archive_dir / "sync_bundles_summary.tsv",
            )

    validation = validate_bundle_dir(output_dir, {result.slug for result in selected})
    if not validation.ok:
        LOGGER.error("Bundle validation failed: %s", validation)
        return 1
    if duplicates:
        LOGGER.info(
            "Archived duplicate source variants: %s",
            ", ".join(result.source.id for result in duplicates),
        )
    if archive_dir is not None:
        LOGGER.info("Archived previous/noncanonical output files at %s", archive_dir)
    LOGGER.info("Published %s canonical organization bundles to %s", len(selected), output_dir)
    return 1 if any(result.sync_result.errors for result in selected) else 0


def handle_export(args: argparse.Namespace) -> int:
    export_jobs(
        JobDatabase(args.db),
        output_path=args.output,
        output_format=args.format,
        source_id=args.source_id,
        status=args.status,
    )
    LOGGER.info("Exported jobs to %s", args.output)
    return 0


def handle_refresh_deadlines(args: argparse.Namespace) -> int:
    policy = load_policy(args.robots_policy)
    sources = [
        source
        for source in load_sources(args.config)
        if source.enabled and (args.source_id is None or source.id == args.source_id)
    ]
    if args.source_id and not sources:
        LOGGER.error("No enabled source found for source_id=%s", args.source_id)
        return 1

    if args.separate_by_source:
        return _refresh_deadlines_separate_by_source(args, sources, policy)

    db = JobDatabase(args.db)
    db.initialize()
    results = [
        sync_source_with_selective_details(
            source,
            db=db,
            policy=policy,
            deadline_refresh_days=args.deadline_refresh_days,
            refresh_all_details=args.refresh_all_details,
            missing_run_threshold=args.missing_run_threshold,
        )
        for source in sources
    ]
    for result in results:
        if result.errors:
            LOGGER.error("%s errors=%s", result.source_id, "; ".join(result.errors))
        LOGGER.info(
            "%s fetched=%s inserted=%s updated=%s unchanged=%s missing=%s closed=%s",
            result.source_id,
            result.fetched,
            result.inserted,
            result.updated,
            result.unchanged,
            result.missing,
            result.closed,
        )

    source_filter = args.source_id
    export_jobs(
        db,
        output_path=args.json_output,
        output_format="json",
        source_id=source_filter,
        status="open",
    )
    export_jobs(
        db,
        output_path=args.csv_output,
        output_format="csv",
        source_id=source_filter,
        status="open",
    )
    export_jobs(
        db,
        output_path=args.history_json_output,
        output_format="json",
        source_id=source_filter,
    )
    export_jobs(
        db,
        output_path=args.history_csv_output,
        output_format="csv",
        source_id=source_filter,
    )
    LOGGER.info("Exported current open JSON to %s", args.json_output)
    LOGGER.info("Exported current open CSV to %s", args.csv_output)
    LOGGER.info("Exported all-history JSON to %s", args.history_json_output)
    LOGGER.info("Exported all-history CSV to %s", args.history_csv_output)
    return 1 if any(result.errors for result in results) else 0


def _refresh_deadlines_separate_by_source(
    args: argparse.Namespace,
    sources: list[OrganizationSource],
    policy: RobotsPolicy,
) -> int:
    results = []
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    slugs = set()
    for source in sources:
        slug = _source_output_slug(source)
        slugs.add(slug)
        paths = _source_output_paths(output_dir, slug)
        db = JobDatabase(paths["db"])
        db.initialize()
        result = sync_source_with_selective_details(
            source,
            db=db,
            policy=policy,
            deadline_refresh_days=args.deadline_refresh_days,
            refresh_all_details=args.refresh_all_details,
            missing_run_threshold=args.missing_run_threshold,
        )
        results.append(result)
        if result.errors:
            LOGGER.error("%s errors=%s", result.source_id, "; ".join(result.errors))
        LOGGER.info(
            "%s fetched=%s inserted=%s updated=%s unchanged=%s missing=%s closed=%s",
            result.source_id,
            result.fetched,
            result.inserted,
            result.updated,
            result.unchanged,
            result.missing,
            result.closed,
        )
        export_jobs(
            db,
            output_path=paths["current_json"],
            output_format="json",
            source_id=source.id,
            status="open",
        )
        export_jobs(
            db,
            output_path=paths["current_csv"],
            output_format="csv",
            source_id=source.id,
            status="open",
        )
        export_jobs(
            db,
            output_path=paths["history_json"],
            output_format="json",
            source_id=source.id,
        )
        export_jobs(
            db,
            output_path=paths["history_csv"],
            output_format="csv",
            source_id=source.id,
        )
        LOGGER.info("%s DB: %s", source.id, paths["db"])
        LOGGER.info("%s current JSON: %s", source.id, paths["current_json"])
        LOGGER.info("%s history JSON: %s", source.id, paths["history_json"])
    validation = validate_bundle_dir(output_dir, slugs)
    if not validation.ok:
        LOGGER.error("Bundle validation failed: %s", validation)
        return 1
    return 1 if any(result.errors for result in results) else 0


def _source_output_slug(source: OrganizationSource) -> str:
    return source_output_slug(source)


def _source_output_paths(output_dir: Path, slug: str) -> dict[str, Path]:
    return source_output_paths(output_dir, slug)


def _bundle_sources(args: argparse.Namespace) -> list[OrganizationSource]:
    selected_ids = set(args.source_id or [])
    skipped_ids = set(args.skip_source_id or [])
    sources = []
    for source in load_sources(args.config):
        if source.id in skipped_ids:
            continue
        if source.id.startswith("sample_") and not args.include_samples:
            continue
        if selected_ids:
            if source.id in selected_ids:
                sources.append(source)
            continue
        if source.enabled or args.include_disabled:
            sources.append(source)
    if selected_ids:
        found_ids = {source.id for source in sources}
        missing = sorted(selected_ids - found_ids - skipped_ids)
        for source_id in missing:
            LOGGER.error("No configured source found for source_id=%s", source_id)
    return sources


def _archive_dir_for(output_dir: Path) -> Path:
    timestamp = datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%SZ")
    base = output_dir.parent / f"{output_dir.name}_archive_{timestamp}"
    if not base.exists():
        return base
    for index in range(1, 10_000):
        candidate = output_dir.parent / f"{output_dir.name}_archive_{timestamp}_{index}"
        if not candidate.exists():
            return candidate
    raise RuntimeError(f"Could not allocate archive directory for {output_dir}")


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    configure_logging(verbose=args.verbose)
    return args.handler(args)


if __name__ == "__main__":
    raise SystemExit(main())
