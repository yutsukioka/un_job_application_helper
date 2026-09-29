#!/usr/bin/env python3
"""Validate a UNOPS skill-selection sidecar against a supplied catalog.

Python 3.10+; standard library only. This checks structure, exact labels, source
metadata, and applicable count constraints. It cannot establish that claims are
true, that a portal limit is authentic, or that a vacancy requirement is met.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class ValidationError(ValueError):
    """An input cannot be interpreted as a valid catalog/selection."""


@dataclass(frozen=True)
class Catalog:
    sha256: str
    records: dict[str, dict[str, str]]


def load_catalog(path: Path) -> Catalog:
    """Find the three-column header after optional instruction rows."""
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    records: dict[str, dict[str, str]] = {}
    numbers: set[str] = set()
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.reader(handle)
        header_found = False
        for index, row in enumerate(reader):
            if [cell.strip() for cell in row] == ["No", "Skills List", "Description"]:
                header_found = True
                break
            if index >= 9:
                break
        if not header_found:
            raise ValidationError("Catalog header No, Skills List, Description not found in first ten records.")
        for row in reader:
            if not row or not any(cell.strip() for cell in row):
                continue
            if len(row) != 3:
                raise ValidationError(f"Catalog record ending on physical line {reader.line_num} has {len(row)} columns; expected three.")
            number, name, description = row
            number = number.strip()
            if not number or not name.strip() or not description.strip():
                raise ValidationError(f"Empty catalog field at physical line {reader.line_num}.")
            if name in records:
                raise ValidationError(f"Duplicate catalog skill name: {name!r}.")
            if number in numbers:
                raise ValidationError(f"Duplicate catalog No: {number!r}.")
            records[name] = {"catalog_no": number, "description": description}
            numbers.add(number)
    if not records:
        raise ValidationError("Catalog has no skill records.")
    return Catalog(digest, records)


def _nonempty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _nonnegative_int(value: Any) -> bool:
    return type(value) is int and value >= 0  # bool is deliberately rejected


def validate_selection(selection: Any, catalog: Catalog) -> dict[str, Any]:
    """Return errors/warnings; do not normalize or invent missing data."""
    errors: list[str] = []
    warnings: list[str] = []
    if not isinstance(selection, dict):
        return {"valid": False, "errors": ["Selection must be a JSON object."], "warnings": [], "selected_count": None}
    if type(selection.get("schema_version")) is not int or selection.get("schema_version") != 1:
        errors.append("schema_version must be integer 1.")
    mode = selection.get("mode")
    if not isinstance(mode, str) or mode not in {"vacancy", "reference_portfolio"}:
        errors.append("mode must be vacancy or reference_portfolio.")
    if mode == "vacancy" and not _nonempty_string(selection.get("vacancy_id")):
        errors.append("Vacancy mode requires a nonempty vacancy_id.")
    if mode == "reference_portfolio" and selection.get("vacancy_id") is not None:
        errors.append("A reference portfolio must have vacancy_id=null.")
    if selection.get("catalog_sha256") != catalog.sha256:
        errors.append("catalog_sha256 does not match the exact catalog bytes.")

    budget = selection.get("requested_selection_budget")
    if not _nonnegative_int(budget):
        errors.append("requested_selection_budget must be a nonnegative integer.")
    scope = selection.get("selection_scope")
    scopes = {"PROFILE", "APPLICATION", "PER_ROLE"}
    if not isinstance(scope, str) or scope not in scopes:
        errors.append("selection_scope must be PROFILE, APPLICATION or PER_ROLE.")
    if scope == "PER_ROLE" and not _nonempty_string(selection.get("selection_role_id")):
        errors.append("PER_ROLE selections require selection_role_id.")
    limit = selection.get("platform_skill_limit")
    limit_status = selection.get("platform_skill_limit_status")
    limit_scope = selection.get("platform_skill_limit_scope")
    if not isinstance(limit_status, str) or limit_status not in {"VERIFIED", "UNVERIFIED"}:
        errors.append("platform_skill_limit_status must be VERIFIED or UNVERIFIED.")
    if not isinstance(limit_scope, str) or limit_scope not in scopes | {"UNKNOWN"}:
        errors.append("Invalid platform_skill_limit_scope.")
    if limit_status == "UNVERIFIED":
        if limit is not None:
            errors.append("An unverified platform limit must be null, not a numeric assumption.")
        if limit_scope != "UNKNOWN":
            warnings.append("Limit scope supplied despite an unverified limit; check its separate source.")
        warnings.append("UNOPS platform skill limit is unverified; only the requested budget is checked.")
    elif limit_status == "VERIFIED":
        if not _nonnegative_int(limit):
            errors.append("A verified platform limit must be a nonnegative integer.")
        if not isinstance(limit_scope, str) or limit_scope not in scopes:
            errors.append("A verified platform limit requires a known scope.")
        proof = selection.get("platform_skill_limit_evidence")
        if not isinstance(proof, dict) or not all(_nonempty_string(proof.get(k)) for k in ("source", "locator", "observed_at")):
            errors.append("A verified limit requires source, locator and observed_at evidence metadata.")

    skills = selection.get("skills")
    if not isinstance(skills, list):
        errors.append("skills must be a JSON array.")
        skills = []
    count = len(skills)
    if _nonnegative_int(budget) and count > budget:
        errors.append(f"Selected {count} skills exceeds requested budget {budget}.")
    if limit_status == "VERIFIED" and _nonnegative_int(limit):
        if limit_scope == scope and count > limit:
            errors.append(f"Selected {count} skills exceeds verified {scope} limit {limit}.")
        elif limit_scope != scope:
            warnings.append("Verified platform limit applies to another scope; validate that scope's collection separately.")
    seen: set[str] = set()
    for index, skill in enumerate(skills, start=1):
        prefix = f"skills[{index - 1}]"
        if not isinstance(skill, dict):
            errors.append(f"{prefix} must be an object, not a display string.")
            continue
        name = skill.get("name")
        if not _nonempty_string(name):
            errors.append(f"{prefix}.name must be a nonempty exact label.")
        else:
            if name in seen:
                errors.append(f"Duplicate selected skill: {name!r}.")
            seen.add(name)
            record = catalog.records.get(name)
            if record is None:
                errors.append(f"{prefix}: exact name {name!r} is not in the catalog.")
            elif skill.get("catalog_no") != record["catalog_no"]:
                errors.append(f"{prefix}: catalog_no does not match {name!r}; use the No column as a string.")
        if not _nonempty_string(skill.get("selection_reason")):
            errors.append(f"{prefix}.selection_reason is required.")
        requirements = skill.get("requirement_ids")
        if not isinstance(requirements, list) or any(not _nonempty_string(r) for r in requirements):
            errors.append(f"{prefix}.requirement_ids must be an array of nonempty strings.")
        elif mode == "vacancy" and not requirements:
            errors.append(f"{prefix}: vacancy mode requires at least one requirement ID.")
        evidence = skill.get("evidence")
        if not isinstance(evidence, list) or not evidence:
            errors.append(f"{prefix}: at least one supported evidence record is required.")
            continue
        for evidence_index, item in enumerate(evidence):
            where = f"{prefix}.evidence[{evidence_index}]"
            if not isinstance(item, dict):
                errors.append(f"{where} must be an object.")
                continue
            if item.get("status") != "SUPPORTED":
                errors.append(f"{where}: final selected evidence must be SUPPORTED; keep unresolved claims in the assertion ledger.")
            for key in ("role_id", "source", "locator", "claim"):
                if not _nonempty_string(item.get(key)):
                    errors.append(f"{where}.{key} must be a nonempty string.")
            if scope == "PER_ROLE" and item.get("role_id") != selection.get("selection_role_id"):
                errors.append(f"{where}: evidence must belong to selection_role_id for PER_ROLE selection.")
    if mode == "reference_portfolio":
        warnings.append("Reference portfolio only: no vacancy-specific match or shortlist readiness is established.")
    warnings.append("Structural validation does not verify claim truth, requirement satisfaction, exact Position Area choices or live portal behavior.")
    return {"valid": not errors, "catalog_records": len(catalog.records), "selected_count": count, "errors": errors, "warnings": warnings}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", required=True, type=Path)
    parser.add_argument("--selection", required=True, type=Path)
    parser.add_argument("--report", type=Path, help="Optional new JSON report path (never overwritten).")
    args = parser.parse_args(argv)
    try:
        catalog = load_catalog(args.catalog)
        with args.selection.open("r", encoding="utf-8-sig") as handle:
            selection = json.load(handle)
        result = validate_selection(selection, catalog)
        text = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
        if args.report:
            args.report.parent.mkdir(parents=True, exist_ok=True)
            with args.report.open("x", encoding="utf-8") as handle:
                handle.write(text)
        sys.stdout.write(text)
        return 0 if result["valid"] else 1
    except (OSError, UnicodeError, csv.Error, json.JSONDecodeError, ValidationError) as exc:
        print(f"Validation could not complete: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
