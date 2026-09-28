#!/usr/bin/env python3
"""Check a paired evidence ledger against clean EHF paragraphs; read-only.

This verifies revisions, role identity and linkage structure, not the truth of
evidence, semantic equivalence, expertise eligibility, duration or Word layout.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys


POLICIES = {
    "SUPPORTED": {"OK_TO_INTEGRATE"},
    "UNSUPPORTED_BUT_PLAUSIBLE": {"INTEGRATE_WITH_CONFIRM_TAG", "HOLD_AS_PLACEHOLDER"},
    "CONFLICTING": {"DO_NOT_INTEGRATE_UNTIL_RESOLVED"},
    "AMBIGUOUS": {"HOLD_AS_PLACEHOLDER"},
}
ROLE_FIELDS = ("job_title", "employer", "from_date", "to_date")


def require(condition, message):
    if not condition:
        raise ValueError(message)


def nonempty(value):
    return isinstance(value, str) and bool(value.strip())


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def unique_rows(rows, field, label):
    require(isinstance(rows, list), f"{label} must be an array")
    result = {}
    for row in rows:
        require(isinstance(row, dict) and nonempty(row.get(field)), f"Invalid {label} identity")
        require(row[field] not in result, f"Duplicate {label} identity: {row[field]}")
        result[row[field]] = row
    return result


def validate(ledger, ehf, crosswalk):
    require(ledger.get("schema_version") == crosswalk.get("schema_version") == 1,
            "Unsupported linkage schema")
    require(nonempty(ledger.get("application_id")) and
            ledger["application_id"] == crosswalk.get("application_id"),
            "Application scope differs")
    sources = unique_rows(ledger.get("source_manifest"), "source_id", "source")
    require(sources, "Source manifest is empty")
    unverifiable = []
    for source_id, source in sources.items():
        if "path" in source:
            require(nonempty(source["path"]) and Path(source["path"]).is_absolute(),
                    f"Source {source_id} needs an absolute path")
            require(digest(source["path"]) == source.get("sha256"), f"Stale source: {source_id}")
        else:
            require(nonempty(source.get("version")) and nonempty(source.get("description")),
                    f"Non-file source {source_id} needs a version and description")
            unverifiable.append(source_id)

    roles = unique_rows(ledger.get("roles"), "role_id", "role")
    jobs = ehf.get("jobs")
    require(isinstance(jobs, list) and len(jobs) == len(roles), "EHF role coverage differs")
    job_roles = {}
    for index, job in enumerate(jobs):
        require(isinstance(job, dict) and all(k in job for k in ROLE_FIELDS), "Invalid EHF job")
        matches = [rid for rid, role in roles.items()
                   if all(k in role and role[k] == job[k] for k in ROLE_FIELDS)]
        require(len(matches) == 1, f"EHF job {index} has missing or ambiguous role identity")
        require(matches[0] not in job_roles.values(), "EHF repeats a role")
        job_roles[index] = matches[0]

    domains = ledger.get("domains")
    require(isinstance(domains, list), "Domains must be an array")
    evidence, pairs = {}, set()
    for domain in domains:
        require(isinstance(domain, dict), "Invalid domain")
        pair = (domain.get("area_id"), domain.get("subarea_id"))
        require(all(nonempty(v) for v in pair) and pair not in pairs, "Invalid or duplicate domain pair")
        pairs.add(pair)
        records = unique_rows(domain.get("evidence"), "evidence_id", "evidence")
        require(records, "A mapped domain needs evidence or a held assertion")
        for eid, record in records.items():
            require(eid not in evidence, f"Duplicate evidence identity: {eid}")
            status = record.get("assertion_status")
            require(status in POLICIES and record.get("integration_policy") in POLICIES[status],
                    f"Inconsistent assertion policy: {eid}")
            rid = record.get("role_id")
            require(rid is None or rid in roles, f"Unknown role in evidence: {eid}")
            score = record.get("role_relevance")
            require((rid is None and score is None) or
                    (rid is not None and type(score) is int and score in (1, 2, 3)),
                    f"Invalid historical role relevance: {eid}")
            require(nonempty(record.get("action")), f"Missing source action: {eid}")
            require(record.get("result") is None or nonempty(record["result"]), f"Invalid result: {eid}")
            refs = record.get("source_refs")
            require(isinstance(refs, list) and refs, f"Missing source references: {eid}")
            for ref in refs:
                require(isinstance(ref, dict) and ref.get("source_id") in sources and
                        nonempty(ref.get("locator")), f"Invalid source locator: {eid}")
            require(isinstance(record.get("practice_periods"), list), f"Missing practice-period array: {eid}")
            evidence[eid] = record

    eligible = {eid for eid, e in evidence.items()
                if e["assertion_status"] == "SUPPORTED" and e.get("role_id") in roles}
    links = unique_rows(crosswalk.get("links"), "evidence_id", "link")
    held = unique_rows(crosswalk.get("held"), "evidence_id", "held evidence")
    require(set(links) == eligible, "Links must cover every eligible record and no held/unknown evidence")
    require(set(held) == set(evidence) - eligible, "Held list must account for all unresolved records")
    require(all(nonempty(row.get("reason")) for row in held.values()), "Held evidence needs a reason")
    target_count = 0
    for eid, link in links.items():
        targets = link.get("targets")
        require(isinstance(targets, list) and targets, f"No narrative target: {eid}")
        seen = set()
        for target in targets:
            require(isinstance(target, dict), f"Invalid target: {eid}")
            j, section, p = (target.get(k) for k in ("job_index", "section", "paragraph_index"))
            require(type(j) is int and j in job_roles and job_roles[j] == evidence[eid]["role_id"],
                    f"Target points to a different role: {eid}")
            require(section in ("responsibilities", "achievements"), f"Invalid narrative section: {eid}")
            paragraphs = jobs[j].get(section)
            paragraphs = [paragraphs] if isinstance(paragraphs, str) else paragraphs
            require(isinstance(paragraphs, list) and type(p) is int and 0 <= p < len(paragraphs),
                    f"Invalid paragraph position: {eid}")
            require(nonempty(target.get("text")) and paragraphs[p] == target["text"],
                    f"Stale or changed narrative text: {eid}")
            require((j, section, p) not in seen, f"Duplicate target: {eid}")
            seen.add((j, section, p))
            target_count += 1
    return {"status": "PASS", "scope": "structural linkage only", "roles": len(roles),
            "domains": len(domains), "eligible_evidence": len(eligible),
            "linked_targets": target_count, "held_evidence": len(held),
            "non_file_sources_requiring_review": unverifiable}


def validate_files(ledger_path, ehf_path, crosswalk_path):
    ledger, ehf, crosswalk = [json.loads(Path(p).read_text(encoding="utf-8"))
                              for p in (ledger_path, ehf_path, crosswalk_path)]
    require(all(isinstance(x, dict) for x in (ledger, ehf, crosswalk)), "Inputs must be JSON objects")
    require(crosswalk.get("ledger_sha256") == digest(ledger_path), "Stale ledger revision in crosswalk")
    require(crosswalk.get("ehf_input_sha256") == digest(ehf_path), "Stale EHF revision in crosswalk")
    return validate(ledger, ehf, crosswalk)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ledger", required=True, type=Path)
    parser.add_argument("--ehf-input", required=True, type=Path)
    parser.add_argument("--crosswalk", required=True, type=Path)
    args = parser.parse_args()
    try:
        report = validate_files(args.ledger, args.ehf_input, args.crosswalk)
    except (ValueError, OSError, TypeError, KeyError) as exc:
        print(json.dumps({"status": "FAIL", "error": str(exc)}))
        return 1
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
