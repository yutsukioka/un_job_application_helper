#!/usr/bin/env python3
"""Build the packaged CCOG markdown reference from the source PDF.

This script is intentionally dependency-light. It uses Ghostscript's txtwrite
device, which is available on this Mac, to extract the attached ICSC CCOG PDF.
The packaged runtime reads the generated markdown, not the PDF directly.
"""

from __future__ import annotations

import argparse
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path


DEFAULT_PDF = Path(__file__).resolve().parents[3] / "inputs" / "CCOG_9_2015.pdf"
DEFAULT_OUTPUT = (
    Path(__file__).resolve().parents[1]
    / "jobagg"
    / "classification"
    / "rules"
    / "ccog_reference_full.md"
)
EXPECTED_PAGES = 77
EXPECTED_ENTRIES = 221
SKIP_CODES = {"1.J.04.e"}

COMMON_VERBS = {
    "administer",
    "advise",
    "analyse",
    "analyze",
    "apply",
    "arrange",
    "assess",
    "assist",
    "audit",
    "authorize",
    "carry out",
    "certify",
    "check",
    "classify",
    "collect",
    "compile",
    "conduct",
    "control",
    "coordinate",
    "counsel",
    "define",
    "design",
    "determine",
    "develop",
    "direct",
    "draft",
    "ensure",
    "establish",
    "evaluate",
    "examine",
    "execute",
    "facilitate",
    "forecast",
    "formulate",
    "guide",
    "identify",
    "implement",
    "inspect",
    "install",
    "instruct",
    "integrate",
    "interpret",
    "investigate",
    "lead",
    "liaise",
    "maintain",
    "manage",
    "mobilize",
    "monitor",
    "negotiate",
    "observe",
    "operate",
    "organize",
    "oversee",
    "participate",
    "perform",
    "plan",
    "prepare",
    "process",
    "produce",
    "programme",
    "promote",
    "propose",
    "provide",
    "publish",
    "purchase",
    "recommend",
    "record",
    "report",
    "represent",
    "research",
    "review",
    "revise",
    "schedule",
    "study",
    "supervise",
    "support",
    "survey",
    "train",
    "translate",
    "verify",
    "write",
}

SCOPE_TERMS = [
    "programmes",
    "programme",
    "policies",
    "policy",
    "projects",
    "project",
    "operations",
    "services",
    "systems",
    "budget",
    "financial",
    "accounts",
    "accounting",
    "procurement",
    "human resources",
    "personnel",
    "staff",
    "logistics",
    "supply chain",
    "inventory",
    "information systems",
    "data processing",
    "databases",
    "communications",
    "telecommunications",
    "networks",
    "security",
    "safety",
    "protection",
    "legal",
    "legislation",
    "treaties",
    "conventions",
    "public information",
    "media",
    "publications",
    "training",
    "education",
    "development",
    "research",
    "analysis",
    "evaluation",
    "monitoring",
    "health",
    "medical",
    "clinical",
    "epidemiological",
    "environment",
    "environmental",
    "climate",
    "agriculture",
    "food",
    "nutrition",
    "engineering",
    "construction",
    "maintenance",
    "translation",
    "interpretation",
    "editing",
    "archives",
    "records",
    "documents",
    "library",
    "humanitarian",
    "relief",
    "emergency",
    "political affairs",
    "peacekeeping",
    "peacebuilding",
    "economic",
    "social",
    "statistics",
    "fundraising",
    "donor relations",
    "resource mobilization",
    "audit",
    "compliance",
    "investigation",
    "ethics",
    "knowledge management",
    "information management",
    "conference",
    "meeting",
]

HEADER_RE = re.compile(
    r"^\s*((?:[1LI]\.?\s*[A-U]\.?(?:\s*[0-9O]{1,2}(?:\.?\s*[a-z])?)?"
    r"|2\.?\s*[1-3P]\.?(?:\s*[0-9O]{1,2}(?:\.?\s*[a-z])?)?))\.?\s+(.+?)\s*$"
)


