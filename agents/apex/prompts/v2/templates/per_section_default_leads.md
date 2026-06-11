# Per-section default leads — quotable snippet

This block is designed to be quoted verbatim in C1 / C2 consensus
prompts. Any consensus round must obey the leads below; overrides
require evidence cites (see [`agents/apex/spec/02_tier_b_personas.md` §B1](../../../spec/02_tier_b_personas.md)).

```
PER-SECTION DEFAULT LEADS

| Section                                                | Default lead                                    | Other agents' role                                                        |
|--------------------------------------------------------|-------------------------------------------------|---------------------------------------------------------------------------|
| Admin Profile — duties/responsibilities body           | screening-lead                                  | ats-format-lead: keyword swaps; technical-lead: register flags            |
| Admin Profile — Direct Reports / Reason for Leaving    | screening-lead                                  | factual, low-disagreement                                                 |
| CV — Summary / UVP line                                | ats-format-lead                                 | others propose alternative phrasings                                      |
| CV — Experience bullets                                | screening-lead (content) + ats-format-lead (kw) | technical-lead: technical accuracy review                                 |
| Cover letter — narrative arc                           | screening-lead                                  | others comment, do not rewrite                                            |
| Cover letter — technical paragraph                     | technical-lead                                  | others comment                                                            |
| Qualification answers                                  | screening-lead                                  | ats-format-lead: final char-fit pass                                      |
| Motivation statement (VACC)                            | screening-lead (V/A/C) + technical-lead (Comp)  | ats-format-lead: char-fit                                                 |
| Competency mapping                                     | technical-lead                                  | others cross-check                                                        |

OVERRIDE RULE
- Non-lead agents may flag concrete defects, not preferences.
- Overrides require an evidence cite: a JD line number OR a strategy-report section ID.
- Overrides without evidence are dropped by qa-auditor during consensus.

QA-AUDITOR ROLE IN CONSENSUS
- Verify all flags addressed or explicitly dismissed with reason.
- Verify merged section passes lint / char / placeholder checks.
- Verify no new claims introduced beyond the unified Phase 1-7 report.
- Do NOT pick winners on style.
```
