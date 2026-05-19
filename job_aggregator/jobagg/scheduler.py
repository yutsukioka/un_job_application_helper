"""Command-line entry point for job aggregation runs."""

from __future__ import annotations

import argparse
import csv
import json
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from jobagg.classification import classify_database
from jobagg.classification.audit import audit_classification, audit_to_markdown
from jobagg.classification.classifiers.ccog import ccog_tree
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
from jobagg.pipelines.consolidation import (
    consolidate_bundle_databases,
    write_organization_summary,
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
    parser.add_argument(
        "--saved-searches",
        default="config/saved_searches.json",
        help="Saved searches JSON path.",
    )
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
        "--skip-classify",
        action="store_true",
        help="Do not run classification before writing bundle exports.",
    )
    bundles.add_argument(
        "--no-archive",
        action="store_true",
        help="Do not archive overwritten, duplicate, or noncanonical output files.",
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
        default="ccog-filter-v1",
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
    if getattr(args, "_missing_source_ids", []):
        return 1
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
                classify=not args.skip_classify,
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


def handle_consolidate_bundles(args: argparse.Namespace) -> int:
    result = consolidate_bundle_databases(
        output_dir=args.output_dir,
        slug=args.slug,
    )
    if args.summary_output:
        write_organization_summary(result, args.summary_output)
        LOGGER.info("Wrote organization summary to %s", args.summary_output)
    LOGGER.info(
        "Consolidated %s source databases into %s",
        len(result.source_db_paths),
        result.db_path,
    )
    LOGGER.info("Current open jobs: %s", result.current_count)
    LOGGER.info("History jobs: %s", result.history_count)
    LOGGER.info("Current JSON: %s", result.current_json_path)
    LOGGER.info("Current CSV: %s", result.current_csv_path)
    LOGGER.info("History JSON: %s", result.history_json_path)
    LOGGER.info("History CSV: %s", result.history_csv_path)
    return 0


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
