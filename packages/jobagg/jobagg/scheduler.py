"""Command-line entry point for job aggregation runs."""

from __future__ import annotations

import argparse
import csv
import getpass
import json
import os
import re
import sqlite3
import sys
import tempfile
import webbrowser
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

from jobagg.classification import classify_database
from jobagg.classification.audit import audit_classification, audit_to_markdown
from jobagg.classification.classifiers.ccog import ccog_tree
from jobagg.classification.models import CLASSIFICATION_VERSION
from jobagg.db import JobDatabase
from jobagg.filters.explain import explain_job_match, explain_to_text
from jobagg.filters.facets import facet_counts
from jobagg.filters.query import response_to_dict, search_collected_jobs, search_vacancies
from jobagg.filters.saved_searches import (
    SavedSearch,
    get_saved_search,
    list_saved_searches,
    remove_saved_search,
    request_to_dict,
    save_search,
)
from jobagg.filters.schemas import VacancyFilters, VacancySearchRequest
from jobagg.models import OrganizationSource, SyncResult
from jobagg.observability.logging import configure_logging, get_logger
from jobagg.ops_check import collect_ops_check, ops_check_to_markdown
from jobagg.pipelines.bundles import (
    BundleResult,
    publish_canonical_results,
    source_output_paths,
    source_output_slug,
    validate_bundle_dir,
    write_source_bundle,
    write_summary,
)
from jobagg.pipelines.consolidation import (
    consolidate_bundle_databases,
    write_organization_summary,
)
from jobagg.pipelines.exports import export_jobs
from jobagg.pipelines.sync_source import (
    fetch_schedule_policy,
    load_sources,
    sync_all,
    sync_source_with_selective_details,
)
from jobagg.robots import RobotsPolicy, load_policy

LOGGER = get_logger(__name__)
PACKAGE_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = PACKAGE_ROOT.parents[1]
DEFAULT_CONFIG_PATH = PACKAGE_ROOT / "config" / "organizations.yaml"
DEFAULT_ROBOTS_POLICY_PATH = PACKAGE_ROOT / "config" / "robots_policy.yaml"
DEFAULT_OUTPUT_DIR = Path(os.environ.get("JOBAGG_OUTPUT_DIR", REPO_ROOT / "private" / "jobagg" / "output"))
DEFAULT_DB_PATH = DEFAULT_OUTPUT_DIR / "jobagg.sqlite3"
DEFAULT_SAVED_SEARCHES_PATH = Path(
    os.environ.get("JOBAGG_SAVED_SEARCHES", REPO_ROOT / "private" / "jobagg" / "saved_searches.json")
)


