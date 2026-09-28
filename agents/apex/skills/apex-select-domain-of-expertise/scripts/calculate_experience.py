#!/usr/bin/env python3
"""Union source-anchored practice intervals; report date uncertainty, not bands."""
from __future__ import annotations

import argparse
import calendar
from datetime import date
import json
from pathlib import Path
import re
import sys


def date_bounds(value):
    if not isinstance(value, str):
        raise ValueError("Date must be an ISO date, month or year string")
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        exact = date.fromisoformat(value)
        return exact, exact, "day"
    if re.fullmatch(r"\d{4}-\d{2}", value):
        year, month = map(int, value.split("-"))
        return date(year, month, 1), date(year, month, calendar.monthrange(year, month)[1]), "month"
    if re.fullmatch(r"\d{4}", value):
        year = int(value)
        return date(year, 1, 1), date(year, 12, 31), "year"
    raise ValueError("Use source date precision: YYYY-MM-DD, YYYY-MM or YYYY")


def union(intervals):
    merged = []
    for start, end in sorted(intervals):
        if start > end:
            continue
        if merged and start <= merged[-1][1] + 1:
            merged[-1][1] = max(end, merged[-1][1])
        else:
            merged.append([start, end])
    return merged


def describe(intervals):
    return [{"start": date.fromordinal(a).isoformat(), "end": date.fromordinal(b).isoformat(),
             "covered_calendar_days": b - a + 1} for a, b in intervals]


def calculate(ledger):
    if not isinstance(ledger, dict) or not isinstance(ledger.get("intervals"), list):
        raise ValueError("Ledger needs an intervals array")
    cutoff = date.fromisoformat(ledger["as_of_date"])
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", ledger["as_of_date"]):
        raise ValueError("as_of_date must have day precision")
    lower, upper, records, issues = [], [], [], []
    for index, row in enumerate(ledger["intervals"]):
        try:
            if not isinstance(row, dict) or not isinstance(row.get("source"), str) or not row["source"].strip():
                raise ValueError("Each interval needs a source locator")
            lo_start, hi_start, start_precision = date_bounds(row.get("start"))
            current = row.get("end") in (None, "", "present", "Present")
            if current:
                if row.get("current_confirmed") is not True or not isinstance(row.get("current_status_source"), str) or not row["current_status_source"].strip():
                    raise ValueError("Open-ended interval requires supported current status and its source")
                lo_end = hi_end = cutoff
                end_precision = "assessment_cutoff"
            else:
                lo_end, hi_end, end_precision = date_bounds(row["end"])
                lo_end, hi_end = min(lo_end, cutoff), min(hi_end, cutoff)
            if lo_start > cutoff:
                raise ValueError("Interval starts after the assessment cutoff")
            if hi_end < lo_start:
                raise ValueError("End precedes start")
            earliest = (lo_start.toordinal(), hi_end.toordinal())
            latest = (hi_start.toordinal(), lo_end.toordinal())
            upper.append(earliest)
            if latest[0] <= latest[1]:
                lower.append(latest)
            records.append({"interval_index": index, "source": row["source"], "start_as_supplied": row["start"],
                            "end_as_supplied": row.get("end"), "start_precision": start_precision,
                            "end_precision": end_precision,
                            "current_status_source": row.get("current_status_source") if current else None})
        except (ValueError, TypeError, KeyError):
            # Return concise issues, never the raw ledger or a traceback.
            message = "Invalid or unresolved source/date interval"
            if isinstance(row, dict):
                if not row.get("source"):
                    message = "Missing source locator"
                elif row.get("end") in (None, "", "present", "Present") and (
                    row.get("current_confirmed") is not True or not row.get("current_status_source")
                ):
                    message = "Current status is not supported for this open interval"
            issues.append({"interval_index": index, "issue": message})
    lo_union, hi_union = union(lower), union(upper)
    lo_days = sum(b - a + 1 for a, b in lo_union)
    hi_days = sum(b - a + 1 for a, b in hi_union)
    return {"as_of_date": cutoff.isoformat(), "all_intervals_resolved": not issues,
            "calculation_convention": "Inclusive calendar dates; imprecise dates yield bounds, not invented exact dates. No FTE or portal-year conversion.",
            "qualified_intervals_supplied_by_caller": True,
            "supported_intervals_count": len(records), "source_ledger": records,
            "known_covered_days_min": lo_days, "known_covered_days_max": hi_days,
            "date_precision_uncertainty": lo_days != hi_days,
            "minimum_coverage_periods": describe(lo_union), "maximum_coverage_periods": describe(hi_union),
            "issues": issues, "portal_experience_band": None,
            "band_note": "No automatic band. Missing intervals mean known covered-day totals are not a complete career total; validate relevance and band interpretation separately."}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ledger", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        result = calculate(json.loads(args.ledger.read_text(encoding="utf-8")))
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["all_intervals_resolved"] else 2
    except (OSError, ValueError, TypeError, KeyError):
        print("Invalid or unreadable ledger; supply intervals and an exact as_of_date", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
