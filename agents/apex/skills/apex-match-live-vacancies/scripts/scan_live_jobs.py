#!/usr/bin/env python3
"""Read-only vacancy inventory and broad review triage; never a final fit decision.

Only the standard library is required.  The database is opened mode=ro, in one
read transaction.  All open records are considered on every run; force IDs also
retrieve non-open records, for reconciliation with an existing workbook.
"""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import closing
from datetime import date, datetime, time, timedelta, timezone
import hashlib
from html import unescape
import json
from pathlib import Path
import re
import sqlite3
import sys
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

UTC = timezone.utc
ROOT = Path(__file__).resolve().parents[5]
VERSION = "live-vacancy-scan-1"

# Discovery families, not qualification tests.  A body match is as eligible for
# human review as a title match; finance/procurement/HR are intentionally present.
FAMILIES = {
    "social_protection_cash": r"social (?:protection|policy|assistance)|cash (?:transfer|assistance|program)|\bHOPE\b|universal health coverage",
    "monitoring_evaluation": r"monitoring (?:and|&) evaluation|\bM&E\b|\bMEAL\b|evaluat(?:ion|or)|results.framework|impact assessment",
    "programme_project": r"program(?:me)? (?:management|manager|officer|specialist|coordination|delivery|design)|project (?:management|manager|coordination|implementation|delivery|planning)",
    "grants_partnerships": r"\bgrants?\b|partnership|donor (?:relations|report|coordination)|resource mobili[sz]|fundraising|fund.raising|external relations",
    "finance_accounting": r"\bfinanc(?:e|ial)\b|\baccounting\b|\bbudget(?:ing|s)?\b|cash management|reconciliation|treasury|disbursement",
    "procurement_supply": r"procurement|purchasing|supply.chain|supply management|logistics|vendor|tender|contract management",
    "risk_assurance": r"\brisk\b|\bassurance\b|\bHACT\b|internal controls?|compliance|fraud|investigat|safeguards?|accountability|grievance",
    "operations_administration": r"\boperations\b|administrat(?:ion|ive)|office management|business continuity|corporate services|support services",
    "people_management_hr": r"human resources|\bHR\b|recruit(?:ment|ing)|personnel|payroll|staff (?:management|supervision|training)|team (?:management|leadership)",
    "data_information": r"\bdata\b|information management|analytics|Power BI|Python|Stata|\bGIS\b|database|dashboard|digital (?:product|transformation)",
    "policy_research": r"policy (?:analysis|research|development|advice)|\bresearch\b|economi(?:st|cs)|public policy|evidence.based",
    "knowledge_learning": r"knowledge (?:management|sharing|products)|capacity (?:building|development)|training|learning|guidance|technical assistance",
    "humanitarian_recovery": r"humanitarian|emergency (?:response|preparedness)|refugee|displacement|resilien|recovery|disaster|crisis",
    "governance_coordination": r"governance|intergovernmental|secretariat|executive (?:support|coordination)|committee|coordination|inter.agency|stakeholder engagement",
    "environment_livelihoods": r"livelihood|sustainable development|climate|environment|forest|agricultur|rural development|\bWASH\b",
    "communications_reporting": r"communications?|public information|reporting|briefing|public speaking|advocacy|outreach",
}
LANGUAGES = (
    "English", "Japanese", "French", "Spanish", "Arabic", "Portuguese", "Russian",
    "Chinese", "German", "Korean", "Burmese", "Myanmar", "Somali", "Filipino",
    "Tagalog", "Tajik", "Malagasy", "Sinhala", "Tamil", "Swahili", "Kiswahili",
    "Hindi", "Urdu", "Bengali", "Nepali", "Dari", "Pashto", "Ukrainian",
    "Italian", "Dutch", "Turkish", "Indonesian", "Thai", "Vietnamese", "Samoan",
)
LANG_PATTERN = "(?:" + "|".join(LANGUAGES) + ")"
BOILERPLATE = re.compile(
    r"Additional Information|Terms and Conditions|UNOPS does not accept unsolicited|"
    r"UNOPS embraces diversity|The United Nations places no restrictions|"
    r"The United Nations is committed to creating a diverse|"
    r"UNICEF is committed to diversity|Reasonable accommodation|Recruitment fraud", re.I
)
SECTION_START = re.compile(
    r"Education Requirements|Experience Requirements|Required Qualifications|"
    r"Qualifications and Experience|Qualifications,? Experience|"
    r"Education and Experience|Minimum Qualifications|Technical Requirements|"
    r"Professional Experience|Selection Criteria|Required Skills", re.I
)
DEFAULT_SCOPE = {
    "national_countries": [],  # Explicit search preferences are not citizenship evidence.
    "citizenships": [],
    "work_authorized_countries": [],
    "languages": {},
    "disabled_families": [],
    "include_internships": False,
    "stale_after_days": 7,
}