def _non_negative_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"invalid integer value: {value}") from exc
    if parsed < 0:
        raise argparse.ArgumentTypeError("value must be 0 or greater")
    return parsed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="jobagg", description="Aggregate job postings by ATS family.")
    parser.add_argument("--db", default=str(DEFAULT_DB_PATH), help="SQLite database path.")
    parser.add_argument(
        "--saved-searches",
        default=str(DEFAULT_SAVED_SEARCHES_PATH),
        help="Saved searches JSON path.",
    )
    parser.add_argument("--verbose", action="store_true", help="Enable debug logging.")
    subcommands = parser.add_subparsers(dest="command", required=True)

    init_db = subcommands.add_parser("init-db", help="Create or migrate the SQLite schema.")
    init_db.set_defaults(handler=handle_init_db)

    sync = subcommands.add_parser("sync", help="Synchronize enabled sources from organizations.yaml.")
    sync.add_argument("--config", default=str(DEFAULT_CONFIG_PATH), help="Organizations config path.")
    sync.add_argument("--robots-policy", default=str(DEFAULT_ROBOTS_POLICY_PATH), help="Robots policy path.")
    sync.add_argument("--include-disabled", action="store_true", help="Run disabled sample sources too.")
    sync.set_defaults(handler=handle_sync)

    fetch_schedule = subcommands.add_parser(
        "fetch-schedule",
        help="Dry-run the effective list/detail fetch schedule without contacting sources.",
    )
    fetch_schedule.add_argument("--config", default=str(DEFAULT_CONFIG_PATH), help="Organizations config path.")
    fetch_schedule.add_argument(
        "--source-id",
        action="append",
        help="Only show this source ID. Repeat to show multiple sources.",
    )
    fetch_schedule.add_argument(
        "--include-disabled",
        action="store_true",
        help="Include disabled sources in the dry-run schedule.",
    )
    fetch_schedule.add_argument(
        "--format",
        choices=["text", "json"],
        default="text",
        help="Output format.",
    )
    fetch_schedule.add_argument("--output", help="Optional output path. Defaults to stdout.")
    fetch_schedule.set_defaults(handler=handle_fetch_schedule)

    source_health = subcommands.add_parser(
        "source-health-report",
        help="Dry-run source health, pacing, breaker, and health-report schema diagnostics without live fetches.",
    )
    source_health.add_argument("--config", default=str(DEFAULT_CONFIG_PATH), help="Organizations config path.")
    source_health.add_argument(
        "--output-dir",
        default=str(DEFAULT_OUTPUT_DIR),
        help="Directory containing canonical *_jobs.sqlite3 bundles and sync_bundles_health.json.",
    )
    source_health.add_argument(
        "--sources",
        help="Comma-separated source IDs to include.",
    )
    source_health.add_argument(
        "--source-id",
        action="append",
        help="Source ID to include. Repeat for multiple sources.",
    )
    source_health.add_argument("--dry-run", action="store_true", help="Assert no live fetch will be attempted.")
    source_health.add_argument(
        "--format",
        choices=["json", "text"],
        default="text",
        help="Output format.",
    )
    source_health.add_argument("--output", help="Optional output path. Defaults to stdout.")
    source_health.set_defaults(handler=handle_source_health_report)

    bundles = subcommands.add_parser(
        "sync-bundles",
        help="Sync sources into canonical per-organization SQLite/JSON/CSV bundles.",
    )
    bundles.add_argument("--config", default=str(DEFAULT_CONFIG_PATH), help="Organizations config path.")
    bundles.add_argument("--robots-policy", default=str(DEFAULT_ROBOTS_POLICY_PATH), help="Robots policy path.")
    bundles.add_argument(
        "--output-dir",
        default=str(DEFAULT_OUTPUT_DIR),
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
        "--detail-page-limit",
        type=_non_negative_int,
        help=(
            "One-off override for max detail pages per source in this sync-bundles run. "
            "Use with --source-id and --refresh-all-details to deliberately drain a backlog."
        ),
    )
    bundles.add_argument(
        "--keep-extra-output-files",
        action="store_true",
        help="Do not archive noncanonical files already in --output-dir.",
    )
    bundles.add_argument(
        "--skip-classify",
        action="store_true",
        help="Do not run classification before writing bundle exports.",
    )
    bundles.add_argument(
        "--no-archive",
        action="store_true",
        help="Do not archive overwritten, duplicate, or noncanonical output files.",
    )
    bundles.add_argument(
        "--skip-consolidate",
        action="store_true",
        help="Do not refresh all_jobs.sqlite3/current/history after publishing bundles.",
    )
    bundles.add_argument(
        "--allow-source-degraded",
        action="store_true",
        help="Return exit 0 when publishing succeeds but one or more sources are degraded.",
    )
    bundles.add_argument(
        "--health-report-output",
        help="Optional JSON health report path. Defaults to sync_bundles_health.json in --output-dir.",
    )
    bundles.add_argument(
        "--browser-cookie-assist",
        action="store_true",
        help=(
            "Interactive mode for WAF-challenged sources: open the source in a browser, "
            "then inject a user-provided Cookie header into that source's HTTP client."
        ),
    )
    bundles.add_argument(
        "--browser-cookie-assist-on-block",
        action="store_true",
        help=(
            "Reactive browser cookie assist: run normally first, then open the browser "
            "and retry affected sources only when a browser-assist source hits a block."
        ),
    )
    bundles.add_argument(
        "--browser-cookie-source-id",
        action="append",
        default=[],
        help=(
            "Source ID to use with --browser-cookie-assist. Repeat for multiple sources. "
            "Defaults to selected sources with extra.browser_cookie_assist=true."
        ),
    )
    bundles.add_argument(
        "--browser-cookie-file",
        help=(
            "Path containing a Cookie header for --browser-cookie-assist. "
            "If omitted, JOBAGG_BROWSER_COOKIE_HEADER or an interactive hidden prompt is used."
        ),
    )
    bundles.add_argument(
        "--browser-cookie-env",
        default="JOBAGG_BROWSER_COOKIE_HEADER",
        help="Environment variable containing the Cookie header for --browser-cookie-assist.",
    )
    bundles.add_argument(
        "--no-browser-open",
        action="store_true",
        help="With --browser-cookie-assist, do not open the browser before reading the Cookie header.",
    )
    bundles.set_defaults(handler=handle_sync_bundles)

    consolidate = subcommands.add_parser(
        "consolidate-bundles",
        help="Consolidate existing per-organization bundles into all_jobs outputs without fetching.",
    )
    consolidate.add_argument(
        "--output-dir",
        default="output",
        help="Directory containing existing *_jobs.sqlite3 bundles.",
    )
    consolidate.add_argument(
        "--slug",
        default="all",
        help="Output slug for consolidated files. Defaults to all.",
    )
    consolidate.add_argument(
        "--summary-output",
        help="Optional CSV path for per-organization current/history counts.",
    )
    consolidate.set_defaults(handler=handle_consolidate_bundles)

    export = subcommands.add_parser("export", help="Export persisted jobs.")
    export.add_argument("--format", choices=["json", "csv"], default="json", help="Export format.")
    export.add_argument("--output", required=True, help="Output file path.")
    export.add_argument("--source-id", help="Optional source filter.")
    export.add_argument("--status", choices=["open", "missing", "closed"], help="Optional job status filter.")
    export.set_defaults(handler=handle_export)

    classify = subcommands.add_parser("classify", help="Classify persisted jobs for filtering.")
    classify.add_argument("--source-id", help="Only classify this source ID.")
    classify.add_argument("--all", action="store_true", help="Classify all persisted jobs.")
    classify.add_argument("--status", choices=["open", "missing", "closed"], help="Optional status filter.")
    classify.add_argument(
        "--version",
        default=CLASSIFICATION_VERSION,
        help="Classification version label to store.",
    )
    classify.add_argument(
        "--reclassify-all",
        action="store_true",
        help="Re-classify every job, even if its source content is unchanged since the last run.",
    )
    classify.set_defaults(handler=handle_classify)

    audit = subcommands.add_parser(
        "audit-classification",
        help="Audit classification coverage, confidence, and source quality.",
    )
    audit.add_argument("--all", action="store_true", help="Audit all classified sources.")
    audit.add_argument("--source-id", action="append", default=[], help="Source ID filter. Repeat for multiple sources.")
    audit.add_argument("--status", choices=["open", "missing", "closed"], default="open", help="Job status filter.")
    audit.add_argument(
        "--format",
        choices=["json", "markdown"],
        default="json",
        help="Output format.",
    )
    audit.add_argument("--output", help="Optional output file path. Defaults to stdout.")
    audit.set_defaults(handler=handle_audit_classification)

    ops = subcommands.add_parser(
        "ops-check",
        help="Write a Markdown operational health report for collected bundles.",
    )
    ops.add_argument("--all", action="store_true", help="Inspect all *_jobs.sqlite3 bundle databases.")
    ops.add_argument("--output", help="Markdown output path. Defaults to stdout.")
    ops.add_argument(
        "--output-dir",
        help="Bundle output directory. Defaults to the parent directory of --output when provided.",
    )
    ops.set_defaults(handler=handle_ops_check)

    filter_cmd = subcommands.add_parser("filter", help="Filter classified vacancies.")
    _add_filter_arguments(filter_cmd)
    filter_cmd.add_argument(
        "--format",
        choices=["json", "csv", "markdown"],
        default="json",
        help="Output format.",
    )
    filter_cmd.add_argument("--output", help="Optional output file path. Defaults to stdout.")
    filter_cmd.set_defaults(handler=handle_filter)

    search = subcommands.add_parser("search", help="Search classified vacancies with list filters.")
    _add_search_arguments(search)
    search.add_argument(
        "--format",
        choices=["json", "csv", "markdown"],
        default="json",
        help="Output format.",
    )
    search.add_argument("--output", help="Optional output file path. Defaults to stdout.")
    search.add_argument("--explain", action="store_true", help="Include per-filter match evaluation.")
    search.set_defaults(handler=handle_search)

    search_debug = subcommands.add_parser("search-debug", help="Explain why one job matches or misses a search.")
    _add_search_arguments(search_debug)
    search_debug.add_argument("--job-key", required=True, help="Job key to evaluate.")
    search_debug.add_argument(
        "--format",
        choices=["text", "json"],
        default="text",
        help="Output format.",
    )
    search_debug.add_argument("--output", help="Optional output file path. Defaults to stdout.")
    search_debug.set_defaults(handler=handle_search_debug)

    saved = subcommands.add_parser("saved-search", help="Add, list, run, and remove saved searches.")
    saved_subcommands = saved.add_subparsers(dest="saved_search_command", required=True)

    saved_add = saved_subcommands.add_parser("add", help="Create or replace a saved search.")
    saved_add.add_argument("name", help="Saved search name.")
    saved_add.add_argument("--description", help="Optional saved search description.")
    saved_add.add_argument("--overwrite", action="store_true", help="Replace an existing saved search.")
    _add_search_arguments(saved_add)
    saved_add.set_defaults(handler=handle_saved_search_add)

    saved_list = saved_subcommands.add_parser("list", help="List saved searches.")
    saved_list.add_argument(
        "--format",
        choices=["json", "text"],
        default="text",
        help="Output format.",
    )
    saved_list.add_argument("--output", help="Optional output file path. Defaults to stdout.")
    saved_list.set_defaults(handler=handle_saved_search_list)

    saved_run = saved_subcommands.add_parser("run", help="Run one saved search or all saved searches.")
    saved_run.add_argument("name", nargs="?", help="Saved search name.")
    saved_run.add_argument("--all", action="store_true", help="Run all saved searches.")
    saved_run.add_argument(
        "--format",
        choices=["json", "csv", "markdown"],
        default="json",
        help="Output format.",
    )
    saved_run.add_argument("--output", help="Optional output file path. Defaults to stdout.")
    saved_run.add_argument("--explain", action="store_true", help="Include per-filter match evaluation.")
    saved_run.set_defaults(handler=handle_saved_search_run)

    saved_remove = saved_subcommands.add_parser("remove", help="Remove a saved search.")
    saved_remove.add_argument("name", help="Saved search name.")
    saved_remove.set_defaults(handler=handle_saved_search_remove)

    facets = subcommands.add_parser("facets", help="Print facet counts for classified vacancies.")
    _add_filter_arguments(facets)
    facets.add_argument("--output", help="Optional JSON output path. Defaults to stdout.")
    facets.set_defaults(handler=handle_facets)

    tree = subcommands.add_parser("ccog-tree", help="Print the packaged CCOG tree.")
    tree.add_argument("--output", help="Optional JSON output path. Defaults to stdout.")
    tree.set_defaults(handler=handle_ccog_tree)

    refresh = subcommands.add_parser(
        "refresh-deadlines",
        help="Sync listings and fetch details only for jobs whose deadlines need checking.",
    )
    refresh.add_argument("--config", default=str(DEFAULT_CONFIG_PATH), help="Organizations config path.")
    refresh.add_argument("--robots-policy", default=str(DEFAULT_ROBOTS_POLICY_PATH), help="Robots policy path.")
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
    refresh.add_argument("--json-output", default=str(DEFAULT_OUTPUT_DIR / "jobs_current.json"), help="Stable JSON export path.")
    refresh.add_argument("--csv-output", default=str(DEFAULT_OUTPUT_DIR / "jobs_current.csv"), help="Stable CSV export path.")
    refresh.add_argument(
        "--history-json-output",
        default=str(DEFAULT_OUTPUT_DIR / "jobs_history.json"),
        help="Stable JSON export path for all jobs ever seen.",
    )
    refresh.add_argument(
        "--history-csv-output",
        default=str(DEFAULT_OUTPUT_DIR / "jobs_history.csv"),
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


def handle_fetch_schedule(args: argparse.Namespace) -> int:
    selected_ids = set(args.source_id or [])
    sources = [
        source
        for source in load_sources(args.config)
        if (source.enabled or args.include_disabled)
        and (not selected_ids or source.id in selected_ids)
    ]
    missing = selected_ids.difference(source.id for source in sources)
    if missing:
        LOGGER.error("Unknown or disabled source IDs: %s", ", ".join(sorted(missing)))
        return 1

    rows = [
        {
            "source_id": source.id,
            "name": source.name,
            "ats_family": source.ats_family,
            "enabled": source.enabled,
            "policy": fetch_schedule_policy(source),
        }
        for source in sources
    ]
    if args.format == "json":
        _write_text(json.dumps(rows, indent=2, sort_keys=True, ensure_ascii=True) + "\n", args.output)
        return 0

    lines = []
    for row in rows:
        policy = row["policy"]
        delay = policy.get("oracle_detail_min_delay_seconds", policy.get("detail_min_delay_seconds", 0))
        jitter = policy.get("oracle_detail_jitter_seconds", policy.get("detail_jitter_seconds", 0))
        batch_size = policy.get("oracle_detail_batch_size", policy.get("detail_batch_size", ""))
        batch_pause = policy.get(
            "oracle_detail_batch_pause_seconds",
            policy.get("detail_batch_pause_seconds", 0),
        )
        stop_after = policy.get(
            "oracle_detail_stop_after_transient_failures",
            policy.get("stop_after_transient_failures", policy.get("detail_stop_after_transient_failures", "")),
        )
        cooldown = policy.get(
            "oracle_detail_failure_cooldown_seconds",
            policy.get("host_cooldown_seconds", policy.get("detail_failure_cooldown_seconds", "")),
        )
        concurrency = policy.get("oracle_detail_concurrency", policy.get("detail_concurrency", 1))
        lines.append(
            (
                f"{row['source_id']} list_interval={policy.get('list_fetch_interval_minutes')}m "
                f"jitter={policy.get('list_fetch_jitter_minutes')}m "
                f"failed_cooldown={policy.get('failed_source_cooldown_minutes')}m "
                f"repeated_failed_cooldown={policy.get('repeated_failed_source_cooldown_minutes')}m "
                f"detail_concurrency={concurrency} "
                f"detail_max={policy.get('max_detail_pages_per_run', '')} "
                f"detail_delay={delay}s+0..{jitter}s "
                f"batch={batch_size} pause={batch_pause}s "
                f"stop_after_transient={stop_after} cooldown={cooldown}s"
            )
        )
    _write_text("\n".join(lines) + ("\n" if lines else ""), args.output)
    return 0


def handle_source_health_report(args: argparse.Namespace) -> int:
    selected_ids = _selected_source_ids(args)
    sources = [
        source
        for source in load_sources(args.config)
        if not selected_ids or source.id in selected_ids
    ]
    missing = selected_ids.difference(source.id for source in sources)
    if missing:
        LOGGER.error("Unknown source IDs: %s", ", ".join(sorted(missing)))
        return 1
    output_dir = Path(args.output_dir)
    rows = [
        _source_health_dry_run_row(source, output_dir=output_dir)
        for source in sources
    ]
    health_schema = _validate_sync_bundles_health_schema(output_dir / "sync_bundles_health.json")
    payload = {
        "dry_run": True,
        "live_fetch_attempted": False,
        "health_report_schema": health_schema,
        "sources": rows,
    }
    if args.format == "json":
        _write_text(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=True) + "\n", args.output)
        return 0
    lines = [
        (
            f"{row['source_id']} publishability={row['latest_publishability_classification']} "
            f"run={row['latest_run_classification']} "
            f"list_breaker={row['circuit_breakers'].get('list', {}).get('state', 'unknown')} "
            f"detail_breaker={row['circuit_breakers'].get('detail', {}).get('state', 'unknown')} "
            f"detail_pending={row['detail_backlog_counts'].get('pending', 0)} "
            f"missing_gate_valid={row['missing_closed_gate_valid']} "
            f"detail_max={row['policy'].get('max_detail_pages_per_run', '')}"
        )
        for row in rows
    ]
    lines.append(
        "health_report_schema="
        + ("ok" if health_schema["ok"] else f"missing:{','.join(health_schema['missing'])}")
    )
    _write_text("\n".join(lines) + "\n", args.output)
    return 0


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
    if getattr(args, "_missing_source_ids", []):
        return 1
    if not sources:
        LOGGER.error("No sources selected for sync-bundles")
        return 1
    if args.browser_cookie_assist:
        try:
            _apply_browser_cookie_assist(args, sources)
        except RuntimeError as exc:
            LOGGER.error("Browser cookie assist failed: %s", exc)
            return 1

    output_dir = Path(args.output_dir)
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    archive_dir = None if args.no_archive else _archive_dir_for(output_dir)
    all_results = []
    consolidated = None
    with tempfile.TemporaryDirectory(prefix="jobagg_bundles_", dir=output_dir.parent) as staging:
        staging_dir = Path(staging)
        for source in sources:
            result = _write_source_bundle_for_sync_bundles(
                args,
                source,
                output_dir=output_dir,
                staging_dir=staging_dir,
                policy=policy,
            )
            if _should_retry_with_browser_cookie_assist(args, source, result):
                LOGGER.warning(
                    "%s appears blocked during detail fetch; opening browser cookie assist and retrying source",
                    source.id,
                )
                try:
                    _apply_browser_cookie_assist(args, [source])
                except RuntimeError as exc:
                    LOGGER.error("Browser cookie assist failed for %s: %s", source.id, exc)
                else:
                    result = _write_source_bundle_for_sync_bundles(
                        args,
                        source,
                        output_dir=output_dir,
                        staging_dir=staging_dir,
                        policy=policy,
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
        _write_sync_bundles_health_report(
            selected,
            output_dir=output_dir,
            output_path=args.health_report_output,
            consolidated=None,
            fatal_errors_count=1,
            exit_code=1,
        )
        return 1
    safety_violations = _source_safety_gate_violations(selected)
    if safety_violations:
        for violation in safety_violations:
            LOGGER.error("Safety gate violation: %s", violation)
        _write_sync_bundles_health_report(
            selected,
            output_dir=output_dir,
            output_path=args.health_report_output,
            consolidated=None,
            fatal_errors_count=len(safety_violations),
            exit_code=1,
        )
        return 1
    if duplicates:
        LOGGER.info(
            "Archived duplicate source variants: %s",
            ", ".join(result.source.id for result in duplicates),
        )
    if archive_dir is not None:
        LOGGER.info("Archived previous/noncanonical output files at %s", archive_dir)
    if not args.skip_consolidate:
        try:
            consolidated = consolidate_bundle_databases(output_dir=output_dir)
        except Exception as exc:
            LOGGER.error("Consolidation failed: %s", exc)
            _write_sync_bundles_health_report(
                selected,
                output_dir=output_dir,
                output_path=args.health_report_output,
                consolidated=None,
                fatal_errors_count=1,
                exit_code=1,
            )
            return 1
        LOGGER.info(
            "Refreshed consolidated database %s with %s current, %s current detail-complete, "
            "%s current weak-detail, %s expired moved to history, and %s history jobs",
            consolidated.db_path,
            consolidated.current_count,
            consolidated.current_detail_complete_count,
            consolidated.current_detail_weak_count,
            consolidated.expired_moved_to_history_count,
            consolidated.history_count,
        )
    LOGGER.info("Published %s canonical organization bundles to %s", len(selected), output_dir)
    source_warning_ids = {result.sync_result.source_id for result in _source_warning_results(selected)}
    if consolidated is not None:
        source_warning_ids.update(_consolidated_warning_source_ids(consolidated.db_path))
    source_health_exit_code = 2 if source_warning_ids else 0
    exit_code = source_health_exit_code
    if source_health_exit_code == 2 and args.allow_source_degraded:
        exit_code = 0
    health_report = _write_sync_bundles_health_report(
        selected,
        output_dir=output_dir,
        output_path=args.health_report_output,
        consolidated=consolidated,
        fatal_errors_count=0,
        exit_code=exit_code,
        source_health_exit_code=source_health_exit_code,
        allow_source_degraded=bool(args.allow_source_degraded),
    )
    if source_warning_ids:
        LOGGER.warning(
            "Published with source warnings: %s",
            ", ".join(item["source_id"] for item in health_report["sources"] if item["warning"]),
        )
    return exit_code


def _write_source_bundle_for_sync_bundles(
    args: argparse.Namespace,
    source: OrganizationSource,
    *,
    output_dir: Path,
    staging_dir: Path,
    policy: RobotsPolicy,
) -> BundleResult:
    if args.detail_page_limit is not None:
        source = replace(
            source,
            extra={**source.extra, "max_detail_pages_per_run": args.detail_page_limit},
        )
    return write_source_bundle(
        source,
        output_dir=staging_dir,
        policy=policy,
        file_slug=source.id,
        seed_db_path=source_output_paths(output_dir, source_output_slug(source))["db"],
        selective_details=not args.full_sync,
        deadline_refresh_days=args.deadline_refresh_days,
        refresh_all_details=args.refresh_all_details,
        missing_run_threshold=args.missing_run_threshold,
        classify=not args.skip_classify,
    )


def _should_retry_with_browser_cookie_assist(
    args: argparse.Namespace,
    source: OrganizationSource,
    result: BundleResult,
) -> bool:
    if not getattr(args, "browser_cookie_assist_on_block", False):
        return False
    explicit_target_ids = set(getattr(args, "browser_cookie_source_id", []) or [])
    if source.id not in explicit_target_ids and not _truthy(source.extra.get("browser_cookie_assist")):
        return False
    if _truthy(source.extra.get("browser_cookie_assist_active")):
        return False
    sync = result.sync_result
    diagnostics = sync.diagnostics
    if diagnostics is not None and diagnostics.detail_failed <= 0:
        return False
    if diagnostics is None and not sync.errors:
        return False
    error_text = " ".join(sync.errors).casefold()
    return any(
        marker in error_text
        for marker in (
            "detail refresh returned no detail",
            "awswaf",
            "waf",
            "captcha",
            "verify that you're not a robot",
            "no_detail_response",
            "blocked",
        )
    )


def _apply_browser_cookie_assist(args: argparse.Namespace, sources: list[OrganizationSource]) -> None:
    if not (
        getattr(args, "browser_cookie_assist", False)
        or getattr(args, "browser_cookie_assist_on_block", False)
    ):
        return
    selected_by_id = {source.id: source for source in sources}
    target_ids = set(getattr(args, "browser_cookie_source_id", []) or [])
    if not target_ids:
        target_ids = {
            source.id
            for source in sources
            if _truthy(source.extra.get("browser_cookie_assist"))
        }
    if not target_ids:
        raise RuntimeError(
            "no selected sources have browser_cookie_assist=true; pass --browser-cookie-source-id"
        )
    missing = sorted(target_ids - set(selected_by_id))
    if missing:
        raise RuntimeError(f"browser cookie assist source not selected: {', '.join(missing)}")

    for source_id in sorted(target_ids):
        source = selected_by_id[source_id]
        cookie_url = _browser_cookie_url(source)
        if not getattr(args, "no_browser_open", False):
            LOGGER.warning(
                "Opening %s for browser cookie assist. Complete any human check, then provide the Cookie header.",
                cookie_url,
            )
            webbrowser.open(cookie_url)
        cookie_header = _read_browser_cookie_header(args, source)
        source.extra = {
            **source.extra,
            "cookie_header": cookie_header,
            "browser_cookie_assist_active": True,
        }
        LOGGER.info("Browser cookie assist enabled for %s", source.id)


def _browser_cookie_url(source: OrganizationSource) -> str:
    return str(
        source.extra.get("browser_cookie_url")
        or source.extra.get("listing_url")
        or source.base_url
    )


def _read_browser_cookie_header(args: argparse.Namespace, source: OrganizationSource) -> str:
    file_path = getattr(args, "browser_cookie_file", None)
    if file_path:
        return _normalize_cookie_header(Path(file_path).read_text(encoding="utf-8"))

    env_name = str(getattr(args, "browser_cookie_env", None) or "JOBAGG_BROWSER_COOKIE_HEADER")
    env_value = os.environ.get(env_name)
    if env_value:
        return _normalize_cookie_header(env_value)

    if not sys.stdin.isatty():
        raise RuntimeError(
            "interactive Cookie prompt is unavailable; pass --browser-cookie-file "
            f"or set {env_name}"
        )
    prompt = (
        f"Paste Cookie header for {source.id} after completing the browser challenge "
        "(input hidden): "
    )
    return _normalize_cookie_header(getpass.getpass(prompt))


def _normalize_cookie_header(value: str) -> str:
    text = value.strip()
    if not text:
        raise RuntimeError("Cookie header is empty")

    header_match = re.search(r"(?im)^\s*cookie\s*:\s*(?P<value>.+?)\s*$", text)
    if header_match:
        text = header_match.group("value").strip()
    else:
        curl_match = re.search(
            r"""(?is)(?:^|\s)-H\s+(['"])Cookie:\s*(?P<value>.*?)\1""",
            text,
        )
        if curl_match:
            text = curl_match.group("value").strip()

    text = text.replace("\r", "").strip()
    if "\n" in text:
        raise RuntimeError("Cookie header must be a single header line")
    if text.lower().startswith("cookie:"):
        text = text.split(":", 1)[1].strip()
    if "=" not in text:
        raise RuntimeError("Cookie header does not look like name=value cookies")
    return text


def _truthy(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if value in (None, ""):
        return False
    return str(value).strip().casefold() in {"1", "true", "yes", "y", "on"}


def handle_consolidate_bundles(args: argparse.Namespace) -> int:
    result = consolidate_bundle_databases(
        output_dir=args.output_dir,
        slug=args.slug,
    )
    _refresh_existing_health_report_after_consolidation(result)
    if args.summary_output:
        write_organization_summary(result, args.summary_output)
        LOGGER.info("Wrote organization summary to %s", args.summary_output)
    LOGGER.info(
        "Consolidated %s source databases into %s",
        len(result.source_db_paths),
        result.db_path,
    )
    LOGGER.info("Current open jobs: %s", result.current_count)
    LOGGER.info("Current detail-complete jobs: %s", result.current_detail_complete_count)
    LOGGER.info("Current weak-detail jobs: %s", result.current_detail_weak_count)
    LOGGER.info("Expired jobs moved to history: %s", result.expired_moved_to_history_count)
    LOGGER.info("History jobs: %s", result.history_count)
    LOGGER.info("Total jobs: %s", result.total_count)
    LOGGER.info("Current JSON: %s", result.current_json_path)
    LOGGER.info("Current CSV: %s", result.current_csv_path)
    LOGGER.info("History JSON: %s", result.history_json_path)
    LOGGER.info("History CSV: %s", result.history_csv_path)
    return 0


def _refresh_existing_health_report_after_consolidation(result) -> None:
    path = result.output_dir / "sync_bundles_health.json"
    if not path.exists():
        return
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return
    payload["current_jobs_count"] = result.current_count
    payload["trusted_current_jobs_count"] = result.trusted_current_count
    payload["application_ready_jobs_count"] = result.application_ready_count
    payload["current_detail_complete_count"] = result.current_detail_complete_count
    payload["current_detail_weak_count"] = result.current_detail_weak_count
    payload["expired_moved_to_history_count"] = result.expired_moved_to_history_count
    payload["history_jobs_count"] = result.history_count
    payload["total_jobs_count"] = result.total_count
    detail_backlog_counts = _detail_backlog_counts_from_db(result.db_path)
    payload["detail_backlog_counts"] = detail_backlog_counts
    payload["detail_pending_count"] = int(detail_backlog_counts.get("pending") or 0)
    payload["consolidation_refreshed_at"] = datetime.now(tz=UTC).isoformat()
    payload["consolidation_source_db_count"] = len(result.source_db_paths)
    payload["consolidation_status_counts"] = result.status_counts
    sources = payload.get("sources")
    if isinstance(sources, list):
        for row in sources:
            slug = str(row.get("slug") or row.get("source_id") or "")
            if not slug:
                continue
            sidecar = _bundle_health_sidecar(result.output_dir, slug)
            if sidecar["detail_backlog_counts"]:
                row["detail_backlog_counts"] = sidecar["detail_backlog_counts"]
                row["detail_pending"] = sidecar["detail_backlog_counts"].get("pending", 0)
            else:
                row["detail_backlog_counts"] = {}
                row["detail_pending"] = 0
            if sidecar["circuit_breakers"]:
                row["circuit_breakers"] = sidecar["circuit_breakers"]
            row["cooldown_until"] = sidecar["cooldown_until"]
            errors = row.get("errors") if isinstance(row.get("errors"), list) else []
            row["last_error_summary"] = _last_error_summary(errors, sidecar["last_backlog_error"])
        detail_quality_warning_sources = _merge_consolidated_status_into_health_rows(
            sources,
            result.db_path,
        )
        payload["detail_quality_warning_sources"] = detail_quality_warning_sources
        payload["detail_quality_warning_count"] = len(detail_quality_warning_sources)
        warning_sources = [row.get("source_id") for row in sources if row.get("warning")]
        if warning_sources and payload.get("publish_result") == "success":
            payload["publish_result"] = "success_with_source_warnings"
        if warning_sources and int(payload.get("source_health_exit_code") or 0) == 0:
            payload["source_health_exit_code"] = 2
    path.write_text(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=True) + "\n", encoding="utf-8")


def _source_safety_gate_violations(results) -> list[str]:
    violations = []
    for result in results:
        sync = result.sync_result
        diagnostics = sync.diagnostics
        if diagnostics is None:
            continue
        if (sync.missing or sync.closed) and not diagnostics.missing_transition_allowed:
            violations.append(
                f"{sync.source_id}: missing={sync.missing} closed={sync.closed} "
                "while missing_transition_allowed=false"
            )
    return violations


def _source_warning_results(results) -> list:
    return [
        result
        for result in results
        if _publishability_for_sync_result(result.sync_result)
        not in {"ok", "ok_empty"}
    ]


def _consolidated_warning_source_ids(db_path: Path) -> set[str]:
    if not db_path.exists():
        return set()
    try:
        with sqlite3.connect(db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """
                SELECT source_id, source_freshness_status, open_jobs,
                       stale_current_jobs, weak_detail_jobs
                FROM consolidated_source_status
                WHERE open_jobs > 0
                   OR stale_current_jobs > 0
                   OR weak_detail_jobs > 0
                """
            ).fetchall()
    except sqlite3.DatabaseError:
        return set()
    warnings = set()
    for row in rows:
        freshness = str(row["source_freshness_status"] or "")
        if (
            freshness not in {"", "fresh"}
            or int(row["stale_current_jobs"] or 0) > 0
            or int(row["weak_detail_jobs"] or 0) > 0
        ):
            warnings.add(str(row["source_id"]))
    return warnings


def _publishability_for_sync_result(sync: SyncResult) -> str:
    diagnostics = sync.diagnostics
    if diagnostics is None:
        return "source_inconclusive" if sync.errors else "ok"
    if diagnostics.publishability_classification:
        return diagnostics.publishability_classification
    classification = diagnostics.run_classification or ""
    if classification in {"ok", "ok_empty"} and not sync.errors:
        return classification
    if classification in {"detail_degraded", "publishable_detail_degraded"}:
        return "publishable_detail_degraded"
    if classification in {"source_adapter_broken", "adapter_failed"}:
        return "publishable_list_only"
    if classification in {"blocked", "source_blocked"} or diagnostics.blocked:
        return "source_blocked"
    if classification in {"parser_error", "source_parser_error"}:
        return "source_parser_error"
    return "source_inconclusive" if sync.errors else "ok"


def _selected_source_ids(args: argparse.Namespace) -> set[str]:
    selected = set(args.source_id or [])
    for chunk in str(getattr(args, "sources", "") or "").split(","):
        value = chunk.strip()
        if value:
            selected.add(value)
    return selected


def _source_health_dry_run_row(source: OrganizationSource, *, output_dir: Path) -> dict:
    slug = source_output_slug(source)
    sidecar = _bundle_health_sidecar(output_dir, slug)
    latest = _latest_source_diagnostics(output_dir, slug, source.id)
    policy = fetch_schedule_policy(source)
    return {
        "source_id": source.id,
        "slug": slug,
        "enabled": source.enabled,
        "ats_family": source.ats_family,
        "policy": policy,
        "latest_run_classification": latest.get("run_classification"),
        "latest_publishability_classification": latest.get("publishability_classification"),
        "latest_health_status": latest.get("health_status"),
        "latest_fetched_count": latest.get("fetched"),
        "pagination_complete": latest.get("pagination_complete"),
        "verified_empty": latest.get("verified_empty"),
        "missing_transition_allowed": latest.get("missing_transition_allowed"),
        "missing_closed_gate_valid": _missing_closed_gate_valid(latest),
        "detail_attempted": latest.get("detail_attempted", 0),
        "detail_succeeded": latest.get("detail_succeeded", 0),
        "detail_failed": latest.get("detail_failed", 0),
        "detail_skipped": latest.get("detail_skipped", 0),
        "detail_backlog_counts": sidecar["detail_backlog_counts"],
        "circuit_breakers": sidecar["circuit_breakers"],
        "cooldown_until": sidecar["cooldown_until"],
        "last_error_summary": latest.get("last_error_summary") or sidecar["last_backlog_error"],
    }


def _latest_source_diagnostics(output_dir: Path, slug: str, source_id: str) -> dict:
    db_path = source_output_paths(output_dir, slug)["db"]
    if not db_path.exists():
        return {}
    try:
        with sqlite3.connect(db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                """
                SELECT sr.fetched, sr.missing, sr.closed, sr.errors_json,
                       d.run_classification, d.publishability_classification,
                       d.health_status, d.pagination_complete, d.empty_reason,
                       d.missing_transition_allowed, d.detail_attempted,
                       d.detail_succeeded, d.detail_failed, d.detail_skipped
                FROM source_runs sr
                LEFT JOIN source_run_diagnostics d ON d.source_run_id = sr.id
                WHERE sr.source_id = ?
                ORDER BY sr.id DESC
                LIMIT 1
                """,
                (source_id,),
            ).fetchone()
    except sqlite3.DatabaseError as exc:
        return {"last_error_summary": f"latest diagnostics read failed: {exc}"}
    if row is None:
        return {}
    errors = json.loads(row["errors_json"] or "[]")
    return {
        "fetched": int(row["fetched"] or 0),
        "missing": int(row["missing"] or 0),
        "closed": int(row["closed"] or 0),
        "run_classification": row["run_classification"],
        "publishability_classification": row["publishability_classification"],
        "health_status": row["health_status"],
        "pagination_complete": _sqlite_bool(row["pagination_complete"]),
        "verified_empty": row["empty_reason"]
        in {"verified_total_zero", "verified_structural_empty", "verified_text_empty"},
        "missing_transition_allowed": bool(row["missing_transition_allowed"]),
        "detail_attempted": int(row["detail_attempted"] or 0),
        "detail_succeeded": int(row["detail_succeeded"] or 0),
        "detail_failed": int(row["detail_failed"] or 0),
        "detail_skipped": int(row["detail_skipped"] or 0),
        "last_error_summary": _last_error_summary(errors, None),
    }


def _sqlite_bool(value) -> bool | None:
    if value is None:
        return None
    return bool(value)


def _missing_closed_gate_valid(latest: dict) -> bool:
    changed_missing = int(latest.get("missing") or 0) or int(latest.get("closed") or 0)
    return not changed_missing or bool(latest.get("missing_transition_allowed"))


def _validate_sync_bundles_health_schema(path: Path) -> dict:
    top_level = {
        "publish_result",
        "exit_code",
        "fatal_errors_count",
        "current_jobs_count",
        "trusted_current_jobs_count",
        "application_ready_jobs_count",
        "current_detail_complete_count",
        "current_detail_weak_count",
        "expired_moved_to_history_count",
        "history_jobs_count",
        "total_jobs_count",
        "bundles_published_count",
        "degraded_source_count",
        "inconclusive_source_count",
        "source_adapter_broken_count",
        "detail_backlog_counts",
        "detail_pending_count",
        "sources",
    }
    source_level = {
        "source_id",
        "health_status",
        "publishability_classification",
        "fetched_count",
        "pagination_complete",
        "verified_empty",
        "blocked",
        "transient_error",
        "scope_validation_status",
        "scope_passed",
        "missing_transition_allowed",
        "missing_closed_safety_gate_passed",
        "detail_attempted",
        "detail_succeeded",
        "detail_failed",
        "detail_skipped",
        "detail_pending",
        "circuit_breakers",
        "last_error_summary",
        "cooldown_until",
        "consolidated_open_jobs",
        "consolidated_trusted_current_jobs",
        "consolidated_application_ready_jobs",
        "consolidated_stale_current_jobs",
        "consolidated_expired_current_jobs",
        "consolidated_weak_detail_jobs",
    }
    if not path.exists():
        return {
            "ok": False,
            "path": str(path),
            "missing": sorted(top_level),
            "source_missing": sorted(source_level),
            "error": "health report not found",
        }
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return {
            "ok": False,
            "path": str(path),
            "missing": sorted(top_level),
            "source_missing": sorted(source_level),
            "error": f"invalid json: {exc}",
        }
    missing = sorted(top_level.difference(report))
    source_missing: list[str] = []
    sources = report.get("sources")
    if isinstance(sources, list) and sources:
        missing_by_source: set[str] = set()
        for source in sources:
            missing_fields = sorted(source_level.difference(source))
            source_id = source.get("source_id", "<unknown>")
            missing_by_source.update(f"{source_id}:{field}" for field in missing_fields)
        source_missing = sorted(missing_by_source)
    elif "sources" not in missing:
        source_missing = sorted(source_level)
    return {
        "ok": not missing and not source_missing,
        "path": str(path),
        "missing": missing,
        "source_missing": source_missing,
        "error": None,
    }


def _write_sync_bundles_health_report(
    results,
    *,
    output_dir: Path,
    output_path: str | None,
    consolidated,
    fatal_errors_count: int,
    exit_code: int,
    source_health_exit_code: int | None = None,
    allow_source_degraded: bool = False,
) -> dict:
    source_rows = []
    for result in results:
        sync = result.sync_result
        diagnostics = sync.diagnostics
        publishability = _publishability_for_sync_result(sync)
        sidecar = _bundle_health_sidecar(output_dir, result.slug)
        errors = list(sync.errors)
        source_rows.append(
            {
                "source_id": sync.source_id,
                "slug": result.slug,
                "publishability_classification": publishability,
                "run_classification": diagnostics.run_classification if diagnostics else None,
                "health_status": diagnostics.health_status if diagnostics else None,
                "list_breaker_state": (
                    sidecar["circuit_breakers"].get("list", {}).get("state")
                    or (diagnostics.list_breaker_state if diagnostics else None)
                ),
                "detail_breaker_state": (
                    sidecar["circuit_breakers"].get("detail", {}).get("state")
                    or (diagnostics.detail_breaker_state if diagnostics else None)
                ),
                "fetched": sync.fetched,
                "fetched_count": sync.fetched,
                "inserted": sync.inserted,
                "updated": sync.updated,
                "unchanged": sync.unchanged,
                "missing": sync.missing,
                "closed": sync.closed,
                "detail_attempted": diagnostics.detail_attempted if diagnostics else 0,
                "detail_succeeded": diagnostics.detail_succeeded if diagnostics else 0,
                "detail_failed": diagnostics.detail_failed if diagnostics else 0,
                "detail_skipped": diagnostics.detail_skipped if diagnostics else 0,
                "detail_pending": sidecar["detail_backlog_counts"].get("pending", 0),
                "detail_backlog_counts": sidecar["detail_backlog_counts"],
                "pagination_complete": diagnostics.pagination_complete if diagnostics else None,
                "verified_empty": _diagnostics_verified_empty(diagnostics),
                "blocked": diagnostics.blocked if diagnostics else False,
                "transient_error": diagnostics.transient_error if diagnostics else False,
                "scope_validation_status": (
                    diagnostics.scope_validation_status if diagnostics else None
                ),
                "scope_passed": _scope_passed(
                    diagnostics.scope_validation_status if diagnostics else None
                ),
                "missing_transition_allowed": (
                    diagnostics.missing_transition_allowed if diagnostics else False
                ),
                "missing_closed_safety_gate_passed": (
                    diagnostics.missing_transition_allowed if diagnostics else False
                ),
                "circuit_breakers": sidecar["circuit_breakers"],
                "cooldown_until": sidecar["cooldown_until"],
                "last_error_summary": _last_error_summary(errors, sidecar["last_backlog_error"]),
                "errors": errors,
                "warning": publishability not in {"ok", "ok_empty"},
                "consolidated_only": False,
                "source_freshness_status": None,
                "consolidated_open_jobs": 0,
                "consolidated_stale_current_jobs": 0,
                "consolidated_trusted_current_jobs": 0,
                "consolidated_application_ready_jobs": 0,
                "consolidated_expired_current_jobs": 0,
                "consolidated_weak_detail_jobs": 0,
            }
        )
    detail_quality_warning_sources: list[str] = []
    if consolidated is not None:
        detail_quality_warning_sources = _merge_consolidated_status_into_health_rows(
            source_rows,
            consolidated.db_path,
        )
    publishable_degraded = [
        row["source_id"]
        for row in source_rows
        if row["publishability_classification"]
        in {"publishable_detail_degraded", "publishable_list_only"}
    ]
    source_inconclusive = [
        row["source_id"]
        for row in source_rows
        if row["publishability_classification"]
        in {"source_inconclusive", "source_blocked", "source_parser_error"}
    ]
    adapter_broken = [
        row["source_id"]
        for row in source_rows
        if row["run_classification"] == "source_adapter_broken"
        or row["publishability_classification"]
        in {"publishable_list_only", "source_adapter_broken"}
    ]
    warning_sources = [row["source_id"] for row in source_rows if row["warning"]]
    source_health_exit_code = source_health_exit_code if source_health_exit_code is not None else exit_code
    detail_backlog_counts = (
        _detail_backlog_counts_from_db(consolidated.db_path)
        if consolidated is not None
        else {}
    )
    report = {
        "publish_result": (
            "failed"
            if fatal_errors_count
            else "success_with_source_warnings"
            if warning_sources
            else "success"
        ),
        "fatal_errors_count": fatal_errors_count,
        "publishable_degraded_sources": publishable_degraded,
        "source_inconclusive_sources": source_inconclusive,
        "detail_adapter_broken_sources": adapter_broken,
        "detail_quality_warning_sources": detail_quality_warning_sources,
        "degraded_source_count": len(publishable_degraded),
        "inconclusive_source_count": len(source_inconclusive),
        "source_adapter_broken_count": len(adapter_broken),
        "detail_quality_warning_count": len(detail_quality_warning_sources),
        "detail_backlog_counts": detail_backlog_counts,
        "detail_pending_count": int(detail_backlog_counts.get("pending") or 0),
        "current_jobs_count": consolidated.current_count if consolidated is not None else None,
        "trusted_current_jobs_count": (
            consolidated.trusted_current_count if consolidated is not None else None
        ),
        "application_ready_jobs_count": (
            consolidated.application_ready_count if consolidated is not None else None
        ),
        "current_detail_complete_count": (
            consolidated.current_detail_complete_count if consolidated is not None else None
        ),
        "current_detail_weak_count": (
            consolidated.current_detail_weak_count if consolidated is not None else None
        ),
        "expired_moved_to_history_count": (
            consolidated.expired_moved_to_history_count if consolidated is not None else None
        ),
        "history_jobs_count": consolidated.history_count if consolidated is not None else None,
        "total_jobs_count": consolidated.total_count if consolidated is not None else None,
        "bundles_published_count": len(results),
        "exit_code": exit_code,
        "source_health_exit_code": source_health_exit_code,
        "allow_source_degraded": allow_source_degraded,
        "sources": source_rows,
    }
    destination = Path(output_path) if output_path else output_dir / "sync_bundles_health.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(report, indent=2, sort_keys=True, ensure_ascii=True) + "\n", encoding="utf-8")
    LOGGER.info("Wrote sync-bundles health report to %s", destination)
    LOGGER.info(
        "publish_result=%s fatal_errors_count=%s degraded_sources=%s inconclusive_sources=%s exit_code=%s",
        report["publish_result"],
        fatal_errors_count,
        len(publishable_degraded),
        len(source_inconclusive),
        exit_code,
    )
    return report


def _diagnostics_verified_empty(diagnostics) -> bool:
    return bool(
        diagnostics is not None
        and diagnostics.empty_reason
        in {"verified_total_zero", "verified_structural_empty", "verified_text_empty"}
    )


def _last_error_summary(errors: list[str], backlog_error: str | None) -> str | None:
    if errors:
        return errors[-1][:500]
    if backlog_error:
        return backlog_error[:500]
    return None


def _merge_consolidated_status_into_health_rows(source_rows: list[dict], db_path: Path) -> list[str]:
    statuses = _read_consolidated_source_status(db_path)
    for row in source_rows:
        _ensure_consolidated_health_defaults(row)
    if not statuses:
        return []
    rows_by_source = {str(row["source_id"]): row for row in source_rows}
    detail_quality_warning_sources: list[str] = []
    for source_id, status in statuses.items():
        weak_detail_jobs = int(status.get("weak_detail_jobs") or 0)
        stale_current_jobs = int(status.get("stale_current_jobs") or 0)
        expired_current_jobs = int(status.get("expired_current_jobs") or 0)
        source_freshness_status = status.get("source_freshness_status")
        consolidated_warning = (
            source_freshness_status not in {None, "", "fresh"}
            or stale_current_jobs > 0
            or weak_detail_jobs > 0
        )
        if weak_detail_jobs > 0:
            detail_quality_warning_sources.append(source_id)
        if source_id in rows_by_source:
            row = rows_by_source[source_id]
            row.update(
                {
                    "source_freshness_status": source_freshness_status,
                    "consolidated_only": False,
                    "consolidated_open_jobs": int(status.get("open_jobs") or 0),
                    "consolidated_stale_current_jobs": stale_current_jobs,
                    "consolidated_trusted_current_jobs": int(
                        status.get("trusted_current_jobs") or 0
                    ),
                    "consolidated_application_ready_jobs": int(
                        status.get("application_ready_jobs") or 0
                    ),
                    "consolidated_expired_current_jobs": expired_current_jobs,
                    "consolidated_weak_detail_jobs": weak_detail_jobs,
                }
            )
            row["warning"] = bool(row.get("warning")) or consolidated_warning
            continue

        publishability = (
            status.get("publishability_classification")
            or ("source_inconclusive" if consolidated_warning else "ok")
        )
        source_rows.append(
            {
                "source_id": source_id,
                "slug": source_id,
                "publishability_classification": publishability,
                "run_classification": status.get("run_classification"),
                "health_status": status.get("health_status") or ("issue" if consolidated_warning else "ok"),
                "source_freshness_status": source_freshness_status,
                "list_breaker_state": None,
                "detail_breaker_state": None,
                "fetched": status.get("fetched"),
                "fetched_count": status.get("fetched"),
                "inserted": None,
                "updated": None,
                "unchanged": None,
                "missing": None,
                "closed": None,
                "detail_attempted": 0,
                "detail_succeeded": 0,
                "detail_failed": 0,
                "detail_skipped": 0,
                "detail_pending": 0,
                "detail_backlog_counts": {},
                "pagination_complete": _sqlite_bool(status.get("pagination_complete")),
                "verified_empty": _sqlite_bool(status.get("verified_empty")),
                "blocked": _sqlite_bool(status.get("blocked")) or False,
                "transient_error": _sqlite_bool(status.get("transient_error")) or False,
                "scope_validation_status": status.get("scope_validation_status"),
                "scope_passed": _scope_passed(status.get("scope_validation_status")),
                "missing_transition_allowed": bool(status.get("missing_transition_allowed")),
                "missing_closed_safety_gate_passed": bool(
                    status.get("missing_transition_allowed")
                ),
                "circuit_breakers": {},
                "cooldown_until": None,
                "last_error_summary": (
                    "source contributes consolidated current rows but was not present in latest health run"
                ),
                "errors": [],
                "warning": consolidated_warning,
                "consolidated_only": True,
                "consolidated_open_jobs": int(status.get("open_jobs") or 0),
                "consolidated_stale_current_jobs": stale_current_jobs,
                "consolidated_trusted_current_jobs": int(status.get("trusted_current_jobs") or 0),
                "consolidated_application_ready_jobs": int(
                    status.get("application_ready_jobs") or 0
                ),
                "consolidated_expired_current_jobs": expired_current_jobs,
                "consolidated_weak_detail_jobs": weak_detail_jobs,
            }
        )
    return sorted(detail_quality_warning_sources)


def _ensure_consolidated_health_defaults(row: dict) -> None:
    row.setdefault("blocked", False)
    row.setdefault("transient_error", False)
    row.setdefault("scope_validation_status", None)
    row.setdefault("scope_passed", _scope_passed(row.get("scope_validation_status")))
    row.setdefault(
        "missing_closed_safety_gate_passed",
        bool(row.get("missing_transition_allowed")),
    )
    row.setdefault("consolidated_open_jobs", 0)
    row.setdefault("consolidated_stale_current_jobs", 0)
    row.setdefault("consolidated_trusted_current_jobs", 0)
    row.setdefault("consolidated_application_ready_jobs", 0)
    row.setdefault("consolidated_expired_current_jobs", 0)
    row.setdefault("consolidated_weak_detail_jobs", 0)


def _read_consolidated_source_status(db_path: Path) -> dict[str, dict]:
    if not db_path.exists():
        return {}
    try:
        with sqlite3.connect(db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                """
                SELECT 1
                FROM sqlite_master
                WHERE type = 'table' AND name = 'consolidated_source_status'
                """
            ).fetchone()
            if row is None:
                return {}
            rows = conn.execute(
                """
                SELECT *
                FROM consolidated_source_status
                ORDER BY source_id
                """
            ).fetchall()
    except sqlite3.DatabaseError:
        return {}
    return {str(row["source_id"]): dict(row) for row in rows}


def _scope_passed(value: object) -> bool:
    return value in {None, "passed", "not_applicable"}


def _detail_backlog_counts_from_db(db_path: Path) -> dict[str, int]:
    if not db_path.exists():
        return {}
    try:
        with sqlite3.connect(db_path) as conn:
            conn.row_factory = sqlite3.Row
            tables = {
                row["name"]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type IN ('table', 'view')"
                ).fetchall()
            }
            if "detail_backlog" not in tables or "jobs" not in tables:
                return {}
            rows = conn.execute(
                """
                SELECT b.detail_status, COUNT(*) AS items
                FROM detail_backlog b
                JOIN jobs j ON j.job_key = b.job_key
                WHERE j.status = 'open'
                GROUP BY b.detail_status
                ORDER BY b.detail_status
                """
            ).fetchall()
    except sqlite3.DatabaseError:
        return {}
    return {str(row["detail_status"]): int(row["items"] or 0) for row in rows}


