# Filter Usage

## Common searches

Nairobi international P2-P4:

```bash
jobagg search --city Nairobi --country KE --scope international --grade P2 --grade P3 --grade P4
```

Remote internships:

```bash
jobagg search --work-modality home_based --contract-category internship_unknown
```

UNV Specialist roles:

```bash
jobagg search --source-id unv_uvp --unv-category un_volunteer_specialist
```

Local G-level logistics/procurement support:

```bash
jobagg search --ccog-family 2.2.06 --grade-family G
```

Consultant jobs in Africa:

```bash
jobagg search --contract-category consultant --region Africa
```

## Explain and debug

Include per-filter evidence for matched rows:

```bash
jobagg search --city Nairobi --country KE --scope international --grade P2 --grade P3 --grade P4 --explain
```

Explain one job against a saved or ad-hoc query:

```bash
jobagg search-debug --job-key unicef_pageup:12345 --city Nairobi --country KE --scope international --grade P2 --grade P3 --grade P4
```

## Classification audit

Audit all open jobs:

```bash
jobagg audit-classification --all --format markdown --output reports/classification_audit.md
```

Audit one source:

```bash
jobagg audit-classification --source-id unv_uvp --format markdown
```

## Saved searches

Save the Nairobi international P2-P4 search:

```bash
jobagg saved-search add nairobi-international-p2-p4 --city Nairobi --country KE --scope international --grade P2 --grade P3 --grade P4
```

Run it:

```bash
jobagg saved-search run nairobi-international-p2-p4 --format markdown --output outputs/nairobi_p2_p4.md
```

Run all saved searches:

```bash
jobagg saved-search run --all --format json --output outputs/saved_search_runs.json
```

List and remove saved searches:

```bash
jobagg saved-search list
jobagg saved-search remove nairobi-international-p2-p4
```

## Confidence

High confidence usually means the value came from a direct source field.
Medium confidence usually means the value was derived from a title or structured text.
Low confidence usually means the classifier made a weak text inference.

By default, search filters exclude low-confidence grade and location matches.
Use `--include-low-confidence` only for exploratory searches.