def digest(value):
    data = value if isinstance(value, bytes) else str(value).encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def plain(value):
    text = re.sub(r"<\s*(?:br|/p|/div|/li|/tr|/h[1-6])\b[^>]*>", "\n", str(value or ""), flags=re.I)
    return re.sub(r"[ \t\r\f\v]+", " ", unescape(re.sub(r"<[^>]*>", " ", text))).strip()


def section(text, name):
    """Read exactly one canonical H2 section, ignoring headings inside fences."""
    result, active, fence = [], False, None
    for line in text.splitlines():
        marker = re.match(r"^ {0,3}(`{3,}|~{3,})(.*)$", line)
        if fence:
            if marker and marker[1][0] == fence[0] and len(marker[1]) >= fence[1] and not marker[2].strip():
                fence = None
            if active:
                result.append(line)
            continue
        if marker:
            fence = (marker[1][0], len(marker[1]))
        if re.match(r"^##\s+", line):
            if active:
                break
            active = line.strip() == "## " + name
        elif active:
            result.append(line)
    return "\n".join(result).strip()


def approved_evidence(text):
    """Do not use explicitly controlled/unconfirmed sub-sections as facts."""
    kept, withheld, skip_level = [], [], None
    for line in text.splitlines():
        heading = re.match(r"^(#{3,6})\s+(.*)", line)
        if heading:
            level = len(heading[1])
            if skip_level and level <= skip_level:
                skip_level = None
            if re.search(r"controlled integration|unconfirmed|requiring confirmation|hold.as.placeholder|candidate assertions?|user.submitted.*narratives|\b(?:spouse|husband|wife|household context|family members?)\b", heading[2], re.I):
                skip_level = level
        third_party = re.search(r"^\s*(?:[-*]\s*)?(?:(?:spouse|husband|wife|family member|household context)\b|partner\s*:)|\bmy (?:spouse|husband|wife|partner)\b", line, re.I)
        if skip_level or third_party or re.search(r"\[Confirm\b|\[User to Insert|Candidate Assertion|not established|not evidenced|do not claim", line, re.I):
            withheld.append(line)
        else:
            kept.append(line)
    return "\n".join(kept), len(withheld)


def load_profile(profile_path, feedback_path=None, scope_path=None):
    raw = Path(profile_path).read_text(encoding="utf-8")
    history = section(raw, "USER_JOB_HISTORY_TEXT")
    if not history:
        raise ValueError("Profile needs a populated ## USER_JOB_HISTORY_TEXT section")
    approved = section(Path(feedback_path).read_text(encoding="utf-8"), "APPROVED_UPDATES") if feedback_path else ""
    history_evidence, withheld_history = approved_evidence(history)
    approved_clean, withheld_approved = approved_evidence(approved)
    evidence = history_evidence + "\n" + approved_clean
    settings = json.loads(json.dumps(DEFAULT_SCOPE))
    language_lines = "\n".join(line for line in evidence.splitlines() if re.match(r"\s*(?:[-*]\s*)?(?:Languages?|Language proficiency|My languages?)\s*:", line, re.I))
    for language in LANGUAGES:
        matches = re.findall(r"\b" + language + r"\s*\(([^)]+)\)", language_lines, re.I)
        if matches:
            settings["languages"][language.lower()] = matches[-1].lower()
    scope = json.loads(Path(scope_path).read_text(encoding="utf-8")) if scope_path else {}
    if set(scope) - set(settings):
        raise ValueError("Unknown scope keys: " + ", ".join(sorted(set(scope) - set(settings))))
    settings.update(scope)
    for key in ("national_countries", "citizenships", "work_authorized_countries", "disabled_families"):
        if not isinstance(settings[key], list):
            raise ValueError(key + " must be a list")
    if not isinstance(settings["languages"], dict):
        raise ValueError("languages must be a mapping")
    if not isinstance(settings["include_internships"], bool):
        raise ValueError("include_internships must be boolean")
    if not isinstance(settings["stale_after_days"], (int, float)) or settings["stale_after_days"] <= 0:
        raise ValueError("stale_after_days must be positive")
    settings["languages"] = {str(k).lower(): str(v).lower() for k, v in settings["languages"].items()}
    lines = [plain(line) for line in evidence.splitlines() if plain(line)]
    families = {}
    for family, pattern in FAMILIES.items():
        if family in settings["disabled_families"]:
            continue
        snippets = [line for line in lines if re.search(pattern, line, re.I)]
        if snippets:
            families[family] = [snippet[:400] for snippet in snippets[:3]]
    manifest = {
        "profile_path": str(Path(profile_path).resolve()),
        "profile_section": "USER_JOB_HISTORY_TEXT",
        "profile_section_sha256": digest(history),
        "feedback_path": str(Path(feedback_path).resolve()) if feedback_path else None,
        "feedback_section": "APPROVED_UPDATES" if feedback_path else None,
        "approved_updates_sha256": digest(approved) if feedback_path else None,
        "used_evidence_sha256": digest(evidence),
        "withheld_lines": withheld_history + withheld_approved,
        "scope": settings,
        "scope_sha256": digest(json.dumps(settings, sort_keys=True)),
        "families": families,
        "boundary": "No target JD, admin profile, unresolved feedback or generated output used as applicant evidence. Family snippets indicate discovery evidence, not proof of every requirement.",
    }
    return {"settings": settings, "families": families, "manifest": manifest}


