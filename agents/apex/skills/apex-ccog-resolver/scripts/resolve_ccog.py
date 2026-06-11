#!/usr/bin/env python3
"""
Deterministic helper for apex-ccog-resolver.

This script reads a confirmed vacancy-context classification and the full CCOG
resource, scores entries against JD signals only, and writes a compact
`ccog_reference_resolved.md` file.

It deliberately refuses to run when the resource file is still a schema stub.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import statistics
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

SECTION_RE = re.compile(r"^##\s+(.+?)\s*$", re.MULTILINE)
ENTRY_SPLIT_RE = re.compile(r"(?m)^###\s+")
HEADER_RE = re.compile(r"^(?P<code>.+?)\s+[—-]\s+(?P<title>.+?)\s*$")
# Match both **Label:** value (colon inside bold) and **Label**: value (colon outside)
FIELD_RE = re.compile(r"^\*\*(?P<label>[^*:]+):?\*\*:?\s*(?P<value>.+?)\s*$")
WORD_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9./-]*")

GENERIC_MATCH_TERMS = {
    "administer",
    "advise",
    "apply",
    "assist",
    "coordinate",
    "develop",
    "development",
    "ensure",
    "establish",
    "implement",
    "manage",
    "management",
    "monitor",
    "organize",
    "perform",
    "plan",
    "prepare",
    "provide",
    "review",
    "service",
    "services",
    "support",
    "system",
    "systems",
    "work",
}

TITLE_STOPWORDS = GENERIC_MATCH_TERMS | {
    "and",
    "assistant",
    "associate",
    "chief",
    "for",
    "head",
    "international",
    "junior",
    "lead",
    "national",
    "of",
    "officer",
    "senior",
    "specialist",
    "the",
}

TITLE_SYNONYMS: Dict[str, Sequence[str]] = {
    "analytics": ("analysis", "statistics", "information"),
    "data": ("database", "databases", "information", "statistics", "analytics"),
    "management": ("knowledge", "information", "records", "documentation"),
    "monitoring": ("evaluation", "statistics", "analysis"),
    "reporting": ("reports", "documentation", "information", "statistics"),
}

VACANCY_TYPE_HINTS: Dict[str, Sequence[str]] = {
    "SECRETARIAT": ("1.A.02.e", "1.A.10.b", "1.L.03.a"),
    "PEACEKEEPING": ("1.L.03.a", "1.A.10.b", "1.G.02"),
    "SPM": ("1.L.03.a", "1.A.10.b", "1.G.02"),
    "DEVELOPMENT_AGENCY": ("1.A.02.e", "1.A.02.f", "1.A.11"),
    "HUMANITARIAN_AGENCY": ("1.S.01", "1.L.04", "1.A.10.b"),
    "SPECIALIZED_AGENCY": (),
    "OTHER": (),
}

REGISTER_GROUPS: Sequence[Tuple[str, Sequence[str]]] = (
    ("Political/Diplomatic Register", ("1.L.03.a", "1.A.10.b", "1.G.02")),
    ("Programmatic Register", ("1.A.02.e", "1.A.02.f", "1.A.11")),
    ("Humanitarian Register", ("1.S.01", "1.L.04")),
    ("Donor/Resource Mobilisation Register", ("1.A.10.c",)),
    ("Information Management Register", ("1.A.05", "1.A.05.a", "1.C.04")),
    ("Administrative/Operational Register", ("1.A.12", "1.A.06", "1.A.09")),
)
FALLBACK_REGISTER = "Technical/Specialized Register"

REQUIRED_MIN_ENTRIES = 20


@dataclass
class CCOGEntry:
    code: str
    title: str
    family: str
    canonical_verbs: List[str]
    scope_descriptors: List[str]
    level_signal: str
    common_job_code_titles: List[str]


@dataclass
class ScoredEntry:
    entry: CCOGEntry
    verb_overlap: float
    scope_overlap: float
    title_bonus: float
    vacancy_type_bonus: int
    title_mismatch_penalty: float
    domain_gate_penalty: float
    total_score: float
    matched_verbs: List[str]
    matched_scope: List[str]
    title_overlap_tokens: List[str]


def normalize(text: str) -> str:
    text = text.lower()
    text = text.replace("’", "'").replace("“", '"').replace("”", '"')
    text = re.sub(r"[\u2013\u2014]", "-", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def tokenize(text: str) -> set[str]:
    return {w.lower() for w in WORD_RE.findall(text or "")}


def significant_tokens(text: str) -> set[str]:
    return {
        token
        for token in tokenize(text)
        if len(token) >= 3 and token not in TITLE_STOPWORDS and not token.isdigit()
    }


def expand_title_tokens(tokens: set[str]) -> set[str]:
    expanded = set(tokens)
    for token in list(tokens):
        expanded.update(TITLE_SYNONYMS.get(token, ()))
    return expanded


def split_list_field(value: str) -> List[str]:
    parts = [p.strip() for p in re.split(r"[;,]", value) if p.strip()]
    return parts


def extract_section(markdown: str, heading: str) -> str:
    lines = markdown.splitlines()
    capture = False
    block: List[str] = []
    target = f"## {heading}".strip()
    for line in lines:
        if line.strip().startswith("## "):
            if capture:
                break
            capture = line.strip() == target
            continue
        if capture:
            block.append(line)
    text = "\n".join(block).strip()
    if text.startswith("```") and text.endswith("```"):
        text = "\n".join(text.splitlines()[1:-1]).strip()
    return text.strip()


def extract_vacancy_title(jd_text: str) -> Optional[str]:
    patterns = (
        r"(?im)^\|\s*Job Title\s*\|\s*(?P<title>[^|]+?)\s*\|",
        r"(?im)^\s*Job Title\s*[:|-]\s*(?P<title>.+?)\s*$",
        r"(?im)^\s*Post Title\s*[:|-]\s*(?P<title>.+?)\s*$",
        r"(?im)^\s*Position\s*[:|-]\s*(?P<title>.+?)\s*$",
    )
    for pattern in patterns:
        match = re.search(pattern, jd_text)
        if match:
            title = match.group("title").strip()
            if title:
                return title
    for line in jd_text.splitlines():
        line = line.strip().strip("|").strip()
        if not line or line.lower().startswith(("job opening", "department", "org.", "posting")):
            continue
        if 3 <= len(line.split()) <= 12:
            return line
    return None


def parse_context_pack(path: Path) -> Tuple[str, str, Optional[str], Optional[str]]:
    raw = path.read_text(encoding="utf-8")
    jd = extract_section(raw, "JOB_DESCRIPTION_TEXT")
    req = extract_section(raw, "JOB_REQUIREMENT_TEXT")
    ccog = extract_section(raw, "CCOG_CLASSIFICATION")
    vacancy_type = None
    if ccog:
        for line in ccog.splitlines():
            if line.strip().startswith("TYPE:"):
                value = line.split(":", 1)[1].strip()
                if value and value != "[PASTE HERE]":
                    vacancy_type = value
                    break
    title = extract_vacancy_title(jd)
    return jd, req, vacancy_type, title


def parse_ccog_entries(markdown_text: str) -> List[CCOGEntry]:
    if "Schema Stub Only" in markdown_text or "proposal stub" in markdown_text.lower():
        raise ValueError(
            "The resource file is still a schema stub. Replace it with the full "
            "validated CCOG database before running this resolver."
        )

    entries: List[CCOGEntry] = []
    parts = ENTRY_SPLIT_RE.split(markdown_text)
    # The first split chunk is preamble; remaining chunks are entries.
    for part in parts[1:]:
        part = part.strip()
        if not part:
            continue
        lines = part.splitlines()
        header_match = HEADER_RE.match(lines[0].strip())
        if not header_match:
            continue

        fields: Dict[str, str] = {}
        for line in lines[1:]:
            field_match = FIELD_RE.match(line.strip())
            if field_match:
                fields[field_match.group("label").strip()] = field_match.group("value").strip()

        entry = CCOGEntry(
            code=header_match.group("code").strip(),
            title=header_match.group("title").strip(),
            family=fields.get("Family", ""),
            canonical_verbs=split_list_field(fields.get("Canonical verbs", "")),
            scope_descriptors=split_list_field(fields.get("Scope descriptors", "")),
            level_signal=fields.get("Level signal", ""),
            common_job_code_titles=split_list_field(fields.get("Common Job Code Titles", "")),
        )
        entries.append(entry)

    if len(entries) < REQUIRED_MIN_ENTRIES:
        raise ValueError(
            f"Parsed only {len(entries)} CCOG entries. The resource file does not "
            f"look like the full database; expected at least {REQUIRED_MIN_ENTRIES} entries."
        )
    return entries


def phrase_document_frequencies(entries: Sequence[CCOGEntry]) -> Dict[str, int]:
    frequencies: Dict[str, int] = defaultdict(int)
    for entry in entries:
        seen: set[str] = set()
        for phrase in [*entry.canonical_verbs, *entry.scope_descriptors]:
            p = normalize(phrase)
            if p and not is_generic_phrase(p):
                seen.add(p)
        for phrase in seen:
            frequencies[phrase] += 1
    return frequencies


def is_generic_phrase(phrase: str) -> bool:
    tokens = tokenize(phrase)
    if not tokens:
        return True
    return all(token in GENERIC_MATCH_TERMS for token in tokens)


def phrase_weight(phrase: str, document_frequencies: Dict[str, int], corpus_size: int) -> float:
    p = normalize(phrase)
    if is_generic_phrase(p):
        return 0.0
    df = max(1, document_frequencies.get(p, 1))
    return 1.0 + math.log((1 + corpus_size) / (1 + df))


def phrase_overlap(
    phrases: Sequence[str],
    haystack_norm: str,
    haystack_tokens: set[str],
    document_frequencies: Dict[str, int],
    corpus_size: int,
) -> Tuple[float, List[str]]:
    hits: List[str] = []
    score = 0.0
    for phrase in phrases:
        p = normalize(phrase)
        if not p or p.startswith("[") or is_generic_phrase(p):
            continue
        if " " in p:
            if p in haystack_norm:
                hits.append(phrase)
                score += phrase_weight(p, document_frequencies, corpus_size)
        else:
            if p in haystack_tokens:
                hits.append(phrase)
                score += phrase_weight(p, document_frequencies, corpus_size)
    # Deduplicate while preserving order
    deduped = list(dict.fromkeys(hits))
    return score, deduped


def title_score(entry: CCOGEntry, title: str) -> Tuple[float, float, List[str]]:
    if not title:
        return 0.0, 0.0, []
    title_tokens = significant_tokens(title)
    expanded_title_tokens = expand_title_tokens(title_tokens)
    combined = " ".join([entry.title] + entry.common_job_code_titles)
    entry_tokens = significant_tokens(combined)
    exact_overlap = title_tokens & entry_tokens
    overlap = expanded_title_tokens & entry_tokens
    if not overlap:
        return 0.0, -6.0, []
    if exact_overlap:
        bonus = 6.0 if len(exact_overlap) >= 2 else 4.0
    else:
        bonus = 3.0 if len(overlap) >= 2 else 1.5
    return bonus, 0.0, sorted(overlap)


def vacancy_type_bonus(entry: CCOGEntry, vacancy_type: str) -> int:
    hints = VACANCY_TYPE_HINTS.get(vacancy_type.upper(), ())
    for code in hints:
        if entry.code.startswith(code) or entry.family.startswith(code):
            return 2
    return 0


def domain_gate_penalty(
    entry: CCOGEntry,
    title: str,
    source_text: str,
    title_overlap_tokens: Sequence[str],
    vacancy_type_bonus_value: int,
) -> float:
    if title_overlap_tokens or vacancy_type_bonus_value:
        return 0.0
    source_tokens = significant_tokens(f"{title}\n{source_text}")
    entry_tokens = significant_tokens(
        " ".join([entry.title, entry.family, *entry.common_job_code_titles, *entry.scope_descriptors])
    )
    domain_overlap = source_tokens & entry_tokens
    if len(domain_overlap) >= 2:
        return -5.0
    return -10.0


def score_entries(
    entries: Sequence[CCOGEntry],
    jd_text: str,
    requirements_text: str,
    vacancy_type: str,
    title: str,
) -> List[ScoredEntry]:
    source_text = f"{jd_text}\n{requirements_text}".strip()
    haystack_norm = normalize(source_text)
    haystack_tokens = tokenize(source_text)
    document_frequencies = phrase_document_frequencies(entries)
    corpus_size = len(entries)

    scored: List[ScoredEntry] = []
    for entry in entries:
        v_count, v_hits = phrase_overlap(
            entry.canonical_verbs, haystack_norm, haystack_tokens, document_frequencies, corpus_size
        )
        s_count, s_hits = phrase_overlap(
            entry.scope_descriptors, haystack_norm, haystack_tokens, document_frequencies, corpus_size
        )
        t_bonus, t_penalty, title_overlap = title_score(entry, title)
        vt_bonus = vacancy_type_bonus(entry, vacancy_type)
        gate_penalty = domain_gate_penalty(entry, title, source_text, title_overlap, vt_bonus)
        total = (v_count * 2.0) + s_count + t_bonus + vt_bonus + t_penalty + gate_penalty
        scored.append(
            ScoredEntry(
                entry=entry,
                verb_overlap=v_count,
                scope_overlap=s_count,
                title_bonus=t_bonus,
                vacancy_type_bonus=vt_bonus,
                title_mismatch_penalty=t_penalty,
                domain_gate_penalty=gate_penalty,
                total_score=total,
                matched_verbs=v_hits,
                matched_scope=s_hits,
                title_overlap_tokens=title_overlap,
            )
        )
    scored.sort(
        key=lambda s: (
            0 if s.title_overlap_tokens or s.vacancy_type_bonus else 1,
            -s.total_score,
            s.entry.code,
            s.entry.title,
        )
    )
    return scored


def register_group_for_code(code: str) -> str:
    for group_name, prefixes in REGISTER_GROUPS:
        for prefix in prefixes:
            if code.startswith(prefix):
                return group_name
    return FALLBACK_REGISTER


def select_entries(
    scored_entries: Sequence[ScoredEntry],
    vacancy_type: str,
    include_codes: Sequence[str],
    include_families: Sequence[str],
    top_n: int,
) -> List[ScoredEntry]:
    positive_scores = [s.total_score for s in scored_entries if s.total_score > 0]
    if positive_scores:
        threshold = statistics.median(positive_scores)
    else:
        threshold = 0

    selected = [s for s in scored_entries if s.total_score >= threshold and s.total_score > 0]

    if len(selected) < 8:
        selected = list(scored_entries[: min(max(8, top_n), len(scored_entries))])

    force_selected: List[ScoredEntry] = []
    vacancy_hint_codes = VACANCY_TYPE_HINTS.get(vacancy_type.upper(), ())
    for s in scored_entries:
        code = s.entry.code
        family = s.entry.family
        if any(code.startswith(c) for c in vacancy_hint_codes):
            force_selected.append(s)
            continue
        if any(code.startswith(c) for c in include_codes):
            force_selected.append(s)
            continue
        if any(code.startswith(f) or family.startswith(f) for f in include_families):
            force_selected.append(s)

    by_code = {s.entry.code: s for s in selected}
    for s in force_selected:
        by_code[s.entry.code] = s

    merged = sorted(
        by_code.values(),
        key=lambda s: (
            0 if s.title_overlap_tokens or s.vacancy_type_bonus else 1,
            -s.total_score,
            s.entry.code,
            s.entry.title,
        ),
    )

    if len(merged) > max(20, top_n):
        merged = merged[: max(20, top_n)]
    else:
        merged = merged[: min(20, len(merged))]
    return merged


def derive_primary_secondary(selected: Sequence[ScoredEntry]) -> Tuple[Optional[CCOGEntry], List[CCOGEntry]]:
    if not selected:
        return None, []
    primary = selected[0].entry
    secondaries: List[CCOGEntry] = []
    seen = {primary.code}
    for scored in selected[1:]:
        if scored.entry.code in seen:
            continue
        seen.add(scored.entry.code)
        secondaries.append(scored.entry)
        if len(secondaries) >= 3:
            break
    return primary, secondaries


def render_entry(entry: CCOGEntry) -> str:
    return "\n".join(
        [
            f"### {entry.code} — {entry.title}",
            f"**Family:** {entry.family}",
            f"**Canonical verbs:** {', '.join(entry.canonical_verbs)}",
            f"**Scope descriptors:** {', '.join(entry.scope_descriptors)}",
            f"**Level signal:** {entry.level_signal}",
            f"**Common Job Code Titles:** {', '.join(entry.common_job_code_titles)}",
        ]
    )


def render_output(
    selected: Sequence[ScoredEntry],
    vacancy_title: str,
    vacancy_type: str,
    output_file: Path,
) -> None:
    primary, secondaries = derive_primary_secondary(selected)
    grouped: Dict[str, List[CCOGEntry]] = defaultdict(list)
    for scored in selected:
        grouped[register_group_for_code(scored.entry.code)].append(scored.entry)

    ordered_groups = [group for group, _ in REGISTER_GROUPS] + [FALLBACK_REGISTER]
    lines: List[str] = [
        f"# CCOG Resolved Reference — {vacancy_title or 'Target Vacancy'}",
        f"## Resolved on: {datetime.now(timezone.utc).date().isoformat()}",
        f"## Vacancy type: {vacancy_type}",
        f"## Total entries: {len(selected)}",
        f"## Primary family: {primary.code + ' — ' + primary.title if primary else 'Not resolved'}",
        "## Secondary families: "
        + ("; ".join(f"{e.code} — {e.title}" for e in secondaries) if secondaries else "None"),
        "## Resolver mode: JD-only (candidate history excluded)",
        "",
    ]

    for group_name in ordered_groups:
        entries = grouped.get(group_name, [])
        if not entries:
            continue
        lines.append(f"### {group_name}")
        for entry in entries:
            lines.append(render_entry(entry))
            lines.append("")

    output_file.parent.mkdir(parents=True, exist_ok=True)
    output_file.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def dump_debug_json(scored: Sequence[ScoredEntry], path: Path) -> None:
    payload = []
    for s in scored:
        payload.append(
            {
                "code": s.entry.code,
                "title": s.entry.title,
                "family": s.entry.family,
                "verb_overlap": s.verb_overlap,
                "scope_overlap": s.scope_overlap,
                "title_bonus": s.title_bonus,
                "vacancy_type_bonus": s.vacancy_type_bonus,
                "title_mismatch_penalty": s.title_mismatch_penalty,
                "domain_gate_penalty": s.domain_gate_penalty,
                "total_score": s.total_score,
                "matched_verbs": s.matched_verbs,
                "matched_scope": s.matched_scope,
                "title_overlap_tokens": s.title_overlap_tokens,
            }
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Resolve a vacancy-specific CCOG subset.")
    parser.add_argument("--context-pack", type=Path, help="Path to private/inputs/application_context.md")
    parser.add_argument("--jd-file", type=Path, help="Path to a plain-text JD file")
    parser.add_argument("--requirements-file", type=Path, help="Path to a plain-text requirements file")
    parser.add_argument("--vacancy-type", help="Confirmed vacancy type")
    parser.add_argument("--title", help="Vacancy title")
    parser.add_argument(
        "--resource-file",
        type=Path,
        required=True,
        help="Path to ccog_reference_full.md",
    )
    parser.add_argument(
        "--output-file",
        type=Path,
        required=True,
        help="Path to write ccog_reference_resolved.md",
    )
    parser.add_argument("--include-code", action="append", default=[], help="Force-include exact code/prefix")
    parser.add_argument("--include-family", action="append", default=[], help="Force-include family code/prefix")
    parser.add_argument("--top-n", type=int, default=15, help="Selection target before the hard cap")
    parser.add_argument("--debug-json", type=Path, help="Optional diagnostics output")
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)

    jd_text = ""
    req_text = ""
    inferred_vacancy_type = None
    inferred_title = None

    if args.context_pack:
        jd_text, req_text, inferred_vacancy_type, inferred_title = parse_context_pack(args.context_pack)

    if args.jd_file:
        jd_text = args.jd_file.read_text(encoding="utf-8").strip()
    if args.requirements_file:
        req_text = args.requirements_file.read_text(encoding="utf-8").strip()

    vacancy_type = (args.vacancy_type or inferred_vacancy_type or "").strip()
    title = (args.title or inferred_title or "").strip()

    if not jd_text:
        print("ERROR: JOB_DESCRIPTION_TEXT is required.", file=sys.stderr)
        return 2
    if not vacancy_type:
        print(
            "ERROR: A confirmed vacancy type is required. Supply --vacancy-type or "
            "populate ## CCOG_CLASSIFICATION in the context pack.",
            file=sys.stderr,
        )
        return 2

    raw_resource = args.resource_file.read_text(encoding="utf-8")
    try:
        entries = parse_ccog_entries(raw_resource)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 3

    scored = score_entries(entries, jd_text, req_text, vacancy_type, title)
    selected = select_entries(
        scored_entries=scored,
        vacancy_type=vacancy_type,
        include_codes=args.include_code,
        include_families=args.include_family,
        top_n=max(8, min(args.top_n, 20)),
    )
    render_output(selected, title or "Target Vacancy", vacancy_type, args.output_file)

    if args.debug_json:
        dump_debug_json(scored, args.debug_json)

    primary, secondaries = derive_primary_secondary(selected)
    secondary_text = "; ".join(f"{e.code} — {e.title}" for e in secondaries) if secondaries else "None"
    print(f"Resolved {len(selected)} entries.")
    print(f"Primary family: {primary.code} — {primary.title}" if primary else "Primary family: Not resolved")
    print(f"Secondary families: {secondary_text}")
    print(f"Wrote: {args.output_file}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