def _bundle_health_sidecar(output_dir: Path, slug: str) -> dict:
    db_path = source_output_paths(output_dir, slug)["db"]
    sidecar = {
        "detail_backlog_counts": {},
        "circuit_breakers": {},
        "cooldown_until": None,
        "last_backlog_error": None,
    }
    if not db_path.exists():
        return sidecar
    try:
        with sqlite3.connect(db_path) as conn:
            conn.row_factory = sqlite3.Row
            tables = {
                row["name"]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                ).fetchall()
            }
            if "detail_backlog" in tables:
                if "jobs" in tables:
                    sidecar["detail_backlog_counts"] = {
                        row["detail_status"]: int(row["items"])
                        for row in conn.execute(
                            """
                            SELECT b.detail_status, COUNT(*) AS items
                            FROM detail_backlog b
                            JOIN jobs j ON j.job_key = b.job_key
                            WHERE j.status = 'open'
                            GROUP BY b.detail_status
                            """
                        ).fetchall()
                    }
                    error_row = conn.execute(
                        """
                        SELECT b.last_error
                        FROM detail_backlog b
                        JOIN jobs j ON j.job_key = b.job_key
                        WHERE j.status = 'open'
                            AND b.last_error IS NOT NULL
                            AND b.last_error <> ''
                        ORDER BY b.updated_at DESC
                        LIMIT 1
                        """
                    ).fetchone()
                else:
                    sidecar["detail_backlog_counts"] = {
                        row["detail_status"]: int(row["items"])
                        for row in conn.execute(
                            """
                            SELECT detail_status, COUNT(*) AS items
                            FROM detail_backlog
                            GROUP BY detail_status
                            """
                        ).fetchall()
                    }
                    error_row = conn.execute(
                        """
                        SELECT last_error
                        FROM detail_backlog
                        WHERE last_error IS NOT NULL AND last_error <> ''
                        ORDER BY updated_at DESC
                        LIMIT 1
                        """
                    ).fetchone()
                if error_row is not None:
                    sidecar["last_backlog_error"] = str(error_row["last_error"])
            if "source_circuit_breakers" in tables:
                _normalize_expired_breakers_for_health_sidecar(conn)
                cooldowns = []
                breakers = {}
                for row in conn.execute(
                    """
                    SELECT breaker_type, state, failure_count, success_count,
                           cooldown_until, last_reason
                    FROM source_circuit_breakers
                    ORDER BY breaker_type
                    """
                ).fetchall():
                    cooldown_until = row["cooldown_until"]
                    if cooldown_until:
                        cooldowns.append(str(cooldown_until))
                    breakers[str(row["breaker_type"])] = {
                        "state": row["state"],
                        "failure_count": int(row["failure_count"] or 0),
                        "success_count": int(row["success_count"] or 0),
                        "cooldown_until": cooldown_until,
                        "last_reason": row["last_reason"],
                    }
                sidecar["circuit_breakers"] = breakers
                sidecar["cooldown_until"] = min(cooldowns) if cooldowns else None
    except sqlite3.DatabaseError as exc:
        sidecar["last_backlog_error"] = f"health sidecar read failed: {exc}"
    return sidecar