def parse_datetime(value):
    """Parse supported dates without lexicographic comparisons or guessed offsets."""
    if not value:
        return None
    value = str(value).strip()
    numeric = re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{4})", value)
    if numeric:
        first, second = int(numeric[1]), int(numeric[2])
        # Neither the locale nor the numeric field order is established.  A
        # plausible day/month swap must stay unresolved, never become expired.
        if first <= 12 and second <= 12 and first != second:
            return None
        if second > 12 and first <= 12:
            try:
                return datetime.strptime(value, "%m/%d/%Y")
            except ValueError:
                return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        pass
    for fmt in ("%d-%b-%Y", "%d %b %Y", "%d %B %Y", "%B %d, %Y", "%d/%m/%Y",
                "%d/%b/%Y, %I:%M:%S %p", "%d/%b/%Y, %H:%M:%S", "%d-%b-%Y %H:%M", "%d %b %Y %H:%M",
                "%d-%b-%Y, %I:%M:%S %p", "%d-%b-%Y, %I:%M %p", "%d-%b-%Y, %H:%M:%S", "%d-%b-%Y, %H:%M"):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    return None


def get_zone(value):
    if not value:
        return None
    try:
        return ZoneInfo(str(value))
    except (ZoneInfoNotFoundError, ValueError):
        m = re.fullmatch(r"(?:UTC|GMT)?([+-])(\d{1,2}):?(\d{2})?", str(value))
        if m:
            minutes = int(m[2]) * 60 + int(m[3] or 0)
            if minutes <= 14 * 60:
                return timezone(timedelta(minutes=minutes * (1 if m[1] == "+" else -1)))
    return None


