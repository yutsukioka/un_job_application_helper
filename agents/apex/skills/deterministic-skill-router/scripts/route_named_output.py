#!/usr/bin/env python3
"""Conservative requested-output gate for named UNESCO outputs and UNOPS Option 10.

This is a diagnostic pre-route, not a replacement for the general router.
DEFER means continue its existing rules; NO_MATCH excludes content generation
for a research/explanation/installation/status request about these capabilities.
"""
from __future__ import annotations

import argparse
import json
import re

NAMES = ("apex-generate-unesco-employment-history", "apex-select-domain-of-expertise", "apex-curate-publications", "apex-unops-application-fit")
VERBS = r"(?:use|run|invoke|generate|create|make|prepare|draft|produce|regenerate|update|revise|fill|complete|select|recommend|review|assess|curate)"
SOURCE = re.compile(r"\s+(?:from|based on|using|with evidence from|against)\b", re.I)


def route(request: str) -> dict:
    text = " ".join(request.casefold().split()).strip()
    text = re.sub(r"^(?:please\s+)?(?:can|could|would) you\s+", "", text)
    text = re.sub(r"^please\s+", "", text)
    relevant = any(term in text for term in (*NAMES, "unesco", "publication", "domain of expertise", "ehf", "unops", "option 10", "option10"))
    meta = (re.match(r"^(?:research|explain|investigate|install|enable|implement|build a skill|create a skill|are\b|is\b|what\b|how\b|do you\b)", text)
            or re.search(r"\b(?:installed|installation|ready to use|ready for use)\b", text))
    if 'unops' in text and re.search(
        r"\b(?:set up|setup|adapt|install|implement)\b.*\b(?:skill|package|pack|environment)\b"
        r"|\b(?:create|build|update|revise)\b.*\b(?:skill|skill package|skill pack)\b", text
    ):
        meta = True
    if relevant and meta:
        return {"decision": "NO_MATCH", "skill": None, "reason": "Capability research/status/installation is not applicant-content generation"}
    target = SOURCE.split(text, maxsplit=1)[0]
    for name in NAMES:
        explicit = re.fullmatch(r"\$?" + re.escape(name) + r"[.!]?", target)
        operation = re.match(r"^" + VERBS + r"\s+(?:the\s+)?\$?" + re.escape(name) + r"\b", target)
        if explicit or operation:
            return {"decision": "MATCH", "skill": name, "reason": "Explicit requested skill"}
    match = re.match(r"^" + VERBS + r"\s+(?:(?:a|an|the|my|our|these)\s+)?(.+)$", target)
    if not match:
        return {"decision": "DEFER", "skill": None, "reason": "No named-output target"}
    output = match.group(1)
    if re.match(r"(?:phase 8\s+)?option\s*10\b", output) or re.match(
        r"unops\s+(?:application fit|fit plan|position areas?|skills?(?: mapping| selection| list)?|role/skills companion)\b", output
    ):
        skill = NAMES[3]
    elif re.match(r"(?:unesco\s+)?(?:employment history form|ehf)\b", output) and ("unesco" in text or output.startswith("ehf")):
        skill = NAMES[0]
    elif re.match(r"(?:unesco\s+)?(?:domain(?:s)? of expertise|expertise (?:selection|selections|entries|domains))\b", output):
        skill = NAMES[1]
    elif re.match(r"(?:unesco\s+)?(?:publication(?:s)?|bibliography|publication and written-output list)\b", output):
        skill = NAMES[2]
    else:
        return {"decision": "DEFER", "skill": None, "reason": "Requested output belongs to another route"}
    return {"decision": "MATCH", "skill": skill, "reason": "Requested output before source clauses"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("request")
    args = parser.parse_args()
    print(json.dumps(route(args.request), indent=2))


if __name__ == "__main__":
    main()