def _normalize_expired_breakers_for_health_sidecar(conn: sqlite3.Connection) -> None:
    now = datetime.now(tz=UTC)
    rows = conn.execute(
        """
        SELECT source_id, breaker_type, cooldown_until, last_reason
        FROM source_circuit_breakers
        WHERE state = 'open'
            AND cooldown_until IS NOT NULL
            AND TRIM(cooldown_until) <> ''
        """
    ).fetchall()
    for row in rows:
        try:
            cooldown = datetime.fromisoformat(str(row["cooldown_until"]).replace("Z", "+00:00"))
        except ValueError:
            continue
        if cooldown.tzinfo is None:
            cooldown = cooldown.replace(tzinfo=UTC)
        if cooldown > now:
            continue
        reason = str(row["last_reason"] or "cooldown expired")
        if "cooldown expired" not in reason.casefold():
            reason = f"{reason}; cooldown expired"
        conn.execute(
            """
            UPDATE source_circuit_breakers
            SET state = 'half_open',
                cooldown_until = NULL,
                last_reason = ?,
                updated_at = ?
            WHERE source_id = ? AND breaker_type = ?
            """,
            (reason, now.isoformat(), row["source_id"], row["breaker_type"]),
        )


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


def handle_classify(args: argparse.Namespace) -> int:
    db = JobDatabase(args.db)
    db.initialize()
    count = classify_database(
        db,
        source_id=args.source_id,
        status=args.status,
        version=args.version,
        force=getattr(args, "reclassify_all", False),
    )
    LOGGER.info("Classified %s jobs", count)
    return 0