def deadline(row, now):
    zone_name = row.get("closes_tz")
    zone = get_zone(zone_name)
    sources = [(key, row.get(key)) for key in ("closes_at", "closes_at_local") if row.get(key)]
    # Employer notice date can reveal a bad normalized database instant.
    published = re.search(r"Posting End Date\s+([0-9]{1,2}[- ][A-Za-z]{3,9}[- ][0-9]{4})", plain(row.get("description")), re.I)
    if published:
        sources.append(("notice_posting_end_date", published[1]))
    parsed, issues = [], []
    if zone_name and zone is None:
        issues.append("unrecognized_timezone")
    for source, value in sources:
        dt = parse_datetime(value)
        if dt is None:
            issues.append("unparsed_deadline:" + source)
            continue
        timed = bool(re.search(r"(?:T|\s|,)\s*\d{1,2}:\d{2}", str(value)))
        if timed and dt.tzinfo is None and zone:
            dt = dt.replace(tzinfo=zone)
        kind = "instant" if timed and dt.tzinfo else "local_datetime" if timed else "date_only"
        parsed.append({"source": source, "published": str(value), "kind": kind, "value": dt})
    instants = [p["value"].astimezone(UTC) for p in parsed if p["kind"] == "instant"]
    dates = [p["value"].date() for p in parsed if p["kind"] != "instant"]
    if len(set(instants)) > 1:
        issues.append("conflicting_deadline_instants")
    if len(set(dates)) > 1:
        issues.append("conflicting_published_dates")
    if instants and dates:
        comparison = instants[0].astimezone(zone).date() if zone else instants[0].date()
        if any(d != comparison for d in dates):
            issues.append("instant_vs_published_date_conflict")
    if any(p["kind"] == "local_datetime" for p in parsed):
        issues.append("local_time_without_known_timezone")
    if issues:
        state = "deadline_review"
    elif instants:
        state = "expired" if instants[0] <= now else "future_instant"
    elif dates:
        day = dates[0]
        if zone:
            local_today = now.astimezone(zone).date()
            state = "expired" if day < local_today else "closing_date_review" if day == local_today else "future_date"
        else:
            # The date's end in UTC-12 is its latest possible worldwide cutoff.
            latest = datetime.combine(day + timedelta(days=1), time(12), UTC)
            state = "expired" if latest <= now else "closing_date_review" if day <= now.date() else "future_date"
        issues.append("date_only_cutoff_unverified")
    else:
        state = "deadline_review"
        issues.append("missing_deadline")
    return {
        "state": state, "issues": issues, "timezone": zone_name,
        "utc_instant": instants[0].isoformat() if len(set(instants)) == 1 else None,
        "published_date": dates[0].isoformat() if len(set(dates)) == 1 else None,
        "sources": [{**p, "value": p["value"].isoformat()} for p in parsed],
    }


def substantive_text(row):
    body = plain(row.get("description"))
    body = BOILERPLATE.split(body, maxsplit=1)[0]
    requirements = SECTION_START.search(body)
    if requirements:
        return body[requirements.start():], "requirements_sections"
    # Duties still count when an employer does not use standardized headings.
    start = re.search(r"Duties and Responsibilities|Functions / Key Results Expected|Responsibilities|Job Purpose|Role Purpose|What you will do", body, re.I)
    if start:
        body = body[start.start():]
    # Exclude generic competency libraries from discovery matching.
    body = re.split(r"\bCompetencies\b|\bCore Values\b|\bCore Competencies\b", body, maxsplit=1, flags=re.I)[0]
    return body, "duties_or_unstructured_notice"


def family_matches(row, profile):
    title = plain(row.get("title"))
    requirements, basis = substantive_text(row)
    found = []
    for family, evidence in profile["families"].items():
        pattern = FAMILIES[family]
        title_hit = re.search(pattern, title, re.I)
        requirement_hit = re.search(pattern, requirements, re.I)
        if title_hit or requirement_hit:
            hit = requirement_hit
            excerpt = requirements[max(0, hit.start() - 90):hit.end() + 190] if hit else title
            found.append({"family": family, "title_signal": bool(title_hit), "body_signal": bool(requirement_hit),
                          "body_basis": basis, "vacancy_excerpt": excerpt,
                          "profile_evidence": evidence[:2]})
    return found


def contract_category(row):
    body = plain(row.get("description"))
    header = body[:3500]
    match = re.search(r"Contract Type\s+(.+?)(?=Posting Start Date|Duration|Job Highlight|About the|\n\n|$)", header, re.I)
    authoritative = match[1].strip() if match else None
    if authoritative:
        text, source = authoritative, "notice_contract_header"
    else:
        explicit = re.search(r"\b(?:LICA|IICA|IPSA|NPSA)[ -]?\d{1,2}\b|\blocally recruited\b|\binternationally recruited\b|\bnational officer\b|\binternship\b", header, re.I)
        if explicit:
            text, source = explicit[0], "notice_explicit_text"
        else:
            text = " ".join(str(row.get(k) or "") for k in ("grade_code", "national_international", "contract_category", "standard_scope"))
            source = "database_classification_unverified"
    if re.search(r"\bLICA\b|\bNPSA\b|\bLICA\d|\bNPSA\d|\bGS[- ]?\d|\bG[- ]?\d|\bNO[- ]?[A-D1-4]\b|national|locally recruited|local /", text, re.I) and not re.search(r"international", text, re.I):
        category = "local"
    elif re.search(r"\bLICA|\bNPSA|Staff - GS", text, re.I):
        category = "local"
    elif re.search(r"internship|\bintern\b|trainee", text + " " + str(row.get("title") or ""), re.I):
        category = "internship"
    elif re.search(r"IICA|IPSA|Staff - IP|\bIP P|\bP[- ]?[1-5]\b|\bD[- ]?[12]\b|international", text, re.I):
        category = "international"
    else:
        category = "unknown"
    classified = str(row.get("national_international") or "").lower()
    conflict = (category == "local" and classified == "international") or (category == "international" and classified in {"local", "national"})
    return {"category": category, "source": source, "published_contract": authoritative or text,
            "classification_conflict": conflict}