@dataclass(slots=True)
class Entry:
    code: str
    title: str
    definition: str = ""
    family_code: str = ""
    family_name: str = ""
    canonical_verbs: list[str] = field(default_factory=list)
    scope_descriptors: list[str] = field(default_factory=list)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build packaged CCOG reference from PDF.")
    parser.add_argument("--pdf", type=Path, default=DEFAULT_PDF)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)

    pages = pdf_page_count(args.pdf)
    if pages != EXPECTED_PAGES:
        raise SystemExit(f"Expected {EXPECTED_PAGES} PDF pages, got {pages}: {args.pdf}")
    text = extract_text(args.pdf)
    entries = parse_entries(text)
    if len(entries) != EXPECTED_ENTRIES:
        raise SystemExit(f"Expected {EXPECTED_ENTRIES} CCOG entries, got {len(entries)}")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(render(entries), encoding="utf-8")
    print(f"Wrote {len(entries)} CCOG entries to {args.output}")
    return 0


def pdf_page_count(path: Path) -> int:
    command = [
        "gs",
        "-q",
        "-dNOSAFER",
        "-dNODISPLAY",
        "-c",
        f"({path}) (r) file runpdfbegin pdfpagecount = quit",
    ]
    result = subprocess.run(command, check=True, capture_output=True, text=True)
    return int(result.stdout.strip())


def extract_text(path: Path) -> str:
    command = ["gs", "-q", "-dNOSAFER", "-sDEVICE=txtwrite", "-o", "-", str(path)]
    result = subprocess.run(command, check=True, capture_output=True, text=True)
    return result.stdout


def parse_entries(text: str) -> list[Entry]:
    lines = text.splitlines()
    start = next(i for i, line in enumerate(lines) if "CCOG DEFINITIONS" in line)
    entries: list[Entry] = []
    current: Entry | None = None
    for line in lines[start:]:
        match = HEADER_RE.match(line)
        title = match.group(2).strip() if match else ""
        if match and len(title.split()) <= 20:
            if current is not None:
                finalize_entry(current, entries)
            current = Entry(code=normalize_code(match.group(1)), title=clean_text(title))
            continue
        if current is not None:
            stripped = line.strip()
            if stripped and not stripped.isdigit():
                current.definition = f"{current.definition} {clean_text(stripped)}".strip()
    if current is not None:
        finalize_entry(current, entries)
    return entries


def finalize_entry(entry: Entry, entries: list[Entry]) -> None:
    if entry.code in SKIP_CODES:
        return
    entry.family_code, entry.family_name = derive_family(entry, entries)
    lower = entry.definition.casefold()
    entry.canonical_verbs = [verb for verb in sorted(COMMON_VERBS) if re.search(rf"\b{re.escape(verb)}\b", lower)]
    entry.scope_descriptors = [term for term in SCOPE_TERMS if term in lower]
    entries.append(entry)


def derive_family(entry: Entry, previous: list[Entry]) -> tuple[str, str]:
    parts = entry.code.split(".")
    family_code = ".".join(parts[:2]) if len(parts) >= 2 else entry.code
    if entry.code == family_code:
        return entry.code, entry.title
    for candidate in reversed(previous):
        if candidate.code == family_code:
            return candidate.code, candidate.title
    return family_code, family_code


def normalize_code(raw: str) -> str:
    parts = [part for part in re.split(r"[\s.]+", raw.strip().rstrip(".")) if part]
    if parts and parts[0] in {"I", "L"}:
        parts[0] = "1"
    normalized = []
    for index, part in enumerate(parts):
        if index >= 2:
            part = part.replace("O", "0").replace("o", "0")
        normalized.append(part)
    return ".".join(normalized)


def clean_text(value: str) -> str:
    return " ".join(value.replace("\u2013", "-").replace("\u2014", "-").split())


def render(entries: list[Entry]) -> str:
    lines = [
        "# CCOG Full Reference - ICSC Common Classification of Occupational Groups (2015)",
        "",
        f"Total entries: {len(entries)}",
        "",
        "Source: inputs/CCOG_9_2015.pdf",
        "Extracted and structured for use by job_aggregator.",
        "",
        "---",
        "",
    ]
    current_family = None
    for entry in entries:
        if entry.family_code != current_family:
            current_family = entry.family_code
            lines.extend([f"## Family: {entry.family_code} - {entry.family_name}", ""])
        lines.extend(
            [
                f"### {entry.code} - {entry.title}",
                f"**Family:** {entry.family_code} - {entry.family_name}",
                f"**Canonical verbs:** {', '.join(entry.canonical_verbs) or 'N/A'}",
                f"**Scope descriptors:** {', '.join(entry.scope_descriptors) or 'N/A'}",
                f"**Level signal:** {'General Service' if entry.code.startswith('2.') else 'Professional/Managerial'}",
                f"**Common Job Code Titles:** {entry.title}",
                "",
            ]
        )
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