def handle_audit_classification(args: argparse.Namespace) -> int:
    db = JobDatabase(args.db)
    db.initialize()
    audit = audit_classification(
        db,
        source_ids=getattr(args, "source_id", []) or None,
        status=getattr(args, "status", "open"),
    )
    if args.format == "markdown":
        _write_text(audit_to_markdown(audit), args.output)
        return 0
    _write_text(json.dumps(audit, indent=2, ensure_ascii=True) + "\n", args.output)
    return 0


def handle_ops_check(args: argparse.Namespace) -> int:
    output_dir = args.output_dir
    if output_dir is None and args.output:
        output_dir = str(Path(args.output).parent)
    report = collect_ops_check(
        db_path=args.db,
        output_dir=output_dir,
        all_bundles=args.all,
    )
    _write_text(ops_check_to_markdown(report), args.output)
    if args.output:
        LOGGER.info("Wrote operational check report to %s", args.output)
    if report.fail_count:
        LOGGER.error("Operational check found %s failure(s)", report.fail_count)
        return 1
    if report.warn_count:
        LOGGER.warning("Operational check found %s warning(s)", report.warn_count)
    return 0


def handle_filter(args: argparse.Namespace) -> int:
    db = JobDatabase(args.db)
    db.initialize()
    filters = _filters_from_args(args)
    rows = search_vacancies(db, filters)
    if args.format == "json":
        payload = json.dumps(rows, indent=2, ensure_ascii=True)
        _write_text(payload + "\n", args.output)
        return 0
    if args.format == "markdown":
        _write_markdown(rows, args.output, title="Filtered Vacancies", total=len(rows))
        return 0
    _write_csv(rows, args.output)
    return 0