def language_level(value):
    if re.search(r"native|mother tongue", value, re.I):
        return 5
    if re.search(r"fluent|professional|advanced|expert|proficient", value, re.I):
        return 4
    if re.search(r"intermediate|working|good knowledge", value, re.I):
        return 3
    if re.search(r"basic|limited|beginner", value, re.I):
        return 2
    return 0


def language_requirements(text, known):
    text = BOILERPLATE.split(plain(text), maxsplit=1)[0]
    results = []
    # Avature table flattened to text: independently parse each language row so
    # "English Fluent Required French Fluent Desirable" cannot contaminate flags.
    table_pattern = re.compile(r"\b(" + LANG_PATTERN + r")\s+(Native|Fluent|Advanced|Intermediate|Basic|Expert|Working(?: knowledge)?)\s+(Required|Desirable|Desired|Preferred)\b", re.I)
    covered = []
    for match in table_pattern.finditer(text):
        lang, level, requirement = match.groups()
        results.append({"language": lang.lower(), "level": level.lower(), "requirement": "required" if requirement.lower() == "required" else "desirable",
                        "evidence": match[0], "alternative": False})
        covered.append(match.span())
    # Remove parsed rows, retaining surrounding prose.
    for start, end in reversed(covered):
        text = text[:start] + " " * (end - start) + text[end:]
    for clause in re.split(r"(?<=[.;])\s+|\n", text):
        names = re.findall(r"\b" + LANG_PATTERN + r"\b", clause, re.I)
        if not names or not re.search(r"required|essential|mandatory|desirable|desired|preferred|an asset|must|fluency|fluent|working knowledge", clause, re.I):
            continue
        if len(clause) > 600:
            # A huge flattened clause cannot safely establish a language veto.
            continue
        desired = bool(re.search(r"desirable|desired|preferred|an asset|advantage", clause, re.I))
        required = bool(re.search(r"required|essential|mandatory|\bmust\b", clause, re.I))
        requirement = "ambiguous" if desired and required else "desirable" if desired else "required" if required else "ambiguous"
        level = re.search(r"native|fluent|fluency|advanced|expert|intermediate|working knowledge|basic|limited", clause, re.I)
        level_text = level[0].lower().replace("fluency", "fluent") if level else "unspecified"
        alternatives = bool(re.search(r"\bor\b|either|one of", clause, re.I)) and len(set(n.lower() for n in names)) > 1
        for language in names:
            results.append({"language": language.lower(), "level": level_text, "requirement": requirement,
                            "evidence": clause.strip(), "alternative": alternatives})
    unique = []
    seen = set()
    for requirement in results:
        key = tuple(requirement[k] for k in ("language", "level", "requirement", "alternative"))
        if key in seen:
            continue
        seen.add(key)
        proficiency = known.get(requirement["language"], "")
        minimum = language_level(requirement["level"])
        requirement["profile_proficiency"] = proficiency or None
        requirement["evidence_status"] = "not_established" if not proficiency else "level_unresolved" if minimum == 0 else "supported" if language_level(proficiency) >= minimum else "below_stated_level"
        unique.append(requirement)
    return unique


