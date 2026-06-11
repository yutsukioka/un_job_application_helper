# Tier E — Integrity guardians (deep dive)

> Companion to [00_consolidated_plan.md §"Tier E"](00_consolidated_plan.md).

## E1. `metric-lineage-guardian` micro-agent

### Contract

- **Owns:** `metric_ledger.md` (and its `metric_ledger.json` twin).
- **Authored once:** during P0a, by `screening-lead` as writer.
- **Frozen:** during S1/S2/S3, C1, D1/D2/D3, C2.
- **Enforces:** any generator output containing a metric is rejected
  if either:
  - the metric value is not present in the ledger, OR
  - the metric value matches but the **scope** (e.g., budget unit,
    timeframe, team size denominator) differs from the ledger entry.

### Ledger entry schema

```yaml
- metric_id: BUDGET_UNICEF_2022
  value: "USD 1.8M"
  scope: "annual programme budget, UNICEF Country Office, FY2022"
  source: "JOB_HISTORY §UNICEF 2022"
  allowed_phrasings:
    - "$1.8M"
    - "USD 1.8 million"
    - "1.8M USD annual programme budget"
```

### Rejection behavior

When a generator produces text with a metric not matching any ledger
entry, the guardian:

1. Annotates the offending line with `<!-- METRIC_LINEAGE: unknown -->`.
2. Prevents `qa-auditor` test-result PASS until either:
   - the generator rewrites the line using a ledger-approved phrasing, or
   - the user adds a new ledger entry (only via P0a re-run, not in-place).

## E2. JD coverage controls

The workflow uses three complementary checks. E2a is deterministic phrase
coverage at C2; E2b and E2c are deeper semantic checks at R2 after independent
evaluation and panel-response artifacts exist.

### E2a. Phrase coverage hard floor

#### Calculation

```
jd_coverage_score =
  (count of TERM_EXTRACTOR 5★ terms appearing at least once across the
   unified Phase 8 outputs)
  /
  (total count of TERM_EXTRACTOR 5★ terms)
```

Optionally extended to include `JD_KEYWORD_BANK` Tier-1 phrases with
configurable weight.

#### Floor

- Default: `0.70` (70% of 5★ terms must appear).
- Configurable per vacancy via `## RUN_MODE` extension:

```
## RUN_MODE
JD_COVERAGE_FLOOR: 0.70
```

#### Enforcement

- Calculated by `qa-auditor` on consensus server C2 after merging.
- If `jd_coverage_score < floor`, `qa-auditor`'s test-result is FAIL
  with a structured list of missing terms; C2 loops back to IMPLEMENT
  for one revision pass (bounded by `MAX_REVISION_PASSES`).
- If still below floor after the bounded loop, finalization is
  **blocked** and the missing terms are surfaced to the user.

### E2b. Requirement-by-requirement coverage matrix

At R2, `qa-auditor` maps each core JD requirement from
`phase1_2_core_requirements.md` across the canonical Admin Profile, CV, Cover
Letter, Qualification Answers, and Motivation Statement if present. Each
requirement is marked `HIGH`, `MEDIUM`, `LOW`, or `GAP`; any `GAP` requires a
remediation-plan entry.

### E2c. Unsupported-claim scan

At R2, `qa-auditor` checks factual claims in canonical outputs against
`inputs/application_context.md` and `metric_ledger.md`. Claims with no support
are flagged as `UNSUPPORTED`; claims that conflict with metric lineage are
flagged as `CONTRADICTORY`.

### Why only E2a uses numeric scoring

Keyword presence is a Boolean fact, not an aesthetic judgment. Replacing
subjective "keyword integrity" guidance with a numeric floor is the one
place numeric scoring genuinely works. E2b and E2c are structured semantic
checks, not numeric score forecasts.