def handle_search(args: argparse.Namespace) -> int:
    db = JobDatabase(args.db)
    db.initialize()
    request = _search_request_from_args(args)
    response = search_collected_jobs(db, request)
    if getattr(args, "explain", False):
        for result in response.results:
            result["filter_evaluation"] = explain_job_match(db, result["job_key"], request)["checks"]
    score_path = getattr(args, "score_against", None)
    min_score = getattr(args, "min_score", None)
    if score_path or min_score is not None:
        from jobagg.scoring import load_strategy_signals, score_jobs

        if not score_path:
            raise SystemExit("--min-score requires --score-against")
        signals = load_strategy_signals(score_path)
        scored = score_jobs(response.results, signals)
        if min_score is not None:
            scored = [job for job in scored if job["score"] >= min_score]
        scored.sort(key=lambda job: job["score"], reverse=True)
        response.results = scored
    if args.format == "json":
        payload = json.dumps(response_to_dict(response), indent=2, ensure_ascii=True)
        _write_text(payload + "\n", args.output)
        return 0
    if args.format == "markdown":
        _write_markdown(
            response.results,
            args.output,
            title="Job Search Results",
            total=response.total,
        )
        return 0
    _write_csv(response.results, args.output)
    return 0


def handle_search_debug(args: argparse.Namespace) -> int:
    db = JobDatabase(args.db)
    db.initialize()
    explanation = explain_job_match(db, args.job_key, _search_request_from_args(args))
    exit_code = 0 if explanation.get("found") and explanation.get("matched") else 1
    if args.format == "json":
        _write_text(json.dumps(explanation, indent=2, ensure_ascii=True) + "\n", args.output)
        return exit_code
    _write_text(explain_to_text(explanation), args.output)
    return exit_code