def eligibility(row, profile):
    body = plain(row.get("description"))
    contract = contract_category(row)
    flags = []
    if contract["classification_conflict"]:
        flags.append("contract_classification_conflict_notice_takes_precedence")
    internal = re.search(r"(?:Vacancy Type\s+)?Internal candidates only|open (?:only|exclusively) to (?:internal|current)|only (?:internal|current (?:UNOPS|staff)) candidates", body, re.I)
    if internal:
        flags.append("internal_eligibility_not_established")
    clauses = re.split(r"(?<=[.;])\s+|\n", body)
    restrictions = [c.strip() for c in clauses if re.search(
        r"(?:only|must|required|eligible|restricted|open to|nationalit|citizen|work permit).{0,110}(?:nationals?|citizen|residen|work permit|right to work)|"
        r"(?:nationals?|citizen|residen|work permit|right to work).{0,110}(?:only|must|required|eligible|restricted)|"
        r"commuting area|locally recruited", c, re.I)]
    # General diversity references to nationalities are not restrictions.
    restrictions = [c for c in restrictions if not re.search(r"wide range of nationalities|regardless of|diversity|equal employment", c, re.I)]
    if restrictions:
        flags.append("nationality_residence_or_work_rights_review")
    location = plain(row.get("location"))
    remote = bool(re.search(r"home[ -]based|\bremote\b|work from home", location + " " + str(row.get("title") or ""), re.I)) or row.get("work_modality") == "remote"
    if re.search(r"not remote|remote\s*[:=]\s*no", location + " " + str(row.get("title") or ""), re.I):
        remote = False
    if contract["category"] == "local":
        flags.append("local_contract_eligibility_unresolved")
        if remote:
            flags.append("home_based_does_not_establish_global_eligibility")
    elif contract["category"] == "unknown":
        flags.append("recruitment_scope_unresolved")
    if contract["category"] == "internship" and not profile["settings"]["include_internships"]:
        flags.append("career_stage_review_internship")
    languages = language_requirements(body, profile["settings"]["languages"])
    unsupported = [x for x in languages if x["requirement"] == "required" and x["evidence_status"] != "supported" and not x["alternative"]]
    if unsupported:
        flags.append("required_language_evidence_gap")
    if any(x["alternative"] or x["requirement"] == "ambiguous" for x in languages):
        flags.append("language_clause_requires_manual_review")
    if not languages:
        flags.append("language_requirements_not_extracted")
    return {"contract": contract, "remote": remote, "flags": flags, "languages": languages,
            "internal_restriction": internal[0] if internal else None,
            "local_restriction_excerpts": restrictions[:8],
            "scope_note": "Local category is not a citizenship-only finding. Confirm residence/work rights and employer rules. Profile national_countries is a search preference, not citizenship."}


def read_gate(path):
    if not path.exists():
        raise ValueError("Publication-state file is missing: " + str(path))
    state = json.loads(path.read_text(encoding="utf-8"))
    if state.get("database_transactions_complete") is not True:
        raise ValueError("Publication gate refuses scan: database_transactions_complete is not true")
    if not state.get("generation_id"):
        raise ValueError("Publication-state file has no generation_id")
    return state


