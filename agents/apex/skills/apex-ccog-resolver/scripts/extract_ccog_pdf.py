#!/usr/bin/env python3
"""Extract CCOG entries from the ICSC CCOG 2015 PDF into structured markdown.

Usage:
    python extract_ccog_pdf.py \
        --pdf agents/apex/skills/apex-ccog-resolver/resource/CCOG_9_2015.pdf \
        --output agents/apex/skills/apex-ccog-resolver/resource/ccog_reference_full.md

Requirements: pdfplumber
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import pdfplumber


# --- Constants ---

# Pages 18-76 (0-indexed: 17-75) contain actual definitions.
DEFINITIONS_START_PAGE = 17  # 0-indexed
DEFINITIONS_END_PAGE = 76    # 0-indexed exclusive (page 77 is blank)

# Regex to match CCOG codes like "1. A.01.", "1. A.01.a.", "2.1.01.", "1.A.02.f.", etc.
# The PDF uses inconsistent spacing: "1. A.01." vs "1.A.02.f." vs "L.I.03.i." (typo)
CODE_PATTERN = re.compile(
    r'^(?:'
    r'(?:[1L]\.?\s*[A-U]\.?\s*\d{1,2}(?:\.?\s*[a-z])?)'  # Professional: 1.A.01.a
    r'|'
    r'(?:2\.?\s*[1-3P]\.?\s*\d{1,2}(?:\.?\s*[a-z])?)'     # General Service: 2.1.01.a
    r'|'
    r'(?:1\.?\s*[A-U])'                                     # Family level: 1.A
    r'|'
    r'(?:2\.?\s*[1-3P])'                                    # GS family: 2.1
    r')'
    r'\.?\s*$'
)

# More flexible pattern to find code+title at start of a line
CODE_TITLE_RE = re.compile(
    r'^'
    r'('
    # Professional codes: 1.A.01.a or 1. A.01. a or L.I.03.i (typo in PDF) or I.G.04
    r'(?:[1LI]\.?\s*[A-U]\.?\s*\d{1,2}(?:\.?\s*[a-z])?\.?)'
    r'|'
    # General Service codes: 2.1.01.a or 2. 1.01. a or 2. P.
    r'(?:2\.?\s*[1-3P]\.?\s*\d{1,2}(?:\.?\s*[a-z])?\.?)'
    r'|'
    # Family-level headers: 1. A. or 2.1. etc.
    r'(?:[1LI]\.?\s*[A-U]\.?)'
    r'|'
    r'(?:2\.?\s*[1-3P]\.?)'
    r')'
    r'\s+'    # space between code and title
    r'(.+)'  # title text
    r'$'
)

# Verbs commonly used in CCOG definitions (action verbs)
COMMON_VERBS = {
    "administer", "advise", "analyse", "analyze", "apply", "arrange", "assess",
    "assist", "audit", "authorize", "carry out", "certify", "check", "classify",
    "collect", "compile", "conduct", "control", "coordinate", "counsel",
    "define", "design", "determine", "develop", "direct", "draft", "ensure",
    "establish", "evaluate", "examine", "execute", "facilitate", "forecast",
    "formulate", "guide", "identify", "implement", "inspect", "install",
    "instruct", "integrate", "interpret", "investigate", "lead", "liaise",
    "maintain", "manage", "mobilize", "monitor", "negotiate", "observe",
    "operate", "organize", "oversee", "participate", "perform", "plan",
    "prepare", "process", "produce", "programme", "promote", "propose",
    "provide", "publish", "purchase", "recommend", "record", "report",
    "represent", "research", "review", "revise", "schedule", "study",
    "supervise", "support", "survey", "train", "translate", "verify", "write",
}


@dataclass
class CCOGEntry:
    code: str
    title: str
    family_code: str = ""
    family_name: str = ""
    definition: str = ""
    canonical_verbs: list[str] = field(default_factory=list)
    scope_descriptors: list[str] = field(default_factory=list)
    level_signal: str = ""
    common_job_titles: list[str] = field(default_factory=list)


def normalize_code(raw: str) -> str:
    """Normalize a CCOG code to consistent format like 1.A.01.a"""
    # Remove all spaces
    code = raw.strip().rstrip(".")
    # Fix known typos: L.I.03.i -> 1.I.03.i, I.G.04 -> 1.G.04
    if code.startswith("L.I."):
        code = "1.I." + code[4:]
    if code.startswith("I.G."):
        code = "1.G." + code[4:]

    # Remove spaces around dots and between parts
    parts = re.split(r'[\s.]+', code)
    parts = [p for p in parts if p]

    if not parts:
        return code

    # Reconstruct: number.letter.number.letter
    result_parts = []
    for p in parts:
        result_parts.append(p)

    return ".".join(result_parts)


def extract_verbs(text: str) -> list[str]:
    """Extract canonical action verbs from a definition paragraph."""
    text_lower = text.lower()
    found = []
    for verb in sorted(COMMON_VERBS):
        # Match as whole word or at start of phrase
        pattern = r'\b' + re.escape(verb) + r'\b'
        if re.search(pattern, text_lower):
            found.append(verb)
    return found


def extract_scope_descriptors(text: str) -> list[str]:
    """Extract scope nouns/phrases from a definition paragraph."""
    # Common scope descriptor patterns in CCOG definitions
    scope_terms = [
        "programmes", "programme", "policies", "policy", "projects", "project",
        "operations", "services", "systems", "budgets", "budget",
        "financial", "accounts", "accounting", "procurement",
        "human resources", "personnel", "staff",
        "logistics", "supply chain", "inventory",
        "information systems", "data processing", "databases",
        "communications", "telecommunications", "networks",
        "security", "safety", "protection",
        "legal", "legislation", "treaties", "conventions",
        "public information", "media", "publications",
        "training", "education", "development",
        "research", "analysis", "evaluation", "monitoring",
        "health", "medical", "clinical", "epidemiological",
        "environment", "environmental", "climate",
        "agriculture", "food", "nutrition",
        "engineering", "construction", "maintenance",
        "translation", "interpretation", "editing",
        "archives", "records", "documents", "library",
        "humanitarian", "relief", "emergency",
        "political affairs", "peacekeeping", "peacebuilding",
        "economic", "social", "development",
        "statistics", "statistical",
        "fundraising", "donor relations", "resource mobilization",
        "audit", "compliance", "investigation", "ethics",
        "knowledge management", "information management",
        "conference", "meeting",
    ]
    text_lower = text.lower()
    found = []
    for term in scope_terms:
        if term in text_lower:
            found.append(term)
    return found


def determine_level(code: str) -> str:
    """Determine level signal from code prefix."""
    if code.startswith("2."):
        return "General Service"
    return "Professional/Managerial"


def derive_family(code: str, all_entries: dict[str, str]) -> tuple[str, str]:
    """Derive the family code and name for an entry."""
    parts = code.split(".")
    # For entries like 1.A.01.a, look up 1.A.01 first, then 1.A
    # For entries like 1.A.01, look up 1.A
    # For entries like 1.A, it IS the family
    for depth in range(len(parts) - 1, 0, -1):
        parent_code = ".".join(parts[:depth])
        if parent_code in all_entries:
            # Find the closest family-level parent
            # Family level is typically 2 parts for professional (1.A) or 2 parts for GS (2.1)
            if depth <= 2:
                return parent_code, all_entries[parent_code]

    # If code itself is family-level
    if len(parts) <= 2:
        return code, all_entries.get(code, "")

    # Fallback: use first two parts
    family_code = ".".join(parts[:2])
    return family_code, all_entries.get(family_code, "")


def derive_job_titles(code: str, title: str) -> list[str]:
    """Generate common UN job titles from the CCOG title."""
    titles = [title]
    # Add "Officer" variants common in UN system
    base = title.rstrip("s")  # Remove plural
    if "specialist" in title.lower():
        officer_title = re.sub(r'(?i)\bspecialists?\b', 'Officer', title)
        titles.append(officer_title)
    return titles


def fix_ocr_artifacts(text: str) -> str:
    """Fix common OCR artifacts in the CCOG PDF.

    The PDF uses the letter 'O' instead of digit '0' in some codes,
    e.g., '1. L.O3.a.' instead of '1.L.03.a.'
    """
    # Fix O→0 in CCOG code patterns: digit-or-start followed by .O then digit
    # Matches things like ".O1." ".O3." but not words like "Organization"
    text = re.sub(
        r'(?<=[.\s])O(\d[.\s])',
        r'0\1',
        text,
    )
    # Also fix at line start: "1. S.O1." pattern
    text = re.sub(
        r'(?<=\.)O(\d)',
        r'0\1',
        text,
    )
    return text


def extract_all_text(pdf_path: Path) -> str:
    """Extract text from definition pages of the CCOG PDF."""
    all_text = []
    with pdfplumber.open(str(pdf_path)) as pdf:
        for i in range(DEFINITIONS_START_PAGE, min(DEFINITIONS_END_PAGE, len(pdf.pages))):
            page = pdf.pages[i]
            text = page.extract_text()
            if text:
                # Remove page numbers at the end
                text = re.sub(r'\n\d{1,2}\s*$', '', text)
                # Fix OCR artifacts
                text = fix_ocr_artifacts(text)
                all_text.append(text)
    return "\n".join(all_text)


def parse_entries(full_text: str) -> list[CCOGEntry]:
    """Parse the extracted text into CCOG entries."""
    lines = full_text.split("\n")

    # First pass: identify all code+title positions and collect titles for family lookup
    raw_entries: list[tuple[int, str, str, str]] = []  # (line_idx, raw_code, normalized_code, title)
    title_map: dict[str, str] = {}  # normalized_code -> title

    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if not line:
            i += 1
            continue

        match = CODE_TITLE_RE.match(line)
        if match:
            raw_code = match.group(1).strip()
            title = match.group(2).strip()
            norm_code = normalize_code(raw_code)

            # Skip section headers like "IV. CCOG DEFINITIONS" or "1. PROFESSIONAL..."
            if title.isupper() and len(title) > 30:
                i += 1
                continue
            # Skip "GENERAL SERVICES AND RELATED CATEGORIES" style headers
            if "GENERAL SERVICES" in title.upper() or "PROFESSIONAL MANAGERIAL" in title.upper():
                i += 1
                continue

            raw_entries.append((i, raw_code, norm_code, title))
            title_map[norm_code] = title

        i += 1

    # Second pass: collect definition text between entries
    entries: list[CCOGEntry] = []

    for idx, (line_idx, raw_code, norm_code, title) in enumerate(raw_entries):
        # Definition text runs from current title line to next entry
        if idx + 1 < len(raw_entries):
            next_line_idx = raw_entries[idx + 1][0]
        else:
            next_line_idx = len(lines)

        # Collect definition text (skip the title line itself)
        def_lines = []
        for j in range(line_idx + 1, next_line_idx):
            text = lines[j].strip()
            if text:
                def_lines.append(text)

        definition = " ".join(def_lines)
        # Clean up PDF artifacts
        definition = re.sub(r'\s{2,}', ' ', definition)
        definition = definition.strip()

        # Sometimes the definition starts on the same line after the title
        # Check if the title line has definition text after a period
        title_line = lines[line_idx].strip()
        match = CODE_TITLE_RE.match(title_line)
        if match:
            captured_title = match.group(2).strip()
            # The "title" may contain definition text run together
            # Split at the first sentence that looks like a definition
            # Look for pattern: "Title text\nDefinition starts here..."

        # Extract structured fields
        canonical_verbs = extract_verbs(definition)
        scope_descs = extract_scope_descriptors(definition)
        level = determine_level(norm_code)
        family_code, family_name = derive_family(norm_code, title_map)
        job_titles = derive_job_titles(norm_code, title)

        entry = CCOGEntry(
            code=norm_code,
            title=title,
            family_code=family_code,
            family_name=family_name,
            definition=definition,
            canonical_verbs=canonical_verbs,
            scope_descriptors=scope_descs,
            level_signal=level,
            common_job_titles=job_titles,
        )
        entries.append(entry)

    return entries


def render_markdown(entries: list[CCOGEntry]) -> str:
    """Render entries to the structured markdown format expected by resolve_ccog.py."""
    lines = [
        "# CCOG Full Reference — ICSC Common Classification of Occupational Groups (2015)",
        "",
        f"Total entries: {len(entries)}",
        "",
        "Source: ICSC CCOG 9th edition (August 2015), 77 pages.",
        "Extracted and structured for use by apex-ccog-resolver.",
        "",
        "---",
        "",
    ]

    current_family = ""
    for entry in entries:
        # Add family header when it changes
        family_label = f"{entry.family_code} — {entry.family_name}" if entry.family_name else entry.family_code
        if family_label != current_family:
            current_family = family_label
            lines.append(f"## Family: {family_label}")
            lines.append("")

        lines.append(f"### {entry.code} — {entry.title}")
        lines.append(f"**Family:** {entry.family_code} — {entry.family_name}")
        lines.append(f"**Canonical verbs:** {', '.join(entry.canonical_verbs) if entry.canonical_verbs else 'N/A'}")
        lines.append(f"**Scope descriptors:** {', '.join(entry.scope_descriptors) if entry.scope_descriptors else 'N/A'}")
        lines.append(f"**Level signal:** {entry.level_signal}")
        lines.append(f"**Common Job Code Titles:** {', '.join(entry.common_job_titles)}")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Extract CCOG PDF to structured markdown.")
    parser.add_argument("--pdf", type=Path, required=True, help="Path to CCOG_9_2015.pdf")
    parser.add_argument("--output", type=Path, required=True, help="Output markdown path")
    parser.add_argument("--debug", action="store_true", help="Print debug info")
    args = parser.parse_args()

    if not args.pdf.exists():
        print(f"ERROR: PDF not found: {args.pdf}", file=sys.stderr)
        return 1

    print(f"Extracting text from {args.pdf} ...")
    full_text = extract_all_text(args.pdf)
    print(f"Extracted {len(full_text)} characters from definition pages.")

    print("Parsing CCOG entries ...")
    entries = parse_entries(full_text)
    print(f"Parsed {len(entries)} entries.")

    if args.debug:
        for e in entries[:10]:
            print(f"  {e.code}: {e.title} — verbs: {len(e.canonical_verbs)}, scope: {len(e.scope_descriptors)}")

    if len(entries) < 20:
        print(f"WARNING: Only {len(entries)} entries parsed. Expected ~150+. Check PDF structure.", file=sys.stderr)

    markdown = render_markdown(entries)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(markdown, encoding="utf-8")
    print(f"Wrote {len(entries)} entries to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