def handle_saved_search_add(args: argparse.Namespace) -> int:
    try:
        search = save_search(
            args.saved_searches,
            name=args.name,
            description=args.description,
            request=_search_request_from_args(args),
            overwrite=args.overwrite,
        )
    except ValueError as exc:
        LOGGER.error("%s", exc)
        return 1
    _write_text(f"Saved search {search.name!r} written to {args.saved_searches}\n", None)
    return 0


def handle_saved_search_list(args: argparse.Namespace) -> int:
    searches = list_saved_searches(args.saved_searches)
    if args.format == "json":
        _write_text(
            json.dumps([search.to_dict() for search in searches], indent=2, ensure_ascii=True) + "\n",
            args.output,
        )
        return 0
    lines = []
    for search in searches:
        detail = f" - {search.description}" if search.description else ""
        lines.append(f"{search.name}{detail}")
    _write_text(("\n".join(lines) + "\n") if lines else "No saved searches.\n", args.output)
    return 0


def handle_saved_search_run(args: argparse.Namespace) -> int:
    try:
        searches = _saved_searches_to_run(args)
    except (KeyError, ValueError) as exc:
        LOGGER.error("%s", exc)
        return 1
    db = JobDatabase(args.db)
    db.initialize()
    runs = [
        _run_saved_search(db, search, include_explain=args.explain)
        for search in searches
    ]
    if args.format == "json":
        payload = runs[0] if len(runs) == 1 else {"saved_search_runs": runs}
        _write_text(json.dumps(payload, indent=2, ensure_ascii=True) + "\n", args.output)
        return 0
    rows = [
        {**result, "saved_search": run["saved_search"]["name"]}
        for run in runs
        for result in run["response"]["results"]
    ]
    if args.format == "markdown":
        title = f"Saved Search: {runs[0]['saved_search']['name']}" if len(runs) == 1 else "Saved Search Results"
        _write_markdown(rows, args.output, title=title, total=sum(run["response"]["total"] for run in runs))
        return 0
    _write_csv(rows, args.output)
    return 0


def handle_saved_search_remove(args: argparse.Namespace) -> int:
    if not remove_saved_search(args.saved_searches, args.name):
        LOGGER.error("No saved search named %r", args.name)
        return 1
    _write_text(f"Removed saved search {args.name!r}\n", None)
    return 0


def handle_facets(args: argparse.Namespace) -> int:
    db = JobDatabase(args.db)
    db.initialize()
    payload = json.dumps(facet_counts(db, _filters_from_args(args)), indent=2, ensure_ascii=True)
    _write_text(payload + "\n", args.output)
    return 0


def handle_ccog_tree(args: argparse.Namespace) -> int:
    payload = json.dumps(ccog_tree(), indent=2, ensure_ascii=True)
    _write_text(payload + "\n", args.output)
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


def _saved_searches_to_run(args: argparse.Namespace) -> list[SavedSearch]:
    if args.all:
        searches = list_saved_searches(args.saved_searches)
        if not searches:
            raise ValueError("No saved searches to run")
        return searches
    if not args.name:
        raise ValueError("Provide a saved search name or --all")
    return [get_saved_search(args.saved_searches, args.name)]


def _run_saved_search(
    db: JobDatabase,
    search: SavedSearch,
    *,
    include_explain: bool,
) -> dict:
    response = search_collected_jobs(db, search.request)
    if include_explain:
        for result in response.results:
            result["filter_evaluation"] = explain_job_match(
                db,
                result["job_key"],
                search.request,
            )["checks"]
    return {
        "saved_search": {
            "name": search.name,
            "description": search.description,
            "request": request_to_dict(search.request),
        },
        "response": response_to_dict(response),
    }


def _add_filter_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--text", help="Search title, description, location, and department.")
    parser.add_argument("--organization", help="Organization/source org ID filter.")
    parser.add_argument("--source-id", help="Source ID filter.")
    parser.add_argument("--ats-family", help="ATS family filter.")
    parser.add_argument("--ccog-part", help="CCOG part filter.")
    parser.add_argument("--ccog-family", help="CCOG family prefix/code filter.")
    parser.add_argument("--ccog-code", help="Exact CCOG primary code filter.")
    parser.add_argument("--occupational-family-code", help="Derived CCOG family filter.")
    parser.add_argument("--occupational-medium-code", help="Derived CCOG medium filter.")
    parser.add_argument("--mandate-network-code", help="UN Job Network code filter.")
    parser.add_argument("--mandate-family-code", help="UN Job Family code filter.")
    parser.add_argument("--capability-tag", help="Capability tag filter.")
    parser.add_argument("--contract-group", help="Normalized contract group filter.")
    parser.add_argument("--seniority-group", help="Normalized seniority group filter.")
    parser.add_argument("--contract-category", help="Contract category filter.")
    parser.add_argument("--contract-subtype", help="Contract subtype filter.")
    parser.add_argument("--grade-system", help="Grade system filter.")
    parser.add_argument("--grade-family", help="Grade family filter.")
    parser.add_argument("--grade-code", help="Grade code filter.")
    parser.add_argument("--staff-category", help="Staff category filter.")
    parser.add_argument("--max-min-years-experience", type=int, help="Maximum minimum years filter.")
    parser.add_argument("--national-international", help="National/international scope filter.")
    parser.add_argument("--country", help="Country name filter.")
    parser.add_argument("--country-iso3", help="Country ISO3 filter.")
    parser.add_argument("--city", help="City filter.")
    parser.add_argument("--region", help="Region filter.")
    parser.add_argument("--subregion", help="Subregion filter.")
    parser.add_argument("--work-modality", help="Work modality filter.")
    parser.add_argument("--unv-category", help="UNV category filter.")
    parser.add_argument("--unv-volunteer-type", help="UNV volunteer type filter.")
    parser.add_argument("--unv-assignment-duration", help="UNV assignment duration filter.")
    parser.add_argument("--unv-work-arrangement", help="UNV work arrangement filter.")
    parser.add_argument("--unv-hours-per-week", help="UNV hours per week filter.")
    parser.add_argument("--unv-host-entity", help="UNV host entity filter.")
    parser.add_argument("--unv-sdg", help="UNV SDG filter.")
    parser.add_argument("--unv-expertise-area", help="UNV expertise area contains filter.")
    parser.add_argument("--posted-date-from", help="Posted-at lower bound.")
    parser.add_argument("--posted-date-to", help="Posted-at upper bound.")
    parser.add_argument("--closing-date-from", help="Closing-date lower bound.")
    parser.add_argument("--closing-date-to", help="Closing-date upper bound.")
    parser.add_argument("--include-inactive", action="store_true", help="Include missing/closed jobs.")
    parser.add_argument("--needs-review", choices=["true", "false"], help="Filter review flag.")
    parser.add_argument("--limit", type=int, help="Limit returned rows.")


def _add_search_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--text", help="Search title, description, and location text.")
    parser.add_argument(
        "--status",
        action="append",
        help="Job status filter. Defaults to open. Repeat for multiple statuses.",
    )
    parser.add_argument("--organization", action="append", default=[], help="Organization/source org ID filter.")
    parser.add_argument("--source-id", action="append", default=[], help="Source ID filter.")
    parser.add_argument("--ats-family", action="append", default=[], help="ATS family filter.")
    parser.add_argument("--city", action="append", default=[], help="City filter. Repeat for multiple cities.")
    parser.add_argument(
        "--country",
        action="append",
        default=[],
        help="Country filter as ISO2, ISO3, or country name. Repeat for multiple countries.",
    )
    parser.add_argument("--region", action="append", default=[], help="Region filter.")
    parser.add_argument(
        "--location-type",
        action="append",
        help="Location type filter. Defaults to primary, duty_station, and outposted.",
    )
    parser.add_argument(
        "--scope",
        action="append",
        default=[],
        help="National/international scope filter. Repeat for multiple scopes.",
    )
    parser.add_argument("--contract-category", action="append", default=[], help="Contract category filter.")
    parser.add_argument("--grade-system", action="append", default=[], help="Grade system filter.")
    parser.add_argument("--grade-family", action="append", default=[], help="Grade family filter.")
    parser.add_argument("--grade", action="append", default=[], help="Grade code filter such as P2 or P-3.")
    parser.add_argument("--ccog-code", action="append", default=[], help="Exact CCOG primary code filter.")
    parser.add_argument("--ccog-family", action="append", default=[], help="CCOG family code/prefix filter.")
    parser.add_argument("--occupational-family-code", action="append", default=[], help="Derived CCOG family filter.")
    parser.add_argument("--occupational-medium-code", action="append", default=[], help="Derived CCOG medium filter.")
    parser.add_argument("--mandate-network-code", action="append", default=[], help="UN Job Network code filter.")
    parser.add_argument("--mandate-family-code", action="append", default=[], help="UN Job Family code filter.")
    parser.add_argument("--capability-tag", action="append", default=[], help="Capability tag filter.")
    parser.add_argument("--contract-group", action="append", default=[], help="Normalized contract group filter.")
    parser.add_argument("--seniority-group", action="append", default=[], help="Normalized seniority group filter.")
    parser.add_argument("--work-modality", action="append", default=[], help="Work modality filter.")
    parser.add_argument("--unv-category", action="append", default=[], help="UNV category filter.")
    parser.add_argument("--unv-volunteer-type", action="append", default=[], help="UNV volunteer type filter.")
    parser.add_argument("--posted-date-from", help="Posted-at lower bound.")
    parser.add_argument("--posted-date-to", help="Posted-at upper bound.")
    parser.add_argument("--closing-date-from", help="Closing-date lower bound.")
    parser.add_argument("--closing-date-to", help="Closing-date upper bound.")
    parser.add_argument(
        "--min-location-confidence",
        type=float,
        default=0.70,
        help="Minimum normalized location confidence.",
    )
    parser.add_argument(
        "--min-grade-confidence",
        type=float,
        default=0.70,
        help="Minimum grade classification confidence.",
    )
    parser.add_argument(
        "--include-low-confidence",
        action="store_true",
        help="Disable confidence thresholds for broader exploratory search.",
    )
    parser.add_argument("--limit", type=int, default=50, help="Limit returned rows.")
    parser.add_argument("--offset", type=int, default=0, help="Offset returned rows.")
    parser.add_argument(
        "--sort",
        choices=["closing_date_asc", "closing_date_desc", "posted_date_desc"],
        default="closing_date_asc",
        help="Sort order.",
    )
    parser.add_argument(
        "--score-against",
        dest="score_against",
        default=None,
        help=(
            "Path to a strategy report (markdown) or signals JSON. When set, "
            "results are scored against the contained terms / CCOG codes and "
            "annotated with `score` and `score_reasons` fields."
        ),
    )
    parser.add_argument(
        "--min-score",
        dest="min_score",
        type=float,
        default=None,
        help=(
            "Drop results whose strategy fit score is below this threshold "
            "(0..1). Implies --score-against."
        ),
    )