def snapshot(database, forced_ids=(), publication_path=None):
    database = Path(database).resolve(strict=True)
    gate_path = Path(publication_path) if publication_path else database.parent / ".jobagg-publication-state.json"
    before = read_gate(gate_path)
    with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as db:
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA query_only=ON")
        db.execute("BEGIN")
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "jobs" not in tables:
            raise ValueError("Database has no jobs table")
        cols = {r[1] for r in db.execute("PRAGMA table_info(jobs)")}
        required = {"job_key", "title", "status", "description"}
        if required - cols:
            raise ValueError("Missing jobs columns: " + ", ".join(sorted(required - cols)))
        wanted = ["job_key", "source_id", "org_id", "external_id", "title", "location", "department", "employment_type", "status",
                  "posted_at", "closes_at", "closes_at_local", "closes_tz", "apply_url", "source_url", "description",
                  "first_seen_at", "last_seen_at", "source_latest_observed_at", "source_listed_current", "trusted_current",
                  "application_ready", "duplicate_of_job_key", "canonical_job_key", "stale_current", "source_freshness_status",
                  "source_health_status", "detail_quality_status", "deadline_state"]
        select = ["j." + name for name in wanted if name in cols]
        join = ""
        if "vacancy_classifications" in tables:
            ccols = {r[1] for r in db.execute("PRAGMA table_info(vacancy_classifications)")}
            if "vacancy_id" in ccols:
                select += ["c." + name for name in ["grade_code", "grade_family", "national_international", "country_iso2", "country", "work_modality", "contract_category", "standard_scope"] if name in ccols]
                join = " LEFT JOIN vacancy_classifications c ON c.vacancy_id=j.job_key"
        status_counts = dict(db.execute("SELECT lower(status), count(*) FROM jobs GROUP BY lower(status)"))
        params, force_where = [], ""
        forced_ids = sorted(set(forced_ids))
        if forced_ids:
            placeholders = ",".join("?" for _ in forced_ids)
            force_where = " OR j.job_key IN (" + placeholders + ")"
            params += forced_ids
            if "external_id" in cols:
                force_where += " OR CAST(j.external_id AS TEXT) IN (" + placeholders + ")"
                params += forced_ids
        rows = [dict(r) for r in db.execute("SELECT " + ",".join(select) + " FROM jobs j" + join + " WHERE lower(j.status)='open'" + force_where + " ORDER BY j.job_key", params)]
        data_version = db.execute("PRAGMA data_version").fetchone()[0]
        after = read_gate(gate_path)
    identity_keys = ("generation_id", "observation_set_sha256", "plan_sha256", "request_identity_sha256")
    if any(before.get(key) != after.get(key) for key in identity_keys):
        raise ValueError("Publication generation changed during read; discard scan and rerun")
    if len({r["job_key"] for r in rows}) != len(rows):
        raise ValueError("Duplicate vacancy classification join rows; reconcile before scanning")
    missing = [id_ for id_ in forced_ids if not any(id_ in (str(r["job_key"]), str(r.get("external_id"))) for r in rows)]
    metadata = {
        "database": str(database), "sqlite_data_version": data_version,
        "read_mode": "mode=ro; query_only; single read transaction",
        "database_status_counts": status_counts,
        "publication_state_path": str(gate_path.resolve()),
        "publication_before": before, "publication_after": after,
        "publication_generation_consistent": True,
        "database_transactions_complete": True,
        "exports_ready": after.get("state") == "complete",
        "export_readiness_note": "Exports ready" if after.get("state") == "complete" else "Database transaction snapshot permitted; publication exports are not ready. Do not use incomplete exported files.",
        "force_ids": forced_ids, "missing_force_ids": missing,
        "snapshot_record_count": len(rows),
        "full_open_scan": True,
        "open_rows_read": sum(str(r.get("status")).lower() == "open" for r in rows),
        "forced_non_open_rows_read": sum(str(r.get("status")).lower() != "open" for r in rows),
    }
    if metadata["open_rows_read"] != status_counts.get("open", 0):
        raise ValueError("Open-row coverage mismatch")
    return rows, metadata


def triage(row, profile, now, forced=False):
    matched = family_matches(row, profile)
    closing = deadline(row, now)
    gates = eligibility(row, profile)
    review_flags = list(gates["flags"])
    body = plain(row.get("description"))
    if len(body) < 300 or str(row.get("detail_quality_status") or "").lower() in {"missing", "incomplete", "partial", "failed", "stub"}:
        review_flags.append("incomplete_description")
    if closing["state"] in {"deadline_review", "closing_date_review"}:
        review_flags.append("deadline_unresolved")
    observed = parse_datetime(row.get("source_latest_observed_at") or row.get("last_seen_at"))
    if not observed or not observed.tzinfo or (now - observed.astimezone(UTC)).total_seconds() > profile["settings"]["stale_after_days"] * 86400:
        review_flags.append("freshness_needs_verification")
    if row.get("source_listed_current") in (0, False) or row.get("stale_current") in (1, True):
        review_flags.append("current_listing_needs_verification")
    if row.get("trusted_current") in (0, False):
        review_flags.append("database_trust_flag_unset")
    if str(row.get("status")).lower() != "open":
        disposition, reason = "closed_or_expired", "Stored status is not open; forced inclusion retains this record for reconciliation."
    elif closing["state"] == "expired":
        disposition, reason = "closed_or_expired", "Parsed deadline has passed without a detected date/time conflict."
    elif row.get("duplicate_of_job_key"):
        disposition, reason = "duplicate_review", "Potential duplicate; reconcile canonical vacancy before adding."
    elif matched:
        eligibility_flags = [f for f in review_flags if f not in {"language_requirements_not_extracted", "database_trust_flag_unset"}]
        disposition = "eligibility_review" if eligibility_flags else "candidate_review"
        reason = "Applicant evidence overlaps substantive vacancy signals; mandatory criteria and actual experience depth need human assessment."
    elif forced or "incomplete_description" in review_flags:
        disposition, reason = "manual_review", "Forced/prior record or insufficient vacancy text; no automatic rejection on missing discovery terms."
    else:
        disposition, reason = "not_prioritized", "No enabled evidence-family overlap detected; this is a reviewable triage residual, not a finding of ineligibility."
    full = {k: v for k, v in row.items() if k != "raw_json"}
    full.update({"description": body, "description_sha256": digest(body), "forced_review": forced,
                 "deadline": closing, "eligibility": gates, "family_matches": matched,
                 "triage": {"disposition": disposition, "reason": reason, "review_flags": sorted(set(review_flags)),
                            "final_fit": None, "human_assessment_required": forced or disposition in {"candidate_review", "eligibility_review", "manual_review", "duplicate_review"}}})
    summary = {k: v for k, v in full.items() if k != "description"}
    return summary, full


