#!/usr/bin/env python3
"""Offline exact-choice lookup and row validation; never assesses expertise."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

DEFAULT_REFERENCE = Path(__file__).resolve().parents[1] / "references" / "unesco-domain-reference-2026-09-13.json"


def canonical_digest(reference):
    rows = [{"kind": "area", "id": a["id"], "label": a["label"], "parent_id": "-1", "disabled": a["disabled"]}
            for a in reference["areas"]]
    rows += [{"kind": "subarea", "id": c["id"], "label": c["label"], "parent_id": a["id"], "disabled": c["disabled"]}
             for a in reference["areas"] for c in a["subareas"]]
    rows.sort(key=lambda row: (row["kind"], row["parent_id"], row["id"]))
    return hashlib.sha256(json.dumps(rows, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


def validate_reference(reference):
    if not isinstance(reference, dict) or reference.get("schema_version") != 1:
        raise ValueError("Unsupported reference schema")
    if not reference.get("organization") or not reference.get("snapshot_date"):
        raise ValueError("Reference needs organization and snapshot date")
    if type(reference.get("coverage_complete")) is not bool or not isinstance(reference.get("areas"), list):
        raise ValueError("Invalid area inventory or coverage state")
    seen_areas, seen_children = set(), set()

    def check_option(option, seen):
        if not isinstance(option, dict) or not isinstance(option.get("id"), str) or not option["id"]:
            raise ValueError("Option IDs must be nonempty strings; placeholders are not choices")
        if not isinstance(option.get("label"), str) or not option["label"] or type(option.get("disabled")) is not bool:
            raise ValueError("Option needs an exact label and enabled/disabled state")
        if option["id"] in seen:
            raise ValueError("Duplicate option ID in reference")
        seen.add(option["id"])

    for area in reference["areas"]:
        check_option(area, seen_areas)
        if type(area.get("children_complete")) is not bool or not isinstance(area.get("subareas"), list):
            raise ValueError("Area needs child inventory and explicit coverage")
        if reference["coverage_complete"] and not area["children_complete"]:
            raise ValueError("Complete catalog claim conflicts with partial branch")
        for child in area["subareas"]:
            check_option(child, seen_children)
            if child.get("parent_id") != area["id"]:
                raise ValueError("Child parent_id disagrees with its containing area")
    counts = reference.get("counts", {})
    if counts.get("areas") != len(seen_areas) or counts.get("subareas") != len(seen_children):
        raise ValueError("Declared observed counts differ from the reference records")
    years = reference.get("years_of_experience", {})
    if not isinstance(years.get("options"), list) or type(years.get("complete")) is not bool:
        raise ValueError("Reference needs experience options and coverage state")
    seen_years = set()
    for option in years["options"]:
        check_option(option, seen_years)
    digest = reference.get("source", {}).get("canonical_records_sha256")
    if digest is not None and digest != canonical_digest(reference):
        raise ValueError("Canonical option-record digest mismatch")
    return reference


def load_reference(path=DEFAULT_REFERENCE):
    return validate_reference(json.loads(Path(path).read_text(encoding="utf-8")))


def query(reference, area=None, subarea_id=None, experience_id=None):
    if area is None:
        if subarea_id is not None or experience_id is not None:
            raise ValueError("Subarea/experience validation requires an area")
        return {"organization": reference["organization"], "snapshot_date": reference["snapshot_date"],
                "coverage_complete": reference["coverage_complete"],
                "areas": [{"id": a["id"], "label": a["label"], "disabled": a["disabled"],
                           "children_complete": a["children_complete"], "subarea_count": len(a["subareas"])}
                          for a in reference["areas"]]}
    matches = [a for a in reference["areas"] if area in (a["id"], a["label"])]
    if len(matches) != 1:
        raise ValueError("Area ID or exact label must identify one observed area")
    selected = matches[0]
    if selected["disabled"]:
        raise ValueError("Requested area is disabled")
    result = {"snapshot_date": reference["snapshot_date"],
              "area": {k: selected[k] for k in ("id", "label", "children_complete")},
              "catalog_complete": reference["coverage_complete"]}
    if subarea_id is None:
        if experience_id is not None:
            raise ValueError("Experience-row validation requires a subarea ID")
        result["subareas"] = selected["subareas"]
        result["years_of_experience"] = reference["years_of_experience"]
        return result
    children = [c for c in selected["subareas"] if c["id"] == subarea_id]
    if len(children) != 1:
        raise ValueError("Subarea ID is not an observed child of the requested area")
    if children[0]["disabled"]:
        raise ValueError("Requested subarea is disabled")
    result["subarea"] = children[0]
    result["pair_exists_and_enabled"] = True
    if experience_id is not None:
        choices = [v for v in reference["years_of_experience"]["options"] if v["id"] == experience_id]
        if len(choices) != 1 or choices[0]["disabled"]:
            raise ValueError("Experience ID must name one observed enabled option")
        result["experience"] = choices[0]
        result["row_choice_valid"] = True
    result["evidence_and_duration_assessed"] = False
    return result


def validate_rows(reference, rows):
    if not isinstance(rows, list):
        raise ValueError("Rows input must be a JSON array")
    outcomes, seen = [], set()
    for index, row in enumerate(rows):
        try:
            if not isinstance(row, dict) or any(not isinstance(row.get(k), str) or not row[k]
                                                for k in ("area_id", "subarea_id", "experience_id")):
                raise ValueError("Each row needs three nonempty string IDs")
            key = (row["area_id"], row["subarea_id"])
            if key in seen:
                raise ValueError("Duplicate area/subarea pair in proposed rows")
            seen.add(key)
            result = query(reference, row["area_id"], row["subarea_id"], row["experience_id"])
            for field, result_key in (("area_label", "area"), ("subarea_label", "subarea"), ("experience_label", "experience")):
                if field in row and row[field] != result[result_key]["label"]:
                    raise ValueError("Supplied display label differs from the reference")
            outcomes.append({"row_index": index, "valid": True, "choices": result})
        except ValueError as exc:
            outcomes.append({"row_index": index, "valid": False, "issue": str(exc)})
    return {"valid": all(row["valid"] for row in outcomes), "rows": outcomes,
            "evidence_and_duration_assessed": False}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference", type=Path, default=DEFAULT_REFERENCE)
    parser.add_argument("--area")
    parser.add_argument("--subarea-id")
    parser.add_argument("--experience-id")
    parser.add_argument("--validate-rows", type=Path)
    args = parser.parse_args(argv)
    try:
        reference = load_reference(args.reference)
        if args.validate_rows:
            if any((args.area, args.subarea_id, args.experience_id)):
                raise ValueError("Use row-file validation separately from lookup arguments")
            result = validate_rows(reference, json.loads(args.validate_rows.read_text(encoding="utf-8")))
        else:
            result = query(reference, args.area, args.subarea_id, args.experience_id)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 2 if result.get("valid") is False else 0
    except (OSError, KeyError, TypeError, json.JSONDecodeError):
        print("Invalid or unreadable reference/row file", file=sys.stderr)
        return 2
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