def _search_request_from_args(args: argparse.Namespace) -> VacancySearchRequest:
    return VacancySearchRequest(
        text=getattr(args, "text", None),
        status=getattr(args, "status", None) or ["open"],
        organizations=getattr(args, "organization", []) or [],
        source_ids=getattr(args, "source_id", []) or [],
        ats_families=getattr(args, "ats_family", []) or [],
        cities=getattr(args, "city", []) or [],
        countries_iso3=getattr(args, "country", []) or [],
        regions=getattr(args, "region", []) or [],
        location_types=getattr(args, "location_type", None)
        or ["primary", "duty_station", "outposted"],
        national_international=getattr(args, "scope", []) or [],
        contract_categories=getattr(args, "contract_category", []) or [],
        grade_systems=getattr(args, "grade_system", []) or [],
        grade_families=getattr(args, "grade_family", []) or [],
        grade_codes=getattr(args, "grade", []) or [],
        ccog_codes=getattr(args, "ccog_code", []) or [],
        ccog_families=getattr(args, "ccog_family", []) or [],
        occupational_family_codes=getattr(args, "occupational_family_code", []) or [],
        occupational_medium_codes=getattr(args, "occupational_medium_code", []) or [],
        mandate_network_codes=getattr(args, "mandate_network_code", []) or [],
        mandate_family_codes=getattr(args, "mandate_family_code", []) or [],
        capability_tags=getattr(args, "capability_tag", []) or [],
        contract_groups=getattr(args, "contract_group", []) or [],
        seniority_groups=getattr(args, "seniority_group", []) or [],
        work_modalities=getattr(args, "work_modality", []) or [],
        unv_categories=getattr(args, "unv_category", []) or [],
        unv_volunteer_types=getattr(args, "unv_volunteer_type", []) or [],
        posted_date_from=getattr(args, "posted_date_from", None),
        posted_date_to=getattr(args, "posted_date_to", None),
        closing_date_from=getattr(args, "closing_date_from", None),
        closing_date_to=getattr(args, "closing_date_to", None),
        min_location_confidence=getattr(args, "min_location_confidence", 0.70),
        min_grade_confidence=getattr(args, "min_grade_confidence", 0.70),
        include_low_confidence=getattr(args, "include_low_confidence", False),
        limit=getattr(args, "limit", 50),
        offset=getattr(args, "offset", 0),
        sort=getattr(args, "sort", "closing_date_asc"),
    )


def _filters_from_args(args: argparse.Namespace) -> VacancyFilters:
    needs_review = None
    if getattr(args, "needs_review", None) == "true":
        needs_review = True
    elif getattr(args, "needs_review", None) == "false":
        needs_review = False
    return VacancyFilters(
        text=getattr(args, "text", None),
        organization=getattr(args, "organization", None),
        source_id=getattr(args, "source_id", None),
        ats_family=getattr(args, "ats_family", None),
        ccog_part=getattr(args, "ccog_part", None),
        ccog_family=getattr(args, "ccog_family", None),
        ccog_code=getattr(args, "ccog_code", None),
        occupational_family_code=getattr(args, "occupational_family_code", None),
        occupational_medium_code=getattr(args, "occupational_medium_code", None),
        mandate_network_code=getattr(args, "mandate_network_code", None),
        mandate_family_code=getattr(args, "mandate_family_code", None),
        capability_tag=getattr(args, "capability_tag", None),
        contract_group=getattr(args, "contract_group", None),
        seniority_group=getattr(args, "seniority_group", None),
        contract_category=getattr(args, "contract_category", None),
        contract_subtype=getattr(args, "contract_subtype", None),
        grade_system=getattr(args, "grade_system", None),
        grade_family=getattr(args, "grade_family", None),
        grade_code=getattr(args, "grade_code", None),
        staff_category=getattr(args, "staff_category", None),
        max_min_years_experience=getattr(args, "max_min_years_experience", None),
        national_international=getattr(args, "national_international", None),
        country=getattr(args, "country", None),
        country_iso3=getattr(args, "country_iso3", None),
        city=getattr(args, "city", None),
        region=getattr(args, "region", None),
        subregion=getattr(args, "subregion", None),
        work_modality=getattr(args, "work_modality", None),
        unv_category=getattr(args, "unv_category", None),
        unv_volunteer_type=getattr(args, "unv_volunteer_type", None),
        unv_assignment_duration=getattr(args, "unv_assignment_duration", None),
        unv_work_arrangement=getattr(args, "unv_work_arrangement", None),
        unv_hours_per_week=getattr(args, "unv_hours_per_week", None),
        unv_host_entity=getattr(args, "unv_host_entity", None),
        unv_sdg=getattr(args, "unv_sdg", None),
        unv_expertise_area=getattr(args, "unv_expertise_area", None),
        posted_date_from=getattr(args, "posted_date_from", None),
        posted_date_to=getattr(args, "posted_date_to", None),
        closing_date_from=getattr(args, "closing_date_from", None),
        closing_date_to=getattr(args, "closing_date_to", None),
        only_active=not getattr(args, "include_inactive", False),
        needs_review=needs_review,
        limit=getattr(args, "limit", None),
    )


def _write_text(value: str, output: str | None) -> None:
    if output:
        Path(output).parent.mkdir(parents=True, exist_ok=True)
        Path(output).write_text(value, encoding="utf-8")
        return
    sys.stdout.write(value)


def _write_csv(rows: list[dict], output: str | None) -> None:
    fieldnames = sorted({key for row in rows for key in row if key != "raw"})
    if output:
        Path(output).parent.mkdir(parents=True, exist_ok=True)
        handle = Path(output).open("w", newline="", encoding="utf-8")
        close = True
    else:
        handle = sys.stdout
        close = False
    try:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    finally:
        if close:
            handle.close()


def _write_markdown(
    rows: list[dict],
    output: str | None,
    *,
    title: str,
    total: int | None = None,
) -> None:
    lines = [f"# {title}", ""]
    if total is not None:
        lines.extend([f"Total matches: {total}", ""])
    if not rows:
        lines.extend(["No matching jobs.", ""])
        _write_text("\n".join(lines), output)
        return

    columns = [
        ("title", "Title"),
        ("organization", "Organization"),
        ("location", "Location"),
        ("grade", "Grade"),
        ("scope", "Scope"),
        ("contract", "Contract"),
        ("ccog", "CCOG"),
        ("closing", "Closing"),
        ("status", "Status"),
    ]
    lines.append("| " + " | ".join(label for _, label in columns) + " |")
    lines.append("| " + " | ".join("---" for _ in columns) + " |")
    for row in rows:
        lines.append(
            "| "
            + " | ".join(_markdown_field(row, field) for field, _ in columns)
            + " |"
        )
    lines.append("")
    _write_text("\n".join(lines), output)


def _markdown_field(row: dict, field: str) -> str:
    if field == "title":
        title = _markdown_escape(_first_value(row, "title") or "")
        url = _first_value(row, "apply_url", "source_url")
        return f"[{title}]({url})" if title and url else title
    if field == "organization":
        return _markdown_escape(_first_value(row, "organization", "org_id", "source_id") or "")
    if field == "location":
        if row.get("duty_station"):
            value = str(row["duty_station"])
        elif row.get("city") or row.get("country"):
            value = ", ".join(str(part) for part in (row.get("city"), row.get("country")) if part)
        else:
            value = _first_value(row, "location")
        return _markdown_escape(value or "")
    if field == "grade":
        return _markdown_escape(_first_value(row, "grade_code", "grade_family") or "")
    if field == "scope":
        return _markdown_escape(_first_value(row, "national_international") or "")
    if field == "contract":
        return _markdown_escape(_first_value(row, "contract_category", "contract_subtype") or "")
    if field == "ccog":
        code = _first_value(row, "ccog_primary_code", "ccog_family_code")
        label = _first_value(row, "ccog_primary_label", "ccog_family_label")
        value = " - ".join(part for part in (code, label) if part)
        return _markdown_escape(value)
    if field == "closing":
        return _markdown_escape(_first_value(row, "closing_date", "closes_at") or "")
    if field == "status":
        return _markdown_escape(_first_value(row, "status") or "")
    return ""


def _first_value(row: dict, *keys: str) -> str | None:
    for key in keys:
        value = row.get(key)
        if value not in (None, ""):
            return str(value)
    return None


def _markdown_escape(value: str) -> str:
    return " ".join(value.replace("|", "\\|").split())


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
        setattr(args, "_missing_source_ids", missing)
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