def scan(database, profile_path, output_dir, *, feedback_path=None, scope_path=None, as_of=None, forced_ids=(), publication_path=None):
    now = as_of or datetime.now(UTC)
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("--as-of must have an explicit timezone")
    now = now.astimezone(UTC)
    profile = load_profile(profile_path, feedback_path, scope_path)
    rows, metadata = snapshot(database, forced_ids, publication_path)
    projected_hash = hashlib.sha256()
    inventory, candidates = [], []
    for row in rows:
        projected_hash.update(json.dumps(row, sort_keys=True, ensure_ascii=False).encode("utf-8") + b"\n")
        forced = any(id_ in (str(row["job_key"]), str(row.get("external_id"))) for id_ in forced_ids)
        summary, full = triage(row, profile, now, forced)
        inventory.append(summary)
        if full["triage"]["human_assessment_required"]:
            candidates.append(full)
    metadata.update({"version": VERSION, "as_of": now.isoformat(), "created_at": datetime.now(UTC).isoformat(),
                     "snapshot_projection_sha256": projected_hash.hexdigest(),
                     "disposition_counts": dict(Counter(r["triage"]["disposition"] for r in inventory)),
                     "candidate_record_count": len(candidates), "inventory_record_count": len(inventory),
                     "candidate_scope": "Broad function overlap or forced/uncertain records; all require human judgment. Not a final shortlist.",
                     "source_profile": profile["manifest"]})
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    hashes = {}
    for name, records in (("inventory.json", inventory), ("candidates.json", candidates)):
        payload = json.dumps({"metadata": metadata, "records": records}, ensure_ascii=False, indent=2)
        (output / name).write_text(payload + "\n", encoding="utf-8")
        hashes[name] = digest((payload + "\n").encode("utf-8"))
    manifest = {**metadata, "output_sha256": hashes,
                "limitations": ["No network/portal verification performed by this helper.",
                                "Scope, language and specialist flags are evidence for human review, not final eligibility decisions.",
                                "Unmatched families stay in the complete inventory and are not silently excluded.",
                                "Snapshot hash covers selected row content, not the entire mutable SQLite file."]}
    (output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=ROOT / "private/jobagg/output/all_jobs.sqlite3")
    parser.add_argument("--profile", type=Path, default=ROOT / "private/inputs/application_context.md")
    parser.add_argument("--feedback", type=Path)
    parser.add_argument("--scope-config", type=Path, help="JSON overrides of explicitly documented scope/evidence settings")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--as-of", help="Timezone-aware ISO timestamp; does not reconstruct an older database")
    parser.add_argument("--force-id", action="append", default=[], help="Job key or external ID; include even non-open records. Repeat for prior workbook/recommendation IDs.")
    parser.add_argument("--publication-state", type=Path, help="Publication gate; defaults beside the resolved live database")
    args = parser.parse_args(argv)
    try:
        as_of = datetime.fromisoformat(args.as_of.replace("Z", "+00:00")) if args.as_of else None
        result = scan(args.database, args.profile, args.output_dir, feedback_path=args.feedback, scope_path=args.scope_config,
                      as_of=as_of, forced_ids=args.force_id, publication_path=args.publication_state)
    except (ValueError, OSError, sqlite3.Error) as exc:
        parser.exit(2, "Scan refused: " + str(exc) + "\n")
    print(json.dumps({"output_dir": str(args.output_dir.resolve()), "inventory": result["inventory_record_count"],
                      "candidates": result["candidate_record_count"], "dispositions": result["disposition_counts"],
                      "exports_ready": result["exports_ready"], "missing_force_ids": result["missing_force_ids"]}, indent=2))


if __name__ == "__main__":
    main()
